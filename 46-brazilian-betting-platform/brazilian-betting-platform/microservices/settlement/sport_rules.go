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
	"strconv"
	"strings"
)

// SportType identifies a sport for settlement rule selection.
type SportType string

const (
	SportTypeFootball   SportType = "football"
	SportTypeTennis     SportType = "tennis"
	SportTypeBasketball SportType = "basketball"
	SportTypeMMA        SportType = "mma"
	SportTypeESports    SportType = "esports"
)

// MarketSettlementType identifies how a market should be settled.
type MarketSettlementType string

const (
	MarketSettlement1X2          MarketSettlementType = "1x2"
	MarketSettlementOverUnder    MarketSettlementType = "over_under"
	MarketSettlementBothScore    MarketSettlementType = "both_teams_score"
	MarketSettlementDoubleChance MarketSettlementType = "double_chance"
	MarketSettlementHalfTime     MarketSettlementType = "half_time_result"
	MarketSettlementHandicap     MarketSettlementType = "handicap"
	MarketSettlementCorrectScore MarketSettlementType = "correct_score"
	MarketSettlementMatchWinner  MarketSettlementType = "match_winner"
)

// EventResult holds the official result data for settlement.
type EventResult struct {
	EventID      string            `json:"event_id"`
	Sport        SportType         `json:"sport"`
	HomeScore    int               `json:"home_score"`
	AwayScore    int               `json:"away_score"`
	HalfTimeHome int               `json:"half_time_home"`
	HalfTimeAway int               `json:"half_time_away"`
	Status       string            `json:"status"` // "finished", "abandoned", "cancelled"
	ExtraData    map[string]string `json:"extra_data,omitempty"` // e.g., "penalty_home": "4"
}

// SelectionSettlement maps a selection to its outcome.
type SelectionSettlement struct {
	SelectionID   string     `json:"selection_id"`
	SelectionName string     `json:"selection_name"`
	Outcome       BetOutcome `json:"outcome"`
	Reason        string     `json:"reason,omitempty"`
}

// SportSettlementEngine resolves market outcomes based on sport-specific rules.
type SportSettlementEngine struct{}

// NewSportSettlementEngine creates a new settlement engine.
func NewSportSettlementEngine() *SportSettlementEngine {
	return &SportSettlementEngine{}
}

// SettleMarket resolves all selections in a market given an event result.
func (e *SportSettlementEngine) SettleMarket(
	marketType MarketSettlementType,
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	// Cancelled or abandoned events void all bets.
	if result.Status == "cancelled" || result.Status == "abandoned" {
		outcomes := make(map[string]string)
		for _, sel := range selections {
			outcomes[sel.SelectionID] = "void"
		}
		return outcomes, nil
	}

	switch result.Sport {
	case SportTypeFootball:
		return e.settleFootball(marketType, selections, result)
	default:
		// Generic settlement for other sports (match winner only).
		return e.settleGenericMatchWinner(selections, result)
	}
}

// settleFootball applies football-specific settlement rules.
func (e *SportSettlementEngine) settleFootball(
	marketType MarketSettlementType,
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	switch marketType {
	case MarketSettlement1X2, MarketSettlementMatchWinner:
		return e.settleFootball1X2(selections, result)
	case MarketSettlementOverUnder:
		return e.settleFootballOverUnder(selections, result)
	case MarketSettlementBothScore:
		return e.settleFootballBothScore(selections, result)
	case MarketSettlementDoubleChance:
		return e.settleFootballDoubleChance(selections, result)
	case MarketSettlementHalfTime:
		return e.settleFootballHalfTime(selections, result)
	case MarketSettlementCorrectScore:
		return e.settleFootballCorrectScore(selections, result)
	case MarketSettlementHandicap:
		return e.settleFootballHandicap(selections, result)
	default:
		return nil, fmt.Errorf("unsupported market type for football: %s", marketType)
	}
}

// selectionMatches reports whether a selection's identifier signals the
// given canonical outcome label (e.g. "home", "draw", "away", "1x"). Grading
// must key off selection identity, not position: a bet slip only carries
// the leg(s) the player actually chose, not the market's full canonical
// ordering, so index-based grading silently mis-grades any slip that isn't
// exactly [home, draw, away].
func selectionMatches(selectionID, label string) bool {
	return strings.Contains(strings.ToLower(selectionID), label)
}

// settleFootball1X2 settles 1X2 (home/draw/away) markets.
func (e *SportSettlementEngine) settleFootball1X2(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	outcomes := make(map[string]string)

	var winner string
	switch {
	case result.HomeScore > result.AwayScore:
		winner = "home"
	case result.HomeScore == result.AwayScore:
		winner = "draw"
	default:
		winner = "away"
	}

	for _, sel := range selections {
		if selectionMatches(sel.SelectionID, winner) {
			outcomes[sel.SelectionID] = "won"
		} else {
			outcomes[sel.SelectionID] = "lost"
		}
	}

	return outcomes, nil
}

// settleFootballOverUnder settles total goals over/under markets.
func (e *SportSettlementEngine) settleFootballOverUnder(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	totalGoals := result.HomeScore + result.AwayScore
	outcomes := make(map[string]string)

	for _, sel := range selections {
		name := strings.ToLower(sel.SelectionID)
		// Parse the line from selection name (e.g., "mais de 2.5" / "menos de 2.5").
		line := 2.5 // Default line.
		if strings.Contains(name, "1.5") {
			line = 1.5
		} else if strings.Contains(name, "3.5") {
			line = 3.5
		} else if strings.Contains(name, "0.5") {
			line = 0.5
		}

		isOver := strings.Contains(name, "mais") || strings.Contains(name, "over")

		if isOver {
			if float64(totalGoals) > line {
				outcomes[sel.SelectionID] = "won"
			} else {
				outcomes[sel.SelectionID] = "lost"
			}
		} else {
			if float64(totalGoals) < line {
				outcomes[sel.SelectionID] = "won"
			} else {
				outcomes[sel.SelectionID] = "lost"
			}
		}
	}
	return outcomes, nil
}

// settleFootballBothScore settles "both teams to score" markets.
func (e *SportSettlementEngine) settleFootballBothScore(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	bothScored := result.HomeScore > 0 && result.AwayScore > 0
	outcomes := make(map[string]string)

	for _, sel := range selections {
		name := strings.ToLower(sel.SelectionID)
		isYes := strings.Contains(name, "sim") || strings.Contains(name, "yes")

		if isYes {
			if bothScored {
				outcomes[sel.SelectionID] = "won"
			} else {
				outcomes[sel.SelectionID] = "lost"
			}
		} else {
			if !bothScored {
				outcomes[sel.SelectionID] = "won"
			} else {
				outcomes[sel.SelectionID] = "lost"
			}
		}
	}
	return outcomes, nil
}

// settleFootballDoubleChance settles double chance markets (1X, 12, X2).
func (e *SportSettlementEngine) settleFootballDoubleChance(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	outcomes := make(map[string]string)

	homeWin := result.HomeScore > result.AwayScore
	draw := result.HomeScore == result.AwayScore
	awayWin := result.AwayScore > result.HomeScore

	for _, sel := range selections {
		var won bool
		switch {
		case selectionMatches(sel.SelectionID, "1x"): // Home or Draw
			won = homeWin || draw
		case selectionMatches(sel.SelectionID, "x2"): // Away or Draw
			won = awayWin || draw
		case selectionMatches(sel.SelectionID, "12"): // Home or Away
			won = homeWin || awayWin
		default:
			outcomes[sel.SelectionID] = "void"
			continue
		}
		if won {
			outcomes[sel.SelectionID] = "won"
		} else {
			outcomes[sel.SelectionID] = "lost"
		}
	}
	return outcomes, nil
}

// settleFootballHalfTime settles half-time result markets.
func (e *SportSettlementEngine) settleFootballHalfTime(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	outcomes := make(map[string]string)

	var winner string
	switch {
	case result.HalfTimeHome > result.HalfTimeAway:
		winner = "home"
	case result.HalfTimeHome == result.HalfTimeAway:
		winner = "draw"
	default:
		winner = "away"
	}

	for _, sel := range selections {
		if selectionMatches(sel.SelectionID, winner) {
			outcomes[sel.SelectionID] = "won"
		} else {
			outcomes[sel.SelectionID] = "lost"
		}
	}
	return outcomes, nil
}

// settleFootballCorrectScore settles correct score markets.
func (e *SportSettlementEngine) settleFootballCorrectScore(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	outcomes := make(map[string]string)
	actualScore := fmt.Sprintf("%d-%d", result.HomeScore, result.AwayScore)

	for _, sel := range selections {
		// Selection name is expected to contain the score (e.g., "2-1").
		if strings.Contains(sel.SelectionID, actualScore) {
			outcomes[sel.SelectionID] = "won"
		} else {
			outcomes[sel.SelectionID] = "lost"
		}
	}
	return outcomes, nil
}

// settleFootballHandicap settles Asian handicap markets.
func (e *SportSettlementEngine) settleFootballHandicap(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	outcomes := make(map[string]string)
	goalDiff := result.HomeScore - result.AwayScore

	for _, sel := range selections {
		// Parse handicap value from selection data.
		handicapStr, ok := parseHandicap(sel.SelectionID)
		if !ok {
			outcomes[sel.SelectionID] = "void"
			continue
		}

		handicap, err := strconv.ParseFloat(handicapStr, 64)
		if err != nil {
			outcomes[sel.SelectionID] = "void"
			continue
		}

		adjustedDiff := float64(goalDiff) + handicap

		if adjustedDiff > 0 {
			outcomes[sel.SelectionID] = "won"
		} else if adjustedDiff == 0 {
			// Push - stake returned.
			outcomes[sel.SelectionID] = "void"
		} else {
			outcomes[sel.SelectionID] = "lost"
		}
	}
	return outcomes, nil
}

// settleGenericMatchWinner settles a simple two-way match winner market.
func (e *SportSettlementEngine) settleGenericMatchWinner(
	selections []BetSelectionRecord,
	result EventResult,
) (map[string]string, error) {
	outcomes := make(map[string]string)

	if result.HomeScore == result.AwayScore {
		// Draw in two-way market = void.
		for _, sel := range selections {
			outcomes[sel.SelectionID] = "void"
		}
		return outcomes, nil
	}

	winner := "home"
	if result.AwayScore > result.HomeScore {
		winner = "away"
	}

	for _, sel := range selections {
		if selectionMatches(sel.SelectionID, winner) {
			outcomes[sel.SelectionID] = "won"
		} else {
			outcomes[sel.SelectionID] = "lost"
		}
	}
	return outcomes, nil
}

// parseHandicap extracts a handicap value from a selection identifier.
func parseHandicap(selectionID string) (string, bool) {
	// Look for patterns like "+1.5", "-0.5", "+2".
	for _, prefix := range []string{"+", "-"} {
		idx := strings.LastIndex(selectionID, prefix)
		if idx >= 0 {
			return selectionID[idx:], true
		}
	}
	return "", false
}

// VoidReason categorizes why a bet or market was voided.
type VoidReason string

const (
	VoidReasonEventCancelled  VoidReason = "event_cancelled"
	VoidReasonEventAbandoned  VoidReason = "event_abandoned"
	VoidReasonHandicapPush    VoidReason = "handicap_push"
	VoidReasonResultDisputed  VoidReason = "result_disputed"
	VoidReasonTraderOverride  VoidReason = "trader_override"
	VoidReasonRegulatory      VoidReason = "regulatory"
)
