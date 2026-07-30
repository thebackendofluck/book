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

// FootballBuilderTemplates returns predefined popular bet builder combinations
// for football. These templates drive the UX on the event detail page, showing
// players common "quick pick" bundles.
//
// Templates are ordered by popularity based on Brazilian market data.
func FootballBuilderTemplates() []BuilderTemplate {
	return []BuilderTemplate{
		{
			ID:          "fb-home-over25-btts",
			Name:        "Home Win + Over 2.5 Goals + BTTS",
			Description: "Back the home team to win in a high-scoring game where both teams find the net",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketMatch1X2, MarketTotalGoals, MarketBTTS},
			Popular:     true,
		},
		{
			ID:          "fb-result-over25",
			Name:        "Match Result + Over 2.5 Goals",
			Description: "Pick the winner in a match with 3 or more goals",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketMatch1X2, MarketTotalGoals},
			Popular:     true,
		},
		{
			ID:          "fb-result-btts",
			Name:        "Match Result + BTTS",
			Description: "Pick the winner and whether both teams score",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketMatch1X2, MarketBTTS},
			Popular:     true,
		},
		{
			ID:          "fb-btts-over25",
			Name:        "BTTS + Over 2.5 Goals",
			Description: "Both teams score in a high-scoring match",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketBTTS, MarketTotalGoals},
			Popular:     true,
		},
		{
			ID:          "fb-result-ht-ft",
			Name:        "Half Time Result + Full Time Result",
			Description: "Pick the leader at half time and the final winner",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketFirstHalf1X2, MarketMatch1X2},
			Popular:     true,
		},
		{
			ID:          "fb-home-btts-ht",
			Name:        "Home Win + BTTS + First Half Result",
			Description: "Home team wins, both score, with a first half prediction",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketMatch1X2, MarketBTTS, MarketFirstHalf1X2},
			Popular:     false,
		},
		{
			ID:          "fb-result-over25-ht",
			Name:        "Match Result + Over 2.5 + First Half Result",
			Description: "Full match prediction with goals and half-time call",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketMatch1X2, MarketTotalGoals, MarketFirstHalf1X2},
			Popular:     false,
		},
		{
			ID:          "fb-btts-over25-ht",
			Name:        "BTTS + Over 2.5 + First Half Result",
			Description: "Both teams score in a high-scoring match with half-time prediction",
			Sport:       SportFootball,
			Markets:     []MarketType{MarketBTTS, MarketTotalGoals, MarketFirstHalf1X2},
			Popular:     false,
		},
	}
}

// GetTemplateByID returns a template by its ID, or nil if not found.
func GetTemplateByID(id string) *BuilderTemplate {
	for _, t := range FootballBuilderTemplates() {
		if t.ID == id {
			return &t
		}
	}
	return nil
}

// GetPopularTemplates returns only the templates marked as popular.
func GetPopularTemplates(sport Sport) []BuilderTemplate {
	var popular []BuilderTemplate
	var all []BuilderTemplate

	switch sport {
	case SportFootball:
		all = FootballBuilderTemplates()
	default:
		return popular
	}

	for _, t := range all {
		if t.Popular {
			popular = append(popular, t)
		}
	}
	return popular
}

// GetAllTemplates returns all templates for a sport.
func GetAllTemplates(sport Sport) []BuilderTemplate {
	switch sport {
	case SportFootball:
		return FootballBuilderTemplates()
	default:
		return nil
	}
}
