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
	"fmt"
	"math"
	"sync"
)

// CorrelationGraph models the statistical dependency between markets within
// a single event. Edges carry a factor between 0.0 (fully correlated, meaning
// simple multiplication would overstate true probability) and 1.0 (independent).
//
// For bet builder pricing the graph is consulted for every pair of selected
// markets; the pairwise factors are aggregated into a single composite
// adjustment applied to the naive multiplicative odds.
type CorrelationGraph struct {
	mu    sync.RWMutex
	edges map[Sport]map[MarketType][]CorrelationEdge
}

// NewCorrelationGraph creates an empty graph.
func NewCorrelationGraph() *CorrelationGraph {
	return &CorrelationGraph{
		edges: make(map[Sport]map[MarketType][]CorrelationEdge),
	}
}

// unknownPairFactor is used when no correlation edge exists between two
// markets. It deliberately sits below 1.0 (independent) rather than at 1.0:
// an unmodeled correlation should understate the payout, not overstate it.
// Same-event legs are never truly independent, so treating an unknown pair
// as fully independent lets players build same-game parlays priced as if
// the legs were uncorrelated, which inflates their expected value.
const unknownPairFactor = 0.85

// AddEdge inserts a correlation factor between two market types for a sport.
// If symmetric is true the reverse edge is also added.
func (g *CorrelationGraph) AddEdge(sport Sport, a, b MarketType, factor float64, symmetric bool) {
	g.mu.Lock()
	defer g.mu.Unlock()

	if _, ok := g.edges[sport]; !ok {
		g.edges[sport] = make(map[MarketType][]CorrelationEdge)
	}

	g.edges[sport][a] = append(g.edges[sport][a], CorrelationEdge{Target: b, Factor: factor})
	if symmetric {
		g.edges[sport][b] = append(g.edges[sport][b], CorrelationEdge{Target: a, Factor: factor})
	}
}

// GetFactor returns the correlation factor between two markets for a sport.
// Fails closed: when no edge is found (either the sport has no correlation
// graph loaded, or the pair simply has no modeled relationship) it returns
// unknownPairFactor rather than 1.0, so an unmodeled pair still reduces the
// combined odds instead of pricing the legs as independent.
//
// Callers building same-game combinations for a sport with no graph loaded
// at all should reject the request outright rather than rely on this
// fallback for every pair; see HasSport.
func (g *CorrelationGraph) GetFactor(sport Sport, a, b MarketType) float64 {
	g.mu.RLock()
	defer g.mu.RUnlock()

	sportEdges, ok := g.edges[sport]
	if !ok {
		return unknownPairFactor
	}
	for _, edge := range sportEdges[a] {
		if edge.Target == b {
			return edge.Factor
		}
	}
	return unknownPairFactor
}

// HasSport reports whether a correlation graph has been loaded for the
// given sport. Same-game bet builder requests for sports where this
// returns false should be rejected outright rather than priced using
// GetFactor's per-pair fallback.
func (g *CorrelationGraph) HasSport(sport Sport) bool {
	g.mu.RLock()
	defer g.mu.RUnlock()

	_, ok := g.edges[sport]
	return ok
}

// CompositeCorrelation computes the aggregate adjustment factor for a set of
// markets. It examines all unique pairs and combines them multiplicatively.
//
// composite = product of factor(i,j) for all i < j
//
// A composite of 1.0 means no correlation adjustment; values below 1.0
// reduce the combined odds to account for positive correlation between
// outcomes.
func (g *CorrelationGraph) CompositeCorrelation(sport Sport, markets []MarketType) float64 {
	composite := 1.0
	for i := 0; i < len(markets); i++ {
		for j := i + 1; j < len(markets); j++ {
			composite *= g.GetFactor(sport, markets[i], markets[j])
		}
	}
	// Clamp to sensible range.
	return math.Max(0.10, math.Min(1.0, composite))
}

// ListEdges returns all correlation edges for a sport (useful for diagnostics).
func (g *CorrelationGraph) ListEdges(sport Sport) []CorrelationFactor {
	g.mu.RLock()
	defer g.mu.RUnlock()

	var factors []CorrelationFactor
	sportEdges, ok := g.edges[sport]
	if !ok {
		return factors
	}
	seen := make(map[string]bool)
	for src, edges := range sportEdges {
		for _, e := range edges {
			key := fmt.Sprintf("%s-%s", src, e.Target)
			rev := fmt.Sprintf("%s-%s", e.Target, src)
			if seen[key] || seen[rev] {
				continue
			}
			seen[key] = true
			factors = append(factors, CorrelationFactor{
				MarketA:   src,
				MarketB:   e.Target,
				Factor:    e.Factor,
				Sport:     sport,
				Symmetric: true,
			})
		}
	}
	return factors
}

// DefaultFootballCorrelationGraph returns the production correlation matrix
// for football markets. These factors are derived from historical outcome
// analysis of major Brazilian competitions (Brasileirao Serie A, Copa do Brasil).
//
// Key correlations in football:
//   - 1X2 and Total Goals are strongly correlated (home win + high goals)
//   - BTTS and Total Goals are strongly correlated (both score => more goals)
//   - 1X2 and BTTS are moderately correlated (draw + BTTS likely at certain lines)
//   - Correct Score determines 1X2, Total Goals, and BTTS (extremely correlated)
//   - Double Chance and 1X2 are structurally correlated (DC is subset of 1X2)
//   - First Half 1X2 and Full Time 1X2 are moderately correlated
func DefaultFootballCorrelationGraph() *CorrelationGraph {
	g := NewCorrelationGraph()

	// 1X2 vs Total Goals: strongly correlated.
	// Home favorites winning tends to correlate with higher goal totals.
	g.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.65, true)

	// BTTS vs Total Goals: very strongly correlated.
	// Both teams scoring virtually implies 2+ goals.
	g.AddEdge(SportFootball, MarketBTTS, MarketTotalGoals, 0.50, true)

	// 1X2 vs BTTS: moderately correlated.
	// Home/Away wins correlate with one side scoring more.
	g.AddEdge(SportFootball, MarketMatch1X2, MarketBTTS, 0.75, true)

	// Correct Score vs everything: extremely correlated (it determines all others).
	g.AddEdge(SportFootball, MarketCorrectScore, MarketMatch1X2, 0.20, true)
	g.AddEdge(SportFootball, MarketCorrectScore, MarketTotalGoals, 0.15, true)
	g.AddEdge(SportFootball, MarketCorrectScore, MarketBTTS, 0.20, true)

	// Double Chance vs 1X2: structurally correlated (DC is derived from 1X2).
	g.AddEdge(SportFootball, MarketDoubleChance, MarketMatch1X2, 0.30, true)

	// Double Chance vs Total Goals: moderate correlation.
	g.AddEdge(SportFootball, MarketDoubleChance, MarketTotalGoals, 0.70, true)

	// Double Chance vs BTTS: moderate correlation.
	g.AddEdge(SportFootball, MarketDoubleChance, MarketBTTS, 0.80, true)

	// First Half 1X2 vs Full Time 1X2: moderately correlated.
	g.AddEdge(SportFootball, MarketFirstHalf1X2, MarketMatch1X2, 0.60, true)

	// First Half 1X2 vs Total Goals: moderate correlation.
	g.AddEdge(SportFootball, MarketFirstHalf1X2, MarketTotalGoals, 0.70, true)

	// First Half 1X2 vs BTTS: weak-moderate correlation.
	g.AddEdge(SportFootball, MarketFirstHalf1X2, MarketBTTS, 0.80, true)

	// Asian Handicap vs 1X2: strongly correlated (handicap is adjusted match result).
	g.AddEdge(SportFootball, MarketAsianHandicap, MarketMatch1X2, 0.35, true)

	// Asian Handicap vs Total Goals: moderate correlation.
	g.AddEdge(SportFootball, MarketAsianHandicap, MarketTotalGoals, 0.65, true)

	// Draw No Bet vs 1X2: structurally correlated.
	g.AddEdge(SportFootball, MarketDrawNoBet, MarketMatch1X2, 0.25, true)

	// Draw No Bet vs Double Chance: strongly correlated.
	g.AddEdge(SportFootball, MarketDrawNoBet, MarketDoubleChance, 0.30, true)

	return g
}
