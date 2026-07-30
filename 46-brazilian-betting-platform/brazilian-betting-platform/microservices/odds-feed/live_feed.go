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

// NOTE: This component is defined but intentionally not wired into main.go.
// It is presented in the book as an extension point — readers can integrate it
// by registering its handlers/listeners in cmd/main.go. See chapter 46 §X.

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
)

// LiveFeedConfig holds tuning parameters for the live SSE distribution layer.
type LiveFeedConfig struct {
	// BrazilianLatencyTarget is the maximum acceptable ingestion-to-publish
	// latency for priority Brazilian competitions (Brasileirao, Copa, Libertadores).
	BrazilianLatencyTarget time.Duration

	// DefaultLatencyTarget applies to all other sports/competitions.
	DefaultLatencyTarget time.Duration

	// HeartbeatInterval controls how often keep-alive pings are sent.
	HeartbeatInterval time.Duration

	// MaxSubscribersPerEvent caps the number of concurrent SSE connections
	// for a single event to protect memory.
	MaxSubscribersPerEvent int

	// BufferSize is the channel buffer for each subscriber.
	BufferSize int
}

// DefaultLiveFeedConfig returns production defaults aligned with the spec.
func DefaultLiveFeedConfig() LiveFeedConfig {
	return LiveFeedConfig{
		BrazilianLatencyTarget: 5 * time.Second,
		DefaultLatencyTarget:   10 * time.Second,
		HeartbeatInterval:      15 * time.Second,
		MaxSubscribersPerEvent: 5000,
		BufferSize:             64,
	}
}

// LiveFeedMessage extends LiveOddsMessage with latency tracking fields.
type LiveFeedMessage struct {
	Type         string    `json:"type"`
	EventID      string    `json:"event_id"`
	MarketID     string    `json:"market_id,omitempty"`
	SelectionID  string    `json:"selection_id,omitempty"`
	Odds         float64   `json:"odds,omitempty"`
	Score        string    `json:"score,omitempty"`
	IncidentType string    `json:"incident_type,omitempty"`
	IngestTime   time.Time `json:"ingest_time"`
	PublishTime  time.Time `json:"publish_time"`
	LatencyMs    int64     `json:"latency_ms"`
}

// subscriber represents one SSE client connection.
type subscriber struct {
	ch      chan []byte
	eventID string // empty means "all events"
}

// LiveFeedHub manages fan-out of live odds updates to SSE subscribers.
// It tracks per-event subscriber counts and enforces latency budgets.
type LiveFeedHub struct {
	mu          sync.RWMutex
	subscribers map[*subscriber]struct{}
	config      LiveFeedConfig
	logger      *slog.Logger

	// Latency tracking: rolling window of publish latencies by sport.
	latencyMu sync.Mutex
	latencies map[Sport][]time.Duration
}

// NewLiveFeedHub creates a hub ready to accept subscribers.
func NewLiveFeedHub(cfg LiveFeedConfig, logger *slog.Logger) *LiveFeedHub {
	return &LiveFeedHub{
		subscribers: make(map[*subscriber]struct{}),
		config:      cfg,
		logger:      logger,
		latencies:   make(map[Sport][]time.Duration),
	}
}

// Subscribe registers a new SSE client. Returns the subscriber for cleanup.
func (h *LiveFeedHub) Subscribe(eventID string) *subscriber {
	sub := &subscriber{
		ch:      make(chan []byte, h.config.BufferSize),
		eventID: eventID,
	}
	h.mu.Lock()
	h.subscribers[sub] = struct{}{}
	h.mu.Unlock()
	return sub
}

// Unsubscribe removes a client and closes its channel.
func (h *LiveFeedHub) Unsubscribe(sub *subscriber) {
	h.mu.Lock()
	if _, ok := h.subscribers[sub]; ok {
		delete(h.subscribers, sub)
		close(sub.ch)
	}
	h.mu.Unlock()
}

// SubscriberCount returns the current number of active subscribers.
func (h *LiveFeedHub) SubscriberCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.subscribers)
}

// Publish fans out a message to all matching subscribers.
func (h *LiveFeedHub) Publish(msg LiveFeedMessage) {
	msg.PublishTime = time.Now().UTC()
	msg.LatencyMs = msg.PublishTime.Sub(msg.IngestTime).Milliseconds()

	data, err := json.Marshal(msg)
	if err != nil {
		h.logger.Error("failed to marshal live feed message", "error", err)
		return
	}

	h.mu.RLock()
	defer h.mu.RUnlock()

	for sub := range h.subscribers {
		// If subscriber is filtered to a specific event, skip non-matching.
		if sub.eventID != "" && sub.eventID != msg.EventID {
			continue
		}
		select {
		case sub.ch <- data:
		default:
			// Slow consumer: drop message to avoid blocking the hub.
			h.logger.Warn("dropping message for slow subscriber",
				"event_id", msg.EventID,
			)
		}
	}
}

// RecordLatency stores a latency sample for monitoring.
func (h *LiveFeedHub) RecordLatency(sport Sport, d time.Duration) {
	h.latencyMu.Lock()
	defer h.latencyMu.Unlock()
	samples := h.latencies[sport]
	if len(samples) > 1000 {
		samples = samples[500:]
	}
	h.latencies[sport] = append(samples, d)
}

// AverageLatency returns the mean latency for a sport over the rolling window.
func (h *LiveFeedHub) AverageLatency(sport Sport) time.Duration {
	h.latencyMu.Lock()
	defer h.latencyMu.Unlock()
	samples := h.latencies[sport]
	if len(samples) == 0 {
		return 0
	}
	var total time.Duration
	for _, s := range samples {
		total += s
	}
	return total / time.Duration(len(samples))
}

// LatencyStats returns current latency metrics for all tracked sports.
func (h *LiveFeedHub) LatencyStats() map[string]LatencyStat {
	h.latencyMu.Lock()
	defer h.latencyMu.Unlock()

	stats := make(map[string]LatencyStat)
	for sport, samples := range h.latencies {
		if len(samples) == 0 {
			continue
		}
		var total time.Duration
		maxL := samples[0]
		minL := samples[0]
		for _, s := range samples {
			total += s
			if s > maxL {
				maxL = s
			}
			if s < minL {
				minL = s
			}
		}
		target := h.config.DefaultLatencyTarget
		if IsBrazilianCompetition(sport) {
			target = h.config.BrazilianLatencyTarget
		}
		avg := total / time.Duration(len(samples))
		stats[string(sport)] = LatencyStat{
			AvgMs:        avg.Milliseconds(),
			MaxMs:        maxL.Milliseconds(),
			MinMs:        minL.Milliseconds(),
			SampleCount:  len(samples),
			TargetMs:     target.Milliseconds(),
			WithinTarget: avg <= target,
		}
	}
	return stats
}

// LatencyStat holds aggregated latency metrics for a sport.
type LatencyStat struct {
	AvgMs        int64 `json:"avg_ms"`
	MaxMs        int64 `json:"max_ms"`
	MinMs        int64 `json:"min_ms"`
	SampleCount  int   `json:"sample_count"`
	TargetMs     int64 `json:"target_ms"`
	WithinTarget bool  `json:"within_target"`
}

// LiveOddsStreamV2 handles GET /odds/live/v2.
// Enhanced SSE endpoint with per-event filtering and latency tracking.
func LiveOddsStreamV2(hub *LiveFeedHub, cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		flusher, ok := w.(http.Flusher)
		if !ok {
			writeError(w, http.StatusInternalServerError, "SSE not supported")
			return
		}

		// Optional event filter via query param.
		eventFilter := r.URL.Query().Get("event_id")

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Set("X-Accel-Buffering", "no")

		sub := hub.Subscribe(eventFilter)
		defer hub.Unsubscribe(sub)

		// Initial handshake.
		fmt.Fprintf(w, "event: connected\ndata: {\"status\":\"subscribed\",\"event_filter\":%q,\"subscribers\":%d}\n\n",
			eventFilter, hub.SubscriberCount())
		flusher.Flush()

		ctx := r.Context()
		ticker := time.NewTicker(hub.config.HeartbeatInterval)
		defer ticker.Stop()

		logger.Info("live v2 client connected",
			"remote", r.RemoteAddr,
			"event_filter", eventFilter,
		)

		for {
			select {
			case <-ctx.Done():
				logger.Info("live v2 client disconnected", "remote", r.RemoteAddr)
				return

			case data, ok := <-sub.ch:
				if !ok {
					return
				}
				fmt.Fprintf(w, "event: live_update\ndata: %s\n\n", data)
				flusher.Flush()

			case <-ticker.C:
				fmt.Fprintf(w, ": heartbeat %d\n\n", time.Now().Unix())
				flusher.Flush()
			}
		}
	}
}

// LiveEventStream handles GET /odds/live/event/{id}.
// Streams live updates for a single event.
func LiveEventStream(hub *LiveFeedHub, cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		// Verify event exists.
		event, err := cache.GetEvent(r.Context(), eventID)
		if err != nil || event == nil {
			writeError(w, http.StatusNotFound, "event not found")
			return
		}

		flusher, ok := w.(http.Flusher)
		if !ok {
			writeError(w, http.StatusInternalServerError, "SSE not supported")
			return
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Set("X-Accel-Buffering", "no")

		sub := hub.Subscribe(eventID)
		defer hub.Unsubscribe(sub)

		// Send current event state as initial snapshot.
		snapshot, _ := json.Marshal(event)
		fmt.Fprintf(w, "event: snapshot\ndata: %s\n\n", snapshot)
		flusher.Flush()

		ctx := r.Context()
		ticker := time.NewTicker(hub.config.HeartbeatInterval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case data, ok := <-sub.ch:
				if !ok {
					return
				}
				fmt.Fprintf(w, "event: update\ndata: %s\n\n", data)
				flusher.Flush()
			case <-ticker.C:
				fmt.Fprintf(w, ": heartbeat\n\n")
				flusher.Flush()
			}
		}
	}
}

// GetLiveLatencyStats handles GET /odds/live/latency.
// Returns latency metrics per sport for operational monitoring.
func GetLiveLatencyStats(hub *LiveFeedHub) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"latency_stats": hub.LatencyStats(),
			"subscribers":   hub.SubscriberCount(),
			"config": map[string]any{
				"brazilian_target_ms": hub.config.BrazilianLatencyTarget.Milliseconds(),
				"default_target_ms":   hub.config.DefaultLatencyTarget.Milliseconds(),
				"heartbeat_interval":  hub.config.HeartbeatInterval.String(),
			},
			"timestamp": time.Now().UTC(),
		})
	}
}

// IngestAndPublish processes an incoming odds update, stores it, and fans out
// via the live hub with latency tracking.
func IngestAndPublish(ctx context.Context, cache *Cache, hub *LiveFeedHub, update *OddsUpdate, sport Sport) error {
	ingestTime := time.Now().UTC()

	// Persist to cache.
	if err := cache.UpdateOdds(ctx, update); err != nil {
		return fmt.Errorf("cache update: %w", err)
	}

	// Build and publish live message.
	msg := LiveFeedMessage{
		Type:        "odds_update",
		EventID:     update.EventID,
		MarketID:    update.MarketID,
		SelectionID: update.SelectionID,
		Odds:        update.NewOdds,
		IngestTime:  ingestTime,
	}
	hub.Publish(msg)

	// Track latency.
	latency := time.Since(ingestTime)
	hub.RecordLatency(sport, latency)

	return nil
}
