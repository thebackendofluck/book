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
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// LiveOddsStream handles GET /odds/live using Server-Sent Events.
// Clients receive real-time odds updates via a persistent HTTP connection.
func LiveOddsStream(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// SSE requires flusher support.
		flusher, ok := w.(http.Flusher)
		if !ok {
			writeError(w, http.StatusInternalServerError, "SSE not supported")
			return
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Set("X-Accel-Buffering", "no") // Disable nginx buffering.

		ctx := r.Context()
		pubsub := cache.Subscribe(ctx)
		defer pubsub.Close()

		ch := pubsub.Channel()

		// Send an initial heartbeat so the client knows the connection is live.
		fmt.Fprintf(w, "event: connected\ndata: {\"status\":\"subscribed\"}\n\n")
		flusher.Flush()

		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()

		logger.Info("sse client connected", "remote", r.RemoteAddr)

		for {
			select {
			case <-ctx.Done():
				logger.Info("sse client disconnected", "remote", r.RemoteAddr)
				return

			case msg, ok := <-ch:
				if !ok {
					return
				}
				fmt.Fprintf(w, "event: odds_update\ndata: %s\n\n", msg.Payload)
				flusher.Flush()

			case <-ticker.C:
				// Heartbeat to keep the connection alive through proxies.
				fmt.Fprintf(w, ": heartbeat\n\n")
				flusher.Flush()
			}
		}
	}
}

// GetEventOdds handles GET /odds/event/{id}.
func GetEventOdds(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")
		if id == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		event, err := cache.GetEvent(r.Context(), id)
		if err != nil {
			logger.Error("get event failed", "id", id, "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve event")
			return
		}
		if event == nil {
			writeError(w, http.StatusNotFound, "event not found")
			return
		}

		writeJSON(w, http.StatusOK, event)
	}
}

// GetOddsBySport handles GET /odds/sport/{sport}.
// Priority is given to Brazilian competitions (Brasileirão, Copa, Libertadores).
func GetOddsBySport(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sportParam := chi.URLParam(r, "sport")
		sport := Sport(sportParam)

		events, err := cache.GetEventsBySport(r.Context(), sport)
		if err != nil {
			logger.Error("get events by sport failed", "sport", sport, "error", err)
			writeError(w, http.StatusInternalServerError, "could not retrieve events")
			return
		}
		if events == nil {
			events = []Event{}
		}

		// Sort: featured events first, then by start time.
		sortEventsByPriority(events)

		writeJSON(w, http.StatusOK, map[string]any{
			"sport":       sport,
			"competition": GetCompetitionName(sport),
			"events":      events,
			"count":       len(events),
		})
	}
}

// UpdateOdds handles POST /odds/update (internal endpoint — feed provider only).
func UpdateOdds(cache *Cache, monitor *FeedMonitor, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// In production, enforce internal network or API key auth here.
		var update OddsUpdate
		if err := json.NewDecoder(r.Body).Decode(&update); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if update.EventID == "" || update.MarketID == "" || update.SelectionID == "" {
			writeError(w, http.StatusBadRequest, "event_id, market_id, and selection_id are required")
			return
		}
		if update.NewOdds < 1.01 {
			writeError(w, http.StatusBadRequest, "odds must be >= 1.01")
			return
		}
		if update.UpdatedAt.IsZero() {
			update.UpdatedAt = time.Now().UTC()
		}

		// Reject updates blocked by an active kill switch at the event,
		// market, or provider level before they ever reach the cache.
		if monitor != nil {
			if monitor.IsProviderKilled("event", update.EventID) ||
				monitor.IsProviderKilled("market", update.MarketID) ||
				monitor.IsProviderKilled("provider", update.Source) {
				writeError(w, http.StatusForbidden, "odds updates are blocked by an active kill switch")
				return
			}
		}

		if err := cache.UpdateOdds(r.Context(), &update); err != nil {
			logger.Error("odds update failed", "event_id", update.EventID, "error", err)
			writeError(w, http.StatusInternalServerError, "odds update failed")
			return
		}

		logger.Info("odds updated",
			"event_id", update.EventID,
			"selection_id", update.SelectionID,
			"new_odds", update.NewOdds,
			"source", update.Source,
		)

		writeJSON(w, http.StatusOK, map[string]any{
			"status":       "updated",
			"event_id":     update.EventID,
			"selection_id": update.SelectionID,
			"new_odds":     update.NewOdds,
		})
	}
}

// HealthCheck handles GET /health.
func HealthCheck(cache *Cache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		checks := map[string]string{"redis": "ok"}
		status := "ok"
		code := http.StatusOK

		if err := cache.Ping(ctx); err != nil {
			checks["redis"] = "unavailable"
			status = "degraded"
			code = http.StatusServiceUnavailable
		}

		writeJSON(w, code, map[string]any{
			"status":  status,
			"service": "odds-feed",
			"checks":  checks,
			"time":    time.Now().UTC(),
		})
	}
}

// ReadinessCheck handles GET /ready.
func ReadinessCheck(cache *Cache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := cache.Ping(r.Context()); err != nil {
			writeError(w, http.StatusServiceUnavailable, "redis unavailable")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	}
}

// sortEventsByPriority sorts events: featured first, then by start time ascending.
func sortEventsByPriority(events []Event) {
	// Simple insertion sort — event lists are typically short (< 100).
	for i := 1; i < len(events); i++ {
		for j := i; j > 0; j-- {
			a, b := events[j-1], events[j]
			// Featured events first.
			if !a.IsFeatured && b.IsFeatured {
				events[j-1], events[j] = events[j], events[j-1]
				continue
			}
			// Then earliest start time.
			if a.IsFeatured == b.IsFeatured && a.StartTime.After(b.StartTime) {
				events[j-1], events[j] = events[j], events[j-1]
			}
		}
	}
}
