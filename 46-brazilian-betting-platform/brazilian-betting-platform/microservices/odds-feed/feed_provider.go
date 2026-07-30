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
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"time"
)

// FeedProvider abstracts an external odds data provider.
type FeedProvider interface {
	FetchEvent(ctx context.Context, eventID string) (*Event, error)
	FetchLiveEvents(ctx context.Context) ([]Event, error)
	FetchEventsBySport(ctx context.Context, sport Sport) ([]Event, error)
}

// ExternalFeedClient integrates with a real external odds API.
// The mock implementation is used in development and tests.
type ExternalFeedClient struct {
	httpClient *http.Client
	baseURL    string
	apiKey     string
	logger     *slog.Logger
}

// NewExternalFeedClient creates an ExternalFeedClient.
func NewExternalFeedClient(baseURL, apiKey string, logger *slog.Logger) *ExternalFeedClient {
	return &ExternalFeedClient{
		httpClient: &http.Client{Timeout: 10 * time.Second},
		baseURL:    baseURL,
		apiKey:     apiKey,
		logger:     logger,
	}
}

// FetchEvent retrieves a single event from the external feed.
func (c *ExternalFeedClient) FetchEvent(ctx context.Context, eventID string) (*Event, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		fmt.Sprintf("%s/events/%s", c.baseURL, eventID), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-API-Key", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetch event: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("feed provider returned HTTP %d", resp.StatusCode)
	}

	var event Event
	if err := json.NewDecoder(resp.Body).Decode(&event); err != nil {
		return nil, fmt.Errorf("decode event: %w", err)
	}
	return &event, nil
}

// FetchLiveEvents retrieves all currently live events.
func (c *ExternalFeedClient) FetchLiveEvents(ctx context.Context) ([]Event, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		c.baseURL+"/events/live", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-API-Key", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var events []Event
	if err := json.NewDecoder(resp.Body).Decode(&events); err != nil {
		return nil, err
	}
	return events, nil
}

// FetchEventsBySport retrieves events for a specific sport.
func (c *ExternalFeedClient) FetchEventsBySport(ctx context.Context, sport Sport) ([]Event, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		fmt.Sprintf("%s/events/sport/%s", c.baseURL, sport), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-API-Key", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var events []Event
	if err := json.NewDecoder(resp.Body).Decode(&events); err != nil {
		return nil, err
	}
	return events, nil
}

// MockFeedProvider is an in-memory feed provider used in tests and development.
type MockFeedProvider struct {
	events map[string]*Event
	logger *slog.Logger
}

// NewMockFeedProvider creates a MockFeedProvider seeded with Brazilian sports fixtures.
func NewMockFeedProvider(logger *slog.Logger) *MockFeedProvider {
	m := &MockFeedProvider{
		events: make(map[string]*Event),
		logger: logger,
	}
	m.seed()
	return m
}

// seed populates the mock with representative fixtures for development.
func (m *MockFeedProvider) seed() {
	now := time.Now().UTC()

	// Brasileirão fixtures.
	fixtures := []struct{ home, away string }{
		{"Flamengo", "Palmeiras"},
		{"São Paulo", "Corinthians"},
		{"Atlético Mineiro", "Grêmio"},
		{"Fluminense", "Internacional"},
	}
	for i, f := range fixtures {
		startTime := now.Add(time.Duration(i+1) * 2 * time.Hour)
		event := BuildBrasileiraoFixture(f.home, f.away, startTime)
		m.events[event.ID] = &event
	}

	// Copa do Brasil fixture.
	copaEvent := buildGenericEvent(SportCopaBrasil, "Copa do Brasil", "Cruzeiro", "Santos", now.Add(6*time.Hour))
	m.events[copaEvent.ID] = &copaEvent

	// Libertadores fixture.
	libEvent := buildGenericEvent(SportLibertadores, "CONMEBOL Libertadores", "Flamengo", "River Plate", now.Add(8*time.Hour))
	m.events[libEvent.ID] = &libEvent

	// UFC event.
	ufcEvent := buildUFCEvent(now.Add(12 * time.Hour))
	m.events[ufcEvent.ID] = &ufcEvent
}

// FetchEvent returns a mocked event.
func (m *MockFeedProvider) FetchEvent(_ context.Context, eventID string) (*Event, error) {
	ev, ok := m.events[eventID]
	if !ok {
		return nil, fmt.Errorf("event %s not found in mock feed", eventID)
	}
	return ev, nil
}

// FetchLiveEvents returns all mock events with status Live.
func (m *MockFeedProvider) FetchLiveEvents(_ context.Context) ([]Event, error) {
	var live []Event
	for _, ev := range m.events {
		if ev.Status == EventStatusLive {
			live = append(live, *ev)
		}
	}
	return live, nil
}

// FetchEventsBySport returns events for the given sport from the mock.
func (m *MockFeedProvider) FetchEventsBySport(_ context.Context, sport Sport) ([]Event, error) {
	var result []Event
	for _, ev := range m.events {
		if ev.Sport == sport {
			result = append(result, *ev)
		}
	}
	return result, nil
}

// SimulateLiveOddsFluctuation randomly adjusts one selection's odds in a mock event.
// Used by the SSE handler to push realistic odds updates during development.
func (m *MockFeedProvider) SimulateLiveOddsFluctuation() *OddsUpdate {
	// Pick a random event with markets.
	for _, ev := range m.events {
		if len(ev.Markets) == 0 || len(ev.Markets[0].Selections) == 0 {
			continue
		}
		market := ev.Markets[0]
		sel := market.Selections[rand.Intn(len(market.Selections))]

		// Fluctuate odds by ±5%.
		change := (rand.Float64()*0.10 - 0.05)
		newOdds := roundTo2DP(sel.Odds * (1 + change))
		if newOdds < 1.01 {
			newOdds = 1.01
		}

		return &OddsUpdate{
			EventID:     ev.ID,
			MarketID:    market.ID,
			SelectionID: sel.ID,
			NewOdds:     newOdds,
			Source:      "mock",
			UpdatedAt:   time.Now().UTC(),
		}
	}
	return nil
}

func buildGenericEvent(sport Sport, competition, homeTeam, awayTeam string, startTime time.Time) Event {
	return Event{
		ID:          fmt.Sprintf("ev-%s-%d", sport, time.Now().UnixNano()),
		Sport:       sport,
		Competition: competition,
		HomeTeam:    homeTeam,
		AwayTeam:    awayTeam,
		Status:      EventStatusScheduled,
		StartTime:   startTime,
		IsFeatured:  ShouldFeatureEvent(sport),
		Markets: []Market{
			{
				ID:   fmt.Sprintf("mkt-%d", time.Now().UnixNano()),
				Type: MarketTypeMatchWinner,
				Name: "Resultado Final",
				Selections: []Selection{
					{ID: fmt.Sprintf("sel-%d-1", time.Now().UnixNano()), Name: homeTeam, Odds: 2.20, IsActive: true},
					{ID: fmt.Sprintf("sel-%d-2", time.Now().UnixNano()), Name: "Empate", Odds: 3.10, IsActive: true},
					{ID: fmt.Sprintf("sel-%d-3", time.Now().UnixNano()), Name: awayTeam, Odds: 3.40, IsActive: true},
				},
				IsOpen:    true,
				UpdatedAt: time.Now().UTC(),
			},
		},
		UpdatedAt: time.Now().UTC(),
	}
}

func buildUFCEvent(startTime time.Time) Event {
	return Event{
		ID:          fmt.Sprintf("ev-ufc-%d", time.Now().UnixNano()),
		Sport:       SportUFC,
		Competition: "UFC",
		HomeTeam:    "Fighter A",
		AwayTeam:    "Fighter B",
		Status:      EventStatusScheduled,
		StartTime:   startTime,
		IsFeatured:  true,
		Markets: []Market{
			{
				ID:   fmt.Sprintf("mkt-ufc-%d", time.Now().UnixNano()),
				Type: MarketTypeMatchWinner,
				Name: "Vencedor da Luta",
				Selections: []Selection{
					{ID: fmt.Sprintf("sel-ufc-1-%d", time.Now().UnixNano()), Name: "Fighter A", Odds: 1.65, IsActive: true},
					{ID: fmt.Sprintf("sel-ufc-2-%d", time.Now().UnixNano()), Name: "Fighter B", Odds: 2.20, IsActive: true},
				},
				IsOpen:    true,
				UpdatedAt: time.Now().UTC(),
			},
		},
		UpdatedAt: time.Now().UTC(),
	}
}

func roundTo2DP(v float64) float64 {
	return float64(int(v*100+0.5)) / 100
}
