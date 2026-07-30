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

// FeedMonitor is wired into main.go: RunMonitorLoop runs as a background
// goroutine and its HTTP handlers are registered under /feed. See chapter 46
// §X for the full wiring.

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// CircuitState represents the state of a circuit breaker.
type CircuitState string

const (
	CircuitClosed   CircuitState = "closed"    // Normal operation.
	CircuitOpen     CircuitState = "open"      // Provider is failing, use fallback.
	CircuitHalfOpen CircuitState = "half_open" // Testing if provider recovered.
)

// FeedMonitorConfig holds configuration for feed health monitoring.
type FeedMonitorConfig struct {
	// StaleThreshold: a feed is considered stale if no update arrives within this window.
	StaleThreshold time.Duration

	// CircuitOpenThreshold: number of consecutive failures before opening the circuit.
	CircuitOpenThreshold int

	// CircuitHalfOpenDelay: how long to wait before testing a failed provider.
	CircuitHalfOpenDelay time.Duration

	// MonitorInterval: how often to check feed freshness.
	MonitorInterval time.Duration

	// AutoSuspendOnStale: if true, automatically suspend all markets when feed goes stale.
	AutoSuspendOnStale bool
}

// DefaultFeedMonitorConfig returns production defaults.
func DefaultFeedMonitorConfig() FeedMonitorConfig {
	return FeedMonitorConfig{
		StaleThreshold:       30 * time.Second,
		CircuitOpenThreshold: 5,
		CircuitHalfOpenDelay: 60 * time.Second,
		MonitorInterval:      10 * time.Second,
		AutoSuspendOnStale:   true,
	}
}

// ProviderCircuitBreaker tracks failure state for a single feed provider.
type ProviderCircuitBreaker struct {
	Provider          ProviderName `json:"provider"`
	State             CircuitState `json:"state"`
	ConsecutiveFails  int          `json:"consecutive_fails"`
	TotalFails        int          `json:"total_fails"`
	TotalSuccess      int          `json:"total_success"`
	LastSuccess       time.Time    `json:"last_success"`
	LastFailure       time.Time    `json:"last_failure,omitempty"`
	LastFailureReason string       `json:"last_failure_reason,omitempty"`
	OpenedAt          time.Time    `json:"opened_at,omitempty"`
	HalfOpenAt        time.Time    `json:"half_open_at,omitempty"`
}

// FeedFreshnessReport holds per-provider freshness state.
type FeedFreshnessReport struct {
	Provider     ProviderName `json:"provider"`
	LastUpdate   time.Time    `json:"last_update"`
	AgeSec       int64        `json:"age_seconds"`
	IsStale      bool         `json:"is_stale"`
	EventCount   int          `json:"event_count"`
	CircuitState CircuitState `json:"circuit_state"`
}

// KillSwitch tracks manual kill-switch state for operator controls.
type KillSwitch struct {
	Level       string    `json:"level"` // "event", "market", "provider"
	TargetID    string    `json:"target_id"`
	Active      bool      `json:"active"`
	Reason      string    `json:"reason"`
	ActivatedBy string    `json:"activated_by"`
	ActivatedAt time.Time `json:"activated_at"`
}

// FeedMonitor manages feed freshness, circuit breakers, and kill switches.
type FeedMonitor struct {
	mu               sync.Mutex
	config           FeedMonitorConfig
	cache            *Cache
	hub              *LiveFeedHub
	logger           *slog.Logger
	circuits         map[ProviderName]*ProviderCircuitBreaker
	lastUpdates      map[ProviderName]time.Time
	killSwitches     map[string]*KillSwitch // key = "level:targetID"
	activeProvider   ProviderName
	fallbackProvider ProviderName
}

// NewFeedMonitor creates a feed monitor with circuit breakers for each provider.
func NewFeedMonitor(
	cfg FeedMonitorConfig,
	cache *Cache,
	hub *LiveFeedHub,
	primary ProviderName,
	fallback ProviderName,
	logger *slog.Logger,
) *FeedMonitor {
	fm := &FeedMonitor{
		config:           cfg,
		cache:            cache,
		hub:              hub,
		logger:           logger,
		circuits:         make(map[ProviderName]*ProviderCircuitBreaker),
		lastUpdates:      make(map[ProviderName]time.Time),
		killSwitches:     make(map[string]*KillSwitch),
		activeProvider:   primary,
		fallbackProvider: fallback,
	}

	now := time.Now().UTC()
	for _, p := range []ProviderName{primary, fallback} {
		if p == "" {
			continue
		}
		fm.circuits[p] = &ProviderCircuitBreaker{
			Provider:    p,
			State:       CircuitClosed,
			LastSuccess: now,
		}
		fm.lastUpdates[p] = now
	}

	return fm
}

// RecordSuccess records a successful feed update from a provider.
func (fm *FeedMonitor) RecordSuccess(provider ProviderName) {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	cb, ok := fm.circuits[provider]
	if !ok {
		return
	}

	now := time.Now().UTC()
	cb.ConsecutiveFails = 0
	cb.TotalSuccess++
	cb.LastSuccess = now
	fm.lastUpdates[provider] = now

	// If half-open and success, close the circuit.
	if cb.State == CircuitHalfOpen {
		cb.State = CircuitClosed
		fm.logger.Info("circuit breaker closed after recovery",
			"provider", provider,
		)
		// If this was the fallback and primary is still open, switch back.
		if provider == fm.activeProvider {
			return
		}
	}
}

// RecordFailure records a failed feed attempt.
func (fm *FeedMonitor) RecordFailure(provider ProviderName, reason string) {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	cb, ok := fm.circuits[provider]
	if !ok {
		return
	}

	now := time.Now().UTC()
	cb.ConsecutiveFails++
	cb.TotalFails++
	cb.LastFailure = now
	cb.LastFailureReason = reason

	if cb.ConsecutiveFails >= fm.config.CircuitOpenThreshold && cb.State == CircuitClosed {
		cb.State = CircuitOpen
		cb.OpenedAt = now
		fm.logger.Warn("circuit breaker opened",
			"provider", provider,
			"consecutive_fails", cb.ConsecutiveFails,
			"reason", reason,
		)

		// If this is the active provider, failover.
		if provider == fm.activeProvider && fm.fallbackProvider != "" {
			fm.doFailover(provider)
		}
	}
}

// doFailover switches the active provider to the fallback.
func (fm *FeedMonitor) doFailover(failedProvider ProviderName) {
	if fm.fallbackProvider == "" || fm.fallbackProvider == failedProvider {
		fm.logger.Error("no fallback available for failover",
			"failed_provider", failedProvider,
		)
		return
	}

	oldActive := fm.activeProvider
	fm.activeProvider = fm.fallbackProvider

	fm.logger.Warn("feed failover triggered",
		"from", oldActive,
		"to", fm.activeProvider,
	)

	// Publish failover event to subscribers.
	msg := LiveFeedMessage{
		Type:       "feed_failover",
		IngestTime: time.Now().UTC(),
	}
	fm.hub.Publish(msg)
}

// suspendAllLiveMarkets suspends every currently live event in response to a
// dead/stale feed, closing all its markets so no further bets can be placed
// against odds that may no longer be accurate. This suspension is not
// flagged ManuallySuspended: once the feed recovers (RecordSuccess) or a
// trader intervenes via /trader, the normal reopen paths still apply.
func (fm *FeedMonitor) suspendAllLiveMarkets(ctx context.Context, reason string) {
	if fm.cache == nil {
		return
	}
	events, err := fm.cache.GetLiveEvents(ctx)
	if err != nil {
		fm.logger.Error("failed to list live events for stale-feed suspension", "error", err)
		return
	}

	suspended := 0
	for i := range events {
		ev := events[i]
		changed := false
		for mi := range ev.Markets {
			if ev.Markets[mi].IsOpen {
				ev.Markets[mi].IsOpen = false
				for si := range ev.Markets[mi].Selections {
					ev.Markets[mi].Selections[si].IsActive = false
				}
				changed = true
			}
		}
		if !changed {
			continue
		}
		ev.Status = EventStatusSuspended
		ev.UpdatedAt = time.Now().UTC()
		if err := fm.cache.SetEvent(ctx, &ev); err != nil {
			fm.logger.Error("failed to persist stale-feed suspension", "event_id", ev.ID, "error", err)
			continue
		}
		suspended++
	}

	if suspended > 0 {
		fm.logger.Warn("markets suspended due to stale/dead feed",
			"reason", reason,
			"events_suspended", suspended,
		)
		fm.hub.Publish(LiveFeedMessage{
			Type:       "feed_stale_suspend",
			IngestTime: time.Now().UTC(),
		})
	}
}

// ActiveProvider returns the currently active feed provider.
func (fm *FeedMonitor) ActiveProvider() ProviderName {
	fm.mu.Lock()
	defer fm.mu.Unlock()
	return fm.activeProvider
}

// CheckFreshness evaluates all providers for stale data and returns reports.
func (fm *FeedMonitor) CheckFreshness() []FeedFreshnessReport {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	now := time.Now().UTC()
	var reports []FeedFreshnessReport

	for provider, lastUpdate := range fm.lastUpdates {
		age := now.Sub(lastUpdate)
		cb := fm.circuits[provider]
		isStale := age > fm.config.StaleThreshold

		// Transition half-open check.
		if cb.State == CircuitOpen {
			if now.Sub(cb.OpenedAt) > fm.config.CircuitHalfOpenDelay {
				cb.State = CircuitHalfOpen
				cb.HalfOpenAt = now
				fm.logger.Info("circuit breaker half-open, will test provider",
					"provider", provider,
				)
			}
		}

		reports = append(reports, FeedFreshnessReport{
			Provider:     provider,
			LastUpdate:   lastUpdate,
			AgeSec:       int64(age.Seconds()),
			IsStale:      isStale,
			CircuitState: cb.State,
		})
	}

	return reports
}

// IsProviderKilled checks if a kill switch is active for a given target.
func (fm *FeedMonitor) IsProviderKilled(level, targetID string) bool {
	fm.mu.Lock()
	defer fm.mu.Unlock()
	key := level + ":" + targetID
	ks, ok := fm.killSwitches[key]
	return ok && ks.Active
}

// ActivateKillSwitch engages a kill switch at event, market, or provider level.
func (fm *FeedMonitor) ActivateKillSwitch(level, targetID, reason, operator string) {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	key := level + ":" + targetID
	fm.killSwitches[key] = &KillSwitch{
		Level:       level,
		TargetID:    targetID,
		Active:      true,
		Reason:      reason,
		ActivatedBy: operator,
		ActivatedAt: time.Now().UTC(),
	}

	fm.logger.Warn("kill switch activated",
		"level", level,
		"target_id", targetID,
		"reason", reason,
		"operator", operator,
	)
}

// DeactivateKillSwitch disengages a kill switch.
func (fm *FeedMonitor) DeactivateKillSwitch(level, targetID string) {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	key := level + ":" + targetID
	delete(fm.killSwitches, key)

	fm.logger.Info("kill switch deactivated",
		"level", level,
		"target_id", targetID,
	)
}

// GetKillSwitches returns all active kill switches.
func (fm *FeedMonitor) GetKillSwitches() []KillSwitch {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	var switches []KillSwitch
	for _, ks := range fm.killSwitches {
		switches = append(switches, *ks)
	}
	return switches
}

// GetCircuitBreakers returns the state of all circuit breakers.
func (fm *FeedMonitor) GetCircuitBreakers() map[ProviderName]*ProviderCircuitBreaker {
	fm.mu.Lock()
	defer fm.mu.Unlock()

	result := make(map[ProviderName]*ProviderCircuitBreaker)
	for k, v := range fm.circuits {
		copy := *v
		result[k] = &copy
	}
	return result
}

// RunMonitorLoop starts the background freshness check loop.
// Should be called in a goroutine.
func (fm *FeedMonitor) RunMonitorLoop(ctx context.Context) {
	ticker := time.NewTicker(fm.config.MonitorInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			fm.logger.Info("feed monitor stopped")
			return
		case <-ticker.C:
			reports := fm.CheckFreshness()
			for _, r := range reports {
				if r.IsStale {
					fm.logger.Warn("stale feed detected",
						"provider", r.Provider,
						"age_seconds", r.AgeSec,
						"circuit_state", r.CircuitState,
					)
					if fm.config.AutoSuspendOnStale && r.Provider == fm.activeProvider {
						fm.RecordFailure(r.Provider, "stale feed detected")
						fm.suspendAllLiveMarkets(ctx, fmt.Sprintf("provider %s stale for %ds", r.Provider, r.AgeSec))
					}
				}
			}
		}
	}
}

// --- HTTP handlers ---

// GetFeedStatus handles GET /feed/status.
// Returns freshness, circuit breaker, and kill switch state.
func GetFeedStatus(monitor *FeedMonitor) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"active_provider":  monitor.ActiveProvider(),
			"freshness":        monitor.CheckFreshness(),
			"circuit_breakers": monitor.GetCircuitBreakers(),
			"kill_switches":    monitor.GetKillSwitches(),
			"timestamp":        time.Now().UTC(),
		})
	}
}

// ActivateKillSwitchHandler handles POST /feed/kill-switch/activate.
func ActivateKillSwitchHandler(monitor *FeedMonitor, cache *Cache, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Level    string `json:"level"`
			TargetID string `json:"target_id"`
			Reason   string `json:"reason"`
			Operator string `json:"operator"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.Level == "" || req.TargetID == "" {
			writeError(w, http.StatusBadRequest, "level and target_id are required")
			return
		}
		if req.Level != "event" && req.Level != "market" && req.Level != "provider" {
			writeError(w, http.StatusBadRequest, "level must be event, market, or provider")
			return
		}

		monitor.ActivateKillSwitch(req.Level, req.TargetID, req.Reason, req.Operator)

		// If event-level kill switch, suspend all markets.
		if req.Level == "event" {
			ctx := r.Context()
			event, err := cache.GetEvent(ctx, req.TargetID)
			if err == nil && event != nil {
				event.Status = EventStatusSuspended
				// A kill switch is an operator action: mark the event
				// manually suspended so incident-driven auto-reopen never
				// silently overrides it.
				event.ManuallySuspended = true
				for mi := range event.Markets {
					event.Markets[mi].IsOpen = false
					for si := range event.Markets[mi].Selections {
						event.Markets[mi].Selections[si].IsActive = false
					}
				}
				event.UpdatedAt = time.Now().UTC()
				cache.SetEvent(ctx, event) //nolint:errcheck
			}
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "activated",
			"level":     req.Level,
			"target_id": req.TargetID,
			"timestamp": time.Now().UTC(),
		})
	}
}

// DeactivateKillSwitchHandler handles POST /feed/kill-switch/deactivate.
func DeactivateKillSwitchHandler(monitor *FeedMonitor, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Level    string `json:"level"`
			TargetID string `json:"target_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.Level == "" || req.TargetID == "" {
			writeError(w, http.StatusBadRequest, "level and target_id are required")
			return
		}

		monitor.DeactivateKillSwitch(req.Level, req.TargetID)

		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "deactivated",
			"level":     req.Level,
			"target_id": req.TargetID,
			"timestamp": time.Now().UTC(),
		})
	}
}

// ForceFailover handles POST /feed/failover.
// Manually triggers provider failover for operator control.
func ForceFailover(monitor *FeedMonitor, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Reason   string `json:"reason"`
			Operator string `json:"operator"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request body")
			return
		}

		monitor.mu.Lock()
		before := monitor.activeProvider
		monitor.doFailover(before)
		after := monitor.activeProvider
		monitor.mu.Unlock()

		if before == after {
			writeError(w, http.StatusConflict, fmt.Sprintf("failover not possible, no alternative to %s", before))
			return
		}

		logger.Warn("manual failover executed",
			"from", before,
			"to", after,
			"reason", req.Reason,
			"operator", req.Operator,
		)

		writeJSON(w, http.StatusOK, map[string]any{
			"status":        "failover_executed",
			"from_provider": before,
			"to_provider":   after,
			"reason":        req.Reason,
			"timestamp":     time.Now().UTC(),
		})
	}
}
