// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
)

// noopLogger and nopWriter are defined in settlement_test.go.

func TestLiveSettlementEngine_RegisterDependency(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	dep := LiveSettlementDependency{
		EventID:     "ev-001",
		MarketID:    "mkt-001",
		MarketType:  "match_winner",
		DependsOn:   "full_time",
		Description: "Match result depends on final whistle",
	}
	engine.RegisterDependency(dep)

	deps := engine.GetEventDependencies("ev-001")
	if len(deps) != 1 {
		t.Fatalf("expected 1 dependency, got %d", len(deps))
	}
	if deps[0].Status != LiveSettlementPending {
		t.Errorf("status = %s, want pending", deps[0].Status)
	}
	if deps[0].ID == "" {
		t.Error("expected auto-generated ID")
	}
}

func TestLiveSettlementEngine_ConfirmDependency(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})

	err := engine.ConfirmDependency("ev-001", "mkt-001", "official")
	if err != nil {
		t.Fatalf("confirm: %v", err)
	}

	deps := engine.GetEventDependencies("ev-001")
	if deps[0].Status != LiveSettlementConfirmed {
		t.Errorf("status = %s, want confirmed", deps[0].Status)
	}
	if deps[0].ConfirmedBy != "official" {
		t.Errorf("confirmed_by = %s, want official", deps[0].ConfirmedBy)
	}
}

func TestLiveSettlementEngine_ConfirmDependency_NotFound(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	err := engine.ConfirmDependency("ev-nope", "mkt-nope", "feed")
	if err == nil {
		t.Error("expected error for nonexistent dependency")
	}
}

func TestLiveSettlementEngine_DoubleConfirm(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})

	engine.ConfirmDependency("ev-001", "mkt-001", "feed") //nolint:errcheck

	err := engine.ConfirmDependency("ev-001", "mkt-001", "manual")
	if err == nil {
		t.Error("expected error when confirming already-confirmed dependency")
	}
}

func TestLiveSettlementEngine_SubmitResult(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})
	engine.ConfirmDependency("ev-001", "mkt-001", "feed") //nolint:errcheck

	result := LiveMarketResult{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		Results:   map[string]string{"sel-home": "won", "sel-draw": "lost", "sel-away": "lost"},
		Source:    "official",
		HomeScore: 2,
		AwayScore: 1,
	}

	err := engine.SubmitResult(result)
	if err != nil {
		t.Fatalf("submit result: %v", err)
	}

	r := engine.GetResult("ev-001", "mkt-001")
	if r == nil {
		t.Fatal("expected result, got nil")
	}
	if r.Results["sel-home"] != "won" {
		t.Errorf("sel-home result = %s, want won", r.Results["sel-home"])
	}
}

func TestLiveSettlementEngine_SubmitResult_NotConfirmed(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})

	result := LiveMarketResult{
		EventID:  "ev-001",
		MarketID: "mkt-001",
		Results:  map[string]string{"sel-home": "won"},
		Source:   "feed",
	}

	err := engine.SubmitResult(result)
	if err == nil {
		t.Error("expected error when submitting result for unconfirmed dependency")
	}
}

func TestLiveSettlementEngine_IsReadyToSettle(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})

	// Not ready: dependency not confirmed.
	if engine.IsReadyToSettle("ev-001", "mkt-001") {
		t.Error("should not be ready before confirmation")
	}

	engine.ConfirmDependency("ev-001", "mkt-001", "feed") //nolint:errcheck

	// Not ready: no result yet.
	if engine.IsReadyToSettle("ev-001", "mkt-001") {
		t.Error("should not be ready before result submission")
	}

	// Submit result.
	engine.SubmitResult(LiveMarketResult{ //nolint:errcheck
		EventID:  "ev-001",
		MarketID: "mkt-001",
		Results:  map[string]string{"sel-1": "won"},
		Source:   "official",
	})

	if !engine.IsReadyToSettle("ev-001", "mkt-001") {
		t.Error("should be ready after confirmation and result")
	}
}

func TestLiveSettlementEngine_MarkSettled(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})
	engine.ConfirmDependency("ev-001", "mkt-001", "feed") //nolint:errcheck
	engine.MarkSettled("ev-001", "mkt-001")

	deps := engine.GetEventDependencies("ev-001")
	if deps[0].Status != LiveSettlementSettled {
		t.Errorf("status = %s, want settled", deps[0].Status)
	}
}

func TestLiveSettlementEngine_ConfirmByIncident(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	// Register half-time dependency.
	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-ht",
		DependsOn: "half_time",
	})
	// Register full-time dependency.
	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-ft",
		DependsOn: "full_time",
	})

	// Half-time whistle (set_break).
	confirmed := engine.ConfirmByIncident("ev-001", "set_break", "feed")
	if confirmed != 1 {
		t.Errorf("confirmed = %d, want 1 (only half_time dep)", confirmed)
	}

	// Full-time (knockout).
	confirmed = engine.ConfirmByIncident("ev-001", "knockout", "feed")
	if confirmed != 1 {
		t.Errorf("confirmed = %d, want 1 (only full_time dep)", confirmed)
	}

	// Unrecognized incident.
	confirmed = engine.ConfirmByIncident("ev-001", "goal", "feed")
	if confirmed != 0 {
		t.Errorf("confirmed = %d, want 0 for goal (not a settlement trigger)", confirmed)
	}
}

func TestLiveSettlementEngine_PendingCount(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())

	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-001",
		DependsOn: "full_time",
	})
	engine.RegisterDependency(LiveSettlementDependency{
		EventID:   "ev-001",
		MarketID:  "mkt-002",
		DependsOn: "half_time",
	})

	if engine.PendingCount() != 2 {
		t.Errorf("pending = %d, want 2", engine.PendingCount())
	}

	engine.ConfirmDependency("ev-001", "mkt-001", "feed") //nolint:errcheck

	if engine.PendingCount() != 1 {
		t.Errorf("pending = %d, want 1 after confirming one", engine.PendingCount())
	}
}

func TestDefaultFootballMarketDeps(t *testing.T) {
	ids := map[string]string{
		"match_winner":     "mkt-1",
		"half_time_result": "mkt-2",
		"over_under":       "mkt-3",
		"both_teams_score": "mkt-4",
		"double_chance":    "mkt-5",
	}

	defs := DefaultFootballMarketDeps("ev-001", ids)
	if len(defs) != 5 {
		t.Errorf("expected 5 defs, got %d", len(defs))
	}

	// Test that empty market IDs are filtered.
	ids2 := map[string]string{
		"match_winner": "mkt-1",
		// Others missing.
	}
	defs2 := DefaultFootballMarketDeps("ev-001", ids2)
	if len(defs2) != 1 {
		t.Errorf("expected 1 def (only match_winner), got %d", len(defs2))
	}
}

func TestMapIncidentToDependency(t *testing.T) {
	tests := []struct {
		incident string
		want     string
	}{
		{"set_break", "half_time"},
		{"knockout", "full_time"},
		{"goal", ""},
		{"red_card", ""},
		{"penalty", ""},
	}
	for _, tc := range tests {
		got := mapIncidentToDependency(tc.incident)
		if got != tc.want {
			t.Errorf("mapIncidentToDependency(%q) = %q, want %q", tc.incident, got, tc.want)
		}
	}
}

func TestGetLiveSettlementStatus_Handler(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())
	handler := GetLiveSettlementStatus(engine)

	req := httptest.NewRequest(http.MethodGet, "/settle/live/status", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}

	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body) //nolint:errcheck
	if body["pending_dependencies"] == nil {
		t.Error("expected pending_dependencies in response")
	}
}

func TestRegisterDependencyHandler(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())
	handler := RegisterDependencyHandler(engine, noopLogger())

	body := `{"market_id":"mkt-001","market_type":"match_winner","depends_on":"full_time"}`
	req := httptest.NewRequest(http.MethodPost, "/settle/live/event/ev-001/register", strings.NewReader(body))
	rr := httptest.NewRecorder()

	// Use chi router to set URL params correctly.
	r := chi.NewRouter()
	r.Post("/settle/live/event/{id}/register", handler)
	r.ServeHTTP(rr, req)

	if rr.Code != http.StatusCreated {
		t.Errorf("status = %d, want 201", rr.Code)
	}
}

func TestLiveSettlementDependency_Serialization(t *testing.T) {
	now := time.Now().UTC()
	dep := LiveSettlementDependency{
		ID:          "dep-001",
		EventID:     "ev-001",
		MarketID:    "mkt-001",
		MarketType:  "match_winner",
		DependsOn:   "full_time",
		Status:      LiveSettlementConfirmed,
		ConfirmedAt: &now,
		ConfirmedBy: "official",
		CreatedAt:   now,
	}

	data, err := json.Marshal(dep)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var decoded LiveSettlementDependency
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if decoded.Status != LiveSettlementConfirmed {
		t.Errorf("status = %s, want confirmed", decoded.Status)
	}
}

func TestLiveMarketResult_Serialization(t *testing.T) {
	result := LiveMarketResult{
		EventID:    "ev-001",
		MarketID:   "mkt-001",
		MarketType: "match_winner",
		Results:    map[string]string{"sel-1": "won", "sel-2": "lost"},
		Source:     "official",
		HomeScore:  3,
		AwayScore:  1,
	}

	data, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var decoded LiveMarketResult
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if decoded.HomeScore != 3 {
		t.Errorf("home_score = %d, want 3", decoded.HomeScore)
	}
}

func TestLiveSettlementStatus_Constants(t *testing.T) {
	statuses := []LiveSettlementStatus{
		LiveSettlementPending, LiveSettlementConfirmed,
		LiveSettlementSettled, LiveSettlementDisputed,
		LiveSettlementRolledBack,
	}
	for _, s := range statuses {
		if string(s) == "" {
			t.Error("LiveSettlementStatus constant is empty")
		}
	}
}

func TestGetResult_NotFound(t *testing.T) {
	engine := NewLiveSettlementEngine(noopLogger())
	r := engine.GetResult("nope", "nope")
	if r != nil {
		t.Error("expected nil for nonexistent result")
	}
}
