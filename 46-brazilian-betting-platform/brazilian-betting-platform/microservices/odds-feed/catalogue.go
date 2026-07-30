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
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
)

// MarketState represents the trading state of a market.
type MarketState string

const (
	MarketStateOpen         MarketState = "open"
	MarketStateSuspended    MarketState = "suspended"
	MarketStateTraderReview MarketState = "trader_review"
	MarketStateSettled      MarketState = "settled"
	MarketStateVoided       MarketState = "voided"
)

// CatalogueEntry is the enriched event representation for the catalogue API.
type CatalogueEntry struct {
	Event
	ProviderSource string `json:"provider_source"`
	MarketCount    int    `json:"market_count"`
	SelectionCount int    `json:"selection_count"`
	FreshnessAge   int64  `json:"freshness_age_seconds"`
}

// SuspendRequest is the payload to suspend or unsuspend a market or event.
type SuspendRequest struct {
	Reason   string `json:"reason"`
	TraderID string `json:"trader_id"`
}

// TraderAction records a trader override for audit purposes.
type TraderAction struct {
	ID        string    `json:"id"`
	Action    string    `json:"action"` // "suspend", "unsuspend", "odds_override"
	TargetID  string    `json:"target_id"`
	TraderID  string    `json:"trader_id"`
	Reason    string    `json:"reason"`
	Timestamp time.Time `json:"timestamp"`
}

// GetCatalogue handles GET /catalogue/sport/{sport}.
// Returns the full event catalogue for a sport from the provider layer.
func GetCatalogue(registry *ProviderRegistry, cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sportParam := chi.URLParam(r, "sport")
		sport := Sport(sportParam)

		events, err := registry.FetchCatalogue(r.Context(), sport)
		if err != nil {
			logger.Error("catalogue fetch failed", "sport", sport, "error", err)
			writeError(w, http.StatusInternalServerError, "could not fetch catalogue")
			return
		}

		now := time.Now().UTC()
		entries := make([]CatalogueEntry, 0, len(events))
		for _, ev := range events {
			selCount := 0
			for _, m := range ev.Markets {
				selCount += len(m.Selections)
			}
			entries = append(entries, CatalogueEntry{
				Event:          ev,
				ProviderSource: string(registry.primary.Name()),
				MarketCount:    len(ev.Markets),
				SelectionCount: selCount,
				FreshnessAge:   int64(now.Sub(ev.UpdatedAt).Seconds()),
			})
		}

		// Also seed events into cache for downstream use.
		for _, entry := range entries {
			evCopy := entry.Event
			if setErr := cache.SetEvent(r.Context(), &evCopy); setErr != nil {
				logger.Warn("failed to cache catalogue event", "id", evCopy.ID, "error", setErr)
			}
			cache.AddEventToSport(r.Context(), evCopy.Sport, evCopy.ID) //nolint:errcheck
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"sport":           sport,
			"competition":     GetCompetitionName(sport),
			"provider":        registry.primary.Name(),
			"events":          entries,
			"count":           len(entries),
			"provider_health": registry.HealthAll(),
		})
	}
}

// GetFullCatalogue handles GET /catalogue.
// Returns all events across all sports.
func GetFullCatalogue(registry *ProviderRegistry, cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		allSports := SortedBrazilianSports()
		var allEntries []CatalogueEntry
		now := time.Now().UTC()

		for _, sport := range allSports {
			events, err := registry.FetchCatalogue(r.Context(), sport)
			if err != nil {
				logger.Warn("catalogue fetch failed for sport", "sport", sport, "error", err)
				continue
			}
			for _, ev := range events {
				selCount := 0
				for _, m := range ev.Markets {
					selCount += len(m.Selections)
				}
				allEntries = append(allEntries, CatalogueEntry{
					Event:          ev,
					ProviderSource: string(registry.primary.Name()),
					MarketCount:    len(ev.Markets),
					SelectionCount: selCount,
					FreshnessAge:   int64(now.Sub(ev.UpdatedAt).Seconds()),
				})

				// Cache each event.
				evCopy := ev
				cache.SetEvent(r.Context(), &evCopy)                //nolint:errcheck
				cache.AddEventToSport(r.Context(), ev.Sport, ev.ID) //nolint:errcheck
			}
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"events":          allEntries,
			"count":           len(allEntries),
			"provider_health": registry.HealthAll(),
		})
	}
}

// SuspendMarket handles POST /trader/market/{id}/suspend.
// Allows a trader to manually suspend a specific market.
func SuspendMarket(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		marketID := chi.URLParam(r, "id")
		if marketID == "" {
			writeError(w, http.StatusBadRequest, "market id required")
			return
		}

		var req SuspendRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.TraderID == "" {
			writeError(w, http.StatusBadRequest, "trader_id is required")
			return
		}

		// Find and suspend the market across all cached events.
		ctx := r.Context()
		events, err := cache.GetAllEvents(ctx)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "could not scan events")
			return
		}

		found := false
		for _, ev := range events {
			for mi, mkt := range ev.Markets {
				if mkt.ID == marketID {
					ev.Markets[mi].IsOpen = false
					for si := range ev.Markets[mi].Selections {
						ev.Markets[mi].Selections[si].IsActive = false
					}
					ev.UpdatedAt = time.Now().UTC()
					evCopy := ev
					cache.SetEvent(ctx, &evCopy) //nolint:errcheck
					found = true

					logger.Info("market suspended by trader",
						"market_id", marketID,
						"event_id", ev.ID,
						"trader_id", req.TraderID,
						"reason", req.Reason,
					)
					break
				}
			}
			if found {
				break
			}
		}

		if !found {
			writeError(w, http.StatusNotFound, "market not found")
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "suspended",
			"market_id": marketID,
			"trader_id": req.TraderID,
			"reason":    req.Reason,
			"timestamp": time.Now().UTC(),
		})
	}
}

// UnsuspendMarket handles POST /trader/market/{id}/unsuspend.
// Allows a trader to reopen a previously suspended market.
func UnsuspendMarket(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		marketID := chi.URLParam(r, "id")
		if marketID == "" {
			writeError(w, http.StatusBadRequest, "market id required")
			return
		}

		var req SuspendRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.TraderID == "" {
			writeError(w, http.StatusBadRequest, "trader_id is required")
			return
		}

		ctx := r.Context()
		events, err := cache.GetAllEvents(ctx)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "could not scan events")
			return
		}

		found := false
		for _, ev := range events {
			for mi, mkt := range ev.Markets {
				if mkt.ID == marketID {
					ev.Markets[mi].IsOpen = true
					for si := range ev.Markets[mi].Selections {
						ev.Markets[mi].Selections[si].IsActive = true
					}
					ev.UpdatedAt = time.Now().UTC()
					evCopy := ev
					cache.SetEvent(ctx, &evCopy) //nolint:errcheck
					found = true

					logger.Info("market unsuspended by trader",
						"market_id", marketID,
						"event_id", ev.ID,
						"trader_id", req.TraderID,
						"reason", req.Reason,
					)
					break
				}
			}
			if found {
				break
			}
		}

		if !found {
			writeError(w, http.StatusNotFound, "market not found")
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "unsuspended",
			"market_id": marketID,
			"trader_id": req.TraderID,
			"reason":    req.Reason,
			"timestamp": time.Now().UTC(),
		})
	}
}

// SuspendEvent handles POST /trader/event/{id}/suspend.
// Suspends all markets in an event.
func SuspendEvent(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		var req SuspendRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.TraderID == "" {
			writeError(w, http.StatusBadRequest, "trader_id is required")
			return
		}

		ctx := r.Context()
		event, err := cache.GetEvent(ctx, eventID)
		if err != nil || event == nil {
			writeError(w, http.StatusNotFound, "event not found")
			return
		}

		event.Status = EventStatusSuspended
		// A trader suspension is a manual action: mark it so incident-driven
		// auto-reopen never silently overrides it.
		event.ManuallySuspended = true
		for mi := range event.Markets {
			event.Markets[mi].IsOpen = false
			for si := range event.Markets[mi].Selections {
				event.Markets[mi].Selections[si].IsActive = false
			}
		}
		event.UpdatedAt = time.Now().UTC()
		cache.SetEvent(ctx, event) //nolint:errcheck

		logger.Info("event suspended by trader",
			"event_id", eventID,
			"trader_id", req.TraderID,
			"reason", req.Reason,
			"markets_suspended", len(event.Markets),
		)

		writeJSON(w, http.StatusOK, map[string]any{
			"status":            "suspended",
			"event_id":          eventID,
			"markets_suspended": len(event.Markets),
			"trader_id":         req.TraderID,
			"reason":            req.Reason,
			"timestamp":         time.Now().UTC(),
		})
	}
}

// UnsuspendEvent handles POST /trader/event/{id}/unsuspend.
// Reopens all markets in a suspended event.
func UnsuspendEvent(cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		var req SuspendRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.TraderID == "" {
			writeError(w, http.StatusBadRequest, "trader_id is required")
			return
		}

		ctx := r.Context()
		event, err := cache.GetEvent(ctx, eventID)
		if err != nil || event == nil {
			writeError(w, http.StatusNotFound, "event not found")
			return
		}

		// Restore to previous status or scheduled.
		if event.Status == EventStatusSuspended {
			if event.InPlayMinute > 0 {
				event.Status = EventStatusLive
			} else {
				event.Status = EventStatusScheduled
			}
		}

		// Explicit unsuspend clears the manual-suspend flag, restoring
		// normal auto-suspend/auto-reopen handling for future incidents.
		event.ManuallySuspended = false
		for mi := range event.Markets {
			event.Markets[mi].IsOpen = true
			for si := range event.Markets[mi].Selections {
				event.Markets[mi].Selections[si].IsActive = true
			}
		}
		event.UpdatedAt = time.Now().UTC()
		cache.SetEvent(ctx, event) //nolint:errcheck

		logger.Info("event unsuspended by trader",
			"event_id", eventID,
			"trader_id", req.TraderID,
			"reason", req.Reason,
		)

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "unsuspended",
			"event_id":  eventID,
			"trader_id": req.TraderID,
			"reason":    req.Reason,
			"timestamp": time.Now().UTC(),
		})
	}
}

// GetProviderHealth handles GET /provider/health.
// Returns health for all registered providers.
func GetProviderHealth(registry *ProviderRegistry) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"providers": registry.HealthAll(),
			"timestamp": time.Now().UTC(),
		})
	}
}
