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
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// --- Tax calculation tests ---

func TestCalculateTax_WinningBet(t *testing.T) {
	tests := []struct {
		name        string
		stake       float64
		grossReturn float64
		wantTax     float64
		wantNet     float64
	}{
		{
			name: "100 stake, 200 return — profit 100, under monthly threshold, no tax",
			stake: 100, grossReturn: 200,
			wantTax: 0, wantNet: 200,
		},
		{
			name: "1000 stake, 5000 return — profit 4000, taxable 1888, tax 283.20",
			stake: 1000, grossReturn: 5000,
			wantTax: 283.20, wantNet: 4716.80,
		},
		{
			name: "stake equals return — no profit",
			stake: 500, grossReturn: 500,
			wantTax: 0, wantNet: 500,
		},
		{
			name: "loss — zero return",
			stake: 200, grossReturn: 0,
			wantTax: 0, wantNet: 0,
		},
		{
			name: "large win — R$10,000 stake on 10x odds, profit 90000, taxable 87888, tax 13183.20",
			stake: 10000, grossReturn: 100000,
			wantTax: 13183.20, wantNet: 86816.80,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			tax, net := CalculateTax(tc.stake, tc.grossReturn)
			if abs(tax-tc.wantTax) > 0.001 {
				t.Errorf("tax: got %.4f, want %.4f", tax, tc.wantTax)
			}
			if abs(net-tc.wantNet) > 0.001 {
				t.Errorf("net: got %.4f, want %.4f", net, tc.wantTax)
			}
		})
	}
}

func TestCalculateMonthlyTax(t *testing.T) {
	tests := []struct {
		name            string
		netWinnings     float64
		wantTaxable     float64
		wantTax         float64
	}{
		{
			name: "below threshold — no tax",
			netWinnings: 2000.00,
			wantTaxable: 0, wantTax: 0,
		},
		{
			name: "exactly at threshold — no tax",
			netWinnings: 2112.00,
			wantTaxable: 0, wantTax: 0,
		},
		{
			name: "above threshold — R$888 taxable",
			netWinnings: 3000.00,
			wantTaxable: 888.00, wantTax: 133.20,
		},
		{
			name: "high earner",
			netWinnings: 50000.00,
			wantTaxable: 47888.00, wantTax: 7183.20,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			taxable, tax := CalculateMonthlyTax(tc.netWinnings)
			if abs(taxable-tc.wantTaxable) > 0.001 {
				t.Errorf("taxable: got %.4f, want %.4f", taxable, tc.wantTaxable)
			}
			if abs(tax-tc.wantTax) > 0.001 {
				t.Errorf("tax: got %.4f, want %.4f", tax, tc.wantTax)
			}
		})
	}
}

// TestCalculateTax_MonthlyExemptionBoundary exercises finding #21: the
// exemption comparison is done in integer centavos so a gross return that
// lands exactly on the threshold via float64 arithmetic isn't misclassified
// by floating-point representation error.
func TestCalculateTax_MonthlyExemptionBoundary(t *testing.T) {
	stake := 0.0
	grossReturn := 2111.10 + 0.90 // == 2112.00, prone to float64 drift
	taxAmount, netPayout := CalculateTax(stake, grossReturn)
	if taxAmount != 0 {
		t.Errorf("expected no tax exactly at threshold, got tax=%.6f", taxAmount)
	}
	if abs(netPayout-grossReturn) > 0.0001 {
		t.Errorf("expected full payout at threshold, got net=%.6f want=%.6f", netPayout, grossReturn)
	}
}

// --- GGR calculation tests ---

func TestGGRFromSettlements(t *testing.T) {
	settlements := []Settlement{
		{Stake: 100, GrossReturn: 200, TaxWithheld: 15},
		{Stake: 50, GrossReturn: 0, TaxWithheld: 0},
		{Stake: 200, GrossReturn: 400, TaxWithheld: 30},
	}

	totalStake, totalPrizes, totalTax, ggr := GGRFromSettlements(settlements)

	if abs(totalStake-350) > 0.001 {
		t.Errorf("total stake: got %.4f, want 350", totalStake)
	}
	if abs(totalPrizes-600) > 0.001 {
		t.Errorf("total prizes: got %.4f, want 600", totalPrizes)
	}
	if abs(totalTax-45) > 0.001 {
		t.Errorf("total tax: got %.4f, want 45", totalTax)
	}
	// GGR = 350 - 600 = -250 (operator loss). Tax is withheld from the prize,
	// already inside totalPrizes, so it is not subtracted again.
	expectedGGR := 350.0 - 600.0
	if abs(ggr-expectedGGR) > 0.001 {
		t.Errorf("ggr: got %.4f, want %.4f", ggr, expectedGGR)
	}
}

func TestGGRFromSettlements_AllLosses(t *testing.T) {
	settlements := []Settlement{
		{Stake: 100, GrossReturn: 0, TaxWithheld: 0},
		{Stake: 200, GrossReturn: 0, TaxWithheld: 0},
		{Stake: 50, GrossReturn: 0, TaxWithheld: 0},
	}

	totalStake, totalPrizes, totalTax, ggr := GGRFromSettlements(settlements)

	// All losses: GGR = 350 - 0 - 0 = 350 (all kept by operator)
	if abs(totalStake-350) > 0.001 {
		t.Errorf("total stake: got %.4f, want 350", totalStake)
	}
	if totalPrizes != 0 || totalTax != 0 {
		t.Errorf("expected zero prizes and tax, got prizes=%.2f tax=%.2f", totalPrizes, totalTax)
	}
	if abs(ggr-350) > 0.001 {
		t.Errorf("ggr: got %.4f, want 350", ggr)
	}
}

func TestGGRFromSettlements_Empty(t *testing.T) {
	_, _, _, ggr := GGRFromSettlements(nil)
	if ggr != 0 {
		t.Errorf("empty settlements GGR should be 0, got %.4f", ggr)
	}
}

// --- Bet outcome determination tests ---

func TestDetermineBetOutcome_Won(t *testing.T) {
	bet := BetRecord{
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"},
			{SelectionID: "s2"},
		},
	}
	results := map[string]string{"s1": "won", "s2": "won"}
	outcome, _ := determineBetOutcome(bet, results)
	if outcome != BetOutcomeWon {
		t.Errorf("expected won, got %s", outcome)
	}
}

func TestDetermineBetOutcome_Lost(t *testing.T) {
	bet := BetRecord{
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"},
			{SelectionID: "s2"},
		},
	}
	results := map[string]string{"s1": "won", "s2": "lost"}
	outcome, _ := determineBetOutcome(bet, results)
	if outcome != BetOutcomeLost {
		t.Errorf("expected lost, got %s", outcome)
	}
}

func TestDetermineBetOutcome_Void(t *testing.T) {
	bet := BetRecord{
		Stake: 50,
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"},
		},
	}
	results := map[string]string{"s1": "void"}
	outcome, grossReturn := determineBetOutcome(bet, results)
	if outcome != BetOutcomeVoid {
		t.Errorf("expected void, got %s", outcome)
	}
	if grossReturn != 50 {
		t.Errorf("void bet should refund stake: got %.2f, want 50", grossReturn)
	}
}

func TestDetermineBetOutcome_MissingResult(t *testing.T) {
	bet := BetRecord{
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"},
		},
	}
	results := map[string]string{} // s1 missing
	outcome, _ := determineBetOutcome(bet, results)
	if outcome != BetOutcomeVoid {
		t.Errorf("missing selection should be void, got %s", outcome)
	}
}

func TestDetermineBetOutcome_LostBeatsVoid_OrderIndependent(t *testing.T) {
	// A LOST leg must lose the whole bet regardless of where a void leg
	// falls relative to it — grading must not short-circuit on the first
	// void leg encountered.
	voidThenLost := BetRecord{
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"}, // void (missing result)
			{SelectionID: "s2"},
		},
	}
	outcome, _ := determineBetOutcome(voidThenLost, map[string]string{"s2": "lost"})
	if outcome != BetOutcomeLost {
		t.Errorf("void-then-lost: expected lost, got %s", outcome)
	}

	lostThenVoid := BetRecord{
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"},
			{SelectionID: "s2"}, // void (missing result)
		},
	}
	outcome, _ = determineBetOutcome(lostThenVoid, map[string]string{"s1": "lost"})
	if outcome != BetOutcomeLost {
		t.Errorf("lost-then-void: expected lost, got %s", outcome)
	}
}

func TestDetermineBetOutcome_AllLegsVoid(t *testing.T) {
	bet := BetRecord{
		Stake: 25,
		Selections: []BetSelectionRecord{
			{SelectionID: "s1"},
			{SelectionID: "s2"},
		},
	}
	results := map[string]string{"s1": "void", "s2": "void"}
	outcome, grossReturn := determineBetOutcome(bet, results)
	if outcome != BetOutcomeVoid {
		t.Errorf("expected void when every leg voids, got %s", outcome)
	}
	if grossReturn != 25 {
		t.Errorf("fully voided bet should refund stake: got %.2f, want 25", grossReturn)
	}
}

func TestDetermineBetOutcome_PartialVoid_RecomputesOdds(t *testing.T) {
	// One leg voids, one leg wins: the bet should pay out on the surviving
	// leg's odds alone, not the original multi-leg combined odds.
	bet := BetRecord{
		Stake:           10,
		PotentialReturn: 60, // original 2-leg combined odds (irrelevant once voided)
		CombinedOdds:    6.0,
		Selections: []BetSelectionRecord{
			{SelectionID: "s1", OddsValue: 2.5},
			{SelectionID: "s2", OddsValue: 2.4},
		},
	}
	results := map[string]string{"s1": "won", "s2": "void"}
	outcome, grossReturn := determineBetOutcome(bet, results)
	if outcome != BetOutcomeWon {
		t.Errorf("expected won, got %s", outcome)
	}
	want := 10 * 2.5
	if abs(grossReturn-want) > 0.001 {
		t.Errorf("recomputed gross return: got %.4f, want %.4f", grossReturn, want)
	}
}

func TestDetermineBetOutcome_NoVoid_UsesOriginalPotentialReturn(t *testing.T) {
	bet := BetRecord{
		Stake:           10,
		PotentialReturn: 60,
		Selections: []BetSelectionRecord{
			{SelectionID: "s1", OddsValue: 2.5},
			{SelectionID: "s2", OddsValue: 2.4},
		},
	}
	results := map[string]string{"s1": "won", "s2": "won"}
	outcome, grossReturn := determineBetOutcome(bet, results)
	if outcome != BetOutcomeWon {
		t.Errorf("expected won, got %s", outcome)
	}
	if grossReturn != bet.PotentialReturn {
		t.Errorf("fully-won bet should pay the originally quoted return: got %.4f, want %.4f", grossReturn, bet.PotentialReturn)
	}
}

// --- Tax constants ---

func TestTaxConstants(t *testing.T) {
	if BrazilianTaxRate != 0.15 {
		t.Errorf("tax rate should be 0.15, got %.4f", BrazilianTaxRate)
	}
	if MonthlyExemptionThreshold != 2112.00 {
		t.Errorf("exemption threshold should be R$2112.00, got %.2f", MonthlyExemptionThreshold)
	}
}

// --- HTTP handler boundary tests ---

func TestSettleEvent_InvalidBody(t *testing.T) {
	store := &Store{}
	sigap := NewSIGAPSettlementClient("http://stub", "OP001", "", noopLogger())
	handler := SettleEvent(store, sigap, nil, noopLogger())

	req := httptest.NewRequest(http.MethodPost, "/settle/event/ev1", bytes.NewBufferString("{invalid"))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestSettleEvent_EmptyResults(t *testing.T) {
	store := &Store{}
	sigap := NewSIGAPSettlementClient("http://stub", "OP001", "", noopLogger())
	handler := SettleEvent(store, sigap, nil, noopLogger())

	body := `{"event_id":"ev1","results":{},"source":"manual"}`
	req := httptest.NewRequest(http.MethodPost, "/settle/event/ev1", bytes.NewBufferString(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty results, got %d", rr.Code)
	}
}

func TestHealthCheck_ReturnsOK(t *testing.T) {
	handler := HealthCheck()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("health: expected 200, got %d", rr.Code)
	}
	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body)
	if body["status"] != "ok" {
		t.Errorf("health status = %v", body["status"])
	}
}

func TestMaskCPF_Settlement(t *testing.T) {
	if got := maskCPF("123.456.789-09"); got != "123.***.***-09" {
		t.Errorf("maskCPF = %q", got)
	}
}

// --- Model constants ---

func TestSettlementStatus_Values(t *testing.T) {
	statuses := []SettlementStatus{
		SettlementStatusPending, SettlementStatusProcessing,
		SettlementStatusCompleted, SettlementStatusFailed,
	}
	for _, s := range statuses {
		if string(s) == "" {
			t.Errorf("SettlementStatus value is empty")
		}
	}
}

func TestBetOutcome_Values(t *testing.T) {
	outcomes := []BetOutcome{BetOutcomeWon, BetOutcomeLost, BetOutcomeVoid, BetOutcomePush}
	for _, o := range outcomes {
		if string(o) == "" {
			t.Errorf("BetOutcome value is empty")
		}
	}
}

// --- helpers ---

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

var _ = time.Now
