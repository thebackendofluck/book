// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Sportsbook Ledger — main entry point.
// Mirrors Main.scala: starts the HTTP health server, then polls the BMC
// betstream API on a configurable schedule writing bets + feed messages to Postgres.
package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	_ "github.com/lib/pq"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.uber.org/zap"

	"sportsbook-ledger/internal/config"
	"sportsbook-ledger/internal/dao"
	"sportsbook-ledger/internal/feed"
	"sportsbook-ledger/internal/metrics"
)

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	cfg := config.Load()

	connStr := fmt.Sprintf("postgres://%s:%s@%s?sslmode=disable",
		cfg.DatabaseUser, cfg.DatabasePassword,
		extractHostPath(cfg.DatabaseURL))
	db, err := sql.Open("postgres", connStr)
	if err != nil {
		logger.Fatal("open database", zap.Error(err))
	}
	defer db.Close()

	if err := dao.CreateSchema(db); err != nil {
		logger.Fatal("create schema", zap.Error(err))
	}

	feedDAO := dao.NewFeedDAO(db)
	betsDAO := dao.NewBetsDAO(db)

	svc, err := feed.NewService(cfg, feedDAO, betsDAO, logger)
	if err != nil {
		logger.Fatal("create feed service", zap.Error(err))
	}

	// HTTP server: /ping, /health, /metrics
	mux := http.NewServeMux()
	mux.HandleFunc("/ping", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "pong"})
	})
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if err := feedDAO.Ping(); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"status": "unhealthy", "error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
	})
	mux.Handle("/metrics", promhttp.Handler())

	server := &http.Server{Addr: fmt.Sprintf("%s:%d", cfg.Host, cfg.Port), Handler: mux}
	go func() {
		logger.Info("HTTP server starting", zap.String("addr", server.Addr))
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal("HTTP server error", zap.Error(err))
		}
	}()

	// Poll loop — mirrors the while(true) + Thread.sleep in Main.scala
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	ticker := time.NewTicker(time.Duration(cfg.BMCSchedulerRateS) * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			msgs, err := svc.GetMessages(cfg.BMCBatchSize)
			if err != nil {
				logger.Error("failed to fetch messages", zap.Error(err))
				metrics.RecordError("bmc", fmt.Sprintf("%T", err))
				continue
			}
			if len(msgs) > 0 {
				metrics.RecordFetched(len(msgs))
				now := time.Now().UTC()
				var minLag float64 = math.MaxFloat64
				for _, m := range msgs {
					lag := now.Sub(m.UpdatedDate).Seconds()
					if lag < minLag {
						minLag = lag
					}
				}
				metrics.RecordTimeLag(minLag)
			}
			logger.Info("fetch complete", zap.Int("count", len(msgs)))
		case <-quit:
			logger.Info("shutting down")
			return
		}
	}
}

func extractHostPath(url string) string {
	// Strip leading jdbc:postgresql:// or postgres:// scheme
	for _, prefix := range []string{"jdbc:postgresql://", "postgres://", "postgresql://"} {
		if len(url) > len(prefix) && url[:len(prefix)] == prefix {
			return url[len(prefix):]
		}
	}
	return url
}
