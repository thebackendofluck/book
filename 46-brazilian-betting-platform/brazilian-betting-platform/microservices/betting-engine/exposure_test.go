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

// noopLogger and nopWriter are defined in betting_test.go.

func TestDefaultSportExposureLimits(t *testing.T) {
	limits := DefaultSportExposureLimits()

	if _, ok := limits["brasileirao"]; !ok {
		t.Error("expected brasileirao limits")
	}
	if _, ok := limits["default"]; !ok {
		t.Error("expected default limits")
	}

	br := limits["brasileirao"]
	if br.MaxEventExposure != 500000.00 {
		t.Errorf("brasileirao MaxEventExposure = %.2f, want 500000", br.MaxEventExposure)
	}
	if br.MaxSelectionExposure != 100000.00 {
		t.Errorf("brasileirao MaxSelectionExposure = %.2f, want 100000", br.MaxSelectionExposure)
	}
}

func TestExposureTracker_RecordBet(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())

	tracker.RecordBet("ev-1", "mkt-1", "sel-1",
		"Flamengo v Palmeiras", "Match Winner", "Flamengo", "brasileirao",
		100.00, 2.50)

	bucket := tracker.GetBucket("ev-1", "mkt-1", "sel-1")
	if bucket == nil {
		t.Fatal("expected bucket, got nil")
	}
	if bucket.TotalStake != 100.00 {
		t.Errorf("TotalStake = %.2f, want 100.00", bucket.TotalStake)
	}
	if bucket.MaxLiability != 250.00 {
		t.Errorf("MaxLiability = %.2f, want 250.00", bucket.MaxLiability)
	}
	if bucket.BetCount != 1 {
		t.Errorf("BetCount = %d, want 1", bucket.BetCount)
	}
	if bucket.Level != ExposureLevelNormal {
		t.Errorf("Level = %s, want normal for small bet", bucket.Level)
	}
}

func TestExposureTracker_MultipleBets(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())

	// Place 3 bets on same selection.
	for i := 0; i < 3; i++ {
		tracker.RecordBet("ev-1", "mkt-1", "sel-1",
			"Test Event", "Test Market", "Selection", "brasileirao",
			5000.00, 3.00)
	}

	bucket := tracker.GetBucket("ev-1", "mkt-1", "sel-1")
	if bucket.TotalStake != 15000.00 {
		t.Errorf("TotalStake = %.2f, want 15000.00", bucket.TotalStake)
	}
	if bucket.MaxLiability != 45000.00 {
		t.Errorf("MaxLiability = %.2f, want 45000.00 (3 * 5000 * 3.0)", bucket.MaxLiability)
	}
	if bucket.BetCount != 3 {
		t.Errorf("BetCount = %d, want 3", bucket.BetCount)
	}
}

func TestExposureTracker_LevelClassification(t *testing.T) {
	limits := map[string]SportExposureLimit{
		"default": {
			MaxSelectionExposure: 1000.00,
			MaxEventExposure:     5000.00,
			MaxMarketExposure:    2000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
	}
	tracker := NewExposureTracker(limits, noopLogger())

	// Normal: 200 liability / 1000 max = 20%
	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "", "", "", "default", 100.00, 2.00)
	bucket := tracker.GetBucket("ev-1", "mkt-1", "sel-1")
	if bucket.Level != ExposureLevelNormal {
		t.Errorf("Level = %s, want normal at 20%%", bucket.Level)
	}

	// Elevated: 200 + 400 = 600 liability / 1000 = 60%
	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "", "", "", "default", 200.00, 2.00)
	bucket = tracker.GetBucket("ev-1", "mkt-1", "sel-1")
	if bucket.Level != ExposureLevelElevated {
		t.Errorf("Level = %s, want elevated at 60%%", bucket.Level)
	}

	// High: 600 + 200 = 800 / 1000 = 80%
	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "", "", "", "default", 100.00, 2.00)
	bucket = tracker.GetBucket("ev-1", "mkt-1", "sel-1")
	if bucket.Level != ExposureLevelHigh {
		t.Errorf("Level = %s, want high at 80%%", bucket.Level)
	}
}

func TestExposureTracker_GetEventExposure(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())

	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "Event", "Market1", "Sel1", "brasileirao", 1000.00, 2.00)
	tracker.RecordBet("ev-1", "mkt-2", "sel-2", "Event", "Market2", "Sel2", "brasileirao", 500.00, 3.00)

	summary := tracker.GetEventExposure("ev-1")
	if summary.TotalStake != 1500.00 {
		t.Errorf("TotalStake = %.2f, want 1500.00", summary.TotalStake)
	}
	if summary.TotalLiability != 3500.00 {
		t.Errorf("TotalLiability = %.2f, want 3500.00 (2000+1500)", summary.TotalLiability)
	}
	if summary.BetCount != 2 {
		t.Errorf("BetCount = %d, want 2", summary.BetCount)
	}
	if len(summary.Markets) != 2 {
		t.Errorf("expected 2 markets, got %d", len(summary.Markets))
	}
}

func TestExposureTracker_GetBucket_NotFound(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	bucket := tracker.GetBucket("nope", "nope", "nope")
	if bucket != nil {
		t.Error("expected nil for nonexistent bucket")
	}
}

func TestExposureTracker_CheckBetExposure_WithinLimits(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())

	err := tracker.CheckBetExposure("ev-1", "mkt-1", "sel-1", "brasileirao", 100.00, 2.00)
	if err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

func TestExposureTracker_CheckBetExposure_SelectionLimitExceeded(t *testing.T) {
	limits := map[string]SportExposureLimit{
		"default": {
			MaxSelectionExposure: 500.00,
			MaxEventExposure:     5000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
	}
	tracker := NewExposureTracker(limits, noopLogger())

	// First bet fills up to 400 liability.
	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "", "", "", "default", 200.00, 2.00)

	// Second bet would push to 600 > 500 limit.
	err := tracker.CheckBetExposure("ev-1", "mkt-1", "sel-1", "default", 100.00, 2.00)
	if err == nil {
		t.Error("expected exposure limit error, got nil")
	}
}

func TestExposureTracker_CheckBetExposure_EventLimitExceeded(t *testing.T) {
	limits := map[string]SportExposureLimit{
		"default": {
			MaxSelectionExposure: 10000.00,
			MaxEventExposure:     500.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
	}
	tracker := NewExposureTracker(limits, noopLogger())

	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "", "", "", "default", 200.00, 2.00)

	// Event liability is now 400. Adding 200 * 2 = 400 more => 800 > 500.
	err := tracker.CheckBetExposure("ev-1", "mkt-1", "sel-2", "default", 200.00, 2.00)
	if err == nil {
		t.Error("expected event limit error, got nil")
	}
}

func TestExposureTracker_GetAllBuckets(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "", "", "", "default", 100.00, 2.00)
	tracker.RecordBet("ev-2", "mkt-2", "sel-2", "", "", "", "default", 200.00, 3.00)

	buckets := tracker.GetAllBuckets()
	if len(buckets) != 2 {
		t.Errorf("expected 2 buckets, got %d", len(buckets))
	}
}

func TestExposureTracker_GetBucketsAboveLevel(t *testing.T) {
	limits := map[string]SportExposureLimit{
		"default": {
			MaxSelectionExposure: 100.00,
			MaxEventExposure:     1000.00,
			ElevatedThreshold:    0.50,
			HighThreshold:        0.75,
			CriticalThreshold:    0.90,
		},
	}
	tracker := NewExposureTracker(limits, noopLogger())

	// Normal: 20/100 = 20%
	tracker.RecordBet("ev-1", "mkt-1", "sel-normal", "", "", "", "default", 10.00, 2.00)
	// High: 80/100 = 80%
	tracker.RecordBet("ev-2", "mkt-2", "sel-high", "", "", "", "default", 40.00, 2.00)

	elevated := tracker.GetBucketsAboveLevel(ExposureLevelElevated)
	if len(elevated) != 1 {
		t.Errorf("expected 1 elevated+ bucket, got %d", len(elevated))
	}
}

func TestExposureBucket_Serialization(t *testing.T) {
	bucket := ExposureBucket{
		EventID:      "ev-1",
		MarketID:     "mkt-1",
		SelectionID:  "sel-1",
		TotalStake:   1000.00,
		MaxLiability: 2500.00,
		Level:        ExposureLevelElevated,
		UpdatedAt:    time.Now().UTC(),
	}

	data, err := json.Marshal(bucket)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var decoded ExposureBucket
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if decoded.Level != ExposureLevelElevated {
		t.Errorf("level = %s, want elevated", decoded.Level)
	}
}

func TestGetExposureDashboard_Handler(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	tracker.RecordBet("ev-1", "mkt-1", "sel-1", "Event", "Market", "Sel", "brasileirao", 100.00, 2.00)

	handler := GetExposureDashboard(tracker)
	req := httptest.NewRequest(http.MethodGet, "/exposure/dashboard", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}
}

func TestCheckExposureHandler_Allowed(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	handler := CheckExposureHandler(tracker)

	body := `{"event_id":"ev-1","market_id":"mkt-1","selection_id":"sel-1","sport":"brasileirao","stake":100,"odds":2.0}`
	req := httptest.NewRequest(http.MethodPost, "/exposure/check", strings.NewReader(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}

	var resp map[string]any
	json.NewDecoder(rr.Body).Decode(&resp) //nolint:errcheck
	if resp["allowed"] != true {
		t.Error("expected allowed=true for small bet")
	}
}

func TestCheckExposureHandler_InvalidBody(t *testing.T) {
	tracker := NewExposureTracker(DefaultSportExposureLimits(), noopLogger())
	handler := CheckExposureHandler(tracker)

	req := httptest.NewRequest(http.MethodPost, "/exposure/check", strings.NewReader("invalid"))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestExposureLevel_Constants(t *testing.T) {
	levels := []ExposureLevel{
		ExposureLevelNormal, ExposureLevelElevated,
		ExposureLevelHigh, ExposureLevelCritical,
	}
	for _, l := range levels {
		if string(l) == "" {
			t.Error("ExposureLevel constant is empty")
		}
	}
}
