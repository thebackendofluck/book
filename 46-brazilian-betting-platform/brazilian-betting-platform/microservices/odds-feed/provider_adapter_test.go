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
	"context"
	"testing"
)

func TestBetradarAdapter_Name(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	if adapter.Name() != ProviderBetradar {
		t.Errorf("name = %s, want %s", adapter.Name(), ProviderBetradar)
	}
}

func TestBetradarAdapter_Health(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	health := adapter.Health()

	if health.Provider != ProviderBetradar {
		t.Errorf("provider = %s, want betradar", health.Provider)
	}
	if health.Status != ProviderStatusActive {
		t.Errorf("status = %s, want active", health.Status)
	}
	if health.EventCount == 0 {
		t.Error("expected non-zero event count after seeding")
	}
}

func TestBetradarAdapter_FetchCatalogue_Brasileirao(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	events, err := adapter.FetchCatalogue(context.Background(), SportBrasileirão)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected Brasileirao events from adapter")
	}
	for _, ev := range events {
		if ev.Sport != SportBrasileirão {
			t.Errorf("event sport = %s, want %s", ev.Sport, SportBrasileirão)
		}
		if len(ev.Markets) == 0 {
			t.Errorf("event %s has no markets", ev.ID)
		}
	}
}

func TestBetradarAdapter_FetchCatalogue_Libertadores(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	events, err := adapter.FetchCatalogue(context.Background(), SportLibertadores)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected Libertadores events from adapter")
	}
}

func TestBetradarAdapter_FetchCatalogue_CopaBrasil(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	events, err := adapter.FetchCatalogue(context.Background(), SportCopaBrasil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected Copa do Brasil events from adapter")
	}
}

func TestBetradarAdapter_FetchEvent(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())

	// Get an event ID from the catalogue first.
	events, _ := adapter.FetchCatalogue(context.Background(), SportBrasileirão)
	if len(events) == 0 {
		t.Skip("no events available")
	}

	ev, err := adapter.FetchEvent(context.Background(), events[0].ID)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev == nil {
		t.Fatal("expected event, got nil")
	}
	if ev.ID != events[0].ID {
		t.Errorf("event ID mismatch: got %s, want %s", ev.ID, events[0].ID)
	}
}

func TestBetradarAdapter_FetchEvent_NotFound(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	_, err := adapter.FetchEvent(context.Background(), "nonexistent-id")
	if err == nil {
		t.Error("expected error for nonexistent event")
	}
}

func TestBetradarAdapter_FetchLiveOdds(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	updates, err := adapter.FetchLiveOdds(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// There should be live events seeded in the adapter.
	// Updates might be empty if no live events have open markets.
	for _, u := range updates {
		if u.NewOdds < 1.01 {
			t.Errorf("odds %.4f below minimum", u.NewOdds)
		}
		if u.Source != string(ProviderBetradar) {
			t.Errorf("source = %s, want betradar", u.Source)
		}
	}
}

func TestBetradarAdapter_MarketTypes(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	events, _ := adapter.FetchCatalogue(context.Background(), SportBrasileirão)
	if len(events) == 0 {
		t.Skip("no events available")
	}

	ev := events[0]
	marketTypes := make(map[MarketType]bool)
	for _, m := range ev.Markets {
		marketTypes[m.Type] = true
	}

	// Expect at least 1X2 and Over/Under markets.
	if !marketTypes[MarketTypeMatchWinner] {
		t.Error("expected match_winner market type")
	}
	if !marketTypes[MarketTypeOverUnder] {
		t.Error("expected over_under market type")
	}
	if !marketTypes[MarketTypeBothTeamScore] {
		t.Error("expected both_teams_score market type")
	}
	if !marketTypes[MarketTypeDoubleChance] {
		t.Error("expected double_chance market type")
	}
}

func TestProviderRegistry_FetchCatalogue(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	registry := NewProviderRegistry(adapter, nil, noopLogger())

	events, err := registry.FetchCatalogue(context.Background(), SportBrasileirão)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected events from registry")
	}
}

func TestProviderRegistry_HealthAll(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	registry := NewProviderRegistry(adapter, nil, noopLogger())

	health := registry.HealthAll()
	if len(health) == 0 {
		t.Error("expected health entries")
	}
	if health[0].Provider != ProviderBetradar {
		t.Errorf("provider = %s, want betradar", health[0].Provider)
	}
}

func TestProviderRegistry_FetchEvent(t *testing.T) {
	adapter := NewBetradarAdapter(noopLogger())
	registry := NewProviderRegistry(adapter, nil, noopLogger())

	events, _ := adapter.FetchCatalogue(context.Background(), SportBrasileirão)
	if len(events) == 0 {
		t.Skip("no events available")
	}

	ev, err := registry.FetchEvent(context.Background(), events[0].ID)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev == nil {
		t.Fatal("expected event")
	}
}

func TestProviderStatusConstants(t *testing.T) {
	statuses := []ProviderStatus{ProviderStatusActive, ProviderStatusDegraded, ProviderStatusDown}
	for _, s := range statuses {
		if string(s) == "" {
			t.Error("provider status constant is empty")
		}
	}
}
