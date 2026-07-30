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
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// Deposit handles POST /wallet/{cpf}/deposit.
// Initiates a PIX QR code deposit request.
func Deposit(store *Store, pix *PIXClient, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cpf := chi.URLParam(r, "cpf")

		var req DepositRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.Amount <= 0 {
			writeError(w, http.StatusBadRequest, "amount must be positive")
			return
		}

		// Ensure wallet exists.
		if _, err := store.GetWallet(r.Context(), cpf); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				if _, createErr := store.CreateWallet(r.Context(), cpf); createErr != nil {
					logger.Error("create wallet failed", "cpf", maskCPF(cpf), "error", createErr)
					writeError(w, http.StatusInternalServerError, "could not create wallet")
					return
				}
			} else {
				logger.Error("get wallet failed", "cpf", maskCPF(cpf), "error", err)
				writeError(w, http.StatusInternalServerError, "could not retrieve wallet")
				return
			}
		}

		payment, err := pix.InitiateDeposit(r.Context(), cpf, req.Amount)
		if err != nil {
			logger.Error("pix deposit failed", "cpf", maskCPF(cpf), "error", err)
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		writeJSON(w, http.StatusCreated, payment)
	}
}

// Withdraw handles POST /wallet/{cpf}/withdraw.
func Withdraw(store *Store, pix *PIXClient, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cpf := chi.URLParam(r, "cpf")

		var req WithdrawRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.Amount <= 0 {
			writeError(w, http.StatusBadRequest, "amount must be positive")
			return
		}
		if req.PixKey == "" {
			writeError(w, http.StatusBadRequest, "pix_key is required")
			return
		}

		wallet, err := store.GetWallet(r.Context(), cpf)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				writeError(w, http.StatusNotFound, "wallet not found")
				return
			}
			writeError(w, http.StatusInternalServerError, "could not retrieve wallet")
			return
		}
		if wallet.Blocked {
			writeError(w, http.StatusForbidden, fmt.Sprintf("wallet is blocked: %s", wallet.BlockReason))
			return
		}

		payment, err := pix.InitiateWithdrawal(r.Context(), cpf, &req)
		if err != nil {
			if errors.Is(err, ErrInsufficientFunds) {
				writeError(w, http.StatusUnprocessableEntity, err.Error())
				return
			}
			logger.Error("pix withdrawal failed", "cpf", maskCPF(cpf), "error", err)
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		writeJSON(w, http.StatusCreated, payment)
	}
}

// GetBalance handles GET /wallet/{cpf}/balance.
func GetBalance(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cpf := chi.URLParam(r, "cpf")

		wallet, err := store.GetWallet(r.Context(), cpf)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				writeError(w, http.StatusNotFound, "wallet not found")
				return
			}
			logger.Error("get wallet failed", "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve wallet")
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"cpf":            maskCPF(wallet.CPF),
			"balance":        wallet.Balance,
			"bonus_balance":  wallet.BonusBalance,
			"pending_debits": wallet.PendingDebits,
			"blocked":        wallet.Blocked,
			"updated_at":     wallet.UpdatedAt,
		})
	}
}

// GetTransactions handles GET /wallet/{cpf}/transactions.
func GetTransactions(store *Store, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cpf := chi.URLParam(r, "cpf")
		limit := 50
		offset := 0

		if l := r.URL.Query().Get("limit"); l != "" {
			if v, err := strconv.Atoi(l); err == nil && v > 0 && v <= 200 {
				limit = v
			}
		}
		if o := r.URL.Query().Get("offset"); o != "" {
			if v, err := strconv.Atoi(o); err == nil && v >= 0 {
				offset = v
			}
		}

		txs, err := store.GetTransactions(r.Context(), cpf, limit, offset)
		if err != nil {
			logger.Error("get transactions failed", "cpf", maskCPF(cpf), "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve transactions")
			return
		}
		if txs == nil {
			txs = []Transaction{}
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"cpf":          maskCPF(cpf),
			"transactions": txs,
			"count":        len(txs),
			"limit":        limit,
			"offset":       offset,
		})
	}
}

// Reconcile handles POST /wallet/reconcile.
// Triggers a daily reconciliation run.
func Reconcile(engine *ReconciliationEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		date := r.URL.Query().Get("date")
		// Validate date format if provided.
		if date != "" {
			if _, err := time.Parse("2006-01-02", date); err != nil {
				writeError(w, http.StatusBadRequest, "date must be YYYY-MM-DD format")
				return
			}
		}

		result, err := engine.Reconcile(r.Context(), date)
		if err != nil {
			logger.Error("reconciliation failed", "date", date, "error", err)
			writeError(w, http.StatusInternalServerError, "reconciliation failed")
			return
		}

		code := http.StatusOK
		if !result.Reconciled {
			code = http.StatusConflict
		}
		writeJSON(w, code, result)
	}
}

// PIXWebhook handles POST /wallet/webhook/pix.
// Receives payment confirmation from the PSP.
func PIXWebhook(pix *PIXClient, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// In production, validate webhook signature here (HMAC-SHA256 or mTLS).
		var payload PIXWebhookPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			writeError(w, http.StatusBadRequest, "invalid webhook payload")
			return
		}
		if payload.PIXPaymentID == "" || payload.E2EID == "" {
			writeError(w, http.StatusBadRequest, "pix_payment_id and e2e_id are required")
			return
		}

		if err := pix.ConfirmDeposit(r.Context(), &payload); err != nil {
			logger.Error("pix webhook processing failed",
				"pix_id", payload.PIXPaymentID,
				"error", err,
			)
			writeError(w, http.StatusInternalServerError, "webhook processing failed")
			return
		}

		writeJSON(w, http.StatusOK, map[string]string{"status": "accepted"})
	}
}

// HealthCheck handles GET /health.
func HealthCheck() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":  "ok",
			"service": "wallet",
			"time":    time.Now().UTC(),
		})
	}
}

// ReadinessCheck handles GET /ready.
func ReadinessCheck(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Lightweight connectivity check.
		var one int
		if err := store.pool.QueryRow(r.Context(), `SELECT 1`).Scan(&one); err != nil {
			writeError(w, http.StatusServiceUnavailable, "database unavailable")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	}
}
