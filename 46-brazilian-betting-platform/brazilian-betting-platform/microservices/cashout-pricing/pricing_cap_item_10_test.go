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
	"math"
	"testing"
)

// TestCalculateCashoutValue_CapBoundary_item_10 locks the PotentialReturn
// cap behavior at the boundary: equal to PR (no clamp needed), one cent
// over PR (must clamp to PR), one cent under PR (passes through unchanged),
// and the legacy PR=0 bypass.
//
// All cases share the same arithmetic backbone:
//
//	raw = stake * (origOdds/currentOdds) * (1 - margin)
//
// with stake=100, origOdds=2.00, margin=0.05 (default). Picking
// currentOdds=1.00 gives raw = 100 * 2.00 * 0.95 = 190.00 — a round
// reference value we then position relative to PotentialReturn.
//
// This is the rollout DoD for item 10: cap-binding boundary fully
// pinned so a regression on `cashoutValue > bet.PotentialReturn` in
// pricing.go fails loudly.
func TestCalculateCashoutValue_CapBoundary_item_10(t *testing.T) {
	const (
		stake      = 100.00
		origOdds   = 2.00
		curOdds    = 1.00 // raw = 100 * (1/1.00)/(1/2.00) * 0.95 = 190.00
		margin     = 0.05
		rawCashout = 190.00 // pre-cap value, rounded to cents
	)

	cases := []struct {
		name      string
		potential float64
		wantValue float64
		wantElig  bool
	}{
		// raw (190) < PR (200) — cap is a no-op, value passes through.
		{"under-cap-passes-through", 200.00, rawCashout, true},
		// raw (190) == PR (190) — boundary value, no clamp needed (already at cap).
		{"at-cap-exact", 190.00, 190.00, true},
		// raw (190) > PR (189.99) by one cent — MUST clamp down to PR.
		{"over-cap-by-cent-clamps", 189.99, 189.99, true},
		// raw (190) < PR (190.01) by one cent — no clamp, raw passes through.
		{"under-cap-by-cent-no-clamp", 190.01, rawCashout, true},
		// Legacy bypass: PR=0 means "unknown", cap MUST be skipped entirely
		// so legacy snapshots (pre-PR tracking) still price normally.
		{"zero-potential-bypasses-cap", 0.00, rawCashout, true},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			engine := NewCashoutPricingEngine(DefaultPricingConfig(), testLogger())

			bet := BetSnapshot{
				ID:              "bet-cap-boundary-" + c.name,
				CPF:             "111.222.333-44",
				Status:          "accepted",
				Stake:           stake,
				CombinedOdds:    origOdds,
				PotentialReturn: c.potential,
				RemainingStake:  stake,
				Selections: []SelectionSnapshot{
					{
						EventID:     "evt-cap",
						MarketID:    "mkt-cap",
						SelectionID: "sel-cap",
						OddsAtPlace: origOdds,
						CurrentOdds: curOdds,
						EventState:  "live",
						MarketOpen:  true,
					},
				},
			}

			result, err := engine.CalculateCashoutValue(bet, bet.Stake)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if result.Eligible != c.wantElig {
				t.Fatalf("eligible: got %v (reason=%q), want %v", result.Eligible, result.IneligibleReason, c.wantElig)
			}

			// Sanity-check the raw formula against the test's reference value
			// so any drift in the engine's pre-cap calculation is also caught.
			referenceRaw := stake * (1.0 / curOdds) / (1.0 / origOdds) * (1.0 - margin)
			referenceRaw = math.Round(referenceRaw*100) / 100
			if referenceRaw != rawCashout {
				t.Fatalf("test precondition drifted: referenceRaw=%.2f, expected rawCashout=%.2f",
					referenceRaw, rawCashout)
			}

			if math.Abs(result.CashoutValue-c.wantValue) > 0.005 {
				t.Errorf("cashout value: got %.2f, want %.2f (raw=%.2f, PR=%.2f)",
					result.CashoutValue, c.wantValue, referenceRaw, c.potential)
			}

			// When the cap binds (PR > 0 AND raw > PR), result MUST equal PR.
			if c.potential > 0 && referenceRaw > c.potential {
				if result.CashoutValue != c.potential {
					t.Errorf("expected cap clamp to PR=%.2f, got %.2f", c.potential, result.CashoutValue)
				}
			}
			// When the cap does NOT bind (raw <= PR), result MUST be the raw value.
			if c.potential > 0 && referenceRaw <= c.potential {
				if math.Abs(result.CashoutValue-referenceRaw) > 0.005 {
					t.Errorf("cap fired unnecessarily: raw=%.2f, PR=%.2f, got %.2f",
						referenceRaw, c.potential, result.CashoutValue)
				}
			}
			// Legacy bypass: PR=0 -> raw passes through, never clamped.
			if c.potential == 0 {
				if math.Abs(result.CashoutValue-referenceRaw) > 0.005 {
					t.Errorf("PR=0 bypass broken: got %.2f, want raw %.2f",
						result.CashoutValue, referenceRaw)
				}
			}
		})
	}
}
