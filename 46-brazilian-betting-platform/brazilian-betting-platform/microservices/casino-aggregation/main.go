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
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
)

func main() {
	// Structured JSON logging
	zerolog.TimeFieldFormat = zerolog.TimeFormatUnix
	if os.Getenv("LOG_PRETTY") == "true" {
		log.Logger = log.Output(zerolog.ConsoleWriter{Out: os.Stderr})
	}
	log.Logger = log.With().Str("service", "casino-aggregation").Logger()

	port := envOr("PORT", "8083")

	// Initialise dependencies
	providers := NewProviderRegistry()
	rng := NewRNGValidator()
	h := NewHandlers(providers, rng)

	// Router
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			start := time.Now()
			ww := middleware.NewWrapResponseWriter(w, req.ProtoMajor)
			next.ServeHTTP(ww, req)
			log.Info().
				Str("method", req.Method).
				Str("path", req.URL.Path).
				Int("status", ww.Status()).
				Dur("latency_ms", time.Since(start)).
				Msg("request")
		})
	})
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))

	// Routes
	r.Get("/health", h.Health)

	r.Route("/games", func(r chi.Router) {
		r.Get("/catalog", h.GetCatalog)
		r.Post("/launch/{game_id}", h.LaunchGame)
		r.Post("/round/complete", h.RoundComplete)
		r.Get("/sessions/{cpf}", h.GetSessions)
		r.Post("/rng/validate", h.ValidateRNG)
		r.Get("/sigap-report", h.GetSIGAPReport)
	})

	srv := &http.Server{
		Addr:         ":" + port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Start in background
	go func() {
		log.Info().Str("addr", srv.Addr).Msg("Casino Aggregation service starting")
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal().Err(err).Msg("Server failed")
		}
	}()

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Info().Msg("Shutting down...")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Error().Err(err).Msg("Forced shutdown")
	}
	log.Info().Msg("Casino Aggregation service stopped")
}
