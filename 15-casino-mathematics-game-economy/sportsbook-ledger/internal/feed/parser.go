// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package feed handles parsing of BMC betstream JSON messages and extraction
// of Bet records from combination/selections structures.
package feed

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"sportsbook-ledger/internal/models"
)

// ParseFeedMessage deserialises a single BMC message JSON object.
// Mirrors FeedMessageProtocol.read() from the Scala source.
func ParseFeedMessage(raw map[string]interface{}) (*models.FeedMessage, error) {
	messageID, err := toInt64(raw["messageId"])
	if err != nil {
		return nil, fmt.Errorf("messageId: %w", err)
	}
	customerPlayerID, _ := raw["customerPlayerId"].(string)
	updatedDate, err := parseDateTime(raw["updatedDate"])
	if err != nil {
		return nil, fmt.Errorf("updatedDate: %w", err)
	}
	placedDate, err := parseDateTime(raw["placedDate"])
	if err != nil {
		return nil, fmt.Errorf("placedDate: %w", err)
	}
	betStatusID, _ := toInt64(raw["betStatusId"])
	operatorStakeTransactionID, _ := raw["operatorStakeTransactionId"].(string)
	messageType, _ := raw["messageType"].(string)

	combination, ok := raw["combination"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("combination field missing or not an object")
	}

	outcomes := toSlice(combination["outcomes"])
	selections := toSlice(combination["selections"])
	isCombination := len(outcomes) != 1

	combinationRef, _ := toInt64(combination["combinationRef"])
	stake, _ := toFloat64(combination["stake"])
	payout, _ := toFloat64(combination["payout"])

	// eventGroupId is -1 for parlays, taken from the first outcome otherwise
	eventGroupID := int64(-1)
	eventGroup := "Parlays"
	if !isCombination && len(outcomes) > 0 {
		first := outcomes[0].(map[string]interface{})
		eventGroupID, _ = toInt64(first["eventGroupId"])
		eventGroup = extractEventGroupPath(outcomes)
	} else if !isCombination && len(selections) > 0 {
		eventGroup = extractEventGroupPath(selections)
	}

	// Is it a cancellation void (sportsbook counts as a real void)?
	voidType, _ := raw["voidType"].(string)
	isCancellationVoid := false
	if int(betStatusID) == 4 && len(outcomes) > 0 {
		if voidType == "CANCELLATION" {
			isCancellationVoid = true
		} else if voidType == "SETTLEMENT" {
			allPostponed := true
			for _, o := range outcomes {
				om, _ := o.(map[string]interface{})
				if reason, ok := om["voidReason"].(string); !ok || reason != "Postponed" {
					allPostponed = false
					break
				}
			}
			isCancellationVoid = allPostponed
		}
	}

	active := toOutcomes(outcomes)
	if len(active) == 0 {
		active = toOutcomes(selections)
	}
	acmeVoidType := models.FindAcmeVoidType(int(betStatusID), active)

	now := time.Now().UTC()
	raw_bytes, _ := json.Marshal(raw)

	return &models.FeedMessage{
		MessageID:                  messageID,
		CustomerPlayerID:           customerPlayerID,
		UpdatedDate:                updatedDate,
		PlacedDate:                 placedDate,
		BetStatusID:                int(betStatusID),
		OperatorStakeTransactionID: operatorStakeTransactionID,
		CombinationRef:             combinationRef,
		EventGroupID:               eventGroupID,
		MessageType:                messageType,
		Stake:                      stake,
		Payout:                     payout,
		EventGroup:                 eventGroup,
		IsCombination:              isCombination,
		IsCancellationVoid:         isCancellationVoid,
		AcmeVoidType:               acmeVoidType,
		JSON:                       raw_bytes,
		Created:                    &now,
	}, nil
}

// ExtractBets pulls Bet records out of a combination JSON object.
// Mirrors FeedService.extractBets() and processSelection().
func ExtractBets(combination map[string]interface{}, messageID int64) []models.Bet {
	combinationRef, _ := toInt64(combination["combinationRef"])

	if outcomes, ok := combination["outcomes"]; ok {
		return createBetsFromOutcomes(toSlice(outcomes), combinationRef, messageID, "NONE", "", 0)
	}

	if selArr, ok := combination["selections"]; ok {
		var bets []models.Bet
		for _, s := range toSlice(selArr) {
			sel, _ := s.(map[string]interface{})
			bets = append(bets, processSelection(sel, combinationRef, messageID)...)
		}
		return bets
	}
	return nil
}

func processSelection(sel map[string]interface{}, combinationRef, messageID int64) []models.Bet {
	selType, _ := sel["selectionType"].(string)
	status, _ := sel["status"].(string)
	odds, _ := toFloat64(sel["placedOdds"])

	switch selType {
	case "SIMPLE":
		if outcome, ok := sel["outcome"]; ok {
			return createBetsFromOutcomes([]interface{}{outcome}, combinationRef, messageID, selType, status, odds)
		}
	case "BET_BUILDER":
		outerGroup, _ := sel["outerSelectionGroup"].(map[string]interface{})
		innerGroups := toSlice(outerGroup["innerSelectionGroups"])
		var bets []models.Bet
		for _, g := range innerGroups {
			gm, _ := g.(map[string]interface{})
			for _, o := range toSlice(gm["outcomes"]) {
				bets = append(bets, createBetsFromOutcomes([]interface{}{o}, combinationRef, messageID, selType, status, odds)...)
			}
		}
		return bets
	}
	return nil
}

// betIDNamespace anchors the deterministic (UUIDv5) bet IDs derived below.
// Deriving bet_id from the natural key of each bet, rather than generating a
// fresh random UUID per parse, means replaying an already-ingested feed
// message resolves to the same bet_id and the DAO's ON CONFLICT upsert lands
// on the existing row instead of inserting a duplicate.
var betIDNamespace = uuid.MustParse("6c1b9f0e-2e34-4b8a-9c7d-6a2f9e6b0a11")

// deterministicBetID derives a stable UUIDv5 bet ID from the natural key of
// a bet: the feed message it came from, the combination/parlay it belongs
// to, and the selection's identifying fields (event, market, outcome).
func deterministicBetID(messageID, combinationRef, eventID int64, selType, criterionName, outcomeLabel string) string {
	key := fmt.Sprintf("%d|%d|%d|%s|%s|%s", messageID, combinationRef, eventID, selType, criterionName, outcomeLabel)
	return uuid.NewSHA1(betIDNamespace, []byte(key)).String()
}

func createBetsFromOutcomes(outcomes []interface{}, combinationRef, messageID int64, selType, status string, odds float64) []models.Bet {
	var bets []models.Bet
	for _, o := range outcomes {
		om, _ := o.(map[string]interface{})
		if om == nil {
			continue
		}

		live, _ := om["live"].(bool)
		eventID, _ := toInt64(om["eventId"])
		eventName, _ := om["eventName"].(string)
		criterionName, _ := om["criterionName"].(string)
		sportID, _ := om["sportId"].(string)
		outcomeLabel, _ := om["outcomeLabel"].(string)

		// meeting = last element's name in eventGroupPath
		meeting := ""
		if path := toSlice(om["eventGroupPath"]); len(path) > 0 {
			last, _ := path[len(path)-1].(map[string]interface{})
			meeting, _ = last["name"].(string)
		}

		effectiveStatus := status
		effectiveOdds := odds
		if status == "" {
			effectiveStatus, _ = om["status"].(string)
			effectiveOdds, _ = toFloat64(om["odds"])
		}

		bets = append(bets, models.Bet{
			BetID:          deterministicBetID(messageID, combinationRef, eventID, selType, criterionName, outcomeLabel),
			CombinationRef: combinationRef,
			Live:           live,
			EventID:        eventID,
			EventName:      eventName,
			Meeting:        meeting,
			CriterionName:  criterionName,
			SportID:        sportID,
			Status:         effectiveStatus,
			Odds:           effectiveOdds,
			OutcomeLabel:   outcomeLabel,
			MessageID:      messageID,
			SelectionType:  selType,
		})
	}
	return bets
}

func extractEventGroupPath(items []interface{}) string {
	var parts []string
	for _, item := range items {
		m, _ := item.(map[string]interface{})
		if pathArr, ok := m["eventGroupPath"]; ok {
			for _, p := range toSlice(pathArr) {
				pm, _ := p.(map[string]interface{})
				name, _ := pm["name"].(string)
				parts = append(parts, strings.Trim(name, `"`))
			}
		}
	}
	return strings.Join(parts, " - ")
}

func toOutcomes(items []interface{}) []map[string]interface{} {
	var out []map[string]interface{}
	for _, i := range items {
		if m, ok := i.(map[string]interface{}); ok {
			out = append(out, m)
		}
	}
	return out
}

func toSlice(v interface{}) []interface{} {
	if v == nil {
		return nil
	}
	s, _ := v.([]interface{})
	return s
}

func toInt64(v interface{}) (int64, error) {
	switch n := v.(type) {
	case float64:
		return int64(n), nil
	case int64:
		return n, nil
	case int:
		return int64(n), nil
	default:
		return 0, fmt.Errorf("cannot convert %T to int64", v)
	}
}

func toFloat64(v interface{}) (float64, error) {
	switch n := v.(type) {
	case float64:
		return n, nil
	case int64:
		return float64(n), nil
	default:
		return 0, fmt.Errorf("cannot convert %T to float64", v)
	}
}

func parseDateTime(v interface{}) (time.Time, error) {
	s, ok := v.(string)
	if !ok {
		return time.Time{}, fmt.Errorf("expected string, got %T", v)
	}
	return time.Parse(time.RFC3339, s)
}
