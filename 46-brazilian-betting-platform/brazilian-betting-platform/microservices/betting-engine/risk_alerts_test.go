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
	"testing"
	"time"
)

func TestDefaultRiskThresholds(t *testing.T) {
	th := DefaultRiskThresholds()
	if th.LargeStakeThreshold != 10000.00 {
		t.Errorf("LargeStakeThreshold = %.2f, want 10000", th.LargeStakeThreshold)
	}
	if th.RapidBetCount != 10 {
		t.Errorf("RapidBetCount = %d, want 10", th.RapidBetCount)
	}
	if th.LateGoalMinute != 80 {
		t.Errorf("LateGoalMinute = %d, want 80", th.LateGoalMinute)
	}
	if th.ConcentrationThreshold != 0.30 {
		t.Errorf("ConcentrationThreshold = %.2f, want 0.30", th.ConcentrationThreshold)
	}
}

func TestRiskMaskCPF(t *testing.T) {
	tests := []struct {
		cpf  string
		want string
	}{
		{"12345678901", "123.***.***-01"},
		{"abc", "***"},
		{"", "***"},
	}
	for _, tc := range tests {
		got := riskMaskCPF(tc.cpf)
		if got != tc.want {
			t.Errorf("riskMaskCPF(%q) = %q, want %q", tc.cpf, got, tc.want)
		}
	}
}

func TestIsFootballSport(t *testing.T) {
	footballSports := []string{"futebol", "brasileirao", "copa-brasil", "libertadores", "sul-americana"}
	for _, s := range footballSports {
		if !isFootballSport(s) {
			t.Errorf("isFootballSport(%q) = false, want true", s)
		}
	}
	nonFootball := []string{"ufc", "nba", "nfl", "tenis", "volei"}
	for _, s := range nonFootball {
		if isFootballSport(s) {
			t.Errorf("isFootballSport(%q) = true, want false", s)
		}
	}
}

func TestIsAtOrAboveLevel(t *testing.T) {
	tests := []struct {
		actual  ExposureLevel
		minimum ExposureLevel
		want    bool
	}{
		{ExposureLevelCritical, ExposureLevelHigh, true},
		{ExposureLevelHigh, ExposureLevelHigh, true},
		{ExposureLevelElevated, ExposureLevelHigh, false},
		{ExposureLevelNormal, ExposureLevelNormal, true},
	}
	for _, tc := range tests {
		got := isAtOrAboveLevel(tc.actual, tc.minimum)
		if got != tc.want {
			t.Errorf("isAtOrAboveLevel(%s, %s) = %v, want %v", tc.actual, tc.minimum, got, tc.want)
		}
	}
}

func TestRiskEngine_LargeStakeAlert(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	thresholds := DefaultRiskThresholds()
	thresholds.LargeStakeThreshold = 500.00 // Lower for test.

	engine := NewRiskEngine(tracker, thresholds, noopLogger())

	alerts := engine.EvaluateBet("12345678901", "ev-1", "mkt-1", "sel-1", "brasileirao",
		1000.00, 2.00, 0)

	found := false
	for _, a := range alerts {
		if a.Type == RiskAlertTypeLargeStake {
			found = true
			if a.Severity != AlertSeverityWarning {
				t.Errorf("severity = %s, want warning", a.Severity)
			}
		}
	}
	if !found {
		t.Error("expected large stake alert for R$1000 bet with R$500 threshold")
	}
}

func TestRiskEngine_NoAlertBelowThreshold(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	engine := NewRiskEngine(tracker, DefaultRiskThresholds(), noopLogger())

	alerts := engine.EvaluateBet("12345678901", "ev-1", "mkt-1", "sel-1", "brasileirao",
		50.00, 2.00, 0)

	for _, a := range alerts {
		if a.Type == RiskAlertTypeLargeStake {
			t.Error("should not alert for R$50 bet with R$10000 threshold")
		}
	}
}

func TestRiskEngine_RapidBettingAlert(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	thresholds := DefaultRiskThresholds()
	thresholds.RapidBetCount = 3 // Lower for test.

	engine := NewRiskEngine(tracker, thresholds, noopLogger())

	// Place 3 bets rapidly.
	for i := 0; i < 3; i++ {
		engine.EvaluateBet("12345678901", "ev-1", "mkt-1", "sel-1", "brasileirao",
			10.00, 2.00, 0)
	}

	alerts := engine.GetAlerts(AlertSeverityInfo, 100)
	found := false
	for _, a := range alerts {
		if a.Type == RiskAlertTypeRapidBetting {
			found = true
		}
	}
	if !found {
		t.Error("expected rapid betting alert after 3 bets with threshold=3")
	}
}

func TestRiskEngine_LateGoalBettingAlert(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	engine := NewRiskEngine(tracker, DefaultRiskThresholds(), noopLogger())

	alerts := engine.EvaluateBet("12345678901", "ev-1", "mkt-1", "sel-1", "brasileirao",
		100.00, 2.00, 85) // Minute 85

	found := false
	for _, a := range alerts {
		if a.Type == RiskAlertTypeLateGoalBetting {
			found = true
		}
	}
	if !found {
		t.Error("expected late goal betting alert for minute 85")
	}
}

func TestRiskEngine_NoLateGoalForNonFootball(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	engine := NewRiskEngine(tracker, DefaultRiskThresholds(), noopLogger())

	alerts := engine.EvaluateBet("12345678901", "ev-1", "mkt-1", "sel-1", "nba",
		100.00, 2.00, 85)

	for _, a := range alerts {
		if a.Type == RiskAlertTypeLateGoalBetting {
			t.Error("should not generate late goal alert for NBA")
		}
	}
}

func TestRiskEngine_AcknowledgeAlert(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	thresholds := DefaultRiskThresholds()
	thresholds.LargeStakeThreshold = 50.00

	engine := NewRiskEngine(tracker, thresholds, noopLogger())
	alerts := engine.EvaluateBet("12345678901", "ev-1", "mkt-1", "sel-1", "brasileirao",
		100.00, 2.00, 0)

	if len(alerts) == 0 {
		t.Fatal("expected at least one alert")
	}

	if engine.AlertCount() == 0 {
		t.Error("expected unacknowledged alerts")
	}

	engine.AcknowledgeAlert(alerts[0].ID)
	// Count should decrease.
	acknowledged := 0
	for _, a := range engine.GetAlerts(AlertSeverityInfo, 100) {
		if a.Acknowledged {
			acknowledged++
		}
	}
	if acknowledged == 0 {
		t.Error("expected at least one acknowledged alert")
	}
}

func TestRiskEngine_AcknowledgeAlert_NotFound(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	engine := NewRiskEngine(tracker, DefaultRiskThresholds(), noopLogger())

	if engine.AcknowledgeAlert("RISK-999999") {
		t.Error("should return false for nonexistent alert")
	}
}

func TestGetRiskAlerts_Handler(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	engine := NewRiskEngine(tracker, DefaultRiskThresholds(), noopLogger())

	handler := GetRiskAlerts(engine)
	req := httptest.NewRequest(http.MethodGet, "/risk/alerts", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}
}

func TestGetRiskSummary_Handler(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	engine := NewRiskEngine(tracker, DefaultRiskThresholds(), noopLogger())

	handler := GetRiskSummary(engine, tracker)
	req := httptest.NewRequest(http.MethodGet, "/risk/summary", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}

	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body) //nolint:errcheck
	if body["unacknowledged_alerts"] == nil {
		t.Error("expected unacknowledged_alerts in response")
	}
}

func TestRiskAlert_Serialization(t *testing.T) {
	alert := RiskAlert{
		ID:          "RISK-000001",
		Type:        RiskAlertTypeLargeStake,
		Severity:    AlertSeverityWarning,
		EventID:     "ev-001",
		Description: "Large stake test",
		Value:       15000.00,
		Threshold:   10000.00,
		CreatedAt:   time.Now().UTC(),
	}

	data, err := json.Marshal(alert)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var decoded RiskAlert
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if decoded.Type != RiskAlertTypeLargeStake {
		t.Errorf("type = %s, want large_stake", decoded.Type)
	}
}

func TestRiskAlertType_Constants(t *testing.T) {
	types := []RiskAlertType{
		RiskAlertTypeExposureThreshold, RiskAlertTypeSuspiciousPattern,
		RiskAlertTypeRapidBetting, RiskAlertTypeLargeStake,
		RiskAlertTypeConcentratedBets, RiskAlertTypeLateGoalBetting,
		RiskAlertTypeArbitrage,
	}
	for _, at := range types {
		if string(at) == "" {
			t.Error("RiskAlertType constant is empty")
		}
	}
}

func TestAlertSeverity_Constants(t *testing.T) {
	severities := []AlertSeverity{
		AlertSeverityInfo, AlertSeverityWarning, AlertSeverityCritical,
	}
	for _, s := range severities {
		if string(s) == "" {
			t.Error("AlertSeverity constant is empty")
		}
	}
}
