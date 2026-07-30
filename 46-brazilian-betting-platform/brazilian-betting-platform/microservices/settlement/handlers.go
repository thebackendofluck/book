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

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// SettleEvent handles POST /settle/event/{id}.
// Settles all pending bets for the given event based on outcome results.
func SettleEvent(store *Store, sigap *SIGAPSettlementClient, producer *KafkaProducer, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		eventID := chi.URLParam(r, "id")

		var req SettleEventRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if len(req.Results) == 0 {
			writeError(w, http.StatusBadRequest, "results map is required")
			return
		}

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

		var settlements []Settlement
		for _, bet := range bets {
			outcome, grossReturn := determineBetOutcome(bet, req.Results)
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

		logger.Info("event settled",
			"event_id", eventID,
			"total_bets", run.TotalBets,
			"winning_bets", run.WinningBets,
			"ggr", ggr,
		)

		writeJSON(w, http.StatusOK, run)
	}
}

// GetDailyGGR handles GET /ggr/daily.
func GetDailyGGR(calc *GGRCalculator, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		date := r.URL.Query().Get("date")
		if date == "" {
			date = time.Now().UTC().AddDate(0, 0, -1).Format("2006-01-02")
		}

		report, err := calc.CalculateDaily(r.Context(), date)
		if err != nil {
			logger.Error("ggr calculation failed", "date", date, "error", err)
			writeError(w, http.StatusInternalServerError, "could not calculate GGR")
			return
		}

		writeJSON(w, http.StatusOK, report)
	}
}

// GetGGRReport handles GET /ggr/report.
func GetGGRReport(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		date := r.URL.Query().Get("date")
		if date == "" {
			date = time.Now().UTC().AddDate(0, 0, -1).Format("2006-01-02")
		}

		report, err := store.GetGGRReport(r.Context(), date)
		if err != nil {
			logger.Error("get ggr report failed", "date", date, "error", err)
			writeError(w, http.StatusNotFound, "GGR report not found for "+date)
			return
		}

		writeJSON(w, http.StatusOK, report)
	}
}

// WithholdTax handles POST /tax/withhold/{cpf}.
func WithholdTax(calc *TaxCalculator, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cpf := chi.URLParam(r, "cpf")
		period := r.URL.Query().Get("period")
		if period == "" {
			// Default to current month.
			period = time.Now().UTC().Format("2006-01")
		}

		result, err := calc.WithholdTax(r.Context(), cpf, period)
		if err != nil {
			logger.Error("tax withholding failed", "cpf", maskCPF(cpf), "error", err)
			writeError(w, http.StatusInternalServerError, "tax withholding failed")
			return
		}

		writeJSON(w, http.StatusOK, result)
	}
}

// HealthCheck handles GET /health.
func HealthCheck() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":  "ok",
			"service": "settlement",
			"time":    time.Now().UTC(),
		})
	}
}

// ReadinessCheck handles GET /ready.
func ReadinessCheck(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var one int
		if err := store.pool.QueryRow(r.Context(), `SELECT 1`).Scan(&one); err != nil {
			writeError(w, http.StatusServiceUnavailable, "database unavailable")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	}
}

// determineBetOutcome resolves a bet's outcome and gross return from
// selection results. Grading is order-independent: a LOST leg always loses
// the whole bet regardless of how many other legs are void. Void legs are
// otherwise dropped from the bet; if every leg voided, the whole bet is
// void (full stake refund). If at least one leg survives and none lost,
// the bet wins on the combined odds of just the surviving legs (the bet's
// original combined odds only apply when nothing was voided).
func determineBetOutcome(bet BetRecord, results map[string]string) (BetOutcome, float64) {
	voidCount := 0
	survivingOdds := 1.0
	for _, sel := range bet.Selections {
		result, ok := results[sel.SelectionID]
		if !ok {
			result = "void"
		}
		if result == "lost" {
			return BetOutcomeLost, 0
		}
		if result == "void" {
			voidCount++
			continue
		}
		survivingOdds *= sel.OddsValue
	}

	switch {
	case len(bet.Selections) == 0 || voidCount == len(bet.Selections):
		return BetOutcomeVoid, bet.Stake
	case voidCount == 0:
		// No legs voided: pay out on the bet's originally quoted odds.
		return BetOutcomeWon, bet.PotentialReturn
	default:
		return BetOutcomeWon, bet.Stake * survivingOdds
	}
}

// formatBRL is a formatting helper for BRL amounts.
func formatBRL(v float64) string {
	return fmt.Sprintf("%.2f", v)
}
