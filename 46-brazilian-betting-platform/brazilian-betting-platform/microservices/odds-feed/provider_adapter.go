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
	"fmt"
	"log/slog"
	"math/rand"
	"sync"
	"time"

	"github.com/google/uuid"
)

// ProviderName identifies an odds feed provider.
type ProviderName string

const (
	ProviderBetradar     ProviderName = "betradar"
	ProviderBetConstruct ProviderName = "betconstruct"
	ProviderMock         ProviderName = "mock"
)

// ProviderStatus tracks the health state of a feed provider.
type ProviderStatus string

const (
	ProviderStatusActive   ProviderStatus = "active"
	ProviderStatusDegraded ProviderStatus = "degraded"
	ProviderStatusDown     ProviderStatus = "down"
)

// ProviderHealth holds real-time health metrics for a provider.
type ProviderHealth struct {
	Provider   ProviderName   `json:"provider"`
	Status     ProviderStatus `json:"status"`
	LastUpdate time.Time      `json:"last_update"`
	Latency    time.Duration  `json:"latency_ms"`
	ErrorCount int            `json:"error_count"`
	EventCount int            `json:"event_count"`
	StaleCount int            `json:"stale_count"`
}

// ProviderAdapter is the canonical interface for any odds feed provider.
// All provider implementations must normalize their data into the internal
// Event/Market/Selection schema before returning.
type ProviderAdapter interface {
	// Name returns the provider identifier.
	Name() ProviderName

	// FetchCatalogue returns the full event catalogue for a sport.
	FetchCatalogue(ctx context.Context, sport Sport) ([]Event, error)

	// FetchEvent returns a single normalized event by provider-specific ID.
	FetchEvent(ctx context.Context, eventID string) (*Event, error)

	// FetchLiveOdds returns current odds for all live events.
	FetchLiveOdds(ctx context.Context) ([]OddsUpdate, error)

	// Health returns the current provider health status.
	Health() ProviderHealth
}

// ProviderRegistry manages multiple provider adapters with failover.
type ProviderRegistry struct {
	mu        sync.RWMutex
	primary   ProviderAdapter
	fallback  ProviderAdapter
	providers map[ProviderName]ProviderAdapter
	logger    *slog.Logger
}

// NewProviderRegistry creates a registry with a primary and optional fallback adapter.
func NewProviderRegistry(primary ProviderAdapter, fallback ProviderAdapter, logger *slog.Logger) *ProviderRegistry {
	reg := &ProviderRegistry{
		primary:   primary,
		fallback:  fallback,
		providers: make(map[ProviderName]ProviderAdapter),
		logger:    logger,
	}
	reg.providers[primary.Name()] = primary
	if fallback != nil {
		reg.providers[fallback.Name()] = fallback
	}
	return reg
}

// FetchCatalogue tries the primary provider, falling back if unavailable.
func (r *ProviderRegistry) FetchCatalogue(ctx context.Context, sport Sport) ([]Event, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	events, err := r.primary.FetchCatalogue(ctx, sport)
	if err == nil {
		return events, nil
	}

	r.logger.Warn("primary provider failed, trying fallback",
		"primary", r.primary.Name(),
		"error", err,
	)

	if r.fallback != nil {
		return r.fallback.FetchCatalogue(ctx, sport)
	}
	return nil, fmt.Errorf("all providers failed for sport %s: %w", sport, err)
}

// FetchEvent tries providers in order.
func (r *ProviderRegistry) FetchEvent(ctx context.Context, eventID string) (*Event, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	event, err := r.primary.FetchEvent(ctx, eventID)
	if err == nil {
		return event, nil
	}
	if r.fallback != nil {
		return r.fallback.FetchEvent(ctx, eventID)
	}
	return nil, fmt.Errorf("event %s not available from any provider: %w", eventID, err)
}

// HealthAll returns health status for all registered providers.
func (r *ProviderRegistry) HealthAll() []ProviderHealth {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var health []ProviderHealth
	for _, p := range r.providers {
		health = append(health, p.Health())
	}
	return health
}

// BetradarAdapter is a mock Betradar feed adapter that generates realistic
// Brazilian football odds. In production this would connect to the Betradar
// Unified Odds Feed API.
type BetradarAdapter struct {
	mu         sync.RWMutex
	events     map[string]*Event
	lastFetch  time.Time
	errorCount int
	logger     *slog.Logger
}

// NewBetradarAdapter creates a Betradar mock adapter pre-seeded with
// Brazilian football fixtures.
func NewBetradarAdapter(logger *slog.Logger) *BetradarAdapter {
	adapter := &BetradarAdapter{
		events: make(map[string]*Event),
		logger: logger,
	}
	adapter.seedBrazilianFixtures()
	return adapter
}

func (a *BetradarAdapter) Name() ProviderName {
	return ProviderBetradar
}

func (a *BetradarAdapter) Health() ProviderHealth {
	a.mu.RLock()
	defer a.mu.RUnlock()
	status := ProviderStatusActive
	if a.errorCount > 5 {
		status = ProviderStatusDegraded
	}
	if a.errorCount > 20 {
		status = ProviderStatusDown
	}
	return ProviderHealth{
		Provider:   ProviderBetradar,
		Status:     status,
		LastUpdate: a.lastFetch,
		EventCount: len(a.events),
	}
}

func (a *BetradarAdapter) FetchCatalogue(ctx context.Context, sport Sport) ([]Event, error) {
	a.mu.Lock()
	a.lastFetch = time.Now().UTC()
	a.mu.Unlock()

	a.mu.RLock()
	defer a.mu.RUnlock()

	var events []Event
	for _, ev := range a.events {
		if ev.Sport == sport {
			events = append(events, *ev)
		}
	}
	return events, nil
}

func (a *BetradarAdapter) FetchEvent(ctx context.Context, eventID string) (*Event, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()

	ev, ok := a.events[eventID]
	if !ok {
		return nil, fmt.Errorf("betradar: event %s not found", eventID)
	}
	return ev, nil
}

func (a *BetradarAdapter) FetchLiveOdds(ctx context.Context) ([]OddsUpdate, error) {
	a.mu.Lock()
	defer a.mu.Unlock()

	a.lastFetch = time.Now().UTC()
	var updates []OddsUpdate

	for _, ev := range a.events {
		if ev.Status != EventStatusLive {
			continue
		}
		for _, mkt := range ev.Markets {
			if !mkt.IsOpen {
				continue
			}
			for _, sel := range mkt.Selections {
				if !sel.IsActive {
					continue
				}
				// Simulate small odds drift.
				drift := (rand.Float64()*0.06 - 0.03)
				newOdds := roundTo2DP(sel.Odds * (1 + drift))
				if newOdds < 1.01 {
					newOdds = 1.01
				}
				updates = append(updates, OddsUpdate{
					EventID:     ev.ID,
					MarketID:    mkt.ID,
					SelectionID: sel.ID,
					NewOdds:     newOdds,
					Source:      string(ProviderBetradar),
					UpdatedAt:   time.Now().UTC(),
				})
			}
		}
	}
	return updates, nil
}

// seedBrazilianFixtures populates realistic Brasileirao, Copa do Brasil,
// Libertadores, and Sul-Americana fixtures.
func (a *BetradarAdapter) seedBrazilianFixtures() {
	now := time.Now().UTC()

	// Brasileirao Serie A - Round 15 fixtures
	brasileiraoFixtures := []struct {
		home, away string
		hours      int
		live       bool
		homeScore  int
		awayScore  int
		minute     int
	}{
		{"Flamengo", "Palmeiras", 2, false, 0, 0, 0},
		{"Sao Paulo", "Corinthians", 4, false, 0, 0, 0},
		{"Atletico Mineiro", "Gremio", 0, true, 1, 0, 35},
		{"Fluminense", "Internacional", 0, true, 0, 1, 62},
		{"Botafogo", "Bahia", 6, false, 0, 0, 0},
		{"Fortaleza", "Cruzeiro", 8, false, 0, 0, 0},
		{"Vasco da Gama", "Santos", 10, false, 0, 0, 0},
		{"Athletico Paranaense", "Goias", 12, false, 0, 0, 0},
	}

	for _, f := range brasileiraoFixtures {
		ev := a.buildFootballEvent(
			SportBrasileirão, "Brasileirao Serie A",
			f.home, f.away,
			now.Add(time.Duration(f.hours)*time.Hour),
			f.live, f.homeScore, f.awayScore, f.minute,
		)
		a.events[ev.ID] = &ev
	}

	// Copa do Brasil - Quarter-finals
	copaFixtures := []struct{ home, away string }{
		{"Cruzeiro", "Santos"},
		{"Flamengo", "Sao Paulo"},
	}
	for i, f := range copaFixtures {
		ev := a.buildFootballEvent(
			SportCopaBrasil, "Copa do Brasil",
			f.home, f.away,
			now.Add(time.Duration(24+i*2)*time.Hour),
			false, 0, 0, 0,
		)
		a.events[ev.ID] = &ev
	}

	// Libertadores - Group stage
	libertadoresFixtures := []struct{ home, away string }{
		{"Flamengo", "River Plate"},
		{"Palmeiras", "Boca Juniors"},
		{"Atletico Mineiro", "Penarol"},
	}
	for i, f := range libertadoresFixtures {
		ev := a.buildFootballEvent(
			SportLibertadores, "CONMEBOL Libertadores",
			f.home, f.away,
			now.Add(time.Duration(48+i*2)*time.Hour),
			false, 0, 0, 0,
		)
		a.events[ev.ID] = &ev
	}

	// Sul-Americana
	ev := a.buildFootballEvent(
		SportSulAmericana, "CONMEBOL Sul-Americana",
		"Fortaleza", "LDU Quito",
		now.Add(72*time.Hour),
		false, 0, 0, 0,
	)
	a.events[ev.ID] = &ev
}

// buildFootballEvent creates a fully normalized football event with
// canonical market templates (1X2, Over/Under, Both Teams Score, Double Chance).
func (a *BetradarAdapter) buildFootballEvent(
	sport Sport, competition, home, away string,
	startTime time.Time,
	isLive bool, homeScore, awayScore, minute int,
) Event {
	eventID := uuid.NewString()
	now := time.Now().UTC()

	status := EventStatusScheduled
	if isLive {
		status = EventStatusLive
	}

	// Generate realistic odds based on team strengths (simplified model).
	homeOdds := 1.80 + rand.Float64()*1.2
	drawOdds := 2.80 + rand.Float64()*0.8
	awayOdds := 2.50 + rand.Float64()*2.0

	markets := []Market{
		// 1X2 (Match Winner)
		{
			ID:   uuid.NewString(),
			Type: MarketTypeMatchWinner,
			Name: "Resultado Final",
			Selections: []Selection{
				{ID: uuid.NewString(), Name: home, Odds: roundTo2DP(homeOdds), IsActive: true},
				{ID: uuid.NewString(), Name: "Empate", Odds: roundTo2DP(drawOdds), IsActive: true},
				{ID: uuid.NewString(), Name: away, Odds: roundTo2DP(awayOdds), IsActive: true},
			},
			IsOpen:    true,
			IsInPlay:  isLive,
			UpdatedAt: now,
		},
		// Over/Under 2.5
		{
			ID:   uuid.NewString(),
			Type: MarketTypeOverUnder,
			Name: "Total de Gols - Mais/Menos 2.5",
			Selections: []Selection{
				{ID: uuid.NewString(), Name: "Mais de 2.5", Odds: roundTo2DP(1.70 + rand.Float64()*0.4), IsActive: true},
				{ID: uuid.NewString(), Name: "Menos de 2.5", Odds: roundTo2DP(1.90 + rand.Float64()*0.3), IsActive: true},
			},
			IsOpen:    true,
			IsInPlay:  isLive,
			UpdatedAt: now,
		},
		// Both Teams to Score
		{
			ID:   uuid.NewString(),
			Type: MarketTypeBothTeamScore,
			Name: "Ambas Marcam",
			Selections: []Selection{
				{ID: uuid.NewString(), Name: "Sim", Odds: roundTo2DP(1.65 + rand.Float64()*0.3), IsActive: true},
				{ID: uuid.NewString(), Name: "Nao", Odds: roundTo2DP(2.00 + rand.Float64()*0.3), IsActive: true},
			},
			IsOpen:    true,
			IsInPlay:  isLive,
			UpdatedAt: now,
		},
		// Double Chance
		{
			ID:   uuid.NewString(),
			Type: MarketTypeDoubleChance,
			Name: "Dupla Chance",
			Selections: []Selection{
				{ID: uuid.NewString(), Name: home + " ou Empate", Odds: roundTo2DP(1.20 + rand.Float64()*0.3), IsActive: true},
				{ID: uuid.NewString(), Name: home + " ou " + away, Odds: roundTo2DP(1.15 + rand.Float64()*0.2), IsActive: true},
				{ID: uuid.NewString(), Name: away + " ou Empate", Odds: roundTo2DP(1.30 + rand.Float64()*0.4), IsActive: true},
			},
			IsOpen:    true,
			IsInPlay:  isLive,
			UpdatedAt: now,
		},
		// Half-Time Result
		{
			ID:   uuid.NewString(),
			Type: MarketTypeHalfTime,
			Name: "Resultado Primeiro Tempo",
			Selections: []Selection{
				{ID: uuid.NewString(), Name: home, Odds: roundTo2DP(2.30 + rand.Float64()*0.8), IsActive: true},
				{ID: uuid.NewString(), Name: "Empate", Odds: roundTo2DP(2.00 + rand.Float64()*0.4), IsActive: true},
				{ID: uuid.NewString(), Name: away, Odds: roundTo2DP(3.00 + rand.Float64()*1.5), IsActive: true},
			},
			IsOpen:    true,
			IsInPlay:  isLive,
			UpdatedAt: now,
		},
	}

	return Event{
		ID:           eventID,
		Sport:        sport,
		Competition:  competition,
		HomeTeam:     home,
		AwayTeam:     away,
		Status:       status,
		StartTime:    startTime,
		InPlayMinute: minute,
		HomeScore:    homeScore,
		AwayScore:    awayScore,
		Markets:      markets,
		IsFeatured:   ShouldFeatureEvent(sport),
		UpdatedAt:    now,
	}
}
