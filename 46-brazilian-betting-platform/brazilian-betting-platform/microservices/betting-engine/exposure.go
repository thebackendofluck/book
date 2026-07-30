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

// ExposureLevel categorizes the risk tier of an exposure bucket.
type ExposureLevel string

const (
	ExposureLevelNormal   ExposureLevel = "normal"
	ExposureLevelElevated ExposureLevel = "elevated"
	ExposureLevelHigh     ExposureLevel = "high"
	ExposureLevelCritical ExposureLevel = "critical"
)

// ExposureBucket tracks cumulative liability for a single selection within
// an event/market combination. This is the atomic unit of exposure tracking.
type ExposureBucket struct {
	EventID       string        `json:"event_id"`
	MarketID      string        `json:"market_id"`
	SelectionID   string        `json:"selection_id"`
	EventName     string        `json:"event_name"`
	MarketName    string        `json:"market_name"`
	SelectionName string        `json:"selection_name"`
	Sport         string        `json:"sport"`
	TotalStake    float64       `json:"total_stake"`   // Sum of all stakes on this selection.
	BetCount      int           `json:"bet_count"`     // Number of bets on this selection.
	MaxLiability  float64       `json:"max_liability"` // Worst-case payout if selection wins.
	AvgOdds       float64       `json:"avg_odds"`      // Weighted average odds.
	Level         ExposureLevel `json:"level"`
	UpdatedAt     time.Time     `json:"updated_at"`
}

// SportExposureLimit defines maximum exposure rules per sport.
type SportExposureLimit struct {
	Sport                string  `json:"sport"`
	MaxEventExposure     float64 `json:"max_event_exposure"`     // Max liability per event (BRL).
	MaxMarketExposure    float64 `json:"max_market_exposure"`    // Max liability per market (BRL).
	MaxSelectionExposure float64 `json:"max_selection_exposure"` // Max liability per selection (BRL).
	ElevatedThreshold    float64 `json:"elevated_threshold"`     // % of max that triggers "elevated".
	HighThreshold        float64 `json:"high_threshold"`         // % of max that triggers "high".
	CriticalThreshold    float64 `json:"critical_threshold"`     // % of max that triggers "critical".
}

// DefaultSportExposureLimits returns Brazil-market exposure limits.
func DefaultSportExposureLimits() map[string]SportExposureLimit {
	return map[string]SportExposureLimit{
		"brasileirao": {
			Sport:                "brasileirao",
			MaxEventExposure:     500000.00,
			MaxMarketExposure:    200000.00,
			MaxSelectionExposure: 100000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
		"copa-brasil": {
			Sport:                "copa-brasil",
			MaxEventExposure:     400000.00,
			MaxMarketExposure:    150000.00,
			MaxSelectionExposure: 75000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
		"libertadores": {
			Sport:                "libertadores",
			MaxEventExposure:     600000.00,
			MaxMarketExposure:    250000.00,
			MaxSelectionExposure: 120000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
		"ufc": {
			Sport:                "ufc",
			MaxEventExposure:     200000.00,
			MaxMarketExposure:    100000.00,
			MaxSelectionExposure: 50000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
		"nba": {
			Sport:                "nba",
			MaxEventExposure:     300000.00,
			MaxMarketExposure:    120000.00,
			MaxSelectionExposure: 60000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
		"default": {
			Sport:                "default",
			MaxEventExposure:     250000.00,
			MaxMarketExposure:    100000.00,
			MaxSelectionExposure: 50000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
	}
}

// ExposureTracker maintains real-time liability across all events and markets.
type ExposureTracker struct {
	mu      sync.RWMutex
	buckets map[string]*ExposureBucket // key = "eventID:marketID:selectionID"
	limits  map[string]SportExposureLimit
	logger  *slog.Logger
}

// NewExposureTracker creates a tracker with the given sport limits.
func NewExposureTracker(limits map[string]SportExposureLimit, logger *slog.Logger) *ExposureTracker {
	return &ExposureTracker{
		buckets: make(map[string]*ExposureBucket),
		limits:  limits,
		logger:  logger,
	}
}

// bucketKey builds the composite key for an exposure bucket.
func bucketKey(eventID, marketID, selectionID string) string {
	return eventID + ":" + marketID + ":" + selectionID
}

// RecordBet adds a bet's stake and potential payout to the exposure bucket.
func (et *ExposureTracker) RecordBet(
	eventID, marketID, selectionID string,
	eventName, marketName, selectionName, sport string,
	stake, odds float64,
) {
	et.mu.Lock()
	defer et.mu.Unlock()

	key := bucketKey(eventID, marketID, selectionID)
	bucket, ok := et.buckets[key]
	if !ok {
		bucket = &ExposureBucket{
			EventID:       eventID,
			MarketID:      marketID,
			SelectionID:   selectionID,
			EventName:     eventName,
			MarketName:    marketName,
			SelectionName: selectionName,
			Sport:         sport,
		}
		et.buckets[key] = bucket
	}

	// Potential payout = stake * odds (the liability if this selection wins).
	potentialPayout := stake * odds

	// Update weighted average odds.
	totalWeight := bucket.TotalStake*bucket.AvgOdds + stake*odds
	bucket.TotalStake += stake
	if bucket.TotalStake > 0 {
		bucket.AvgOdds = totalWeight / bucket.TotalStake
	}
	bucket.BetCount++
	bucket.MaxLiability += potentialPayout
	bucket.UpdatedAt = time.Now().UTC()

	// Classify exposure level.
	limit := et.getLimitForSport(sport)
	bucket.Level = classifyExposure(bucket.MaxLiability, limit.MaxSelectionExposure, limit)

	et.logger.Debug("exposure updated",
		"key", key,
		"total_stake", bucket.TotalStake,
		"max_liability", bucket.MaxLiability,
		"level", bucket.Level,
	)
}

// getLimitForSport returns the exposure limit for a sport, falling back to default.
func (et *ExposureTracker) getLimitForSport(sport string) SportExposureLimit {
	if limit, ok := et.limits[sport]; ok {
		return limit
	}
	return et.limits["default"]
}

// classifyExposure determines the risk level based on current liability vs max.
func classifyExposure(liability, maxExposure float64, limit SportExposureLimit) ExposureLevel {
	if maxExposure <= 0 {
		return ExposureLevelNormal
	}
	ratio := liability / maxExposure
	switch {
	case ratio >= limit.CriticalThreshold:
		return ExposureLevelCritical
	case ratio >= limit.HighThreshold:
		return ExposureLevelHigh
	case ratio >= limit.ElevatedThreshold:
		return ExposureLevelElevated
	default:
		return ExposureLevelNormal
	}
}

// GetBucket returns the exposure bucket for a specific selection.
func (et *ExposureTracker) GetBucket(eventID, marketID, selectionID string) *ExposureBucket {
	et.mu.RLock()
	defer et.mu.RUnlock()
	key := bucketKey(eventID, marketID, selectionID)
	if b, ok := et.buckets[key]; ok {
		copy := *b
		return &copy
	}
	return nil
}

// GetEventExposure returns total liability for an entire event.
func (et *ExposureTracker) GetEventExposure(eventID string) EventExposureSummary {
	et.mu.RLock()
	defer et.mu.RUnlock()

	summary := EventExposureSummary{
		EventID: eventID,
		Markets: make(map[string]MarketExposureSummary),
	}

	for _, bucket := range et.buckets {
		if bucket.EventID != eventID {
			continue
		}
		summary.TotalStake += bucket.TotalStake
		summary.TotalLiability += bucket.MaxLiability
		summary.BetCount += bucket.BetCount
		summary.Sport = bucket.Sport

		mkt, ok := summary.Markets[bucket.MarketID]
		if !ok {
			mkt = MarketExposureSummary{
				MarketID:   bucket.MarketID,
				MarketName: bucket.MarketName,
			}
		}
		mkt.TotalStake += bucket.TotalStake
		mkt.TotalLiability += bucket.MaxLiability
		mkt.BetCount += bucket.BetCount
		mkt.Selections = append(mkt.Selections, *bucket)
		summary.Markets[bucket.MarketID] = mkt
	}

	// Classify event-level exposure.
	limit := et.getLimitForSport(summary.Sport)
	summary.Level = classifyExposure(summary.TotalLiability, limit.MaxEventExposure, limit)

	return summary
}

// EventExposureSummary aggregates exposure across all markets in an event.
type EventExposureSummary struct {
	EventID        string                           `json:"event_id"`
	Sport          string                           `json:"sport"`
	TotalStake     float64                          `json:"total_stake"`
	TotalLiability float64                          `json:"total_liability"`
	BetCount       int                              `json:"bet_count"`
	Level          ExposureLevel                    `json:"level"`
	Markets        map[string]MarketExposureSummary `json:"markets"`
}

// MarketExposureSummary aggregates exposure for a single market.
type MarketExposureSummary struct {
	MarketID       string           `json:"market_id"`
	MarketName     string           `json:"market_name"`
	TotalStake     float64          `json:"total_stake"`
	TotalLiability float64          `json:"total_liability"`
	BetCount       int              `json:"bet_count"`
	Selections     []ExposureBucket `json:"selections"`
}

// GetAllBuckets returns all exposure buckets for the dashboard.
func (et *ExposureTracker) GetAllBuckets() []ExposureBucket {
	et.mu.RLock()
	defer et.mu.RUnlock()

	result := make([]ExposureBucket, 0, len(et.buckets))
	for _, b := range et.buckets {
		result = append(result, *b)
	}
	return result
}

// GetBucketsAboveLevel returns all buckets at or above the given exposure level.
func (et *ExposureTracker) GetBucketsAboveLevel(minLevel ExposureLevel) []ExposureBucket {
	et.mu.RLock()
	defer et.mu.RUnlock()

	order := map[ExposureLevel]int{
		ExposureLevelNormal:   0,
		ExposureLevelElevated: 1,
		ExposureLevelHigh:     2,
		ExposureLevelCritical: 3,
	}
	minOrder := order[minLevel]

	var result []ExposureBucket
	for _, b := range et.buckets {
		if order[b.Level] >= minOrder {
			result = append(result, *b)
		}
	}
	return result
}

// CheckBetExposure validates whether placing a new bet would breach exposure limits.
// Returns nil if within limits, or an error describing the breach.
// NOTE (TOCTOU): CheckBetExposure and RecordBet take the mutex
// separately, so two concurrent bets can both pass the check before either
// records, exceeding the limit. Before wiring this into the hot path, combine
// check+record into one locked ReserveExposure method.
func (et *ExposureTracker) CheckBetExposure(
	eventID, marketID, selectionID, sport string,
	stake, odds float64,
) error {
	et.mu.RLock()
	defer et.mu.RUnlock()

	additionalLiability := stake * odds
	limit := et.getLimitForSport(sport)

	// Check selection-level limit.
	key := bucketKey(eventID, marketID, selectionID)
	if bucket, ok := et.buckets[key]; ok {
		if bucket.MaxLiability+additionalLiability > limit.MaxSelectionExposure {
			return fmt.Errorf("selection exposure limit exceeded: current %.2f + %.2f > max %.2f",
				bucket.MaxLiability, additionalLiability, limit.MaxSelectionExposure)
		}
	}

	// Check event-level limit.
	var eventLiability float64
	for _, b := range et.buckets {
		if b.EventID == eventID {
			eventLiability += b.MaxLiability
		}
	}
	if eventLiability+additionalLiability > limit.MaxEventExposure {
		return fmt.Errorf("event exposure limit exceeded: current %.2f + %.2f > max %.2f",
			eventLiability, additionalLiability, limit.MaxEventExposure)
	}

	return nil
}

// --- HTTP handlers ---

// GetExposureDashboard handles GET /exposure/dashboard.
// Returns the full exposure dashboard for operations.
func GetExposureDashboard(tracker *ExposureTracker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		minLevel := ExposureLevel(r.URL.Query().Get("min_level"))
		if minLevel == "" {
			minLevel = ExposureLevelNormal
		}

		buckets := tracker.GetBucketsAboveLevel(minLevel)

		// Aggregate by event.
		eventMap := make(map[string]bool)
		for _, b := range buckets {
			eventMap[b.EventID] = true
		}
		var eventSummaries []EventExposureSummary
		for eventID := range eventMap {
			eventSummaries = append(eventSummaries, tracker.GetEventExposure(eventID))
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"events":    eventSummaries,
			"buckets":   buckets,
			"count":     len(buckets),
			"min_level": minLevel,
			"limits":    tracker.limits,
			"timestamp": time.Now().UTC(),
		})
	}
}

// GetEventExposureHandler handles GET /exposure/event/{id}.
func GetEventExposureHandler(tracker *ExposureTracker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		summary := tracker.GetEventExposure(eventID)
		writeJSON(w, http.StatusOK, summary)
	}
}

// CheckExposureHandler handles POST /exposure/check.
// Pre-flight check before bet placement.
func CheckExposureHandler(tracker *ExposureTracker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			EventID     string  `json:"event_id"`
			MarketID    string  `json:"market_id"`
			SelectionID string  `json:"selection_id"`
			Sport       string  `json:"sport"`
			Stake       float64 `json:"stake"`
			Odds        float64 `json:"odds"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		_ = context.Background() // reserved for future async checks

		err := tracker.CheckBetExposure(req.EventID, req.MarketID, req.SelectionID, req.Sport, req.Stake, req.Odds)
		if err != nil {
			writeJSON(w, http.StatusOK, map[string]any{
				"allowed": false,
				"reason":  err.Error(),
			})
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"allowed": true,
		})
	}
}
