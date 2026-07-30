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
	"strings"
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

	store := NewStore(pgPool)
	sigapClient := NewSIGAPSettlementClient(cfg.SIGAPBaseURL, cfg.SIGAPOperatorID, cfg.SIGAPAPIKey, logger)
	ggrCalc := NewGGRCalculator(store, cfg.SIGAPOperatorID, logger)
	taxCalc := NewTaxCalculator(store, logger)
	sportEngine := NewSportSettlementEngine()

	var producer *KafkaProducer
	var consumer *KafkaConsumer
	if len(cfg.KafkaBrokers) > 0 {
		producer = NewKafkaProducer(cfg.KafkaBrokers, logger)
		defer producer.Close()
		consumer = NewKafkaConsumer(cfg.KafkaBrokers, cfg.KafkaGroupID, store, sigapClient, ggrCalc, logger)
		go consumer.Run(context.Background())
		defer consumer.Close()
	}

	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(60 * time.Second))
	r.Use(structuredLogger(logger))

	r.Get("/health", HealthCheck())
	r.Get("/ready", ReadinessCheck(store))

	r.Route("/settle", func(r chi.Router) {
		// Legacy endpoint (backward compatible).
		r.Post("/event/{id}", SettleEvent(store, sigapClient, producer, logger))
		// Phase 1: Sport-specific settlement with official result.
		r.Post("/event/{id}/v2", SettleEventWithRules(store, sportEngine, sigapClient, producer, logger))
		// Phase 1: Convenience football settlement endpoint.
		r.Post("/football/{id}", SettleFootballEvent(store, sportEngine, sigapClient, producer, logger))
	})
	r.Route("/ggr", func(r chi.Router) {
		r.Get("/daily", GetDailyGGR(ggrCalc, logger))
		r.Get("/report", GetGGRReport(store, logger))
	})
	r.Route("/tax", func(r chi.Router) {
		r.Post("/withhold/{cpf}", WithholdTax(taxCalc, logger))
	})

	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		logger.Info("settlement engine starting", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-quit
	logger.Info("shutting down settlement engine")

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("forced shutdown", "error", err)
	}
	logger.Info("settlement engine stopped")
}

type config struct {
	Port            string
	PostgresDSN     string
	KafkaBrokers    []string
	KafkaGroupID    string
	SIGAPBaseURL    string
	SIGAPOperatorID string
	SIGAPAPIKey     string
}

func loadConfig() config {
	brokerStr := getEnv("KAFKA_BROKERS", "")
	var brokers []string
	if brokerStr != "" {
		brokers = strings.Split(brokerStr, ",")
	}
	return config{
		Port:            getEnv("PORT", "8082"),
		PostgresDSN:     getEnv("POSTGRES_DSN", "postgres://settlement:settlement@localhost:5432/settlement?sslmode=disable"),
		KafkaBrokers:    brokers,
		KafkaGroupID:    getEnv("KAFKA_GROUP_ID", "settlement-engine"),
		SIGAPBaseURL:    getEnv("SIGAP_BASE_URL", "https://sigap.sfc.fazenda.gov.br"),
		SIGAPOperatorID: getEnv("SIGAP_OPERATOR_ID", "OP-TEST-001"),
		SIGAPAPIKey:     getEnv("SIGAP_API_KEY", ""),
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
