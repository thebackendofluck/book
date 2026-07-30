// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package models defines the core domain types for the sportsbook ledger.
// Each Bet represents a single selection within a combination (parlay) slip.
// FeedMessage is the top-level object received from the BMC betstream API.
package models

import (
	"strings"
	"time"
)

// AcmeVoidType classifies the reason a bet was voided by the sportsbook.
type AcmeVoidType string

const (
	VoidTypeCancelled AcmeVoidType = "CANCELLED"
	VoidTypeVoided    AcmeVoidType = "VOIDED"
	VoidTypeWin       AcmeVoidType = "WIN"
)

// cancelledReasons mirrors the Scala AcmeVoidType.CancelledReasons set.
var cancelledReasons = map[string]bool{
	"non-starter":                  true,
	"postponed":                    true,
	"stakes refunded":              true,
	"the event has been shortened": true,
	"the participants (home & away) were swapped due to change in venue": true,
	"goodwill gesture":      true,
	"on behalf of operator": true,
	"the bet was placed while we were encountering technical issues": true,
}

const cancelledReasonPrefix = "an obvious mistake"

// MapReasonToAcmeVoidType converts a sportsbook void reason string to a VoidType.
func MapReasonToAcmeVoidType(reason string) AcmeVoidType {
	lower := strings.ToLower(reason)
	if cancelledReasons[lower] || strings.HasPrefix(lower, cancelledReasonPrefix) {
		return VoidTypeCancelled
	}
	return VoidTypeWin
}

// FindAcmeVoidType derives the void classification from status + outcome list.
// betStatusID 4 = voided by sportsbook; 8 = deleted by sportsbook.
func FindAcmeVoidType(betStatusID int, outcomes []map[string]interface{}) *AcmeVoidType {
	switch betStatusID {
	case 4:
		hasCancelled := false
		for _, outcome := range outcomes {
			var vt AcmeVoidType
			if reason, ok := outcome["voidReason"].(string); ok {
				vt = MapReasonToAcmeVoidType(reason)
			} else {
				vt = VoidTypeWin
			}
			if vt == VoidTypeCancelled {
				hasCancelled = true
				break
			}
		}
		if hasCancelled {
			v := VoidTypeCancelled
			return &v
		}
		v := VoidTypeWin
		return &v
	case 8:
		v := VoidTypeVoided
		return &v
	default:
		return nil
	}
}

// Bet represents one selection within a betslip combination.
type Bet struct {
	BetID          string  `db:"bet_id"`
	CombinationRef int64   `db:"combination_ref"`
	Live           bool    `db:"live"`
	EventID        int64   `db:"event_id"`
	EventName      string  `db:"event_name"`
	Meeting        string  `db:"meeting"` // e.g. "World Cup"
	CriterionName  string  `db:"criterion_name"`
	SportID        string  `db:"sport_id"`
	Status         string  `db:"status"`
	Odds           float64 `db:"odds"`
	OutcomeLabel   string  `db:"outcome_label"`
	MessageID      int64   `db:"message_id"`
	SelectionType  string  `db:"selection_type"`
}

// FeedMessage represents a betstream message from the BMC API.
type FeedMessage struct {
	MessageID                  int64         `db:"id"`
	CustomerPlayerID           string        `db:"customer_player_id"`
	UpdatedDate                time.Time     `db:"update_date"`
	PlacedDate                 time.Time     `db:"placed_date"`
	BetStatusID                int           `db:"bet_status_id"`
	OperatorStakeTransactionID string        `db:"operator_stake_transaction_id"`
	CombinationRef             int64         `db:"combination_ref"`
	EventGroupID               int64         `db:"event_group_id"`
	MessageType                string        `db:"message_type"`
	Stake                      float64       `db:"stake"`
	Payout                     float64       `db:"payout"`
	EventGroup                 string        `db:"event_group"`
	IsCombination              bool          `db:"is_combination"`
	IsCancellationVoid         bool          `db:"is_cancellation_void"`
	AcmeVoidType               *AcmeVoidType `db:"acme_void_type"`
	JSON                       []byte        `db:"json"`
	Created                    *time.Time    `db:"created"`
}
