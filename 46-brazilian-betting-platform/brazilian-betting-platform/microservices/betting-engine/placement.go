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
	"context"
	"encoding/json"
	"log/slog"
	"math"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// PlaceBetV2 handles POST /bets/v2.
// This is the Phase 1 enhanced placement handler that adds:
// - Full betslip validation with market compatibility checks
// - Accumulator pricing and correlation controls
// - Wallet balance pre-check and reservation
// - Detailed rejection reasons
func PlaceBetV2(
	store *Store,
	cache *Cache,
	wallet *WalletClient,
	validator *BetslipValidator,
	accumulator *AccumulatorPricing,
	sigap *SIGAPClient,
	integrity *IntegrityChecker,
	logger *slog.Logger,
) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		// Detached context for fire-and-forget background work (SIGAP report,
		// fund release, integrity): the request context is cancelled the moment
		// the handler returns, which would silently abort these outbound calls.
		bgCtx := context.WithoutCancel(ctx)

		r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // cap body at 1 MiB
		var req PlaceBetRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		// Step 1: Session validation.
		session, err := cache.GetSession(ctx, req.SessionID)
		if err != nil || session == nil {
			writeJSON(w, http.StatusUnauthorized, map[string]any{
				"status":  "rejected",
				"reason":  "invalid_session",
				"message": "invalid or expired session",
			})
			return
		}
		if session["cpf"] != req.CPF {
			writeJSON(w, http.StatusForbidden, map[string]any{
				"status":  "rejected",
				"reason":  "cpf_mismatch",
				"message": "session CPF mismatch",
			})
			return
		}

		// Step 2: Full betslip validation.
		validation := validator.ValidateBetslip(ctx, &req)
		if !validation.Valid {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
				"status":   "rejected",
				"reason":   "validation_failed",
				"errors":   validation.Errors,
				"warnings": validation.Warnings,
			})
			return
		}

		// Step 3: For accumulators, run pricing validation.
		combinedOdds := 1.0
		if req.Type == BetTypeMultiple {
			quote := accumulator.PriceAccumulator(req.Selections, req.Stake)
			if !quote.Valid {
				writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
					"status":  "rejected",
					"reason":  "accumulator_validation_failed",
					"errors":  quote.Errors,
					"quote":   quote,
				})
				return
			}
			combinedOdds = quote.CombinedOdds
		} else {
			for _, sel := range req.Selections {
				combinedOdds *= sel.OddsValue
			}
			combinedOdds = math.Round(combinedOdds*100) / 100
		}

		potentialReturn := math.Round(req.Stake*combinedOdds*100) / 100

		// Step 4: Wallet balance check and reservation.
		balance, err := wallet.GetBalance(ctx, req.CPF)
		if err != nil {
			logger.Error("wallet balance check failed", "cpf", maskCPF(req.CPF), "error", err)
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"status":  "rejected",
				"reason":  "wallet_unavailable",
				"message": "could not verify wallet balance",
			})
			return
		}

		if balance.AvailableBalance < req.Stake {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
				"status":  "rejected",
				"reason":  "insufficient_balance",
				"message": formatBRL(balance.AvailableBalance) + " available, need " + formatBRL(req.Stake),
			})
			return
		}

		betID := uuid.NewString()

		// Reserve funds in the wallet.
		reservation, err := wallet.ReserveFunds(ctx, ReservationRequest{
			CPF:       req.CPF,
			BetID:     betID,
			Amount:    req.Stake,
			Reference: "bet_placement",
		})
		if err != nil || reservation.Status != "reserved" {
			logger.Error("wallet reservation failed", "error", err)
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"status":  "rejected",
				"reason":  "wallet_reservation_failed",
				"message": "could not reserve funds",
			})
			return
		}

		// Step 5: Build and persist the bet.
		now := time.Now().UTC()
		selections := make([]BetSelection, 0, len(req.Selections))
		for _, s := range req.Selections {
			selections = append(selections, BetSelection{
				ID:            uuid.NewString(),
				BetID:         betID,
				EventID:       s.EventID,
				MarketID:      s.MarketID,
				SelectionID:   s.SelectionID,
				OddsValue:     s.OddsValue,
				EventName:     s.EventID,
				MarketName:    s.MarketID,
				SelectionName: s.SelectionID,
				Result:        SelectionResultPending,
			})
		}

		bet := &Bet{
			ID:              betID,
			CPF:             req.CPF,
			Type:            req.Type,
			Status:          BetStatusAccepted,
			Stake:           req.Stake,
			PotentialReturn: potentialReturn,
			CombinedOdds:    combinedOdds,
			Selections:      selections,
			IPAddress:       req.IPAddress,
			DeviceID:        req.DeviceID,
			PlacedAt:        now,
			UpdatedAt:       now,
		}

		if err := store.CreateBet(ctx, bet); err != nil {
			logger.Error("failed to create bet", "error", err)
			// Release the wallet reservation on failure.
			go wallet.ReleaseFunds(bgCtx, ReleaseRequest{
				ReservationID: reservation.ReservationID,
				CPF:           req.CPF,
				BetID:         betID,
				Reason:        "bet_rejected",
			})
			writeJSON(w, http.StatusInternalServerError, map[string]any{
				"status":  "rejected",
				"reason":  "persistence_failed",
				"message": "could not place bet",
			})
			return
		}

		// Step 6: Async SIGAP reporting.
		go func() {
			reportID, reportErr := sigap.ReportBet(bgCtx, bet)
			if reportErr != nil {
				logger.Error("sigap report failed", "bet_id", bet.ID, "error", reportErr)
				return
			}
			if updateErr := store.UpdateSIGAPReportID(ctx, bet.ID, reportID); updateErr != nil {
				logger.Error("failed to update sigap report id", "bet_id", bet.ID, "error", updateErr)
			}
		}()

		// Step 7: Async integrity checks.
		go func() {
			if _, intErr := integrity.Evaluate(bgCtx, bet); intErr != nil {
				logger.Error("integrity check failed", "bet_id", bet.ID, "error", intErr)
			}
		}()

		// Increment daily stake counter.
		cache.IncrementDailyStake(ctx, req.CPF, req.Stake) //nolint:errcheck

		logger.Info("bet placed (v2)",
			"bet_id", bet.ID,
			"cpf", maskCPF(bet.CPF),
			"type", bet.Type,
			"stake", bet.Stake,
			"combined_odds", bet.CombinedOdds,
			"potential_return", bet.PotentialReturn,
			"legs", len(bet.Selections),
			"reservation_id", reservation.ReservationID,
		)

		writeJSON(w, http.StatusCreated, map[string]any{
			"status":           "accepted",
			"bet":              bet,
			"reservation_id":   reservation.ReservationID,
			"validation":       validation,
		})
	}
}

// ValidateBetslipEndpoint handles POST /bets/validate.
// Allows the frontend to pre-validate a betslip before placement.
func ValidateBetslipEndpoint(
	validator *BetslipValidator,
	accumulator *AccumulatorPricing,
	logger *slog.Logger,
) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req PlaceBetRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		validation := validator.ValidateBetslip(r.Context(), &req)

		response := map[string]any{
			"valid":    validation.Valid,
			"errors":   validation.Errors,
			"warnings": validation.Warnings,
		}

		// Include accumulator pricing if applicable.
		if req.Type == BetTypeMultiple && len(req.Selections) >= 2 {
			quote := accumulator.PriceAccumulator(req.Selections, req.Stake)
			response["accumulator_quote"] = quote
		} else if len(req.Selections) == 1 {
			response["combined_odds"] = req.Selections[0].OddsValue
			response["potential_return"] = math.Round(req.Stake*req.Selections[0].OddsValue*100) / 100
		}

		writeJSON(w, http.StatusOK, response)
	}
}

// GetAccumulatorQuote handles POST /bets/accumulator/quote.
// Returns pricing for an accumulator without placing the bet.
func GetAccumulatorQuote(accumulator *AccumulatorPricing) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Stake      float64        `json:"stake"`
			Selections []SelectionReq `json:"selections"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if len(req.Selections) < 2 {
			writeError(w, http.StatusBadRequest, "at least 2 selections required for accumulator")
			return
		}

		quote := accumulator.PriceAccumulator(req.Selections, req.Stake)
		writeJSON(w, http.StatusOK, quote)
	}
}

// SettleBetV2 handles POST /bets/{id}/settle/v2.
// Enhanced settlement that handles accumulators correctly with void leg repricing.
func SettleBetV2(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		id := chi.URLParam(r, "id")

		var req SettleBetRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		bet, err := store.GetBet(ctx, id)
		if err != nil {
			writeError(w, http.StatusNotFound, "bet not found")
			return
		}

		if bet.Status == BetStatusSettled {
			writeError(w, http.StatusConflict, "bet already settled")
			return
		}
		if bet.Status == BetStatusCashedOut || bet.Status == BetStatusCancelled {
			writeError(w, http.StatusConflict, "bet cannot be settled in current state")
			return
		}

		// Apply results to selections.
		for i, sel := range bet.Selections {
			result, ok := req.Results[sel.SelectionID]
			if !ok {
				result = SelectionResultVoid
			}
			bet.Selections[i].Result = result
		}

		// Use accumulator settlement logic for multi-leg bets.
		var overallResult SelectionResult
		var actualReturn float64

		if bet.Type == BetTypeMultiple {
			overallResult, actualReturn, _ = SettleAccumulator(bet.Selections, bet.Stake)
		} else {
			// Single bet.
			if len(bet.Selections) > 0 {
				overallResult = bet.Selections[0].Result
			}
			switch overallResult {
			case SelectionResultWon:
				actualReturn = bet.PotentialReturn
			case SelectionResultVoid:
				actualReturn = bet.Stake
			default:
				actualReturn = 0
			}
		}

		// Brazilian income tax: 15% on net profit (Lei 14.790/2023, Art. 31).
		profit := actualReturn - bet.Stake
		var taxWithheld float64
		if profit > 0 {
			taxWithheld = profit * 0.15
		}
		netPayout := actualReturn - taxWithheld

		settlement := &Settlement{
			ID:          uuid.NewString(),
			BetID:       bet.ID,
			CPF:         bet.CPF,
			Result:      overallResult,
			Stake:       bet.Stake,
			Return:      actualReturn,
			Profit:      profit,
			TaxWithheld: taxWithheld,
			NetPayout:   netPayout,
			SettledBy:   req.SettledBy,
		}

		if err := store.SettleBet(ctx, id, settlement); err != nil {
			logger.Error("settle bet v2 failed", "bet_id", id, "error", err)
			writeError(w, http.StatusInternalServerError, "settlement failed")
			return
		}

		logger.Info("bet settled (v2)",
			"bet_id", id,
			"type", bet.Type,
			"legs", len(bet.Selections),
			"result", overallResult,
			"actual_return", actualReturn,
			"net_payout", netPayout,
			"tax_withheld", taxWithheld,
		)

		writeJSON(w, http.StatusOK, settlement)
	}
}
