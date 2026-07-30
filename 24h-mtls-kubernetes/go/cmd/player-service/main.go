// Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// cmd/player-service/main.go — wiring it all together
package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"go.uber.org/zap"
	"casino/pkg/mtls"
)

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	certFile := os.Getenv("TLS_CERT_FILE")
	keyFile  := os.Getenv("TLS_KEY_FILE")
	caFile   := os.Getenv("TLS_CA_BUNDLE")

	cw, err := mtls.NewCertWatcher(certFile, keyFile, caFile, logger)
	if err != nil {
		logger.Fatal("cert watcher init", zap.Error(err))
	}
	if err := cw.Watch(); err != nil {
		logger.Fatal("cert watcher start", zap.Error(err))
	}

	// Create mTLS client for downstream calls (e.g., to wallet-service)
	httpClient := mtls.NewMTLSClient(cw)
	_ = httpClient  // passed to handlers

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	// ... register actual handlers

	// player-service's only legitimate mTLS caller is the API gateway (see
	// manifests/network-policies/player-service-policy.yaml, which restricts
	// ingress on 8443 to the api-gateway namespace). The SPIFFE ID follows
	// the spiffe://casino.internal/ns/<namespace>/sa/<service-account>
	// convention used in bash/register-spire-workloads.sh.
	allowedCallers := []string{
		"spiffe://casino.internal/ns/api-gateway/sa/api-gateway",
	}
	srv := mtls.NewMTLSServer(":8443", mux, cw, allowedCallers)

	go func() {
		logger.Info("starting player-service", zap.String("addr", ":8443"))
		if err := srv.ListenAndServeTLS("", ""); err != nil && err != http.ErrServerClosed {
			logger.Fatal("server error", zap.Error(err))
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGTERM, syscall.SIGINT)
	<-quit

	ctx, cancel := context.WithTimeout(context.Background(), 30*1e9)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("graceful shutdown failed", zap.Error(err))
	}
	logger.Info("player-service stopped")
}
