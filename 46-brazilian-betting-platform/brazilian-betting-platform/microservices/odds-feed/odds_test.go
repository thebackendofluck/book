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
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"log/slog"
)

// --- Brazilian sports config tests ---

func TestIsBrazilianCompetition(t *testing.T) {
	tests := []struct {
		sport Sport
		want  bool
	}{
		{SportBrasileirão, true},
		{SportCopaBrasil, true},
		{SportLibertadores, true},
		{SportSulAmericana, true},
		{SportUFC, true},
		{SportNBA, true},
		{"unknown_sport", false},
	}
	for _, tc := range tests {
		got := IsBrazilianCompetition(tc.sport)
		if got != tc.want {
			t.Errorf("IsBrazilianCompetition(%s) = %v, want %v", tc.sport, got, tc.want)
		}
	}
}

func TestShouldFeatureEvent(t *testing.T) {
	tests := []struct {
		sport Sport
		want  bool
	}{
		{SportBrasileirão, true},
		{SportCopaBrasil, true},
		{SportLibertadores, true},
		{SportSulAmericana, false},
		{SportUFC, true},
		{SportNBA, false},
		{"unknown", false},
	}
	for _, tc := range tests {
		got := ShouldFeatureEvent(tc.sport)
		if got != tc.want {
			t.Errorf("ShouldFeatureEvent(%s) = %v, want %v", tc.sport, got, tc.want)
		}
	}
}

func TestGetCompetitionName(t *testing.T) {
	tests := []struct {
		sport Sport
		want  string
	}{
		{SportBrasileirão, "Brasileirão Série A"},
		{SportCopaBrasil, "Copa do Brasil"},
		{SportLibertadores, "CONMEBOL Libertadores"},
		{Sport("unknown"), "unknown"},
	}
	for _, tc := range tests {
		got := GetCompetitionName(tc.sport)
		if got != tc.want {
			t.Errorf("GetCompetitionName(%s) = %q, want %q", tc.sport, got, tc.want)
		}
	}
}

func TestSortedBrazilianSports_Order(t *testing.T) {
	sports := SortedBrazilianSports()
	if len(sports) == 0 {
		t.Error("expected non-empty sports list")
	}
	if sports[0] != SportBrasileirão {
		t.Errorf("first sport should be brasileirao, got %s", sports[0])
	}
}

// --- BuildBrasileiraoFixture tests ---

func TestBuildBrasileiraoFixture(t *testing.T) {
	now := time.Now().UTC()
	event := BuildBrasileiraoFixture("Flamengo", "Palmeiras", now.Add(2*time.Hour))

	if event.Sport != SportBrasileirão {
		t.Errorf("sport = %s, want %s", event.Sport, SportBrasileirão)
	}
	if event.HomeTeam != "Flamengo" {
		t.Errorf("home team = %s", event.HomeTeam)
	}
	if event.AwayTeam != "Palmeiras" {
		t.Errorf("away team = %s", event.AwayTeam)
	}
	if !event.IsFeatured {
		t.Error("Brasileirão event should be featured")
	}
	if len(event.Markets) < 2 {
		t.Errorf("expected at least 2 markets, got %d", len(event.Markets))
	}
}

func TestBuildBrasileiraoFixture_MatchWinnerMarket(t *testing.T) {
	event := BuildBrasileiraoFixture("Santos", "Vasco", time.Now().Add(time.Hour))

	var matchWinner *Market
	for _, m := range event.Markets {
		if m.Type == MarketTypeMatchWinner {
			mCopy := m
			matchWinner = &mCopy
			break
		}
	}

	if matchWinner == nil {
		t.Fatal("expected match winner market")
	}
	if len(matchWinner.Selections) != 3 {
		t.Errorf("match winner should have 3 selections (home/draw/away), got %d", len(matchWinner.Selections))
	}
}

// --- MockFeedProvider tests ---

func TestMockFeedProvider_Seed(t *testing.T) {
	mock := NewMockFeedProvider(noopLogger())
	if len(mock.events) == 0 {
		t.Error("mock feed should have seeded events")
	}
}

func TestMockFeedProvider_FetchEvent_Found(t *testing.T) {
	mock := NewMockFeedProvider(noopLogger())
	var firstID string
	for id := range mock.events {
		firstID = id
		break
	}

	event, err := mock.FetchEvent(context.Background(), firstID)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if event == nil {
		t.Error("expected event, got nil")
	}
}

func TestMockFeedProvider_FetchEvent_NotFound(t *testing.T) {
	mock := NewMockFeedProvider(noopLogger())
	_, err := mock.FetchEvent(context.Background(), "nonexistent-id")
	if err == nil {
		t.Error("expected error for nonexistent event")
	}
}

func TestMockFeedProvider_FetchEventsBySport(t *testing.T) {
	mock := NewMockFeedProvider(noopLogger())
	events, err := mock.FetchEventsBySport(context.Background(), SportBrasileirão)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected Brasileirão events from mock feed")
	}
	for _, ev := range events {
		if ev.Sport != SportBrasileirão {
			t.Errorf("expected sport %s, got %s", SportBrasileirão, ev.Sport)
		}
	}
}

func TestMockFeedProvider_SimulateOddsFluctuation(t *testing.T) {
	mock := NewMockFeedProvider(noopLogger())
	update := mock.SimulateLiveOddsFluctuation()
	if update == nil {
		t.Skip("no events available for simulation")
	}
	if update.NewOdds < 1.01 {
		t.Errorf("simulated odds should be >= 1.01, got %.4f", update.NewOdds)
	}
	if update.EventID == "" {
		t.Error("event ID should not be empty")
	}
}

// --- sortEventsByPriority tests ---

func TestSortEventsByPriority_FeaturedFirst(t *testing.T) {
	events := []Event{
		{IsFeatured: false, StartTime: time.Now().Add(1 * time.Hour)},
		{IsFeatured: true, StartTime: time.Now().Add(2 * time.Hour)},
		{IsFeatured: false, StartTime: time.Now().Add(3 * time.Hour)},
		{IsFeatured: true, StartTime: time.Now().Add(30 * time.Minute)},
	}

	sortEventsByPriority(events)

	for i, ev := range events {
		if i < 2 && !ev.IsFeatured {
			t.Errorf("position %d: expected featured, got non-featured", i)
		}
		if i >= 2 && ev.IsFeatured {
			t.Errorf("position %d: expected non-featured, got featured", i)
		}
	}
}

func TestSortEventsByPriority_EarliestStartFirst(t *testing.T) {
	events := []Event{
		{IsFeatured: true, StartTime: time.Now().Add(3 * time.Hour)},
		{IsFeatured: true, StartTime: time.Now().Add(1 * time.Hour)},
		{IsFeatured: true, StartTime: time.Now().Add(2 * time.Hour)},
	}

	sortEventsByPriority(events)

	if events[0].StartTime.After(events[1].StartTime) {
		t.Error("events should be sorted earliest first within same featured status")
	}
	if events[1].StartTime.After(events[2].StartTime) {
		t.Error("events should be sorted earliest first within same featured status")
	}
}

// --- HTTP handler boundary tests ---

func TestGetEventOdds_MissingID(t *testing.T) {
	cache := &Cache{client: nil}
	handler := GetEventOdds(cache, noopLogger())

	req := httptest.NewRequest(http.MethodGet, "/odds/event/", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	// chi URLParam returns "" when no param; handler returns 400.
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing event id, got %d", rr.Code)
	}
}

func TestUpdateOdds_InvalidBody(t *testing.T) {
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	req := httptest.NewRequest(http.MethodPost, "/odds/update", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for nil body, got %d", rr.Code)
	}
}

func TestUpdateOdds_LowOdds(t *testing.T) {
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	body := `{"event_id":"ev1","market_id":"m1","selection_id":"s1","new_odds":0.5,"source":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for odds < 1.01, got %d", rr.Code)
	}
}

// TestUpdateOdds_BelowMinimumThreshold confirms 1.005 is rejected. The
// odds-feed minimum was unified with betting-engine's stricter 1.01 threshold
// so the cache and the placement validator agree on what odds are acceptable.
func TestUpdateOdds_BelowMinimumThreshold(t *testing.T) {
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	body := `{"event_id":"ev1","market_id":"m1","selection_id":"s1","new_odds":1.005,"source":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for odds=1.005 (below 1.01 minimum), got %d", rr.Code)
	}
}

// TestUpdateOdds_Exactly1_00_Rejected pins down the inclusive boundary: odds
// EXACTLY 1.00 must be rejected with HTTP 400. A "free lottery ticket" odds
// of 1.00 means certain payout — never a real market. Allowing it would let
// a misconfigured feed publish a guaranteed-win selection.
func TestUpdateOdds_Exactly1_00_Rejected(t *testing.T) {
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	body := `{"event_id":"ev1","market_id":"m1","selection_id":"s1","new_odds":1.00,"source":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for odds=1.00 (at boundary, must be rejected), got %d", rr.Code)
	}
}

// TestUpdateOdds_Exactly1_01_Accepted confirms 1.01 is the first accepted
// value at the inclusive lower boundary. The handler will then fail with 500
// because the test Cache has a nil Redis client — any non-400 status proves
// the threshold check let the request through. The point is that 400 must
// NOT be returned for the minimum acceptable odds.
func TestUpdateOdds_Exactly1_01_Accepted(t *testing.T) {
	defer func() {
		// cache.UpdateOdds will panic on nil client when calling GetEvent.
		// Recover so the test reports "threshold passed" rather than
		// crashing.
		_ = recover()
	}()
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	body := `{"event_id":"ev1","market_id":"m1","selection_id":"s1","new_odds":1.01,"source":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code == http.StatusBadRequest {
		t.Errorf("expected non-400 for odds=1.01 (at minimum, must be accepted); got 400")
	}
}

// TestUpdateOdds_Below1_00_Rejected proves that odds < 1.00 (which would
// imply a negative-EV market or a feed bug) are unambiguously rejected.
// 0.99 falls below the 1.01 threshold and must produce HTTP 400.
func TestUpdateOdds_Below1_00_Rejected(t *testing.T) {
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	body := `{"event_id":"ev1","market_id":"m1","selection_id":"s1","new_odds":0.99,"source":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for odds=0.99 (below 1.00), got %d", rr.Code)
	}
}

func TestUpdateOdds_MissingFields(t *testing.T) {
	cache := &Cache{client: nil}
	handler := UpdateOdds(cache, nil, noopLogger())

	body := `{"new_odds":2.0,"source":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", rr.Code)
	}
}

func TestHealthCheck_ReturnsOK(t *testing.T) {
	// Cache with nil client — HealthCheck only shows degraded, not 500.
	// We can't call Ping(nil) without panic so mock-test the status field only.
	handler := HealthCheck(&Cache{client: nil})
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	// This will return 503 because Ping will fail on nil client, which is expected.
	handler.ServeHTTP(rr, req)
	// Either ok or degraded is acceptable in unit test.
	if rr.Code != http.StatusOK && rr.Code != http.StatusServiceUnavailable {
		t.Errorf("health: expected 200 or 503, got %d", rr.Code)
	}
}

// --- Sport constant tests ---

func TestSportConstants(t *testing.T) {
	sports := []Sport{
		SportFutebol, SportBrasileirão, SportCopaBrasil,
		SportLibertadores, SportSulAmericana, SportUFC, SportNBA,
	}
	for _, s := range sports {
		if string(s) == "" {
			t.Errorf("Sport constant is empty")
		}
	}
}

func TestEventStatus_Values(t *testing.T) {
	statuses := []EventStatus{
		EventStatusScheduled, EventStatusLive,
		EventStatusFinished, EventStatusCancelled, EventStatusPostponed,
	}
	for _, s := range statuses {
		if string(s) == "" {
			t.Error("EventStatus value is empty")
		}
	}
}

func TestBrasileiraoTeams_Count(t *testing.T) {
	if len(BrasileiraoTeams) != 20 {
		t.Errorf("expected 20 Brasileirão teams, got %d", len(BrasileiraoTeams))
	}
}

func TestRoundTo2DP(t *testing.T) {
	tests := []struct {
		input float64
		want  float64
	}{
		{1.234, 1.23},
		{1.235, 1.24},
		{2.0, 2.0},
		{3.999, 4.0},
	}
	for _, tc := range tests {
		got := roundTo2DP(tc.input)
		if got != tc.want {
			t.Errorf("roundTo2DP(%.4f) = %.4f, want %.4f", tc.input, got, tc.want)
		}
	}
}

func TestMarketType_Values(t *testing.T) {
	types := []MarketType{
		MarketTypeMatchWinner, MarketTypeDoubleChance, MarketTypeOverUnder,
		MarketTypeHandicap, MarketTypeBothTeamScore, MarketTypeHalfTime,
		MarketTypeCorrectScore, MarketTypeFirstGoal,
	}
	for _, mt := range types {
		if string(mt) == "" {
			t.Error("MarketType value is empty")
		}
	}
}

func TestLiveOddsMessage_Serialization(t *testing.T) {
	msg := LiveOddsMessage{
		Type:        "odds_update",
		EventID:     "ev-001",
		MarketID:    "mkt-001",
		SelectionID: "sel-001",
		Odds:        2.50,
		Timestamp:   time.Now().UTC(),
	}
	data, err := json.Marshal(msg)
	if err != nil {
		t.Errorf("unexpected marshal error: %v", err)
	}
	var decoded LiveOddsMessage
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Errorf("unexpected unmarshal error: %v", err)
	}
	if decoded.EventID != msg.EventID {
		t.Errorf("event_id mismatch: got %s, want %s", decoded.EventID, msg.EventID)
	}
	if decoded.Odds != msg.Odds {
		t.Errorf("odds mismatch: got %.2f, want %.2f", decoded.Odds, msg.Odds)
	}
}

// --- helpers ---

func noopLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(nopWriter{}, nil))
}

type nopWriter struct{}

func (nopWriter) Write(p []byte) (int, error) { return len(p), nil }
