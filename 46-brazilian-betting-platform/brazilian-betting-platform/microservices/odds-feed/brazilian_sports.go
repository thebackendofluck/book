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
	"time"

	"github.com/google/uuid"
)

// BrazilianSportsConfig defines priority handling and enrichment for Brazilian competitions.
// These competitions receive priority feed updates and featured event promotion.
var BrazilianSportsConfig = map[Sport]CompetitionConfig{
	SportBrasileirão: {
		Name:           "Brasileirão Série A",
		Country:        "Brasil",
		Priority:       1, // Highest priority
		FeaturedOdds:   true,
		UpdateInterval: 10, // seconds between odds updates (live)
	},
	SportCopaBrasil: {
		Name:           "Copa do Brasil",
		Country:        "Brasil",
		Priority:       2,
		FeaturedOdds:   true,
		UpdateInterval: 10,
	},
	SportLibertadores: {
		Name:           "CONMEBOL Libertadores",
		Country:        "South America",
		Priority:       2,
		FeaturedOdds:   true,
		UpdateInterval: 10,
	},
	SportSulAmericana: {
		Name:           "CONMEBOL Sul-Americana",
		Country:        "South America",
		Priority:       3,
		FeaturedOdds:   false,
		UpdateInterval: 15,
	},
	SportUFC: {
		Name:           "UFC",
		Country:        "USA",
		Priority:       3,
		FeaturedOdds:   true,
		UpdateInterval: 30,
	},
	SportNBA: {
		Name:           "NBA",
		Country:        "USA",
		Priority:       4,
		FeaturedOdds:   false,
		UpdateInterval: 30,
	},
}

// CompetitionConfig holds metadata and scheduling for a sport/competition.
type CompetitionConfig struct {
	Name           string
	Country        string
	Priority       int // 1 = highest
	FeaturedOdds   bool
	UpdateInterval int // seconds
}

// IsBrazilianCompetition returns true if the sport is a Brazilian domestic competition.
func IsBrazilianCompetition(sport Sport) bool {
	_, ok := BrazilianSportsConfig[sport]
	return ok
}

// ShouldFeatureEvent returns true if an event should be promoted to the featured section.
func ShouldFeatureEvent(sport Sport) bool {
	cfg, ok := BrazilianSportsConfig[sport]
	if !ok {
		return false
	}
	return cfg.FeaturedOdds
}

// GetCompetitionName returns the display name for a sport.
func GetCompetitionName(sport Sport) string {
	if cfg, ok := BrazilianSportsConfig[sport]; ok {
		return cfg.Name
	}
	return string(sport)
}

// SortedBrazilianSports returns sports ordered by priority (lowest number = highest priority).
func SortedBrazilianSports() []Sport {
	return []Sport{
		SportBrasileirão,
		SportCopaBrasil,
		SportLibertadores,
		SportSulAmericana,
		SportUFC,
		SportNBA,
	}
}

// BuildBrasileiraoFixture returns a sample Brasileirão match event for seeding/testing.
func BuildBrasileiraoFixture(homeTeam, awayTeam string, startTime time.Time) Event {
	eventID := uuid.NewString()

	matchWinnerMarket := Market{
		ID:       uuid.NewString(),
		Type:     MarketTypeMatchWinner,
		Name:     "Resultado Final",
		IsOpen:   true,
		IsInPlay: false,
		Selections: []Selection{
			{ID: uuid.NewString(), Name: homeTeam, Odds: 2.10, IsActive: true},
			{ID: uuid.NewString(), Name: "Empate", Odds: 3.20, IsActive: true},
			{ID: uuid.NewString(), Name: awayTeam, Odds: 3.50, IsActive: true},
		},
		UpdatedAt: time.Now().UTC(),
	}

	overUnderMarket := Market{
		ID:       uuid.NewString(),
		Type:     MarketTypeOverUnder,
		Name:     "Total de Gols",
		IsOpen:   true,
		IsInPlay: false,
		Selections: []Selection{
			{ID: uuid.NewString(), Name: "Mais de 2.5", Odds: 1.85, IsActive: true},
			{ID: uuid.NewString(), Name: "Menos de 2.5", Odds: 1.95, IsActive: true},
		},
		UpdatedAt: time.Now().UTC(),
	}

	return Event{
		ID:          eventID,
		Sport:       SportBrasileirão,
		Competition: GetCompetitionName(SportBrasileirão),
		HomeTeam:    homeTeam,
		AwayTeam:    awayTeam,
		Status:      EventStatusScheduled,
		StartTime:   startTime,
		Markets:     []Market{matchWinnerMarket, overUnderMarket},
		IsFeatured:  true,
		UpdatedAt:   time.Now().UTC(),
	}
}

// BrasileiraoTeams is the list of 20 clubs in Brasileirão Série A 2024.
var BrasileiraoTeams = []string{
	"Flamengo", "Fluminense", "Palmeiras", "São Paulo",
	"Corinthians", "Santos", "Vasco da Gama", "Botafogo",
	"Atlético Mineiro", "Cruzeiro", "América Mineiro", "Internacional",
	"Grêmio", "Athletico Paranaense", "Cuiabá", "Goiás",
	"Fortaleza", "Ceará", "Bahia", "Coritiba",
}
