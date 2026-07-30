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
	"github.com/redis/go-redis/v9"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	cfg := loadConfig()

	redisClient := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPassword,
		DB:       0,
	})
	defer redisClient.Close()

	cache := NewCache(redisClient)

	// Build provider registry with Betradar adapter.
	betradarAdapter := NewBetradarAdapter(logger)
	registry := NewProviderRegistry(betradarAdapter, nil, logger)

	// Live feed hub, feed health monitor, and incident handler. Without
	// these wired in, a dead upstream feed never suspends markets, kill
	// switches are recorded but never enforced, and goal/red-card incidents
	// never auto-suspend or auto-reopen markets.
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), logger)
	monitor := NewFeedMonitor(DefaultFeedMonitorConfig(), cache, hub, ProviderBetradar, "", logger)
	incidentHandler := NewIncidentHandler(cache, hub, DefaultSuspendConfig(), logger)

	monitorCtx, monitorCancel := context.WithCancel(context.Background())
	go monitor.RunMonitorLoop(monitorCtx)

	// Seed mock data on startup in development mode.
	if cfg.UseMockFeed {
		mock := NewMockFeedProvider(logger)
		ctx := context.Background()
		seedCache(ctx, mock, cache, logger)
		// Also seed Betradar adapter events into cache.
		seedAdapterEvents(ctx, registry, cache, logger)
		// Simulate live odds fluctuations every 5 seconds.
		go simulateLiveOdds(ctx, mock, cache, logger)
		go simulateAdapterOdds(ctx, betradarAdapter, cache, logger)
	}

	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(30 * time.Second))
	r.Use(structuredLogger(logger))

	r.Get("/health", HealthCheck(cache))
	r.Get("/ready", ReadinessCheck(cache))

	r.Route("/odds", func(r chi.Router) {
		r.Get("/live", LiveOddsStream(cache, logger))
		r.Get("/event/{id}", GetEventOdds(cache, logger))
		r.Get("/sport/{sport}", GetOddsBySport(cache, logger))
		r.Post("/update", UpdateOdds(cache, monitor, logger))
	})

	// Feed health, kill switches, and manual failover (operator controls).
	r.Route("/feed", func(r chi.Router) {
		r.Get("/status", GetFeedStatus(monitor))
		r.Post("/kill-switch/activate", ActivateKillSwitchHandler(monitor, cache, logger))
		r.Post("/kill-switch/deactivate", DeactivateKillSwitchHandler(monitor, logger))
		r.Post("/failover", ForceFailover(monitor, logger))
	})

	// Live match incidents: goal/red-card/penalty auto-suspend and auto-reopen.
	r.Route("/incidents", func(r chi.Router) {
		r.Post("/", IngestIncident(incidentHandler, logger))
		r.Get("/event/{id}/timeline", GetEventTimeline(incidentHandler, logger))
		r.Get("/status", GetIncidentStatus(incidentHandler))
	})

	// Event catalogue endpoints (Phase 1).
	r.Route("/catalogue", func(r chi.Router) {
		r.Get("/", GetFullCatalogue(registry, cache, logger))
		r.Get("/sport/{sport}", GetCatalogue(registry, cache, logger))
	})

	// Trader suspend/unsuspend controls (Phase 1).
	r.Route("/trader", func(r chi.Router) {
		r.Post("/market/{id}/suspend", SuspendMarket(cache, logger))
		r.Post("/market/{id}/unsuspend", UnsuspendMarket(cache, logger))
		r.Post("/event/{id}/suspend", SuspendEvent(cache, logger))
		r.Post("/event/{id}/unsuspend", UnsuspendEvent(cache, logger))
	})

	// Provider health endpoint.
	r.Get("/provider/health", GetProviderHealth(registry))

	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 0, // SSE requires no write timeout (streaming).
		IdleTimeout:  120 * time.Second,
	}

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		logger.Info("odds feed starting", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-quit
	logger.Info("shutting down odds feed")
	monitorCancel()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("forced shutdown", "error", err)
	}
	logger.Info("odds feed stopped")
}

type config struct {
	Port          string
	RedisAddr     string
	RedisPassword string
	FeedBaseURL   string
	FeedAPIKey    string
	UseMockFeed   bool
}

func loadConfig() config {
	return config{
		Port:          getEnv("PORT", "8083"),
		RedisAddr:     getEnv("REDIS_ADDR", "localhost:6379"),
		RedisPassword: getEnv("REDIS_PASSWORD", ""),
		FeedBaseURL:   getEnv("FEED_BASE_URL", ""),
		FeedAPIKey:    getEnv("FEED_API_KEY", ""),
		UseMockFeed:   getEnv("USE_MOCK_FEED", "true") == "true",
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

// seedCache loads mock events into Redis for development.
func seedCache(ctx context.Context, mock *MockFeedProvider, cache *Cache, logger *slog.Logger) {
	events, _ := mock.FetchLiveEvents(ctx)
	allSports := SortedBrazilianSports()
	for _, sport := range allSports {
		sportEvents, _ := mock.FetchEventsBySport(ctx, sport)
		events = append(events, sportEvents...)
	}

	seeded := 0
	for _, ev := range events {
		evCopy := ev
		if err := cache.SetEvent(ctx, &evCopy); err != nil {
			logger.Warn("failed to seed event", "id", ev.ID, "error", err)
			continue
		}
		cache.AddEventToSport(ctx, ev.Sport, ev.ID) //nolint:errcheck
		seeded++
	}
	logger.Info("mock feed seeded", "events", seeded)
}

// seedAdapterEvents loads events from the provider registry into cache.
func seedAdapterEvents(ctx context.Context, registry *ProviderRegistry, cache *Cache, logger *slog.Logger) {
	allSports := SortedBrazilianSports()
	seeded := 0
	for _, sport := range allSports {
		events, err := registry.FetchCatalogue(ctx, sport)
		if err != nil {
			logger.Warn("adapter seed failed for sport", "sport", sport, "error", err)
			continue
		}
		for _, ev := range events {
			evCopy := ev
			if err := cache.SetEvent(ctx, &evCopy); err != nil {
				logger.Warn("failed to cache adapter event", "id", ev.ID, "error", err)
				continue
			}
			cache.AddEventToSport(ctx, ev.Sport, ev.ID) //nolint:errcheck
			seeded++
		}
	}
	logger.Info("adapter events seeded", "events", seeded)
}

// simulateAdapterOdds periodically pushes odds updates from the Betradar adapter.
func simulateAdapterOdds(ctx context.Context, adapter *BetradarAdapter, cache *Cache, logger *slog.Logger) {
	ticker := time.NewTicker(8 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			updates, err := adapter.FetchLiveOdds(ctx)
			if err != nil {
				logger.Warn("adapter live odds fetch failed", "error", err)
				continue
			}
			for _, u := range updates {
				uCopy := u
				if pubErr := cache.PublishOddsUpdate(ctx, &uCopy); pubErr != nil {
					logger.Warn("adapter odds publish failed", "error", pubErr)
				}
			}
		}
	}
}

// simulateLiveOdds periodically pushes random odds changes for development.
func simulateLiveOdds(ctx context.Context, mock *MockFeedProvider, cache *Cache, logger *slog.Logger) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			update := mock.SimulateLiveOddsFluctuation()
			if update == nil {
				continue
			}
			if err := cache.PublishOddsUpdate(ctx, update); err != nil {
				logger.Warn("simulate odds publish failed", "error", err)
			}
		}
	}
}
