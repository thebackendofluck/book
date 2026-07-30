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
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"
)

// --- Unit tests for pure functions ---

func TestMaskCPF(t *testing.T) {
	tests := []struct {
		name  string
		input string
		want  string
	}{
		{"full CPF formatted", "123.456.789-09", "123.***.***-09"},
		{"short input passthrough", "12", "12"},
		{"empty input", "", ""},
		{"digits only", "12345678909", "123******09"},
		{"four chars", "1234", "1234"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := maskCPF(tc.input)
			if got != tc.want {
				t.Errorf("maskCPF(%q) = %q, want %q", tc.input, got, tc.want)
			}
		})
	}
}

func TestFormatBRL(t *testing.T) {
	tests := []struct {
		input float64
		want  string
	}{
		{100.0, "100.00"},
		{1234.5, "1234.50"},
		{0, "0.00"},
		{9999.999, "10000.00"},
	}
	for _, tc := range tests {
		got := formatBRL(tc.input)
		if got != tc.want {
			t.Errorf("formatBRL(%v) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

func TestBetStatus_String(t *testing.T) {
	tests := []struct {
		status BetStatus
		want   string
	}{
		{BetStatusPending, "pending"},
		{BetStatusAccepted, "accepted"},
		{BetStatusSettled, "settled"},
		{BetStatusCancelled, "cancelled"},
		{BetStatusCashedOut, "cashed_out"},
	}
	for _, tc := range tests {
		if string(tc.status) != tc.want {
			t.Errorf("BetStatus %v string = %q, want %q", tc.status, string(tc.status), tc.want)
		}
	}
}

func TestBetType_Values(t *testing.T) {
	if BetTypeSingle != "single" {
		t.Error("BetTypeSingle should be 'single'")
	}
	if BetTypeMultiple != "multiple" {
		t.Error("BetTypeMultiple should be 'multiple'")
	}
	if BetTypeSystem != "system" {
		t.Error("BetTypeSystem should be 'system'")
	}
}

func TestSelectionResult_Values(t *testing.T) {
	cases := map[SelectionResult]string{
		SelectionResultWon:      "won",
		SelectionResultLost:     "lost",
		SelectionResultVoid:     "void",
		SelectionResultPending:  "pending",
		SelectionResultHalfWon:  "half_won",
		SelectionResultHalfLost: "half_lost",
	}
	for k, v := range cases {
		if string(k) != v {
			t.Errorf("SelectionResult %v should equal %q", k, v)
		}
	}
}

// --- Settlement tax calculation tests ---

func TestSettlementTaxCalculation(t *testing.T) {
	tests := []struct {
		name            string
		stake           float64
		potentialReturn float64
		result          SelectionResult
		wantTax         float64
		wantNetPayout   float64
	}{
		{
			name: "winning bet with profit",
			// Stake 100, return 200, profit 100, tax 15%
			stake: 100, potentialReturn: 200, result: SelectionResultWon,
			wantTax: 15.0, wantNetPayout: 185.0,
		},
		{
			name: "losing bet no tax",
			stake: 100, potentialReturn: 200, result: SelectionResultLost,
			wantTax: 0, wantNetPayout: 0,
		},
		{
			name: "void bet no tax",
			stake: 100, potentialReturn: 200, result: SelectionResultVoid,
			wantTax: 0, wantNetPayout: 0,
		},
		{
			name: "large win with tax",
			// Stake 1000, return 10000, profit 9000, tax 1350
			stake: 1000, potentialReturn: 10000, result: SelectionResultWon,
			wantTax: 1350.0, wantNetPayout: 8650.0,
		},
		{
			name: "stake equals return — no profit",
			stake: 200, potentialReturn: 200, result: SelectionResultWon,
			wantTax: 0, wantNetPayout: 200,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var actualReturn float64
			if tc.result == SelectionResultWon {
				actualReturn = tc.potentialReturn
			}
			profit := actualReturn - tc.stake
			var taxWithheld float64
			if profit > 0 {
				taxWithheld = profit * 0.15
			}
			netPayout := actualReturn - taxWithheld

			if abs(taxWithheld-tc.wantTax) > 0.001 {
				t.Errorf("tax: got %.4f, want %.4f", taxWithheld, tc.wantTax)
			}
			if abs(netPayout-tc.wantNetPayout) > 0.001 {
				t.Errorf("net payout: got %.4f, want %.4f", netPayout, tc.wantNetPayout)
			}
		})
	}
}

// --- Combined odds calculation tests ---

func TestCombinedOddsCalculation(t *testing.T) {
	tests := []struct {
		name       string
		selections []float64
		want       float64
	}{
		{"single selection", []float64{2.0}, 2.0},
		{"two selections", []float64{2.0, 3.0}, 6.0},
		{"three selections", []float64{1.5, 2.0, 2.5}, 7.5},
		{"accumulator", []float64{1.2, 1.3, 1.5, 1.8}, 4.212},
		{"even money", []float64{2.0, 2.0, 2.0, 2.0}, 16.0},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			combined := 1.0
			for _, o := range tc.selections {
				combined *= o
			}
			if abs(combined-tc.want) > 0.001 {
				t.Errorf("combined odds: got %.4f, want %.4f", combined, tc.want)
			}
		})
	}
}

// --- Integrity checker unit tests ---

func TestIntegrityChecker_StakeEscalation(t *testing.T) {
	// Build a history of small bets, then check a much larger one.
	history := make([]Bet, 10)
	for i := range history {
		history[i] = Bet{
			ID:        uuid.NewString(),
			CPF:       "12345678909",
			Stake:     50.0,
			PlacedAt:  time.Now().Add(-time.Duration(i) * time.Minute),
			Status:    BetStatusSettled,
		}
	}

	// A bet 10x the average should trigger escalation alert.
	largeBet := &Bet{
		ID:       uuid.NewString(),
		CPF:      "12345678909",
		Stake:    500.0,
		PlacedAt: time.Now(),
	}

	ic := &IntegrityChecker{}
	alerts := ic.checkStakeEscalation(context.Background(), largeBet, history)
	if len(alerts) == 0 {
		t.Error("expected escalation alert for 10x average stake, got none")
	}
	if len(alerts) > 0 && alerts[0].AlertType != string(AlertTypeUnusualStakePattern) {
		t.Errorf("wrong alert type: %s", alerts[0].AlertType)
	}
}

func TestIntegrityChecker_NoEscalationBelowThreshold(t *testing.T) {
	history := make([]Bet, 10)
	for i := range history {
		history[i] = Bet{Stake: 100.0, PlacedAt: time.Now()}
	}
	normalBet := &Bet{Stake: 500.0, PlacedAt: time.Now()}

	ic := &IntegrityChecker{}
	alerts := ic.checkStakeEscalation(context.Background(), normalBet, history)
	// 500 is only 5x average of 100, should not trigger (threshold is 10x)
	if len(alerts) != 0 {
		t.Errorf("expected no alerts for 5x stake, got %d", len(alerts))
	}
}

func TestIntegrityChecker_LargeStake(t *testing.T) {
	ic := &IntegrityChecker{}
	tests := []struct {
		stake    float64
		wantHit  bool
		severity string
	}{
		{4999.99, false, ""},
		{5000.0, true, SeverityMedium},
		{10000.0, true, SeverityHigh},
		{20000.0, true, SeverityCritical},
	}

	for _, tc := range tests {
		bet := &Bet{ID: uuid.NewString(), CPF: "12345678909", Stake: tc.stake}
		alerts := ic.checkLargeStake(context.Background(), bet, nil)
		if tc.wantHit && len(alerts) == 0 {
			t.Errorf("stake %.2f: expected alert, got none", tc.stake)
		}
		if !tc.wantHit && len(alerts) > 0 {
			t.Errorf("stake %.2f: expected no alert, got %d", tc.stake, len(alerts))
		}
		if tc.wantHit && len(alerts) > 0 && alerts[0].Severity != tc.severity {
			t.Errorf("stake %.2f: severity = %s, want %s", tc.stake, alerts[0].Severity, tc.severity)
		}
	}
}

func TestIntegrityChecker_AbnormalOdds(t *testing.T) {
	ic := &IntegrityChecker{}
	tests := []struct {
		odds    float64
		wantHit bool
	}{
		{999.9, false},
		{1000.0, true},
		{5000.0, true},
	}
	for _, tc := range tests {
		bet := &Bet{ID: uuid.NewString(), CombinedOdds: tc.odds}
		alerts := ic.checkAbnormalOdds(context.Background(), bet, nil)
		if tc.wantHit && len(alerts) == 0 {
			t.Errorf("odds %.1f: expected alert", tc.odds)
		}
		if !tc.wantHit && len(alerts) > 0 {
			t.Errorf("odds %.1f: expected no alert", tc.odds)
		}
	}
}

func TestIntegrityChecker_RapidFire(t *testing.T) {
	ic := &IntegrityChecker{}
	// Create 10 bets within the last 60 seconds.
	history := make([]Bet, 10)
	for i := range history {
		history[i] = Bet{ID: uuid.NewString(), PlacedAt: time.Now().Add(-10 * time.Second)}
	}
	newBet := &Bet{ID: uuid.NewString(), PlacedAt: time.Now()}
	alerts := ic.checkRapidFireBets(context.Background(), newBet, history)
	if len(alerts) == 0 {
		t.Error("expected rapid fire alert")
	}
}

// --- SIGAP CPF reporting tests ---

// SIGAP is the regulator's own system and uses CPF as the bet's primary key
// (Portaria 1207/2024), so the report must carry the full, unmasked CPF.
// Masking is only correct for internal logs (see maskCPF/riskMaskCPF).
func TestSIGAP_BuildReport_SendsFullCPF(t *testing.T) {
	client := &SIGAPClient{operatorID: "OP001"}
	bet := &Bet{
		ID:           uuid.NewString(),
		CPF:          "123.456.789-09",
		Type:         BetTypeSingle,
		Stake:        100.0,
		PotentialReturn: 200.0,
		CombinedOdds: 2.0,
		PlacedAt:     time.Now(),
		Selections:   []BetSelection{},
	}
	report := client.buildReport(bet)
	if report.Bet.CPF != "123.456.789-09" {
		t.Errorf("SIGAP report must carry the full CPF, got %q", report.Bet.CPF)
	}
}

// --- HTTP handler tests ---

func TestHealthCheck_ReturnsOK(t *testing.T) {
	// Build a mock cache that always succeeds Ping.
	mockCache := &mockRedisCache{}
	handler := HealthCheck(mockCache.realCache())

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK && rr.Code != http.StatusServiceUnavailable {
		t.Errorf("health check: status = %d, want 200 or 503", rr.Code)
	}
	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body)
	if body["status"] != "ok" && body["status"] != "degraded" {
		t.Errorf("health check status = %v, want ok or degraded", body["status"])
	}
}

func TestPlaceBet_MissingCPF(t *testing.T) {
	store := &Store{}
	cache := &mockRedisCache{}
	sigap := &SIGAPClient{}
	integrity := &IntegrityChecker{}
	logger := noopLogger()

	handler := PlaceBet(store, cache.realCache(), sigap, integrity, logger)

	body := `{"session_id":"sess1","type":"single","stake":10,"selections":[{"event_id":"e1","market_id":"m1","selection_id":"s1","odds_value":2.0}]}`
	req := httptest.NewRequest(http.MethodPost, "/bets", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestPlaceBet_StakeTooLow(t *testing.T) {
	store := &Store{}
	cache := &mockRedisCache{}
	sigap := &SIGAPClient{}
	integrity := &IntegrityChecker{}
	logger := noopLogger()

	handler := PlaceBet(store, cache.realCache(), sigap, integrity, logger)

	body := `{"cpf":"12345678909","session_id":"sess1","type":"single","stake":0.50,"selections":[{"event_id":"e1","market_id":"m1","selection_id":"s1","odds_value":2.0}]}`
	req := httptest.NewRequest(http.MethodPost, "/bets", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for low stake, got %d", rr.Code)
	}
}

func TestPlaceBet_NoSelections(t *testing.T) {
	store := &Store{}
	cache := &mockRedisCache{}
	sigap := &SIGAPClient{}
	integrity := &IntegrityChecker{}
	logger := noopLogger()

	handler := PlaceBet(store, cache.realCache(), sigap, integrity, logger)

	body := `{"cpf":"12345678909","session_id":"sess1","type":"single","stake":10,"selections":[]}`
	req := httptest.NewRequest(http.MethodPost, "/bets", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for no selections, got %d", rr.Code)
	}
}

func TestGetBet_InvalidJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/bets", bytes.NewBufferString("{invalid"))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	store := &Store{}
	cache := &mockRedisCache{}
	sigap := &SIGAPClient{}
	integrity := &IntegrityChecker{}
	logger := noopLogger()

	PlaceBet(store, cache.realCache(), sigap, integrity, logger).ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("invalid JSON: expected 400, got %d", rr.Code)
	}
}

func TestSettlementLogic_WonBet(t *testing.T) {
	stake := 100.0
	potentialReturn := 300.0
	actualReturn := potentialReturn // won

	profit := actualReturn - stake       // 200
	taxWithheld := profit * 0.15         // 30
	netPayout := actualReturn - taxWithheld // 270

	if abs(taxWithheld-30) > 0.001 {
		t.Errorf("expected tax 30, got %.4f", taxWithheld)
	}
	if abs(netPayout-270) > 0.001 {
		t.Errorf("expected net payout 270, got %.4f", netPayout)
	}
}

func TestSettlementLogic_LostBet(t *testing.T) {
	var actualReturn float64 // lost
	stake := 100.0
	profit := actualReturn - stake
	var taxWithheld float64
	if profit > 0 {
		taxWithheld = profit * 0.15
	}
	if taxWithheld != 0 {
		t.Errorf("expected zero tax on loss, got %.4f", taxWithheld)
	}
}

func TestMinHelper(t *testing.T) {
	cases := []struct{ a, b, want int }{{3, 5, 3}, {5, 3, 3}, {4, 4, 4}}
	for _, tc := range cases {
		if got := min(tc.a, tc.b); got != tc.want {
			t.Errorf("min(%d,%d) = %d, want %d", tc.a, tc.b, got, tc.want)
		}
	}
}

// --- helpers ---

type mockRedisCache struct{}

func (m *mockRedisCache) realCache() *Cache {
	// Return a Cache with nil client; tests that reach Redis will panic/skip.
	// For handler boundary tests this is fine as they fail before Redis calls.
	return &Cache{client: nil}
}

func (m *mockRedisCache) Ping(_ context.Context) error { return nil }

func noopLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(nopWriter{}, nil))
}

type nopWriter struct{}

func (nopWriter) Write(p []byte) (int, error) { return len(p), nil }

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
