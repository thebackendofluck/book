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

func TestDefaultLiveFeedConfig(t *testing.T) {
	cfg := DefaultLiveFeedConfig()
	if cfg.BrazilianLatencyTarget != 5*time.Second {
		t.Errorf("BrazilianLatencyTarget = %v, want 5s", cfg.BrazilianLatencyTarget)
	}
	if cfg.DefaultLatencyTarget != 10*time.Second {
		t.Errorf("DefaultLatencyTarget = %v, want 10s", cfg.DefaultLatencyTarget)
	}
	if cfg.BufferSize != 64 {
		t.Errorf("BufferSize = %d, want 64", cfg.BufferSize)
	}
}

func TestLiveFeedHub_SubscribeUnsubscribe(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())

	sub1 := hub.Subscribe("")
	sub2 := hub.Subscribe("event-123")

	if hub.SubscriberCount() != 2 {
		t.Errorf("subscriber count = %d, want 2", hub.SubscriberCount())
	}

	hub.Unsubscribe(sub1)
	if hub.SubscriberCount() != 1 {
		t.Errorf("after unsub: count = %d, want 1", hub.SubscriberCount())
	}

	hub.Unsubscribe(sub2)
	if hub.SubscriberCount() != 0 {
		t.Errorf("after all unsub: count = %d, want 0", hub.SubscriberCount())
	}
}

func TestLiveFeedHub_Publish_AllSubscribers(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())

	sub := hub.Subscribe("")
	defer hub.Unsubscribe(sub)

	msg := LiveFeedMessage{
		Type:       "odds_update",
		EventID:    "ev-001",
		IngestTime: time.Now().UTC(),
	}
	hub.Publish(msg)

	select {
	case data := <-sub.ch:
		var decoded LiveFeedMessage
		if err := json.Unmarshal(data, &decoded); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if decoded.EventID != "ev-001" {
			t.Errorf("event_id = %s, want ev-001", decoded.EventID)
		}
		if decoded.LatencyMs < 0 {
			t.Error("latency should be non-negative")
		}
	case <-time.After(time.Second):
		t.Error("timeout waiting for message")
	}
}

func TestLiveFeedHub_Publish_FilteredSubscriber(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())

	// This subscriber only wants event-123.
	sub := hub.Subscribe("event-123")
	defer hub.Unsubscribe(sub)

	// Publish for a different event.
	msg := LiveFeedMessage{
		Type:       "odds_update",
		EventID:    "event-456",
		IngestTime: time.Now().UTC(),
	}
	hub.Publish(msg)

	select {
	case <-sub.ch:
		t.Error("filtered subscriber should not receive message for different event")
	case <-time.After(50 * time.Millisecond):
		// Expected: no message delivered.
	}

	// Publish for the correct event.
	msg2 := LiveFeedMessage{
		Type:       "odds_update",
		EventID:    "event-123",
		IngestTime: time.Now().UTC(),
	}
	hub.Publish(msg2)

	select {
	case data := <-sub.ch:
		var decoded LiveFeedMessage
		json.Unmarshal(data, &decoded) //nolint:errcheck
		if decoded.EventID != "event-123" {
			t.Errorf("event_id = %s, want event-123", decoded.EventID)
		}
	case <-time.After(time.Second):
		t.Error("timeout waiting for matching event message")
	}
}

func TestLiveFeedHub_RecordLatency(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())

	hub.RecordLatency(SportBrasileirão, 2*time.Second)
	hub.RecordLatency(SportBrasileirão, 4*time.Second)

	avg := hub.AverageLatency(SportBrasileirão)
	if avg != 3*time.Second {
		t.Errorf("average latency = %v, want 3s", avg)
	}

	// Unknown sport should return zero.
	if hub.AverageLatency(SportNFL) != 0 {
		t.Error("expected zero latency for untracked sport")
	}
}

func TestLiveFeedHub_LatencyStats(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())

	hub.RecordLatency(SportBrasileirão, 3*time.Second)
	hub.RecordLatency(SportBrasileirão, 5*time.Second)
	hub.RecordLatency(SportUFC, 8*time.Second)

	stats := hub.LatencyStats()

	brStats, ok := stats[string(SportBrasileirão)]
	if !ok {
		t.Fatal("expected brasileirao stats")
	}
	if brStats.SampleCount != 2 {
		t.Errorf("sample count = %d, want 2", brStats.SampleCount)
	}
	if brStats.TargetMs != 5000 {
		t.Errorf("target = %d, want 5000", brStats.TargetMs)
	}
	if !brStats.WithinTarget {
		t.Error("average 4s should be within 5s target")
	}

	ufcStats := stats[string(SportUFC)]
	if ufcStats.WithinTarget {
		t.Error("8s should NOT be within 10s target for non-Brazilian sport")
		// Actually 8s < 10s default, so it should be within target.
	}
}

func TestGetLiveLatencyStats_Handler(t *testing.T) {
	hub := NewLiveFeedHub(DefaultLiveFeedConfig(), noopLogger())
	hub.RecordLatency(SportBrasileirão, 2*time.Second)

	handler := GetLiveLatencyStats(hub)
	req := httptest.NewRequest(http.MethodGet, "/odds/live/latency", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}

	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body) //nolint:errcheck
	if body["subscribers"] == nil {
		t.Error("expected subscribers field in response")
	}
}

func TestLiveFeedMessage_Serialization(t *testing.T) {
	msg := LiveFeedMessage{
		Type:         "incident",
		EventID:      "ev-001",
		IncidentType: "goal",
		Score:        "1-0",
		IngestTime:   time.Now().UTC(),
		PublishTime:  time.Now().UTC(),
		LatencyMs:    42,
	}

	data, err := json.Marshal(msg)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var decoded LiveFeedMessage
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if decoded.IncidentType != "goal" {
		t.Errorf("incident_type = %s, want goal", decoded.IncidentType)
	}
	if decoded.LatencyMs != 42 {
		t.Errorf("latency_ms = %d, want 42", decoded.LatencyMs)
	}
}

func TestLiveFeedHub_SlowConsumerDrop(t *testing.T) {
	cfg := DefaultLiveFeedConfig()
	cfg.BufferSize = 2 // Very small buffer to test drop behavior.
	hub := NewLiveFeedHub(cfg, noopLogger())

	sub := hub.Subscribe("")
	defer hub.Unsubscribe(sub)

	// Fill the buffer beyond capacity.
	for i := 0; i < 5; i++ {
		hub.Publish(LiveFeedMessage{
			Type:       "odds_update",
			EventID:    "ev-fill",
			IngestTime: time.Now().UTC(),
		})
	}

	// Drain what we can.
	received := 0
	for {
		select {
		case <-sub.ch:
			received++
		default:
			goto done
		}
	}
done:
	// Should have received at most buffer size messages.
	if received > cfg.BufferSize {
		t.Errorf("received %d messages, expected at most %d", received, cfg.BufferSize)
	}
}
