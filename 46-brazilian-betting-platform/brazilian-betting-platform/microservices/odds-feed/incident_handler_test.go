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
)

func TestClassifySeverity(t *testing.T) {
	tests := []struct {
		incident IncidentType
		want     IncidentSeverity
	}{
		{IncidentGoal, SeverityCritical},
		{IncidentRedCard, SeverityCritical},
		{IncidentPenalty, SeverityCritical},
		{IncidentKnockout, SeverityHigh},
		{IncidentSetBreak, SeverityMedium},
		{IncidentTimeout, SeverityMedium},
		{IncidentTechPause, SeverityLow},
		{IncidentType("unknown"), SeverityLow},
	}

	for _, tc := range tests {
		got := ClassifySeverity(tc.incident)
		if got != tc.want {
			t.Errorf("ClassifySeverity(%s) = %s, want %s", tc.incident, got, tc.want)
		}
	}
}

func TestIsCritical(t *testing.T) {
	if !IsCritical(IncidentGoal) {
		t.Error("goal should be critical")
	}
	if !IsCritical(IncidentRedCard) {
		t.Error("red card should be critical")
	}
	if !IsCritical(IncidentPenalty) {
		t.Error("penalty should be critical")
	}
	if IsCritical(IncidentSetBreak) {
		t.Error("set break should not be critical")
	}
	if IsCritical(IncidentTimeout) {
		t.Error("timeout should not be critical")
	}
}

func TestDefaultSuspendConfig(t *testing.T) {
	cfg := DefaultSuspendConfig()
	if cfg.GoalSuspendDuration != 30*time.Second {
		t.Errorf("GoalSuspendDuration = %v, want 30s", cfg.GoalSuspendDuration)
	}
	if cfg.RedCardSuspendDuration != 20*time.Second {
		t.Errorf("RedCardSuspendDuration = %v, want 20s", cfg.RedCardSuspendDuration)
	}
	if cfg.PenaltySuspendDuration != 45*time.Second {
		t.Errorf("PenaltySuspendDuration = %v, want 45s", cfg.PenaltySuspendDuration)
	}
}

func TestSuspendConfig_SuspendDurationFor(t *testing.T) {
	cfg := DefaultSuspendConfig()

	tests := []struct {
		incident IncidentType
		want     time.Duration
	}{
		{IncidentGoal, 30 * time.Second},
		{IncidentRedCard, 20 * time.Second},
		{IncidentPenalty, 45 * time.Second},
		{IncidentTimeout, 15 * time.Second}, // Default
	}

	for _, tc := range tests {
		got := cfg.SuspendDurationFor(tc.incident)
		if got != tc.want {
			t.Errorf("SuspendDurationFor(%s) = %v, want %v", tc.incident, got, tc.want)
		}
	}
}

func TestEventIncident_Serialization(t *testing.T) {
	incident := EventIncident{
		ID:          "inc-001",
		EventID:     "ev-001",
		Type:        IncidentGoal,
		Severity:    SeverityCritical,
		Description: "Gol do Flamengo",
		Minute:      35,
		Team:        "Flamengo",
		Player:      "Gabigol",
		HomeScore:   1,
		AwayScore:   0,
		Source:      "betradar",
		ReceivedAt:  time.Now().UTC(),
	}

	data, err := json.Marshal(incident)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var decoded EventIncident
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if decoded.Type != IncidentGoal {
		t.Errorf("type = %s, want goal", decoded.Type)
	}
	if decoded.Player != "Gabigol" {
		t.Errorf("player = %s, want Gabigol", decoded.Player)
	}
}

func TestIncidentHandler_Timeline(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	handler := NewIncidentHandler(nil, hub, DefaultSuspendConfig(), noopLogger())

	// Empty timeline.
	tl := handler.GetTimeline("ev-001")
	if len(tl) != 0 {
		t.Errorf("expected empty timeline, got %d", len(tl))
	}
}

func TestIncidentHandler_PendingReopenCount(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	handler := NewIncidentHandler(nil, hub, DefaultSuspendConfig(), noopLogger())

	if handler.PendingReopenCount() != 0 {
		t.Errorf("expected 0 pending reopens, got %d", handler.PendingReopenCount())
	}
}

func TestIngestIncident_MissingEventID(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	handler := NewIncidentHandler(nil, hub, DefaultSuspendConfig(), noopLogger())
	httpHandler := IngestIncident(handler, noopLogger())

	body := `{"type":"goal"}`
	req := httptest.NewRequest(http.MethodPost, "/incidents", strings.NewReader(body))
	rr := httptest.NewRecorder()
	httpHandler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestIngestIncident_MissingType(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	handler := NewIncidentHandler(nil, hub, DefaultSuspendConfig(), noopLogger())
	httpHandler := IngestIncident(handler, noopLogger())

	body := `{"event_id":"ev-001"}`
	req := httptest.NewRequest(http.MethodPost, "/incidents", strings.NewReader(body))
	rr := httptest.NewRecorder()
	httpHandler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestIngestIncident_InvalidBody(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	handler := NewIncidentHandler(nil, hub, DefaultSuspendConfig(), noopLogger())
	httpHandler := IngestIncident(handler, noopLogger())

	req := httptest.NewRequest(http.MethodPost, "/incidents", strings.NewReader("not json"))
	rr := httptest.NewRecorder()
	httpHandler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestGetIncidentStatus_Handler(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	handler := NewIncidentHandler(nil, hub, DefaultSuspendConfig(), noopLogger())
	httpHandler := GetIncidentStatus(handler)

	req := httptest.NewRequest(http.MethodGet, "/incidents/status", nil)
	rr := httptest.NewRecorder()
	httpHandler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}

	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body) //nolint:errcheck
	if body["pending_reopens"] == nil {
		t.Error("expected pending_reopens in response")
	}
}

func TestIncidentType_Constants(t *testing.T) {
	types := []IncidentType{
		IncidentGoal, IncidentRedCard, IncidentPenalty,
		IncidentSetBreak, IncidentKnockout, IncidentTimeout,
		IncidentTechPause,
	}
	for _, it := range types {
		if string(it) == "" {
			t.Error("IncidentType constant is empty")
		}
	}
}

func TestCriticalIncidents_ContainsExpected(t *testing.T) {
	expected := []IncidentType{IncidentGoal, IncidentRedCard, IncidentPenalty}
	for _, e := range expected {
		if !CriticalIncidents[e] {
			t.Errorf("%s should be in CriticalIncidents", e)
		}
	}

	notExpected := []IncidentType{IncidentSetBreak, IncidentTimeout, IncidentTechPause}
	for _, e := range notExpected {
		if CriticalIncidents[e] {
			t.Errorf("%s should NOT be in CriticalIncidents", e)
		}
	}
}
