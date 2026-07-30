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
	"github.com/redis/go-redis/v9"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	cfg := loadConfig()

	// Connect to PostgreSQL.
	pgPool, err := pgxpool.New(context.Background(), cfg.PostgresDSN)
	if err != nil {
		logger.Error("failed to connect to postgres", "error", err)
		os.Exit(1)
	}
	defer pgPool.Close()

	// Connect to Redis.
	redisClient := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPassword,
		DB:       0,
	})
	defer redisClient.Close()

	// Wire dependencies.
	store := NewStore(pgPool)
	cache := NewCache(redisClient)
	integrity := NewIntegrityChecker(store, logger)

	sigapClient, err := NewSIGAPClient(
		cfg.SIGAPBaseURL,
		cfg.SIGAPOperatorID,
		cfg.SIGAPAPIKey,
		cfg.SIGAPCertFile,
		cfg.SIGAPKeyFile,
		logger,
	)
	if err != nil {
		logger.Error("failed to create sigap client", "error", err)
		os.Exit(1)
	}

	// Phase 1: Enhanced betting dependencies.
	walletClient := NewWalletClient(cfg.WalletBaseURL, logger)
	validator := NewBetslipValidator(store, cfg.OddsFeedBaseURL, logger)
	accumulatorPricing := NewAccumulatorPricing(DefaultStakeLimits())

	// Build router.
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))
	r.Use(structuredLogger(logger))

	r.Get("/health", HealthCheck(cache))
	r.Get("/ready", ReadinessCheck(cache))

	r.Route("/bets", func(r chi.Router) {
		// Legacy endpoint (backward compatible).
		r.Post("/", PlaceBet(store, cache, sigapClient, integrity, logger))
		// Phase 1 enhanced endpoints.
		r.Post("/v2", PlaceBetV2(store, cache, walletClient, validator, accumulatorPricing, sigapClient, integrity, logger))
		r.Post("/validate", ValidateBetslipEndpoint(validator, accumulatorPricing, logger))
		r.Post("/accumulator/quote", GetAccumulatorQuote(accumulatorPricing))
		r.Get("/player/{cpf}", GetPlayerBets(store, logger))
		r.Get("/{id}", GetBet(store, logger))
		r.Post("/{id}/cashout", Cashout(store, logger))
		r.Post("/{id}/settle", SettleBet(store, logger))
		r.Post("/{id}/settle/v2", SettleBetV2(store, logger))
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
		logger.Info("betting engine starting", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-quit
	logger.Info("shutting down betting engine")

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("forced shutdown", "error", err)
	}
	logger.Info("betting engine stopped")
}

// config holds all service configuration.
type config struct {
	Port            string
	PostgresDSN     string
	RedisAddr       string
	RedisPassword   string
	SIGAPBaseURL    string
	SIGAPOperatorID string
	SIGAPAPIKey     string
	SIGAPCertFile   string
	SIGAPKeyFile    string
	WalletBaseURL   string
	OddsFeedBaseURL string
}

// loadConfig reads configuration from environment variables.
func loadConfig() config {
	return config{
		Port:            getEnv("PORT", "8080"),
		PostgresDSN:     getEnv("POSTGRES_DSN", "postgres://betting:betting@localhost:5432/betting?sslmode=disable"),
		RedisAddr:       getEnv("REDIS_ADDR", "localhost:6379"),
		RedisPassword:   getEnv("REDIS_PASSWORD", ""),
		SIGAPBaseURL:    getEnv("SIGAP_BASE_URL", "https://sigap.sfc.fazenda.gov.br"),
		SIGAPOperatorID: getEnv("SIGAP_OPERATOR_ID", "OP-TEST-001"),
		SIGAPAPIKey:     getEnv("SIGAP_API_KEY", ""),
		SIGAPCertFile:   getEnv("SIGAP_CERT_FILE", ""),
		SIGAPKeyFile:    getEnv("SIGAP_KEY_FILE", ""),
		WalletBaseURL:   getEnv("WALLET_BASE_URL", ""),       // Empty = mock mode
		OddsFeedBaseURL: getEnv("ODDS_FEED_BASE_URL", "http://localhost:8083"),
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
