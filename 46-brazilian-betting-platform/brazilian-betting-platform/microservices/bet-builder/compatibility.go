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
	"sync"
)

// CompatibilityCatalogue stores the allowed / blocked market combinations
// per sport. This is consulted before pricing to reject structurally
// impossible or commercially unwanted combinations.
type CompatibilityCatalogue struct {
	mu    sync.RWMutex
	rules map[Sport]map[string]CompatibilityRule
}

// NewCompatibilityCatalogue creates an empty catalogue.
func NewCompatibilityCatalogue() *CompatibilityCatalogue {
	return &CompatibilityCatalogue{
		rules: make(map[Sport]map[string]CompatibilityRule),
	}
}

// pairKey returns a deterministic key for an unordered market pair.
func pairKey(a, b MarketType) string {
	if a < b {
		return fmt.Sprintf("%s|%s", a, b)
	}
	return fmt.Sprintf("%s|%s", b, a)
}

// AddRule adds a compatibility rule for a market pair within a sport.
func (c *CompatibilityCatalogue) AddRule(sport Sport, a, b MarketType, allowed bool, reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if _, ok := c.rules[sport]; !ok {
		c.rules[sport] = make(map[string]CompatibilityRule)
	}
	c.rules[sport][pairKey(a, b)] = CompatibilityRule{
		MarketA: a,
		MarketB: b,
		Allowed: allowed,
		Sport:   sport,
		Reason:  reason,
	}
}

// IsCompatible checks whether two market types can be combined for a sport.
// Returns true (allowed) when no explicit rule exists (open-world assumption).
func (c *CompatibilityCatalogue) IsCompatible(sport Sport, a, b MarketType) (bool, string) {
	c.mu.RLock()
	defer c.mu.RUnlock()

	sportRules, ok := c.rules[sport]
	if !ok {
		return true, ""
	}
	rule, ok := sportRules[pairKey(a, b)]
	if !ok {
		return true, ""
	}
	return rule.Allowed, rule.Reason
}

// ValidateSelections checks all pairwise combinations in a set of selections
// and returns any incompatibility errors.
func (c *CompatibilityCatalogue) ValidateSelections(sport Sport, selections []BuilderSelection) []string {
	var errors []string
	for i := 0; i < len(selections); i++ {
		for j := i + 1; j < len(selections); j++ {
			allowed, reason := c.IsCompatible(sport, selections[i].MarketType, selections[j].MarketType)
			if !allowed {
				msg := fmt.Sprintf("markets %s and %s cannot be combined", selections[i].MarketType, selections[j].MarketType)
				if reason != "" {
					msg += ": " + reason
				}
				errors = append(errors, msg)
			}
		}
	}
	// Check for duplicate market types (same market selected twice).
	seen := make(map[MarketType]bool)
	for _, sel := range selections {
		if seen[sel.MarketType] {
			errors = append(errors, fmt.Sprintf("duplicate market type %s; only one selection per market allowed", sel.MarketType))
		}
		seen[sel.MarketType] = true
	}
	return errors
}

// ListRules returns all rules for a sport (useful for diagnostics / API).
func (c *CompatibilityCatalogue) ListRules(sport Sport) []CompatibilityRule {
	c.mu.RLock()
	defer c.mu.RUnlock()

	var rules []CompatibilityRule
	sportRules, ok := c.rules[sport]
	if !ok {
		return rules
	}
	for _, rule := range sportRules {
		rules = append(rules, rule)
	}
	return rules
}

// DefaultFootballCompatibility returns the production compatibility rules
// for football bet builder. Some combinations are blocked because they are
// structurally redundant or the pricing model cannot handle the correlation.
func DefaultFootballCompatibility() *CompatibilityCatalogue {
	c := NewCompatibilityCatalogue()

	// --- Allowed combinations (explicitly marked for clarity) ---

	// 1X2 + Total Goals: classic builder combo.
	c.AddRule(SportFootball, MarketMatch1X2, MarketTotalGoals, true, "")

	// 1X2 + BTTS: popular football combo.
	c.AddRule(SportFootball, MarketMatch1X2, MarketBTTS, true, "")

	// Total Goals + BTTS: allowed, strong correlation handled in pricing.
	c.AddRule(SportFootball, MarketTotalGoals, MarketBTTS, true, "")

	// 1X2 + First Half 1X2: allowed, correlated but common.
	c.AddRule(SportFootball, MarketMatch1X2, MarketFirstHalf1X2, true, "")

	// BTTS + First Half 1X2: allowed.
	c.AddRule(SportFootball, MarketBTTS, MarketFirstHalf1X2, true, "")

	// Total Goals + First Half 1X2: allowed.
	c.AddRule(SportFootball, MarketTotalGoals, MarketFirstHalf1X2, true, "")

	// --- Blocked combinations ---

	// 1X2 + Double Chance: DC is a strict subset of 1X2.
	c.AddRule(SportFootball, MarketMatch1X2, MarketDoubleChance, false,
		"double chance is derived from 1X2; combining them is structurally redundant")

	// 1X2 + Draw No Bet: DNB is structurally derived from 1X2.
	c.AddRule(SportFootball, MarketMatch1X2, MarketDrawNoBet, false,
		"draw no bet is derived from 1X2; combining them is structurally redundant")

	// Double Chance + Draw No Bet: both are 1X2 derivatives.
	c.AddRule(SportFootball, MarketDoubleChance, MarketDrawNoBet, false,
		"both markets are derived from 1X2; cannot combine")

	// 1X2 + Asian Handicap: handicap is a transformed match result.
	c.AddRule(SportFootball, MarketMatch1X2, MarketAsianHandicap, false,
		"asian handicap is a transformed match result; combining with 1X2 is redundant")

	// Correct Score + 1X2: correct score fully determines 1X2.
	c.AddRule(SportFootball, MarketCorrectScore, MarketMatch1X2, false,
		"correct score determines 1X2 outcome")

	// Correct Score + Total Goals: correct score fully determines total goals.
	c.AddRule(SportFootball, MarketCorrectScore, MarketTotalGoals, false,
		"correct score determines total goals outcome")

	// Correct Score + BTTS: correct score fully determines BTTS.
	c.AddRule(SportFootball, MarketCorrectScore, MarketBTTS, false,
		"correct score determines BTTS outcome")

	// Correct Score + Double Chance: fully determined.
	c.AddRule(SportFootball, MarketCorrectScore, MarketDoubleChance, false,
		"correct score determines double chance outcome")

	// Correct Score + Draw No Bet: fully determined.
	c.AddRule(SportFootball, MarketCorrectScore, MarketDrawNoBet, false,
		"correct score determines draw no bet outcome")

	// Correct Score + First Half 1X2: partially determined, block to be safe.
	c.AddRule(SportFootball, MarketCorrectScore, MarketFirstHalf1X2, false,
		"correct score largely determines half-time result")

	// Asian Handicap + Draw No Bet: both are match-result transforms.
	c.AddRule(SportFootball, MarketAsianHandicap, MarketDrawNoBet, false,
		"both are derived from match result; cannot combine")

	// Asian Handicap + Double Chance: both are match-result transforms.
	c.AddRule(SportFootball, MarketAsianHandicap, MarketDoubleChance, false,
		"both are derived from match result; cannot combine")

	return c
}
