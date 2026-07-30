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
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// SIGAPReporter is the interface for SIGAP bet reporting (allows stub injection).
type SIGAPReporter interface {
	ReportBet(ctx interface{ Done() <-chan struct{} }, bet *Bet) (string, error)
}

// writeJSON is a helper to write a JSON response.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// writeError writes a structured JSON error response.
func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// formatBRL formats a float as a currency string suitable for display.
func formatBRL(v float64) string {
	return fmt.Sprintf("%.2f", v)
}

// PlaceBet handles POST /bets.
// Validates the CPF session, enforces responsible gambling limits, persists the bet,
// then asynchronously reports to SIGAP and triggers integrity checks.
func PlaceBet(store *Store, cache *Cache, sigap *SIGAPClient, integrity *IntegrityChecker, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()

		var req PlaceBetRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		if req.CPF == "" || req.SessionID == "" {
			writeError(w, http.StatusBadRequest, "cpf and session_id are required")
			return
		}
		if req.Stake < 1.00 {
			writeError(w, http.StatusBadRequest, "minimum stake is R$1.00")
			return
		}
		if len(req.Selections) == 0 {
			writeError(w, http.StatusBadRequest, "at least one selection required")
			return
		}

		// Verify session.
		session, err := cache.GetSession(ctx, req.SessionID)
		if err != nil || session == nil {
			writeError(w, http.StatusUnauthorized, "invalid or expired session")
			return
		}
		if session["cpf"] != req.CPF {
			writeError(w, http.StatusForbidden, "session CPF mismatch")
			return
		}

		// Responsible gambling: check limits.
		limits, err := store.GetPlayerLimits(ctx, req.CPF)
		if err != nil {
			logger.Error("failed to get player limits", "cpf", maskCPF(req.CPF), "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve player limits")
			return
		}
		if req.Stake > limits.MaxSingleBet {
			writeError(w, http.StatusUnprocessableEntity,
				"stake exceeds single-bet limit of R$"+formatBRL(limits.MaxSingleBet))
			return
		}

		dailyTotal, err := store.GetPlayerDailyStake(ctx, req.CPF)
		if err != nil {
			logger.Warn("failed to get daily stake", "cpf", maskCPF(req.CPF), "error", err)
		}
		if dailyTotal+req.Stake > limits.DailyStakeLimit {
			writeError(w, http.StatusUnprocessableEntity, "daily stake limit would be exceeded")
			return
		}

		// Build the bet.
		now := time.Now().UTC()
		betID := uuid.NewString()
		combinedOdds := 1.0
		selections := make([]BetSelection, 0, len(req.Selections))
		for _, s := range req.Selections {
			combinedOdds *= s.OddsValue
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
			PotentialReturn: req.Stake * combinedOdds,
			CombinedOdds:    combinedOdds,
			Selections:      selections,
			IPAddress:       req.IPAddress,
			DeviceID:        req.DeviceID,
			PlacedAt:        now,
			UpdatedAt:       now,
		}

		if err := store.CreateBet(ctx, bet); err != nil {
			logger.Error("failed to create bet", "error", err)
			writeError(w, http.StatusInternalServerError, "could not place bet")
			return
		}

		// Report to SIGAP asynchronously (non-blocking for the player response).
		go func() {
			reportID, reportErr := sigap.ReportBet(ctx, bet)
			if reportErr != nil {
				logger.Error("sigap report failed", "bet_id", bet.ID, "error", reportErr)
				return
			}
			if updateErr := store.UpdateSIGAPReportID(ctx, bet.ID, reportID); updateErr != nil {
				logger.Error("failed to update sigap report id", "bet_id", bet.ID, "error", updateErr)
			}
		}()

		// Run integrity checks asynchronously.
		go func() {
			if _, intErr := integrity.Evaluate(ctx, bet); intErr != nil {
				logger.Error("integrity check failed", "bet_id", bet.ID, "error", intErr)
			}
		}()

		logger.Info("bet placed",
			"bet_id", bet.ID,
			"cpf", maskCPF(bet.CPF),
			"stake", bet.Stake,
			"combined_odds", bet.CombinedOdds,
		)

		writeJSON(w, http.StatusCreated, bet)
	}
}

// GetBet handles GET /bets/{id}.
func GetBet(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")
		if id == "" {
			writeError(w, http.StatusBadRequest, "bet id required")
			return
		}

		bet, err := store.GetBet(r.Context(), id)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				writeError(w, http.StatusNotFound, "bet not found")
				return
			}
			logger.Error("get bet failed", "id", id, "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve bet")
			return
		}

		writeJSON(w, http.StatusOK, bet)
	}
}

// Cashout handles POST /bets/{id}/cashout.
func Cashout(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		id := chi.URLParam(r, "id")

		var req CashoutRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.Value <= 0 {
			writeError(w, http.StatusBadRequest, "cashout value must be positive")
			return
		}

		bet, err := store.GetBet(ctx, id)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				writeError(w, http.StatusNotFound, "bet not found")
				return
			}
			writeError(w, http.StatusInternalServerError, "could not retrieve bet")
			return
		}

		if bet.CPF != req.CPF {
			writeError(w, http.StatusForbidden, "bet does not belong to this player")
			return
		}
		if bet.Status != BetStatusAccepted && bet.Status != BetStatusPending {
			writeError(w, http.StatusConflict, "bet is not eligible for cashout")
			return
		}
		if req.Value > bet.PotentialReturn {
			writeError(w, http.StatusUnprocessableEntity, "cashout value exceeds potential return")
			return
		}

		if err := store.CashoutBet(ctx, id, req.Value); err != nil {
			logger.Error("cashout failed", "bet_id", id, "error", err)
			writeError(w, http.StatusInternalServerError, "cashout failed")
			return
		}

		logger.Info("bet cashed out", "bet_id", id, "value", req.Value)
		writeJSON(w, http.StatusOK, map[string]any{
			"bet_id":        id,
			"cashout_value": req.Value,
			"status":        BetStatusCashedOut,
		})
	}
}

// GetPlayerBets handles GET /bets/player/{cpf}.
func GetPlayerBets(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cpf := chi.URLParam(r, "cpf")
		if cpf == "" {
			writeError(w, http.StatusBadRequest, "cpf required")
			return
		}

		bets, err := store.GetBetsByPlayer(r.Context(), cpf)
		if err != nil {
			logger.Error("get player bets failed", "cpf", maskCPF(cpf), "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve bets")
			return
		}
		if bets == nil {
			bets = []Bet{}
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"cpf":   maskCPF(cpf),
			"bets":  bets,
			"count": len(bets),
		})
	}
}

// SettleBet handles POST /bets/{id}/settle.
func SettleBet(store *Store, logger *slog.Logger) http.HandlerFunc {
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
			if errors.Is(err, pgx.ErrNoRows) {
				writeError(w, http.StatusNotFound, "bet not found")
				return
			}
			writeError(w, http.StatusInternalServerError, "could not retrieve bet")
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

		// Evaluate each selection outcome.
		overallResult := SelectionResultWon
		for i, sel := range bet.Selections {
			result, ok := req.Results[sel.SelectionID]
			if !ok {
				result = SelectionResultVoid
			}
			bet.Selections[i].Result = result
			if result == SelectionResultLost {
				overallResult = SelectionResultLost
			}
			if result == SelectionResultVoid && overallResult != SelectionResultLost {
				overallResult = SelectionResultVoid
			}
		}

		var actualReturn float64
		if overallResult == SelectionResultWon {
			actualReturn = bet.PotentialReturn
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
			logger.Error("settle bet failed", "bet_id", id, "error", err)
			writeError(w, http.StatusInternalServerError, "settlement failed")
			return
		}

		logger.Info("bet settled",
			"bet_id", id,
			"result", overallResult,
			"net_payout", netPayout,
			"tax_withheld", taxWithheld,
		)
		writeJSON(w, http.StatusOK, settlement)
	}
}

// HealthCheck handles GET /health.
func HealthCheck(cache *Cache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		checks := map[string]string{
			"postgres": "ok",
			"redis":    "ok",
		}
		status := "ok"
		code := http.StatusOK

		if err := cache.Ping(ctx); err != nil {
			checks["redis"] = "unavailable"
			status = "degraded"
			code = http.StatusServiceUnavailable
		}

		writeJSON(w, code, map[string]any{
			"status":  status,
			"checks":  checks,
			"time":    time.Now().UTC(),
			"service": "betting-engine",
		})
	}
}

// ReadinessCheck handles GET /ready.
func ReadinessCheck(cache *Cache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := cache.Ping(r.Context()); err != nil {
			writeError(w, http.StatusServiceUnavailable, "not ready: redis unavailable")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	}
}
