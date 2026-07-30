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

func TestCorrelationGraph_GetFactor_Existing(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	got := g.GetFactor(SportFootball, MarketMatch1X2, MarketTotalGoals)
	if got != 0.65 {
		t.Errorf("factor = %.2f, want 0.65", got)
	}
}

func TestCorrelationGraph_GetFactor_Symmetric(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	// Reverse direction should also return 0.65.
	got := g.GetFactor(SportFootball, MarketTotalGoals, MarketMatch1X2)
	if got != 0.65 {
		t.Errorf("reverse factor = %.2f, want 0.65", got)
	}
}

func TestCorrelationGraph_GetFactor_UnknownSport(t *testing.T) {
	g := NewCorrelationGraph()

	// No sport loaded at all: fails closed to the conservative default
	// instead of treating the legs as independent (1.0).
	got := g.GetFactor(SportFootball, MarketMatch1X2, MarketBTTS)
	if got != unknownPairFactor {
		t.Errorf("unknown-sport factor = %.2f, want %.2f", got, unknownPairFactor)
	}
}

func TestCorrelationGraph_GetFactor_UnknownPairWithinLoadedSport(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	// Football is loaded, but this specific pair has no modeled edge:
	// still fails closed to the conservative default, not 1.0.
	got := g.GetFactor(SportFootball, MarketMatch1X2, MarketBTTS)
	if got != unknownPairFactor {
		t.Errorf("unknown-pair factor = %.2f, want %.2f", got, unknownPairFactor)
	}
}

func TestCorrelationGraph_GetFactor_DifferentSport(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	// Tennis has no graph loaded: fails closed to the conservative default.
	got := g.GetFactor(SportTennis, MarketMatch1X2, MarketTotalGoals)
	if got != unknownPairFactor {
		t.Errorf("cross-sport factor = %.2f, want %.2f", got, unknownPairFactor)
	}
}

func TestCompositeCorrelation_TwoMarkets(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	composite := g.CompositeCorrelation(SportFootball, []MarketType{MarketMatch1X2, MarketTotalGoals})
	if composite != 0.65 {
		t.Errorf("composite = %.2f, want 0.65", composite)
	}
}

func TestCompositeCorrelation_ThreeMarkets(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)
	g.AddEdge(SportFootball, MarketBTTS, MarketTotalGoals, 0.50, true)
	g.AddEdge(SportFootball, MarketMatch1X2, MarketBTTS, 0.75, true)

	// composite = 0.65 * 0.50 * 0.75 = 0.24375
	composite := g.CompositeCorrelation(SportFootball, []MarketType{MarketMatch1X2, MarketTotalGoals, MarketBTTS})
	expected := 0.65 * 0.50 * 0.75
	if math.Abs(composite-expected) > 0.001 {
		t.Errorf("composite = %.4f, want %.4f", composite, expected)
	}
}

func TestCompositeCorrelation_SingleMarket(t *testing.T) {
	g := NewCorrelationGraph()
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	// Single market: no pairs, composite should be 1.0.
	composite := g.CompositeCorrelation(SportFootball, []MarketType{MarketMatch1X2})
	if composite != 1.0 {
		t.Errorf("single-market composite = %.2f, want 1.0", composite)
	}
}

func TestCompositeCorrelation_UnmodeledPair_ConservativeDefault(t *testing.T) {
	g := NewCorrelationGraph()
	// No edges between these markets.

	composite := g.CompositeCorrelation(SportFootball, []MarketType{MarketMatch1X2, MarketPlayerProps})
	if composite != unknownPairFactor {
		t.Errorf("unmodeled composite = %.2f, want %.2f", composite, unknownPairFactor)
	}
}

func TestCompositeCorrelation_FloorClamp(t *testing.T) {
	g := NewCorrelationGraph()
	// Create extremely strong correlations that would push below 0.10.
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.05, true)
	g.AddEdge(SportFootball, MarketBTTS, MarketTotalGoals, 0.05, true)
	g.AddEdge(SportFootball, MarketMatch1X2, MarketBTTS, 0.05, true)

	// Raw: 0.05 * 0.05 * 0.05 = 0.000125, should clamp to 0.10
	composite := g.CompositeCorrelation(SportFootball, []MarketType{MarketMatch1X2, MarketTotalGoals, MarketBTTS})
	if composite != 0.10 {
		t.Errorf("clamped composite = %.2f, want 0.10", composite)
	}
}

func TestDefaultFootballCorrelationGraph_HasExpectedEdges(t *testing.T) {
	g := DefaultFootballCorrelationGraph()

	tests := []struct {
		name   string
		a, b   MarketType
		expect float64
	}{
		{"1X2 vs TotalGoals", MarketMatch1X2, MarketTotalGoals, 0.65},
		{"BTTS vs TotalGoals", MarketBTTS, MarketTotalGoals, 0.50},
		{"1X2 vs BTTS", MarketMatch1X2, MarketBTTS, 0.75},
		{"CorrectScore vs 1X2", MarketCorrectScore, MarketMatch1X2, 0.20},
		{"DoubleChance vs 1X2", MarketDoubleChance, MarketMatch1X2, 0.30},
		{"FirstHalf1X2 vs 1X2", MarketFirstHalf1X2, MarketMatch1X2, 0.60},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := g.GetFactor(SportFootball, tt.a, tt.b)
			if got != tt.expect {
				t.Errorf("factor = %.2f, want %.2f", got, tt.expect)
			}
		})
	}
}

func TestListEdges_Football(t *testing.T) {
	g := DefaultFootballCorrelationGraph()
	edges := g.ListEdges(SportFootball)
	if len(edges) == 0 {
		t.Error("expected non-empty edge list for football")
	}
	// Verify no duplicate pairs.
	seen := make(map[string]bool)
	for _, e := range edges {
		key := string(e.MarketA) + "|" + string(e.MarketB)
		if seen[key] {
			t.Errorf("duplicate edge: %s -> %s", e.MarketA, e.MarketB)
		}
		seen[key] = true
	}
}

func TestListEdges_UnknownSport(t *testing.T) {
	g := DefaultFootballCorrelationGraph()
	edges := g.ListEdges(Sport("cricket"))
	if len(edges) != 0 {
		t.Errorf("expected empty edge list for unknown sport, got %d", len(edges))
	}
}

func TestCorrelationGraph_HasSport(t *testing.T) {
	g := DefaultFootballCorrelationGraph()

	if !g.HasSport(SportFootball) {
		t.Error("expected football to be a loaded sport")
	}
	if g.HasSport(SportTennis) {
		t.Error("expected tennis to not be a loaded sport")
	}
	if g.HasSport(SportBasketball) {
		t.Error("expected basketball to not be a loaded sport")
	}
}

func TestCorrelationGraph_HasSport_EmptyGraph(t *testing.T) {
	g := NewCorrelationGraph()

	if g.HasSport(SportFootball) {
		t.Error("expected empty graph to have no loaded sports")
	}
}
