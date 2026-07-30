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

	// Wire bet builder dependencies. Only sports with a correlation graph
	// loaded here (currently football only) can be priced by the bet
	// builder; BuilderPricingEngine.Quote rejects same-game requests for
	// any other sport instead of silently pricing legs as independent
	// (see CorrelationGraph.HasSport). Adding a new sport requires wiring
	// its own DefaultXCorrelationGraph edges into graph below.
	graph := DefaultFootballCorrelationGraph()
	catalogue := DefaultFootballCompatibility()
	builderCfg := DefaultBuilderConfig()
	pricing := NewBuilderPricingEngine(graph, catalogue, builderCfg)
	engine := NewBuilderEngine(pricing, catalogue, builderCfg)

	// Build router.
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))
	r.Use(structuredLogger(logger))

	r.Get("/health", HealthCheck())
	r.Get("/ready", ReadinessCheck())

	r.Route("/builder", func(r chi.Router) {
		r.Post("/quote", QuoteHandler(engine, logger))
		r.Post("/validate", ValidateHandler(engine, logger))
		r.Get("/templates", TemplatesHandler(logger))
		r.Get("/templates/{sport}", TemplatesBySportHandler(logger))
		r.Get("/correlation/{sport}", CorrelationHandler(graph, logger))
		r.Get("/compatibility/{sport}", CompatibilityHandler(catalogue, logger))
	})

	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown.
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		logger.Info("bet builder engine starting", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-quit
	logger.Info("shutting down bet builder engine")

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("forced shutdown", "error", err)
	}
	logger.Info("bet builder engine stopped")
}

// config holds service configuration.
type config struct {
	Port string
}

// loadConfig reads configuration from environment variables.
func loadConfig() config {
	return config{
		Port: getEnv("PORT", "8086"),
	}
}

// getEnv returns an environment variable value or a default.
func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// structuredLogger returns a Chi middleware that logs requests with slog.
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

// --- HTTP Handlers ---

// HealthCheck returns 200 if the service is alive.
func HealthCheck() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "service": "bet-builder"})
	}
}

// ReadinessCheck returns 200 when the service is ready to accept traffic.
func ReadinessCheck() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready", "service": "bet-builder"})
	}
}

// QuoteHandler prices a bet builder combination.
func QuoteHandler(engine *BuilderEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req QuoteRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
			return
		}

		if req.EventID == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "event_id is required"})
			return
		}
		if req.Stake <= 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "stake must be positive"})
			return
		}

		slip := engine.NewBetslip(req.EventID, req.Sport)
		slip.Selections = req.Selections

		quote := engine.GetQuote(slip, req.Stake)

		logger.Info("builder quote",
			"event_id", req.EventID,
			"sport", req.Sport,
			"selections", len(req.Selections),
			"raw_odds", quote.RawCombinedOdds,
			"adjusted_odds", quote.AdjustedOdds,
			"correlation_factor", quote.CorrelationFactor,
			"valid", quote.Valid,
		)

		writeJSON(w, http.StatusOK, quote)
	}
}

// ValidateHandler checks a builder combination without pricing.
func ValidateHandler(engine *BuilderEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req QuoteRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
			return
		}

		slip := engine.NewBetslip(req.EventID, req.Sport)
		slip.Selections = req.Selections

		errors := engine.ValidateBetslip(slip)
		valid := len(errors) == 0

		writeJSON(w, http.StatusOK, map[string]interface{}{
			"valid":  valid,
			"errors": errors,
		})
	}
}

// TemplatesHandler returns all football builder templates.
func TemplatesHandler(logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		templates := FootballBuilderTemplates()
		writeJSON(w, http.StatusOK, templates)
	}
}

// TemplatesBySportHandler returns templates for a given sport.
func TemplatesBySportHandler(logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sport := Sport(chi.URLParam(r, "sport"))
		templates := GetAllTemplates(sport)
		if templates == nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "no templates for sport"})
			return
		}
		writeJSON(w, http.StatusOK, templates)
	}
}

// CorrelationHandler returns the correlation edges for a sport.
func CorrelationHandler(graph *CorrelationGraph, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sport := Sport(chi.URLParam(r, "sport"))
		edges := graph.ListEdges(sport)
		writeJSON(w, http.StatusOK, edges)
	}
}

// CompatibilityHandler returns the compatibility rules for a sport.
func CompatibilityHandler(catalogue *CompatibilityCatalogue, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sport := Sport(chi.URLParam(r, "sport"))
		rules := catalogue.ListRules(sport)
		writeJSON(w, http.StatusOK, rules)
	}
}

// writeJSON writes a JSON response with the given status code.
func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}
