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
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDefaultFeedMonitorConfig(t *testing.T) {
	cfg := DefaultFeedMonitorConfig()
	if cfg.StaleThreshold != 30*time.Second {
		t.Errorf("StaleThreshold = %v, want 30s", cfg.StaleThreshold)
	}
	if cfg.CircuitOpenThreshold != 5 {
		t.Errorf("CircuitOpenThreshold = %d, want 5", cfg.CircuitOpenThreshold)
	}
	if !cfg.AutoSuspendOnStale {
		t.Error("AutoSuspendOnStale should default to true")
	}
}

func TestFeedMonitor_RecordSuccess(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	fm.RecordSuccess(ProviderBetradar)

	circuits := fm.GetCircuitBreakers()
	cb := circuits[ProviderBetradar]
	if cb.TotalSuccess != 1 {
		t.Errorf("total_success = %d, want 1", cb.TotalSuccess)
	}
	if cb.State != CircuitClosed {
		t.Errorf("state = %s, want closed", cb.State)
	}
}

func TestFeedMonitor_RecordFailure_OpensCircuit(t *testing.T) {
	cfg := DefaultFeedMonitorConfig()
	cfg.CircuitOpenThreshold = 3 // Lower threshold for test.
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(cfg, nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	// Record failures up to threshold.
	for i := 0; i < 3; i++ {
		fm.RecordFailure(ProviderBetradar, "test failure")
	}

	circuits := fm.GetCircuitBreakers()
	cb := circuits[ProviderBetradar]
	if cb.State != CircuitOpen {
		t.Errorf("state = %s, want open after %d failures", cb.State, cfg.CircuitOpenThreshold)
	}
	if cb.ConsecutiveFails != 3 {
		t.Errorf("consecutive_fails = %d, want 3", cb.ConsecutiveFails)
	}
}

func TestFeedMonitor_Failover(t *testing.T) {
	cfg := DefaultFeedMonitorConfig()
	cfg.CircuitOpenThreshold = 2
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(cfg, nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	if fm.ActiveProvider() != ProviderBetradar {
		t.Errorf("initial active = %s, want betradar", fm.ActiveProvider())
	}

	// Trigger failover.
	fm.RecordFailure(ProviderBetradar, "fail 1")
	fm.RecordFailure(ProviderBetradar, "fail 2")

	if fm.ActiveProvider() != ProviderBetConstruct {
		t.Errorf("after failover active = %s, want betconstruct", fm.ActiveProvider())
	}
}

func TestFeedMonitor_SuccessResetsFailCount(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	fm.RecordFailure(ProviderBetradar, "fail")
	fm.RecordFailure(ProviderBetradar, "fail")
	fm.RecordSuccess(ProviderBetradar)

	circuits := fm.GetCircuitBreakers()
	if circuits[ProviderBetradar].ConsecutiveFails != 0 {
		t.Errorf("consecutive fails should reset to 0 after success")
	}
}

func TestFeedMonitor_CheckFreshness_Stale(t *testing.T) {
	cfg := DefaultFeedMonitorConfig()
	cfg.StaleThreshold = 1 * time.Millisecond // Very short for testing.
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(cfg, nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	// Wait for feed to become stale.
	time.Sleep(5 * time.Millisecond)

	reports := fm.CheckFreshness()
	staleCount := 0
	for _, r := range reports {
		if r.IsStale {
			staleCount++
		}
	}
	if staleCount == 0 {
		t.Error("expected at least one stale provider after threshold")
	}
}

func TestFeedMonitor_KillSwitch_ActivateDeactivate(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	fm.ActivateKillSwitch("event", "ev-001", "suspicious activity", "trader-1")

	if !fm.IsProviderKilled("event", "ev-001") {
		t.Error("kill switch should be active")
	}
	if fm.IsProviderKilled("event", "ev-002") {
		t.Error("kill switch should not affect other events")
	}

	switches := fm.GetKillSwitches()
	if len(switches) != 1 {
		t.Errorf("expected 1 kill switch, got %d", len(switches))
	}

	fm.DeactivateKillSwitch("event", "ev-001")
	if fm.IsProviderKilled("event", "ev-001") {
		t.Error("kill switch should be deactivated")
	}
}

func TestFeedMonitor_KillSwitch_Levels(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	fm.ActivateKillSwitch("event", "ev-001", "reason", "op")
	fm.ActivateKillSwitch("market", "mkt-001", "reason", "op")
	fm.ActivateKillSwitch("provider", "betradar", "reason", "op")

	if len(fm.GetKillSwitches()) != 3 {
		t.Errorf("expected 3 kill switches, got %d", len(fm.GetKillSwitches()))
	}
}

func TestGetFeedStatus_Handler(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	handler := GetFeedStatus(fm)
	req := httptest.NewRequest(http.MethodGet, "/feed/status", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}

	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body) //nolint:errcheck
	if body["active_provider"] == nil {
		t.Error("expected active_provider in response")
	}
}

func TestActivateKillSwitch_Handler_InvalidLevel(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	handler := ActivateKillSwitchHandler(fm, nil, noopLogger())
	body := `{"level":"invalid","target_id":"ev-001","reason":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/feed/kill-switch/activate", strings.NewReader(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400 for invalid level", rr.Code)
	}
}

func TestActivateKillSwitch_Handler_MissingFields(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())

	handler := ActivateKillSwitchHandler(fm, nil, noopLogger())
	body := `{"level":"event"}`
	req := httptest.NewRequest(http.MethodPost, "/feed/kill-switch/activate", strings.NewReader(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400 for missing target_id", rr.Code)
	}
}

func TestDeactivateKillSwitch_Handler(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, ProviderBetConstruct, noopLogger())
	fm.ActivateKillSwitch("event", "ev-001", "test", "op")

	handler := DeactivateKillSwitchHandler(fm, noopLogger())
	body := `{"level":"event","target_id":"ev-001"}`
	req := httptest.NewRequest(http.MethodPost, "/feed/kill-switch/deactivate", strings.NewReader(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}
	if fm.IsProviderKilled("event", "ev-001") {
		t.Error("kill switch should be deactivated")
	}
}

func TestCircuitState_Constants(t *testing.T) {
	states := []CircuitState{CircuitClosed, CircuitOpen, CircuitHalfOpen}
	for _, s := range states {
		if string(s) == "" {
			t.Error("CircuitState constant is empty")
		}
	}
}

func TestForceFailover_Handler_NoFallback(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	// No fallback provider.
	fm := NewFeedMonitor(DefaultFeedMonitorConfig(), nil, hub, ProviderBetradar, "", noopLogger())

	handler := ForceFailover(fm, noopLogger())
	body := `{"reason":"test","operator":"admin"}`
	req := httptest.NewRequest(http.MethodPost, "/feed/failover", strings.NewReader(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusConflict {
		t.Errorf("status = %d, want 409 when no fallback available", rr.Code)
	}
}
