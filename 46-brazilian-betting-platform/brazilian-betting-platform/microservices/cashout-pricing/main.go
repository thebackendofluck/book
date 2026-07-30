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
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	cfg := loadConfig()

	// Wire dependencies.
	store := NewQuoteStore()
	pricingCfg := DefaultPricingConfig()
	if cfg.OperatorMargin > 0 {
		pricingCfg.DefaultMargin = cfg.OperatorMargin
	}

	engine := NewCashoutPricingEngine(pricingCfg, logger)
	walletClient := NewWalletClient(cfg.WalletBaseURL, logger)
	sigapClient := NewSIGAPCashoutClient(cfg.SIGAPBaseURL, cfg.SIGAPOperatorID, cfg.SIGAPAPIKey, logger)
	quoteManager := NewQuoteManager(engine, store, walletClient, sigapClient, logger)
	partialManager := NewPartialCashoutManager(engine, logger)
	reconciliation := NewReconciliationEngine(store, cfg.SettlementBaseURL, logger)

	// Start background quote expiry goroutine.
	expiryCtx, expiryCancel := context.WithCancel(context.Background())
	go runQuoteExpiry(expiryCtx, quoteManager, logger)

	// Build router.
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))
	r.Use(structuredLogger(logger))

	r.Get("/health", HealthCheck())
	r.Get("/ready", ReadinessCheck())

	r.Route("/cashout", func(r chi.Router) {
		// Generate a cashout quote for a bet.
		r.Post("/quote", GenerateQuoteHandler(quoteManager, logger))

		// Accept an outstanding cashout quote.
		r.Post("/accept", AcceptQuoteHandler(quoteManager, reconciliation, logger))

		// Check cashout eligibility for a bet.
		r.Post("/eligibility", CheckEligibilityHandler(engine, logger))

		// Get quote details.
		r.Get("/quote/{id}", GetQuoteHandler(store, logger))

		// Invalidate pending quotes for a bet (called by odds-feed on price change).
		r.Post("/invalidate/{betID}", InvalidateQuotesHandler(quoteManager, logger))

		// Calculate partial cashout split preview.
		r.Post("/partial/preview", PartialPreviewHandler(partialManager, logger))

		// Get audit trail for a bet's cashout history.
		r.Get("/audit/{betID}", AuditTrailHandler(reconciliation, logger))

		// Reconcile a bet's cashout history with settlement expectations.
		r.Post("/reconcile/{betID}", ReconcileHandler(reconciliation, logger))
	})

	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown.
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		logger.Info("cashout pricing engine starting", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-quit
	logger.Info("shutting down cashout pricing engine")
	expiryCancel()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("forced shutdown", "error", err)
	}
	logger.Info("cashout pricing engine stopped")
}

// config holds all service configuration.
type config struct {
	Port              string
	WalletBaseURL     string
	SettlementBaseURL string
	SIGAPBaseURL      string
	SIGAPOperatorID   string
	SIGAPAPIKey       string
	OperatorMargin    float64
	BettingEngineURL  string
}

// loadConfig reads configuration from environment variables.
func loadConfig() config {
	margin := 0.0
	if v := os.Getenv("OPERATOR_MARGIN"); v != "" {
		// Parse as float; default 0 means use PricingConfig default.
		var m float64
		if _, err := json.Number(v).Float64(); err == nil {
			m, _ = json.Number(v).Float64()
		}
		margin = m
	}

	return config{
		Port:              getEnv("PORT", "8085"),
		WalletBaseURL:     getEnv("WALLET_BASE_URL", ""),
		SettlementBaseURL: getEnv("SETTLEMENT_BASE_URL", ""),
		SIGAPBaseURL:      getEnv("SIGAP_BASE_URL", "https://sigap.sfc.fazenda.gov.br"),
		SIGAPOperatorID:   getEnv("SIGAP_OPERATOR_ID", "OP-TEST-001"),
		SIGAPAPIKey:       getEnv("SIGAP_API_KEY", ""),
		OperatorMargin:    margin,
		BettingEngineURL:  getEnv("BETTING_ENGINE_URL", "http://localhost:8080"),
	}
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func structuredLogger(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
			next.ServeHTTP(ww, r)
			logger.Info("request",
				"method", r.Method,
				"path", r.URL.Path,
				"status", ww.Status(),
				"bytes", ww.BytesWritten(),
				"duration_ms", time.Since(start).Milliseconds(),
				"request_id", middleware.GetReqID(r.Context()),
			)
		})
	}
}

// runQuoteExpiry periodically expires stale quotes.
func runQuoteExpiry(ctx context.Context, qm *QuoteManager, logger *slog.Logger) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			count, err := qm.ExpireStaleQuotes(ctx)
			if err != nil {
				logger.Error("quote expiry scan failed", "error", err)
				continue
			}
			if count > 0 {
				logger.Info("expired stale quotes", "count", count)
			}
		}
	}
}

// --- HTTP Handlers ---

// HealthCheck returns 200 if the service is running.
func HealthCheck() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok", "service": "cashout-pricing"})
	}
}

// ReadinessCheck returns 200 if the service is ready to accept requests.
func ReadinessCheck() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ready", "service": "cashout-pricing"})
	}
}

// GenerateQuoteHandler handles POST /cashout/quote.
func GenerateQuoteHandler(qm *QuoteManager, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req GenerateQuoteRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}

		if req.BetID == "" || req.CPF == "" {
			http.Error(w, `{"error":"bet_id and cpf are required"}`, http.StatusBadRequest)
			return
		}

		// In production, fetch bet snapshot from betting-engine. For now, use a
		// placeholder that would be replaced by an HTTP call to the betting engine.
		bet, err := fetchBetSnapshot(r.Context(), req.BetID)
		if err != nil {
			logger.Error("failed to fetch bet snapshot", "bet_id", req.BetID, "error", err)
			http.Error(w, `{"error":"bet not found or unavailable"}`, http.StatusNotFound)
			return
		}

		if bet.CPF != req.CPF {
			http.Error(w, `{"error":"cpf does not match bet owner"}`, http.StatusForbidden)
			return
		}

		quote, err := qm.GenerateQuote(r.Context(), *bet, req.CashoutPercent)
		if err != nil {
			logger.Warn("quote generation failed", "bet_id", req.BetID, "error", err)
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
			return
		}

		writeJSON(w, http.StatusOK, quote)
	}
}

// AcceptQuoteHandler handles POST /cashout/accept.
func AcceptQuoteHandler(qm *QuoteManager, recon *ReconciliationEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req AcceptQuoteRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}

		if req.QuoteID == "" || req.BetID == "" || req.CPF == "" {
			http.Error(w, `{"error":"quote_id, bet_id, and cpf are required"}`, http.StatusBadRequest)
			return
		}

		// Fetch a fresh bet snapshot so the quote can be re-priced against
		// current market conditions immediately before crediting. A fetch
		// failure is logged and treated as "skip re-pricing" rather than
		// blocking the accept outright — fetchBetSnapshot is a placeholder
		// for the real betting-engine integration.
		currentBet, err := fetchBetSnapshot(r.Context(), req.BetID)
		if err != nil {
			logger.Warn("could not fetch current bet snapshot for re-pricing", "bet_id", req.BetID, "error", err)
			currentBet = nil
		}

		resp, err := qm.AcceptQuote(r.Context(), req, currentBet)
		if err != nil {
			logger.Warn("quote acceptance failed", "quote_id", req.QuoteID, "error", err)
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
			return
		}

		// Notify settlement service asynchronously.
		go func() {
			quote, _ := qm.store.GetQuote(context.Background(), req.QuoteID)
			if quote != nil {
				if err := recon.NotifySettlement(context.Background(), quote); err != nil {
					logger.Error("settlement notification failed", "quote_id", req.QuoteID, "error", err)
				}
			}
		}()

		writeJSON(w, http.StatusOK, resp)
	}
}

// CheckEligibilityHandler handles POST /cashout/eligibility.
func CheckEligibilityHandler(engine *CashoutPricingEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			BetID string `json:"bet_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}

		bet, err := fetchBetSnapshot(r.Context(), req.BetID)
		if err != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "bet not found"})
			return
		}

		eligibility := engine.CheckEligibility(*bet)
		writeJSON(w, http.StatusOK, eligibility)
	}
}

// GetQuoteHandler handles GET /cashout/quote/{id}.
func GetQuoteHandler(store *QuoteStore, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")
		quote, err := store.GetQuote(r.Context(), id)
		if err != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, quote)
	}
}

// InvalidateQuotesHandler handles POST /cashout/invalidate/{betID}.
func InvalidateQuotesHandler(qm *QuoteManager, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		betID := chi.URLParam(r, "betID")
		var req struct {
			Reason string `json:"reason"`
		}
		json.NewDecoder(r.Body).Decode(&req)

		if req.Reason == "" {
			req.Reason = "odds_change"
		}

		count, err := qm.InvalidatePendingQuotesForBet(r.Context(), betID, req.Reason)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}

		writeJSON(w, http.StatusOK, map[string]interface{}{
			"bet_id":            betID,
			"invalidated_count": count,
			"reason":            req.Reason,
		})
	}
}

// PartialPreviewHandler handles POST /cashout/partial/preview.
func PartialPreviewHandler(pm *PartialCashoutManager, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			BetID          string  `json:"bet_id"`
			CashoutPercent float64 `json:"cashout_percent"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}

		bet, err := fetchBetSnapshot(r.Context(), req.BetID)
		if err != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "bet not found"})
			return
		}

		result, err := pm.CalculatePartialSplit(*bet, req.CashoutPercent)
		if err != nil {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
			return
		}

		writeJSON(w, http.StatusOK, result)
	}
}

// AuditTrailHandler handles GET /cashout/audit/{betID}.
func AuditTrailHandler(recon *ReconciliationEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		betID := chi.URLParam(r, "betID")

		trail, err := recon.BuildAuditTrail(r.Context(), betID)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}

		writeJSON(w, http.StatusOK, map[string]interface{}{
			"bet_id": betID,
			"trail":  trail,
		})
	}
}

// ReconcileHandler handles POST /cashout/reconcile/{betID}.
func ReconcileHandler(recon *ReconciliationEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		betID := chi.URLParam(r, "betID")
		var req struct {
			CurrentStake float64 `json:"current_stake"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}

		if err := recon.ReconcileBetSettlement(r.Context(), betID, req.CurrentStake); err != nil {
			writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
			return
		}

		writeJSON(w, http.StatusOK, map[string]string{
			"bet_id": betID,
			"status": "reconciled",
		})
	}
}

// fetchBetSnapshot is a placeholder that would call the betting engine API.
// In production, this fetches real bet data via HTTP.
func fetchBetSnapshot(_ context.Context, betID string) (*BetSnapshot, error) {
	// Mock implementation for development/testing.
	// In production, call betting-engine GET /bets/{id} and map to BetSnapshot.
	return &BetSnapshot{
		ID:              betID,
		CPF:             "123.456.789-09",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.50,
		PotentialReturn: 250.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				MarketID:    "mkt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.50,
				CurrentOdds: 1.80,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
		PlacedAt: time.Now().Add(-1 * time.Hour),
	}, nil
}

// writeJSON writes a JSON response with the given status code.
func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}
