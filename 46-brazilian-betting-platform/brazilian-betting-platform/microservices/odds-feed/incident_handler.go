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

// IncidentHandler is wired into main.go: its HTTP handlers are registered
// under /incidents. See chapter 46 §X for the full wiring.

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// IncidentType classifies live match events that may affect odds or trading.
type IncidentType string

const (
	IncidentGoal      IncidentType = "goal"
	IncidentRedCard   IncidentType = "red_card"
	IncidentPenalty   IncidentType = "penalty"
	IncidentSetBreak  IncidentType = "set_break"
	IncidentKnockout  IncidentType = "knockout"
	IncidentTimeout   IncidentType = "timeout"
	IncidentTechPause IncidentType = "technical_pause"
)

// CriticalIncidents are incident types that trigger automatic market suspension.
var CriticalIncidents = map[IncidentType]bool{
	IncidentGoal:    true,
	IncidentRedCard: true,
	IncidentPenalty: true,
}

// IncidentSeverity indicates trading impact level.
type IncidentSeverity string

const (
	SeverityCritical IncidentSeverity = "critical" // auto-suspend
	SeverityHigh     IncidentSeverity = "high"     // trader review
	SeverityMedium   IncidentSeverity = "medium"   // log, no action
	SeverityLow      IncidentSeverity = "low"      // informational
)

// EventIncident models a real-time match incident from the data feed.
type EventIncident struct {
	ID          string           `json:"id"`
	EventID     string           `json:"event_id"`
	Type        IncidentType     `json:"type"`
	Severity    IncidentSeverity `json:"severity"`
	Description string           `json:"description"`
	Minute      int              `json:"minute,omitempty"`
	Team        string           `json:"team,omitempty"`
	Player      string           `json:"player,omitempty"`
	HomeScore   int              `json:"home_score,omitempty"`
	AwayScore   int              `json:"away_score,omitempty"`
	Source      string           `json:"source"` // provider name
	ReceivedAt  time.Time        `json:"received_at"`
	ProcessedAt time.Time        `json:"processed_at,omitempty"`
}

// SuspendConfig holds configurable delays for auto-suspend/reopen behavior.
type SuspendConfig struct {
	// GoalSuspendDuration: how long to suspend markets after a goal.
	GoalSuspendDuration time.Duration
	// RedCardSuspendDuration: how long to suspend after a red card.
	RedCardSuspendDuration time.Duration
	// PenaltySuspendDuration: how long to suspend after a penalty is awarded.
	PenaltySuspendDuration time.Duration
	// DefaultSuspendDuration: fallback for other critical incidents.
	DefaultSuspendDuration time.Duration
}

// DefaultSuspendConfig returns production defaults for auto-suspend timing.
func DefaultSuspendConfig() SuspendConfig {
	return SuspendConfig{
		GoalSuspendDuration:    30 * time.Second,
		RedCardSuspendDuration: 20 * time.Second,
		PenaltySuspendDuration: 45 * time.Second,
		DefaultSuspendDuration: 15 * time.Second,
	}
}

// SuspendDurationFor returns the suspension duration for a given incident type.
func (sc SuspendConfig) SuspendDurationFor(t IncidentType) time.Duration {
	switch t {
	case IncidentGoal:
		return sc.GoalSuspendDuration
	case IncidentRedCard:
		return sc.RedCardSuspendDuration
	case IncidentPenalty:
		return sc.PenaltySuspendDuration
	default:
		return sc.DefaultSuspendDuration
	}
}

// IncidentHandler processes live match incidents with auto-suspend logic.
type IncidentHandler struct {
	mu            sync.Mutex
	cache         *Cache
	hub           *LiveFeedHub
	suspendCfg    SuspendConfig
	logger        *slog.Logger
	timeline      map[string][]EventIncident // eventID -> incidents
	pendingReopen map[string]*time.Timer     // eventID -> reopen timer
}

// NewIncidentHandler creates an incident handler wired to cache and live hub.
func NewIncidentHandler(cache *Cache, hub *LiveFeedHub, cfg SuspendConfig, logger *slog.Logger) *IncidentHandler {
	return &IncidentHandler{
		cache:         cache,
		hub:           hub,
		suspendCfg:    cfg,
		logger:        logger,
		timeline:      make(map[string][]EventIncident),
		pendingReopen: make(map[string]*time.Timer),
	}
}

// ClassifySeverity determines the severity of an incident type.
func ClassifySeverity(t IncidentType) IncidentSeverity {
	if CriticalIncidents[t] {
		return SeverityCritical
	}
	switch t {
	case IncidentKnockout:
		return SeverityHigh
	case IncidentSetBreak, IncidentTimeout:
		return SeverityMedium
	case IncidentTechPause:
		return SeverityLow
	default:
		return SeverityLow
	}
}

// IsCritical returns true if the incident type triggers auto-suspend.
func IsCritical(t IncidentType) bool {
	return CriticalIncidents[t]
}

// ProcessIncident handles an incoming incident: records it, optionally suspends
// markets, and schedules auto-reopen.
func (h *IncidentHandler) ProcessIncident(ctx context.Context, incident EventIncident) error {
	h.mu.Lock()
	defer h.mu.Unlock()

	incident.ProcessedAt = time.Now().UTC()
	if incident.ID == "" {
		incident.ID = uuid.NewString()
	}
	if incident.Severity == "" {
		incident.Severity = ClassifySeverity(incident.Type)
	}

	// Append to event timeline.
	h.timeline[incident.EventID] = append(h.timeline[incident.EventID], incident)

	h.logger.Info("incident received",
		"incident_id", incident.ID,
		"event_id", incident.EventID,
		"type", incident.Type,
		"severity", incident.Severity,
		"minute", incident.Minute,
		"source", incident.Source,
	)

	// Update event score if this is a goal.
	if incident.Type == IncidentGoal {
		if err := h.updateScore(ctx, incident); err != nil {
			h.logger.Error("failed to update score", "error", err)
		}
	}

	// Publish incident to live subscribers.
	msg := LiveFeedMessage{
		Type:         "incident",
		EventID:      incident.EventID,
		IncidentType: string(incident.Type),
		Score:        fmt.Sprintf("%d-%d", incident.HomeScore, incident.AwayScore),
		IngestTime:   incident.ReceivedAt,
	}
	h.hub.Publish(msg)

	// Auto-suspend on critical incidents.
	if IsCritical(incident.Type) {
		if err := h.autoSuspend(ctx, incident); err != nil {
			h.logger.Error("auto-suspend failed", "error", err)
			return err
		}
		h.scheduleReopen(ctx, incident)
	}

	return nil
}

// autoSuspend suspends all open markets for the event.
func (h *IncidentHandler) autoSuspend(ctx context.Context, incident EventIncident) error {
	event, err := h.cache.GetEvent(ctx, incident.EventID)
	if err != nil || event == nil {
		return fmt.Errorf("event %s not found for auto-suspend", incident.EventID)
	}

	suspended := 0
	for mi := range event.Markets {
		if event.Markets[mi].IsOpen {
			event.Markets[mi].IsOpen = false
			for si := range event.Markets[mi].Selections {
				event.Markets[mi].Selections[si].IsActive = false
			}
			suspended++
		}
	}

	event.UpdatedAt = time.Now().UTC()
	if err := h.cache.SetEvent(ctx, event); err != nil {
		return fmt.Errorf("persist suspended event: %w", err)
	}

	h.logger.Info("markets auto-suspended",
		"event_id", incident.EventID,
		"incident_type", incident.Type,
		"markets_suspended", suspended,
	)

	// Notify subscribers of suspension.
	suspendMsg := LiveFeedMessage{
		Type:         "market_suspended",
		EventID:      incident.EventID,
		IncidentType: string(incident.Type),
		IngestTime:   time.Now().UTC(),
	}
	h.hub.Publish(suspendMsg)

	return nil
}

// scheduleReopen sets a timer to auto-reopen markets after the configured delay.
func (h *IncidentHandler) scheduleReopen(ctx context.Context, incident EventIncident) {
	// Cancel any existing reopen timer for this event.
	if timer, ok := h.pendingReopen[incident.EventID]; ok {
		timer.Stop()
	}

	delay := h.suspendCfg.SuspendDurationFor(incident.Type)
	eventID := incident.EventID

	h.pendingReopen[eventID] = time.AfterFunc(delay, func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		delete(h.pendingReopen, eventID)

		bgCtx := context.Background()
		if err := h.autoReopen(bgCtx, eventID); err != nil {
			h.logger.Error("auto-reopen failed", "event_id", eventID, "error", err)
		}
	})

	h.logger.Info("auto-reopen scheduled",
		"event_id", eventID,
		"delay", delay.String(),
	)
}

// autoReopen restores all markets to open state.
func (h *IncidentHandler) autoReopen(ctx context.Context, eventID string) error {
	event, err := h.cache.GetEvent(ctx, eventID)
	if err != nil || event == nil {
		return fmt.Errorf("event %s not found for auto-reopen", eventID)
	}

	// An operator (trader action or kill switch) suspended this event
	// manually; auto-reopen must never override that. Only an explicit
	// unsuspend clears the flag.
	if event.ManuallySuspended {
		h.logger.Info("skipping auto-reopen: event is manually suspended",
			"event_id", eventID,
		)
		return nil
	}

	// Only reopen if the event is still live.
	if event.Status != EventStatusLive && event.Status != EventStatusSuspended {
		return nil
	}

	reopened := 0
	for mi := range event.Markets {
		if !event.Markets[mi].IsOpen {
			event.Markets[mi].IsOpen = true
			for si := range event.Markets[mi].Selections {
				event.Markets[mi].Selections[si].IsActive = true
			}
			reopened++
		}
	}

	event.UpdatedAt = time.Now().UTC()
	if event.Status == EventStatusSuspended {
		event.Status = EventStatusLive
	}
	if err := h.cache.SetEvent(ctx, event); err != nil {
		return fmt.Errorf("persist reopened event: %w", err)
	}

	h.logger.Info("markets auto-reopened",
		"event_id", eventID,
		"markets_reopened", reopened,
	)

	// Notify subscribers.
	msg := LiveFeedMessage{
		Type:       "market_reopened",
		EventID:    eventID,
		IngestTime: time.Now().UTC(),
	}
	h.hub.Publish(msg)

	return nil
}

// updateScore updates the cached event score after a goal incident.
func (h *IncidentHandler) updateScore(ctx context.Context, incident EventIncident) error {
	event, err := h.cache.GetEvent(ctx, incident.EventID)
	if err != nil || event == nil {
		return fmt.Errorf("event %s not found for score update", incident.EventID)
	}

	event.HomeScore = incident.HomeScore
	event.AwayScore = incident.AwayScore
	if incident.Minute > 0 {
		event.InPlayMinute = incident.Minute
	}
	event.UpdatedAt = time.Now().UTC()

	return h.cache.SetEvent(ctx, event)
}

// GetTimeline returns the incident history for an event.
func (h *IncidentHandler) GetTimeline(eventID string) []EventIncident {
	h.mu.Lock()
	defer h.mu.Unlock()
	tl := h.timeline[eventID]
	result := make([]EventIncident, len(tl))
	copy(result, tl)
	return result
}

// PendingReopenCount returns the number of events awaiting auto-reopen.
func (h *IncidentHandler) PendingReopenCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.pendingReopen)
}

// --- HTTP handlers ---

// IngestIncident handles POST /incidents.
// Receives a match incident from the feed and triggers auto-suspend logic.
func IngestIncident(handler *IncidentHandler, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var incident EventIncident
		if err := json.NewDecoder(r.Body).Decode(&incident); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if incident.EventID == "" {
			writeError(w, http.StatusBadRequest, "event_id is required")
			return
		}
		if incident.Type == "" {
			writeError(w, http.StatusBadRequest, "type is required")
			return
		}
		if incident.ReceivedAt.IsZero() {
			incident.ReceivedAt = time.Now().UTC()
		}

		if err := handler.ProcessIncident(r.Context(), incident); err != nil {
			logger.Error("incident processing failed", "error", err)
			writeError(w, http.StatusInternalServerError, "incident processing failed")
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"status":       "processed",
			"incident_id":  incident.ID,
			"event_id":     incident.EventID,
			"type":         incident.Type,
			"severity":     incident.Severity,
			"auto_suspend": IsCritical(incident.Type),
			"timestamp":    time.Now().UTC(),
		})
	}
}

// GetEventTimeline handles GET /incidents/event/{id}/timeline.
// Returns the full incident history for an event.
func GetEventTimeline(handler *IncidentHandler, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		eventID := chi.URLParam(r, "id")
		if eventID == "" {
			writeError(w, http.StatusBadRequest, "event id required")
			return
		}

		timeline := handler.GetTimeline(eventID)
		writeJSON(w, http.StatusOK, map[string]any{
			"event_id":  eventID,
			"incidents": timeline,
			"count":     len(timeline),
		})
	}
}

// GetIncidentStatus handles GET /incidents/status.
// Returns operational status of the incident handler.
func GetIncidentStatus(handler *IncidentHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"pending_reopens": handler.PendingReopenCount(),
			"timestamp":       time.Now().UTC(),
		})
	}
}
