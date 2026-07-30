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
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// LiveSettlementStatus tracks the state of a live market settlement.
type LiveSettlementStatus string

const (
	LiveSettlementPending    LiveSettlementStatus = "pending"
	LiveSettlementConfirmed  LiveSettlementStatus = "confirmed"
	LiveSettlementSettled    LiveSettlementStatus = "settled"
	LiveSettlementDisputed   LiveSettlementStatus = "disputed"
	LiveSettlementRolledBack LiveSettlementStatus = "rolled_back"
)

// LiveSettlementDependency represents a condition that must be met before
// a live market can be settled. For example, a "first half result" market
// depends on the half-time whistle incident.
type LiveSettlementDependency struct {
	ID              string               `json:"id"`
	EventID         string               `json:"event_id"`
	MarketID        string               `json:"market_id"`
	MarketType      string               `json:"market_type"`
	DependsOn       string               `json:"depends_on"`       // incident type or event state
	Description     string               `json:"description"`
	Status          LiveSettlementStatus  `json:"status"`
	ConfirmedAt     *time.Time            `json:"confirmed_at,omitempty"`
	ConfirmedBy     string               `json:"confirmed_by,omitempty"` // "feed", "manual", "official"
	SettledAt       *time.Time            `json:"settled_at,omitempty"`
	CreatedAt       time.Time             `json:"created_at"`
}

// LiveMarketResult holds the confirmed result for a live market.
type LiveMarketResult struct {
	EventID      string            `json:"event_id"`
	MarketID     string            `json:"market_id"`
	MarketType   string            `json:"market_type"`
	Results      map[string]string `json:"results"`       // selectionID -> "won"/"lost"/"void"
	Source       string            `json:"source"`        // "feed", "manual", "official"
	ConfirmedAt  time.Time         `json:"confirmed_at"`
	HomeScore    int               `json:"home_score,omitempty"`
	AwayScore    int               `json:"away_score,omitempty"`
	Minute       int               `json:"minute,omitempty"`
}

// LiveSettlementEngine manages settlement dependencies for live markets.
// It ensures markets are only settled when their required conditions are met
// (e.g., half-time whistle for first-half markets, final whistle for match result).
type LiveSettlementEngine struct {
	mu           sync.RWMutex
	dependencies map[string]*LiveSettlementDependency // key = "eventID:marketID"
	results      map[string]*LiveMarketResult         // key = "eventID:marketID"
	logger       *slog.Logger
}

// NewLiveSettlementEngine creates a new engine.
func NewLiveSettlementEngine(logger *slog.Logger) *LiveSettlementEngine {
	return &LiveSettlementEngine{
		dependencies: make(map[string]*LiveSettlementDependency),
		results:      make(map[string]*LiveMarketResult),
		logger:       logger,
	}
}

// depKey builds the composite key for a dependency.
func depKey(eventID, marketID string) string {
	return eventID + ":" + marketID
}

// RegisterDependency declares what a market needs before it can be settled.
func (e *LiveSettlementEngine) RegisterDependency(dep LiveSettlementDependency) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if dep.ID == "" {
		dep.ID = uuid.NewString()
	}
	dep.Status = LiveSettlementPending
	dep.CreatedAt = time.Now().UTC()

	key := depKey(dep.EventID, dep.MarketID)
	e.dependencies[key] = &dep

	e.logger.Info("settlement dependency registered",
		"event_id", dep.EventID,
		"market_id", dep.MarketID,
		"depends_on", dep.DependsOn,
	)
}

// RegisterFootballDependencies sets up standard settlement dependencies for
// a football event's common market types.
func (e *LiveSettlementEngine) RegisterFootballDependencies(eventID string, markets []MarketDef) {
	for _, m := range markets {
		dep := LiveSettlementDependency{
			EventID:     eventID,
			MarketID:    m.MarketID,
			MarketType:  m.MarketType,
			DependsOn:   m.DependsOn,
			Description: m.Description,
		}
		e.RegisterDependency(dep)
	}
}

// MarketDef is a helper for bulk market dependency registration.
type MarketDef struct {
	MarketID    string
	MarketType  string
	DependsOn   string
	Description string
}

// DefaultFootballMarketDeps returns the standard dependency rules for football.
func DefaultFootballMarketDeps(eventID string, marketIDs map[string]string) []MarketDef {
	defs := []MarketDef{
		{
			MarketID:    marketIDs["match_winner"],
			MarketType:  "match_winner",
			DependsOn:   "full_time",
			Description: "Match result depends on final whistle",
		},
		{
			MarketID:    marketIDs["half_time_result"],
			MarketType:  "half_time_result",
			DependsOn:   "half_time",
			Description: "Half-time result depends on half-time whistle",
		},
		{
			MarketID:    marketIDs["over_under"],
			MarketType:  "over_under",
			DependsOn:   "full_time",
			Description: "Total goals depends on final whistle",
		},
		{
			MarketID:    marketIDs["both_teams_score"],
			MarketType:  "both_teams_score",
			DependsOn:   "full_time",
			Description: "Both teams to score depends on final whistle",
		},
		{
			MarketID:    marketIDs["double_chance"],
			MarketType:  "double_chance",
			DependsOn:   "full_time",
			Description: "Double chance depends on final whistle",
		},
	}

	// Filter out markets with empty IDs (not all events have all markets).
	var result []MarketDef
	for _, d := range defs {
		if d.MarketID != "" {
			result = append(result, d)
		}
	}
	return result
}

// ConfirmDependency marks a dependency as met (e.g., half-time whistle received).
func (e *LiveSettlementEngine) ConfirmDependency(eventID, marketID, source string) error {
	e.mu.Lock()
	defer e.mu.Unlock()

	key := depKey(eventID, marketID)
	dep, ok := e.dependencies[key]
	if !ok {
		return fmt.Errorf("no dependency registered for event %s market %s", eventID, marketID)
	}

	if dep.Status != LiveSettlementPending {
		return fmt.Errorf("dependency already in state %s", dep.Status)
	}

	now := time.Now().UTC()
	dep.Status = LiveSettlementConfirmed
	dep.ConfirmedAt = &now
	dep.ConfirmedBy = source

	e.logger.Info("settlement dependency confirmed",
		"event_id", eventID,
		"market_id", marketID,
		"source", source,
	)

	return nil
}

// ConfirmByIncident confirms all dependencies that match a given incident type.
// For example, "set_break" at half-time confirms all "half_time" dependencies.
func (e *LiveSettlementEngine) ConfirmByIncident(eventID, incidentType string, source string) int {
	e.mu.Lock()
	defer e.mu.Unlock()

	// Map incident types to dependency triggers.
	trigger := mapIncidentToDependency(incidentType)
	if trigger == "" {
		return 0
	}

	confirmed := 0
	for _, dep := range e.dependencies {
		if dep.EventID != eventID {
			continue
		}
		if dep.DependsOn != trigger {
			continue
		}
		if dep.Status != LiveSettlementPending {
			continue
		}

		now := time.Now().UTC()
		dep.Status = LiveSettlementConfirmed
		dep.ConfirmedAt = &now
		dep.ConfirmedBy = source
		confirmed++

		e.logger.Info("dependency confirmed by incident",
			"event_id", eventID,
			"market_id", dep.MarketID,
			"incident_type", incidentType,
			"trigger", trigger,
		)
	}

	return confirmed
}

// mapIncidentToDependency converts an incident type to the settlement dependency
// trigger it satisfies.
func mapIncidentToDependency(incidentType string) string {
	switch incidentType {
	case "set_break":
		return "half_time" // Football half-time or set break
	case "knockout":
		return "full_time" // End of match
	default:
		return ""
	}
}

// SubmitResult submits the actual result for a live market.
func (e *LiveSettlementEngine) SubmitResult(result LiveMarketResult) error {
	e.mu.Lock()
	defer e.mu.Unlock()

	key := depKey(result.EventID, result.MarketID)

	// Check dependency is confirmed.
	if dep, ok := e.dependencies[key]; ok {
		if dep.Status != LiveSettlementConfirmed {
			return fmt.Errorf("dependency not confirmed for market %s (status: %s)", result.MarketID, dep.Status)
		}
	}

	result.ConfirmedAt = time.Now().UTC()
	e.results[key] = &result

	e.logger.Info("live market result submitted",
		"event_id", result.EventID,
		"market_id", result.MarketID,
		"source", result.Source,
		"results", result.Results,
	)

	return nil
}

// IsReadyToSettle checks if a market has its dependency confirmed and result submitted.
func (e *LiveSettlementEngine) IsReadyToSettle(eventID, marketID string) bool {
	e.mu.RLock()
	defer e.mu.RUnlock()

	key := depKey(eventID, marketID)

	// Must have confirmed dependency.
	dep, ok := e.dependencies[key]
	if ok && dep.Status != LiveSettlementConfirmed && dep.Status != LiveSettlementSettled {
		return false
	}

	// Must have result.
	_, hasResult := e.results[key]
	return hasResult
}

// GetResult returns the submitted result for a market, if any.
func (e *LiveSettlementEngine) GetResult(eventID, marketID string) *LiveMarketResult {
	e.mu.RLock()
	defer e.mu.RUnlock()

	key := depKey(eventID, marketID)
	if r, ok := e.results[key]; ok {
		copy := *r
		return &copy
	}
	return nil
}

// MarkSettled marks a dependency as fully settled.
func (e *LiveSettlementEngine) MarkSettled(eventID, marketID string) {
	e.mu.Lock()
	defer e.mu.Unlock()

	key := depKey(eventID, marketID)
	if dep, ok := e.dependencies[key]; ok {
		now := time.Now().UTC()
		dep.Status = LiveSettlementSettled
		dep.SettledAt = &now
	}
}

// GetEventDependencies returns all settlement dependencies for an event.
func (e *LiveSettlementEngine) GetEventDependencies(eventID string) []LiveSettlementDependency {
	e.mu.RLock()
	defer e.mu.RUnlock()

	var deps []LiveSettlementDependency
	for _, dep := range e.dependencies {
		if dep.EventID == eventID {
			deps = append(deps, *dep)
		}
	}
	return deps
}

// PendingCount returns the number of unconfirmed dependencies.
func (e *LiveSettlementEngine) PendingCount() int {
	e.mu.RLock()
	defer e.mu.RUnlock()

	count := 0
	for _, dep := range e.dependencies {
		if dep.Status == LiveSettlementPending {
			count++
		}
	}
	return count
}

// --- HTTP handlers ---

// GetLiveSettlementStatus handles GET /settle/live/status.
func GetLiveSettlementStatus(engine *LiveSettlementEngine) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"pending_dependencies": engine.PendingCount(),
			"timestamp":            time.Now().UTC(),
		})
	}
}

// GetEventDependenciesHandler handles GET /settle/live/event/{id}/deps.
func GetEventDependenciesHandler(engine *LiveSettlementEngine) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		deps := engine.GetEventDependencies(eventID)
		writeJSON(w, http.StatusOK, map[string]any{
			"event_id":     eventID,
			"dependencies": deps,
			"count":        len(deps),
		})
	}
}

// ConfirmDependencyHandler handles POST /settle/live/event/{id}/confirm.
func ConfirmDependencyHandler(engine *LiveSettlementEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		var req struct {
			MarketID string `json:"market_id"`
			Source   string `json:"source"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		if err := engine.ConfirmDependency(eventID, req.MarketID, req.Source); err != nil {
			writeError(w, http.StatusConflict, err.Error())
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "confirmed",
			"event_id":  eventID,
			"market_id": req.MarketID,
			"source":    req.Source,
		})
	}
}

// SubmitLiveResultHandler handles POST /settle/live/event/{id}/result.
func SubmitLiveResultHandler(engine *LiveSettlementEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		var result LiveMarketResult
		if err := json.NewDecoder(r.Body).Decode(&result); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		result.EventID = eventID

		if err := engine.SubmitResult(result); err != nil {
			writeError(w, http.StatusConflict, err.Error())
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "result_submitted",
			"event_id":  eventID,
			"market_id": result.MarketID,
			"ready":     engine.IsReadyToSettle(eventID, result.MarketID),
		})
	}
}

// RegisterDependencyHandler handles POST /settle/live/event/{id}/register.
func RegisterDependencyHandler(engine *LiveSettlementEngine, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		var dep LiveSettlementDependency
		if err := json.NewDecoder(r.Body).Decode(&dep); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		dep.EventID = eventID

		engine.RegisterDependency(dep)

		writeJSON(w, http.StatusCreated, map[string]any{
			"status":       "registered",
			"dependency_id": dep.ID,
			"event_id":     eventID,
			"market_id":    dep.MarketID,
			"depends_on":   dep.DependsOn,
		})
	}
}
