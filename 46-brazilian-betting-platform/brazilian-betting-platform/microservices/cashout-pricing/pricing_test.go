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
	"log/slog"
	"math"
	"math/rand/v2"
	"os"
	"testing"
	"time"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelError}))
}

func TestCalculateCashoutValue_SingleBet(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	bet := BetSnapshot{
		ID:              "bet-001",
		CPF:             "123.456.789-09",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.50,
		PotentialReturn: 250.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				MarketID:    "mkt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.50,
				CurrentOdds: 1.80,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !result.Eligible {
		t.Fatalf("expected eligible, got ineligible: %s", result.IneligibleReason)
	}

	// Expected: 100 * (1/1.80) / (1/2.50) * (1 - 0.05)
	// = 100 * (0.5556 / 0.4) * 0.95
	// = 100 * 1.3889 * 0.95
	// = 131.94
	expected := 100.0 * (1.0 / 1.80) / (1.0 / 2.50) * 0.95
	expected = math.Round(expected*100) / 100

	if math.Abs(result.CashoutValue-expected) > 0.01 {
		t.Errorf("expected cashout value %.2f, got %.2f", expected, result.CashoutValue)
	}

	if result.MarginApplied != 0.05 {
		t.Errorf("expected margin 0.05, got %.4f", result.MarginApplied)
	}
}

func TestCalculateCashoutValue_Accumulator(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	bet := BetSnapshot{
		ID:              "bet-002",
		CPF:             "987.654.321-00",
		Status:          "accepted",
		Stake:           50.00,
		CombinedOdds:    6.00,
		PotentialReturn: 300.00,
		RemainingStake:  50.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				MarketID:    "mkt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.50,
				EventState:  "live",
				MarketOpen:  true,
			},
			{
				EventID:     "evt-002",
				MarketID:    "mkt-002",
				SelectionID: "sel-002",
				OddsAtPlace: 3.00,
				CurrentOdds: 2.00,
				EventState:  "scheduled",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !result.Eligible {
		t.Fatalf("expected eligible, got ineligible: %s", result.IneligibleReason)
	}

	// Original implied: (1/2.00) * (1/3.00) = 0.5 * 0.3333 = 0.1667
	// Current implied: (1/1.50) * (1/2.00) = 0.6667 * 0.5 = 0.3333
	// Cashout: 50 * (0.3333 / 0.1667) * 0.95 = 50 * 2.0 * 0.95 = 95.00
	originalImplied := (1.0 / 2.00) * (1.0 / 3.00)
	currentImplied := (1.0 / 1.50) * (1.0 / 2.00)
	expected := 50.0 * (currentImplied / originalImplied) * 0.95
	expected = math.Round(expected*100) / 100

	if math.Abs(result.CashoutValue-expected) > 0.01 {
		t.Errorf("expected cashout value %.2f, got %.2f", expected, result.CashoutValue)
	}
}

func TestCalculateCashoutValue_IneligibleStatus(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	statuses := []string{"settled", "cancelled", "cashed_out"}
	for _, status := range statuses {
		bet := BetSnapshot{
			ID:             "bet-003",
			Status:         status,
			Stake:          100.00,
			RemainingStake: 100.00,
			Selections:     []SelectionSnapshot{{OddsAtPlace: 2.0, CurrentOdds: 1.5, EventState: "live", MarketOpen: true}},
		}

		result, err := engine.CalculateCashoutValue(bet, bet.Stake)
		if err != nil {
			t.Fatalf("unexpected error for status %q: %v", status, err)
		}
		if result.Eligible {
			t.Errorf("expected ineligible for status %q, got eligible", status)
		}
	}
}

func TestCalculateCashoutValue_SuspendedMarket(t *testing.T) {
	// Default config: suspended not allowed.
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	bet := BetSnapshot{
		ID:              "bet-004",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		RemainingStake:  100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 200.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.50,
				EventState:  "suspended",
				MarketOpen:  false,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Eligible {
		t.Error("expected ineligible for suspended market, got eligible")
	}

	// With suspended allowed.
	cfg := DefaultPricingConfig()
	cfg.SuspendedAllowed = true
	engine2 := NewCashoutPricingEngine(cfg, testLogger())

	result2, err := engine2.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result2.Eligible {
		t.Errorf("expected eligible with SuspendedAllowed, got: %s", result2.IneligibleReason)
	}
}

func TestCalculateCashoutValue_BelowMinimum(t *testing.T) {
	cfg := DefaultPricingConfig()
	cfg.MinCashoutBRL = 10.00
	engine := NewCashoutPricingEngine(cfg, testLogger())

	// Tiny stake with odds moving against the player.
	bet := BetSnapshot{
		ID:              "bet-005",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           5.00,
		RemainingStake:  5.00,
		CombinedOdds:    2.00,
		PotentialReturn: 10.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.00,
				CurrentOdds: 5.00, // Odds drifted out (less likely to win)
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Eligible {
		t.Error("expected ineligible below minimum, got eligible")
	}
}

func TestCalculateCashoutValue_MaxCap(t *testing.T) {
	cfg := DefaultPricingConfig()
	cfg.MaxCashoutBRL = 50.00
	engine := NewCashoutPricingEngine(cfg, testLogger())

	// Large bet with favorable odds movement.
	bet := BetSnapshot{
		ID:              "bet-006",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           1000.00,
		RemainingStake:  1000.00,
		CombinedOdds:    3.00,
		PotentialReturn: 3000.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 3.00,
				CurrentOdds: 1.10, // Very likely to win now
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result.Eligible {
		t.Fatalf("expected eligible, got: %s", result.IneligibleReason)
	}
	if result.CashoutValue != 50.00 {
		t.Errorf("expected cashout capped at R$50.00, got R$%.2f", result.CashoutValue)
	}
}

// TestCalculateCashoutValue_NoCap_NormalCase confirms that, when the raw
// formula output sits comfortably below the bet's PotentialReturn, the
// PotentialReturn cap is a no-op: the calculated value passes through
// unchanged. This guards against regressions where the cap is applied too
// aggressively (e.g. clamping every cashout to the potential payout even
// when it should not).
func TestCalculateCashoutValue_NoCap_NormalCase(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	// Stake 100 @ 2.50 -> PotentialReturn 250. Current odds 1.80 -> the
	// formula gives ~131.94, well below the 250 cap, so the cap must NOT
	// fire.
	bet := BetSnapshot{
		ID:              "bet-cap-noop",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.50,
		PotentialReturn: 250.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.50,
				CurrentOdds: 1.80,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result.Eligible {
		t.Fatalf("expected eligible, got: %s", result.IneligibleReason)
	}

	// Raw formula: 100 * (1/1.80) / (1/2.50) * 0.95 = 131.94
	expected := 100.0 * (1.0 / 1.80) / (1.0 / 2.50) * 0.95
	expected = math.Round(expected*100) / 100
	if math.Abs(result.CashoutValue-expected) > 0.01 {
		t.Errorf("expected cashout %.2f (uncapped), got %.2f", expected, result.CashoutValue)
	}
	if result.CashoutValue > bet.PotentialReturn {
		t.Errorf("cashout %.2f exceeded PotentialReturn %.2f", result.CashoutValue, bet.PotentialReturn)
	}
	if result.CashoutValue == bet.PotentialReturn {
		t.Errorf("cap fired unnecessarily: cashout==PotentialReturn==%.2f", result.CashoutValue)
	}
}

// TestCalculateCashoutValue_CapsAtPotentialReturn proves the PotentialReturn
// cap engages when the raw formula would otherwise pay the player more than
// the bet could ever return at full settlement. Without the cap, sharp
// favourites (heavy implied-prob shifts) can produce a cashout offer larger
// than the bet's maximum payout — a clear pricing bug that hands the player
// free EV.
func TestCalculateCashoutValue_CapsAtPotentialReturn(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	// Stake 100 @ 2.00 -> PotentialReturn 200. Current odds collapse to 1.05
	// (very heavy favourite), so the raw formula yields:
	//   100 * (1/1.05) / (1/2.00) * 0.95 = 100 * 1.9048 * 0.95 = 180.95
	// Below 200, so the cap should NOT engage here. Use an even sharper
	// current odds: 1.01 -> 100 * (1/1.01)/(1/2.00)*0.95 = 188.12 -> still
	// below 200. We need the raw value > PotentialReturn. With a 2-leg
	// accumulator where both legs shorten dramatically:
	//   stake=100, original combined odds 4.00 -> PR=400
	//   raw = 100 * (combined_current_implied/combined_original_implied)*0.95
	// If original implied = 0.25 (combined odds 4.0) and current implied
	// = 1/1.05 * 1/1.05 = 0.9070, ratio = 3.628, *0.95 = 344.7 — still less
	// than 400. To exceed PR, the implied ratio must beat 1/(1-margin) = 1.0526
	// AFTER division by original. So we need PR small relative to stake.
	//
	// Easiest construction: tiny PotentialReturn (e.g. odds 1.50, PR=150)
	// with sharp odds drop to 1.05:
	//   raw = 100 * (1/1.05)/(1/1.50)*0.95 = 100 * 1.4286 * 0.95 = 135.71
	// still below 150. So make PR=120 (combined odds 1.20):
	//   raw = 100 * (1/1.05)/(1/1.20)*0.95 = 100 * 1.1429 * 0.95 = 108.57
	// still below 120. The cap only matters when implied-prob ratio > odds.
	// Construct deliberately: original combined odds 3.00 (PR=300), current
	// implied 1.0 (impossible in reality but valid for the math): raw
	// = 100 * 1.0 / 0.3333 * 0.95 = 285.0 -> still below 300.
	//
	// The cap binds only for partial-cashout edge cases where
	// MarginApplied < (1 - originalImplied). To trigger deterministically,
	// use a bet whose PotentialReturn is artificially smaller than the
	// raw formula output. This models bets where PR was rounded down at
	// placement or where the legacy stake/PR ratio was bookkept loosely:
	//   stake=100, PR=110 (cap binds well below the natural ratio),
	//   current odds heavily shortened.
	bet := BetSnapshot{
		ID:              "bet-cap-binds",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 110.00, // Deliberately low cap below the raw formula
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.10, // Heavy favourite now
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result.Eligible {
		t.Fatalf("expected eligible, got: %s", result.IneligibleReason)
	}

	// Raw formula = 100 * (1/1.10)/(1/2.00)*0.95 = 172.73 -> capped at 110.
	rawValue := 100.0 * (1.0 / 1.10) / (1.0 / 2.00) * 0.95
	rawValue = math.Round(rawValue*100) / 100
	if rawValue <= bet.PotentialReturn {
		t.Fatalf("test precondition wrong: raw=%.2f must exceed PR=%.2f", rawValue, bet.PotentialReturn)
	}
	if result.CashoutValue != bet.PotentialReturn {
		t.Errorf("expected cashout capped at PotentialReturn %.2f, got %.2f", bet.PotentialReturn, result.CashoutValue)
	}
}

// TestCalculateCashoutValue_ZeroPotentialReturn_BypassesCap protects backward
// compatibility for legacy bet snapshots persisted before PotentialReturn was
// tracked. When PotentialReturn == 0 (sentinel for "unknown"), the cap MUST
// be skipped so legacy bets continue to price normally. Otherwise every
// legacy bet would be ineligible (cashout capped to zero, below
// MinCashoutBRL).
func TestCalculateCashoutValue_ZeroPotentialReturn_BypassesCap(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	bet := BetSnapshot{
		ID:              "bet-cap-legacy",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 0, // Legacy: not recorded
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.10, // Heavy favourite now
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result.Eligible {
		t.Fatalf("expected eligible (legacy bet), got: %s", result.IneligibleReason)
	}

	// Cap must NOT fire when PR == 0 — value should match the raw formula.
	expected := 100.0 * (1.0 / 1.10) / (1.0 / 2.00) * 0.95
	expected = math.Round(expected*100) / 100
	if math.Abs(result.CashoutValue-expected) > 0.01 {
		t.Errorf("expected cashout %.2f (uncapped for legacy PR==0), got %.2f", expected, result.CashoutValue)
	}
	if result.CashoutValue == 0 {
		t.Errorf("cap zeroed out a legacy bet (PR==0) — backward compatibility broken")
	}
}

func TestCalculateCashoutValue_InvalidStake(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	bet := BetSnapshot{
		ID:              "bet-007",
		Status:          "accepted",
		Stake:           100.00,
		RemainingStake:  100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 200.00,
		Selections: []SelectionSnapshot{
			{OddsAtPlace: 2.00, CurrentOdds: 1.50, EventState: "live", MarketOpen: true},
		},
	}

	// Stake exceeding remaining.
	_, err := engine.CalculateCashoutValue(bet, 150.00)
	if err == nil {
		t.Error("expected error for stake > remaining, got nil")
	}

	// Zero stake.
	_, err = engine.CalculateCashoutValue(bet, 0)
	if err == nil {
		t.Error("expected error for zero stake, got nil")
	}
}

func TestCheckEligibility(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	tests := []struct {
		name     string
		bet      BetSnapshot
		eligible bool
	}{
		{
			name: "eligible accepted bet",
			bet: BetSnapshot{
				ID: "bet-010", Status: "accepted", RemainingStake: 100,
				Selections: []SelectionSnapshot{{EventState: "live", MarketOpen: true}},
			},
			eligible: true,
		},
		{
			name: "settled bet",
			bet: BetSnapshot{
				ID: "bet-011", Status: "settled", RemainingStake: 100,
				Selections: []SelectionSnapshot{{EventState: "live", MarketOpen: true}},
			},
			eligible: false,
		},
		{
			name: "zero remaining stake",
			bet: BetSnapshot{
				ID: "bet-012", Status: "accepted", RemainingStake: 0,
				Selections: []SelectionSnapshot{{EventState: "live", MarketOpen: true}},
			},
			eligible: false,
		},
		{
			name: "voided selection",
			bet: BetSnapshot{
				ID: "bet-013", Status: "accepted", RemainingStake: 100,
				Selections: []SelectionSnapshot{{SelectionID: "sel-x", EventState: "voided", MarketOpen: true}},
			},
			eligible: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := engine.CheckEligibility(tt.bet)
			if result.Eligible != tt.eligible {
				t.Errorf("expected eligible=%v, got=%v (reason: %s)", tt.eligible, result.Eligible, result.Reason)
			}
		})
	}
}

func TestCombinedImpliedProbability(t *testing.T) {
	selections := []SelectionSnapshot{
		{OddsAtPlace: 2.00, CurrentOdds: 1.50},
		{OddsAtPlace: 3.00, CurrentOdds: 2.00},
	}

	originalProb := combinedImpliedProbability(selections, true)
	currentProb := combinedImpliedProbability(selections, false)

	expectedOriginal := (1.0 / 2.00) * (1.0 / 3.00)
	expectedCurrent := (1.0 / 1.50) * (1.0 / 2.00)

	if math.Abs(originalProb-expectedOriginal) > 0.0001 {
		t.Errorf("expected original implied %.4f, got %.4f", expectedOriginal, originalProb)
	}
	if math.Abs(currentProb-expectedCurrent) > 0.0001 {
		t.Errorf("expected current implied %.4f, got %.4f", expectedCurrent, currentProb)
	}
}

func TestCalculateCashoutValue_OddsAgainstPlayer(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	// Odds drifted out: bet is now less likely to win, so cashout should
	// be less than original stake.
	bet := BetSnapshot{
		ID:              "bet-020",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		RemainingStake:  100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 200.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.00,
				CurrentOdds: 3.00, // Less likely now
				EventState:  "live",
				MarketOpen:  true,
			},
		},
		PlacedAt: time.Now().Add(-30 * time.Minute),
	}

	result, err := engine.CalculateCashoutValue(bet, bet.Stake)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !result.Eligible {
		t.Fatalf("expected eligible, got: %s", result.IneligibleReason)
	}

	// Cashout should be less than stake since odds moved against player.
	if result.CashoutValue >= bet.Stake {
		t.Errorf("expected cashout value < stake (%.2f), got %.2f", bet.Stake, result.CashoutValue)
	}

	// Verify: 100 * (1/3.00) / (1/2.00) * 0.95 = 100 * 0.6667 * 0.95 = 63.33
	expected := 100.0 * (1.0 / 3.00) / (1.0 / 2.00) * 0.95
	expected = math.Round(expected*100) / 100
	if math.Abs(result.CashoutValue-expected) > 0.01 {
		t.Errorf("expected cashout %.2f, got %.2f", expected, result.CashoutValue)
	}
}

// TestCalculateCashoutValue_NeverExceedsPotentialReturn is a property-based
// test: for N randomly generated but otherwise valid bet snapshots, the
// engine's returned CashoutValue MUST never exceed bet.PotentialReturn when
// PR > 0. The invariant is the core defensive guarantee of the cap: an
// operator can never accidentally offer more than the bet could ever win.
//
// We sample stake, original odds, current odds, and PR independently across
// realistic ranges (and a few pathological ones) to cover sharp odds shifts.
// 10000 iterations gives strong coverage without slowing the suite past ~1s.
func TestCalculateCashoutValue_NeverExceedsPotentialReturn(t *testing.T) {
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

	// Deterministic seed so failures reproduce. v2 PCG generator.
	src := rand.NewPCG(0xC45CA1B0, 0xCAFEBABE)
	rng := rand.New(src)

	const iterations = 10000
	for i := 0; i < iterations; i++ {
		// Stake: R$1 to R$10 000.
		stake := 1.0 + rng.Float64()*9999.0

		// Original odds: 1.01 to 50.0 (matches odds-feed accepted range).
		originalOdds := 1.01 + rng.Float64()*48.99

		// Current odds: independently drawn from the same range, which
		// covers both heavy-favourite collapse (current << original) and
		// heavy-drift cases (current >> original).
		currentOdds := 1.01 + rng.Float64()*48.99

		// PotentialReturn modelled as stake * combined original odds,
		// then randomly perturbed downward in 30% of cases to mimic
		// legacy rounding/truncation that tightens the cap.
		pr := stake * originalOdds
		if rng.Float64() < 0.3 {
			pr *= 0.5 + rng.Float64()*0.5 // 50%-100% of natural PR
		}
		pr = math.Round(pr*100) / 100

		bet := BetSnapshot{
			ID:              "prop-bet",
			CPF:             "000.000.000-00",
			Status:          "accepted",
			Stake:           stake,
			CombinedOdds:    originalOdds,
			PotentialReturn: pr,
			RemainingStake:  stake,
			Selections: []SelectionSnapshot{
				{
					EventID:     "evt-prop",
					SelectionID: "sel-prop",
					OddsAtPlace: originalOdds,
					CurrentOdds: currentOdds,
					EventState:  "live",
					MarketOpen:  true,
				},
			},
		}

		result, err := engine.CalculateCashoutValue(bet, bet.Stake)
		if err != nil {
			t.Fatalf("iter %d: unexpected error: %v (bet=%+v)", i, err, bet)
		}
		// Skip ineligible cases (e.g. below MinCashoutBRL) — the cap
		// invariant only applies to eligible offers.
		if !result.Eligible {
			continue
		}
		if pr > 0 && result.CashoutValue > pr {
			t.Fatalf(
				"INVARIANT VIOLATED at iter %d: cashout %.4f > PotentialReturn %.4f (stake=%.2f origOdds=%.4f curOdds=%.4f)",
				i, result.CashoutValue, pr, stake, originalOdds, currentOdds,
			)
		}
	}
}
