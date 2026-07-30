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
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	cfg := loadConfig()

	pgPool, err := pgxpool.New(context.Background(), cfg.PostgresDSN)
	if err != nil {
		logger.Error("failed to connect to postgres", "error", err)
		os.Exit(1)
	}
	defer pgPool.Close()

	// Wire dependencies.
	store := NewStore(pgPool)
	pixClient := NewPIXClient(store, cfg.PSPBaseURL, cfg.PSPAPIKey, cfg.MerchantCPFCNPJ, logger)
	reconciler := NewReconciliationEngine(store, logger)

	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))
	r.Use(structuredLogger(logger))

	r.Get("/health", HealthCheck())
	r.Get("/ready", ReadinessCheck(store))

	r.Route("/wallet", func(r chi.Router) {
		r.Post("/{cpf}/deposit", Deposit(store, pixClient, logger))
		r.Post("/{cpf}/withdraw", Withdraw(store, pixClient, logger))
		r.Get("/{cpf}/balance", GetBalance(store, logger))
		r.Get("/{cpf}/transactions", GetTransactions(store, logger))
		r.Post("/reconcile", Reconcile(reconciler, logger))
		r.Post("/webhook/pix", PIXWebhook(pixClient, logger))
	})

	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		logger.Info("wallet service starting", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-quit
	logger.Info("shutting down wallet service")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("forced shutdown", "error", err)
	}
	logger.Info("wallet service stopped")
}

type config struct {
	Port            string
	PostgresDSN     string
	PSPBaseURL      string
	PSPAPIKey       string
	MerchantCPFCNPJ string
}

func loadConfig() config {
	return config{
		Port:            getEnv("PORT", "8081"),
		PostgresDSN:     getEnv("POSTGRES_DSN", "postgres://wallet:wallet@localhost:5432/wallet?sslmode=disable"),
		PSPBaseURL:      getEnv("PSP_BASE_URL", "https://psp.example.com"),
		PSPAPIKey:       getEnv("PSP_API_KEY", ""),
		MerchantCPFCNPJ: getEnv("MERCHANT_CPF_CNPJ", "00000000000000"),
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
				"duration_ms", time.Since(start).Milliseconds(),
				"request_id", middleware.GetReqID(r.Context()),
			)
		})
	}
}
