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
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// SettleEventWithRules handles POST /settle/event/{id}/v2.
// Enhanced settlement using sport-specific rules from the EventResult payload.
func SettleEventWithRules(
	store *Store,
	engine *SportSettlementEngine,
	sigap *SIGAPSettlementClient,
	producer *KafkaProducer,
	logger *slog.Logger,
) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		eventID := chi.URLParam(r, "id")

		var result EventResult
		if err := json.NewDecoder(r.Body).Decode(&result); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		result.EventID = eventID

		if result.Status == "" {
			result.Status = "finished"
		}

		// Get all unsettled bets for this event.
		bets, err := store.GetUnsettledBetsForEvent(ctx, eventID)
		if err != nil {
			logger.Error("get unsettled bets failed", "event_id", eventID, "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve bets")
			return
		}

		if len(bets) == 0 {
			writeJSON(w, http.StatusOK, map[string]any{
				"event_id": eventID,
				"message":  "no unsettled bets found for this event",
			})
			return
		}

		now := time.Now().UTC()
		run := &EventSettlementRun{
			ID:        uuid.NewString(),
			EventID:   eventID,
			Status:    SettlementStatusProcessing,
			TotalBets: len(bets),
			StartedAt: now,
		}

		// Build selection results from sport-specific rules.
		// For each bet, resolve the outcome based on the market type.
		selectionResults := make(map[string]string)

		// In a full implementation, we'd look up the market type for each
		// selection from the odds-feed service. For Phase 1, we settle
		// using the 1X2 rules as the primary market type.
		for _, bet := range bets {
			marketResults, mktErr := engine.SettleMarket(
				MarketSettlement1X2,
				bet.Selections,
				result,
			)
			if mktErr != nil {
				logger.Warn("market settlement failed, using void",
					"bet_id", bet.ID, "error", mktErr)
				for _, sel := range bet.Selections {
					selectionResults[sel.SelectionID] = "void"
				}
				continue
			}
			for selID, outcome := range marketResults {
				selectionResults[selID] = outcome
			}
		}

		var settlements []Settlement
		for _, bet := range bets {
			outcome, grossReturn := determineBetOutcome(bet, selectionResults)
			switch outcome {
			case BetOutcomeWon:
				run.WinningBets++
			case BetOutcomeLost:
				run.LosingBets++
			default:
				run.VoidBets++
			}

			taxAmount, netPayout := CalculateTax(bet.Stake, grossReturn)
			ggrContrib := bet.Stake - grossReturn // tax already inside grossReturn

			s := Settlement{
				ID:          uuid.NewString(),
				BetID:       bet.ID,
				EventID:     eventID,
				CPF:         bet.CPF,
				Outcome:     outcome,
				Stake:       bet.Stake,
				GrossReturn: grossReturn,
				TaxWithheld: taxAmount,
				NetPayout:   netPayout,
				GGRContrib:  ggrContrib,
				SettledAt:   now,
			}

			if err := store.SaveSettlement(ctx, &s); err != nil {
				logger.Error("save settlement failed", "bet_id", bet.ID, "error", err)
				continue
			}

			// Report to SIGAP asynchronously.
			go func(settlement Settlement) {
				if reportErr := sigap.ReportSettlement(ctx, &settlement); reportErr != nil {
					logger.Error("sigap settlement report failed",
						"bet_id", settlement.BetID, "error", reportErr)
				} else {
					store.MarkSettlementSIGAPReported(ctx, settlement.ID) //nolint:errcheck
				}
			}(s)

			// Publish to Kafka.
			if producer != nil {
				go func(settlement Settlement) {
					if pubErr := producer.PublishSettlement(ctx, &settlement); pubErr != nil {
						logger.Warn("kafka publish failed", "bet_id", settlement.BetID, "error", pubErr)
					}
				}(s)
			}

			settlements = append(settlements, s)
		}

		// Update run totals.
		_, totalPrizes, totalTax, ggr := GGRFromSettlements(settlements)
		for _, s := range settlements {
			run.TotalStake += s.Stake
		}
		run.TotalPrizesPaid = totalPrizes
		run.TotalTaxWithheld = totalTax
		run.GGR = ggr
		run.Status = SettlementStatusCompleted
		completedAt := time.Now().UTC()
		run.CompletedAt = &completedAt

		if err := store.SaveEventSettlementRun(ctx, run); err != nil {
			logger.Error("save settlement run failed", "error", err)
		}

		logger.Info("event settled with sport rules",
			"event_id", eventID,
			"sport", result.Sport,
			"score", formatScore(result.HomeScore, result.AwayScore),
			"total_bets", run.TotalBets,
			"winning_bets", run.WinningBets,
			"losing_bets", run.LosingBets,
			"void_bets", run.VoidBets,
			"ggr", ggr,
		)

		writeJSON(w, http.StatusOK, map[string]any{
			"event_id":     eventID,
			"sport":        result.Sport,
			"result":       formatScore(result.HomeScore, result.AwayScore),
			"run":          run,
			"settlements":  len(settlements),
		})
	}
}

// SettleFootballEvent handles POST /settle/football/{id}.
// Convenience endpoint for settling a football event with explicit score input.
func SettleFootballEvent(
	store *Store,
	engine *SportSettlementEngine,
	sigap *SIGAPSettlementClient,
	producer *KafkaProducer,
	logger *slog.Logger,
) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")

		var req struct {
			HomeScore    int `json:"home_score"`
			AwayScore    int `json:"away_score"`
			HalfTimeHome int `json:"half_time_home"`
			HalfTimeAway int `json:"half_time_away"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		// Build an EventResult for the sport settlement engine.
		result := EventResult{
			EventID:      eventID,
			Sport:        SportTypeFootball,
			HomeScore:    req.HomeScore,
			AwayScore:    req.AwayScore,
			HalfTimeHome: req.HalfTimeHome,
			HalfTimeAway: req.HalfTimeAway,
			Status:       "finished",
		}

		ctx := r.Context()
		bets, err := store.GetUnsettledBetsForEvent(ctx, eventID)
		if err != nil {
			logger.Error("get unsettled bets failed", "event_id", eventID, "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve bets")
			return
		}

		now := time.Now().UTC()
		run := &EventSettlementRun{
			ID:        uuid.NewString(),
			EventID:   eventID,
			Status:    SettlementStatusProcessing,
			TotalBets: len(bets),
			StartedAt: now,
		}

		// Settle using 1X2 rules.
		selectionResults := make(map[string]string)
		for _, bet := range bets {
			marketResults, _ := engine.SettleMarket(MarketSettlement1X2, bet.Selections, result)
			for selID, outcome := range marketResults {
				selectionResults[selID] = outcome
			}
		}

		var settlements []Settlement
		for _, bet := range bets {
			outcome, grossReturn := determineBetOutcome(bet, selectionResults)
			switch outcome {
			case BetOutcomeWon:
				run.WinningBets++
			case BetOutcomeLost:
				run.LosingBets++
			default:
				run.VoidBets++
			}

			taxAmount, netPayout := CalculateTax(bet.Stake, grossReturn)
			ggrContrib := bet.Stake - grossReturn // tax already inside grossReturn

			s := Settlement{
				ID:          uuid.NewString(),
				BetID:       bet.ID,
				EventID:     eventID,
				CPF:         bet.CPF,
				Outcome:     outcome,
				Stake:       bet.Stake,
				GrossReturn: grossReturn,
				TaxWithheld: taxAmount,
				NetPayout:   netPayout,
				GGRContrib:  ggrContrib,
				SettledAt:   now,
			}

			if err := store.SaveSettlement(ctx, &s); err != nil {
				logger.Error("save settlement failed", "bet_id", bet.ID, "error", err)
				continue
			}
			settlements = append(settlements, s)
		}

		_, totalPrizes, totalTax, ggr := GGRFromSettlements(settlements)
		for _, s := range settlements {
			run.TotalStake += s.Stake
		}
		run.TotalPrizesPaid = totalPrizes
		run.TotalTaxWithheld = totalTax
		run.GGR = ggr
		run.Status = SettlementStatusCompleted
		completedAt := time.Now().UTC()
		run.CompletedAt = &completedAt
		store.SaveEventSettlementRun(ctx, run) //nolint:errcheck

		logger.Info("football event settled",
			"event_id", eventID,
			"score", formatScore(req.HomeScore, req.AwayScore),
			"total_bets", run.TotalBets,
		)

		writeJSON(w, http.StatusOK, run)
	}
}

func formatScore(home, away int) string {
	return fmt.Sprintf("%d-%d", home, away)
}
