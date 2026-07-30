// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//go:build integration

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/go-chi/chi/v5"
)

// Integration tests exercise the full HTTP cycle for /cashout/quote with
// realistic BetSnapshot payloads. They wire QuoteManager + CashoutPricingEngine
// + QuoteStore through a chi router (same composition main() uses) and bypass
// the fetchBetSnapshot mock by serializing the bet directly on the request.
//
// Run with: go test -tags=integration ./...

// quoteRequestWithBet is the request envelope used by the integration test
// handler. It carries the full BetSnapshot inline so each test can drive a
// distinct cashout scenario without depending on the production fetcher.
type quoteRequestWithBet struct {
	Bet            BetSnapshot `json:"bet"`
	CashoutPercent float64     `json:"cashout_percent,omitempty"`
}

// integrationTestServer builds a router whose POST /cashout/quote consumes the
// bet directly from the request body, hitting the same QuoteManager pipeline
// the production handler uses.
func integrationTestServer(t *testing.T) *httptest.Server {
	t.Helper()

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelError}))
	cfg := DefaultPricingConfig()
	engine := NewCashoutPricingEngine(cfg, logger)
	store := NewQuoteStore()
	// Wallet and SIGAP clients are nil-safe for the GenerateQuote path
	// because the integration tests do not exercise AcceptQuote (which is
	// the only consumer of those dependencies). Passing nils keeps the
	// test isolated to the pricing/cap behaviour.
	qm := NewQuoteManager(engine, store, nil, nil, logger)

	r := chi.NewRouter()
	r.Post("/cashout/quote", func(w http.ResponseWriter, r *http.Request) {
		var req quoteRequestWithBet
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
			return
		}
		quote, err := qm.GenerateQuote(context.Background(), req.Bet, req.CashoutPercent)
		if err != nil {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, quote)
	})
	r.Get("/health", HealthCheck())

	return httptest.NewServer(r)
}

// postQuote is a thin helper that POSTs a quote request and decodes the response.
func postQuote(t *testing.T, srv *httptest.Server, body quoteRequestWithBet) (int, map[string]any) {
	t.Helper()
	buf, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	resp, err := http.Post(srv.URL+"/cashout/quote", "application/json", bytes.NewReader(buf))
	if err != nil {
		t.Fatalf("POST /cashout/quote: %v", err)
	}
	defer resp.Body.Close()
	var out map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return resp.StatusCode, out
}

// TestQuote_HealthEndpoint sanity check that the test server boots and the
// health route responds.
func TestQuote_HealthEndpoint(t *testing.T) {
	srv := integrationTestServer(t)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
}

// TestQuote_LiveBet_ExtremeFavorite_CapsAtPotentialReturn covers the headline
// scenario the PotentialReturn cap was introduced for: a bet whose live odds
// have collapsed so far in the player's favour that the raw cashout formula
// would offer more than the bet could ever pay out.
//
// Construction: stake R$100, original combined odds 2.00, deliberately low
// PotentialReturn R$110 (legacy rounding artefact or reduced PR). Current
// odds 1.10 ⇒ raw = 100 * (1/1.10)/(1/2.00) * 0.95 = R$172.73, which exceeds
// PR. The cap must clamp the offer at R$110.
func TestQuote_LiveBet_ExtremeFavorite_CapsAtPotentialReturn(t *testing.T) {
	srv := integrationTestServer(t)
	defer srv.Close()

	bet := BetSnapshot{
		ID:              "bet-int-cap-1",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 110.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-live-1",
				MarketID:    "mkt-1x2",
				SelectionID: "sel-home",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.10,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	status, body := postQuote(t, srv, quoteRequestWithBet{Bet: bet, CashoutPercent: 1.0})
	if status != http.StatusOK {
		t.Fatalf("expected 200, got %d (body=%v)", status, body)
	}
	offered, ok := body["offered_value"].(float64)
	if !ok {
		t.Fatalf("offered_value missing or non-numeric: %v", body["offered_value"])
	}
	if offered != bet.PotentialReturn {
		t.Errorf("expected cashout capped at PotentialReturn %.2f, got %.2f", bet.PotentialReturn, offered)
	}
	if offered > bet.PotentialReturn {
		t.Errorf("cashout %.2f exceeded PotentialReturn %.2f — cap not engaged", offered, bet.PotentialReturn)
	}
}

// TestQuote_LiveBet_HeavyDog_NoCap covers the symmetric scenario: the player's
// pick has drifted heavily against them (odds blew out from 2.00 to 5.00).
// The formula produces a small cashout offer well below PotentialReturn, so
// the cap must NOT engage and the player must receive the raw value.
func TestQuote_LiveBet_HeavyDog_NoCap(t *testing.T) {
	srv := integrationTestServer(t)
	defer srv.Close()

	bet := BetSnapshot{
		ID:              "bet-int-dog",
		CPF:             "555.666.777-88",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 200.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-live-2",
				MarketID:    "mkt-1x2",
				SelectionID: "sel-away",
				OddsAtPlace: 2.00,
				CurrentOdds: 5.00,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	status, body := postQuote(t, srv, quoteRequestWithBet{Bet: bet, CashoutPercent: 1.0})
	if status != http.StatusOK {
		t.Fatalf("expected 200, got %d (body=%v)", status, body)
	}
	offered := body["offered_value"].(float64)

	// Raw formula: 100 * (1/5.00)/(1/2.00) * 0.95 = 100 * 0.4 * 0.95 = 38.00
	expected := 100.0 * (1.0 / 5.0) / (1.0 / 2.0) * 0.95
	expected = math.Round(expected*100) / 100
	if math.Abs(offered-expected) > 0.01 {
		t.Errorf("expected raw cashout %.2f (no cap), got %.2f", expected, offered)
	}
	if offered >= bet.PotentialReturn {
		t.Errorf("dog scenario should never produce cashout >= PotentialReturn (%.2f vs %.2f)", offered, bet.PotentialReturn)
	}
}

// TestQuote_BetWithZeroPotentialReturn_LegacyData_BypassesCap covers backward
// compatibility: legacy bet records persisted before PotentialReturn was a
// tracked field arrive with PR == 0. The cap must skip those bets so they
// price normally; otherwise every legacy bet would be capped to zero and
// rejected by the MinCashoutBRL gate.
func TestQuote_BetWithZeroPotentialReturn_LegacyData_BypassesCap(t *testing.T) {
	srv := integrationTestServer(t)
	defer srv.Close()

	bet := BetSnapshot{
		ID:              "bet-int-legacy",
		CPF:             "111.222.333-44",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.00,
		PotentialReturn: 0, // Sentinel for legacy data
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-live-3",
				MarketID:    "mkt-1x2",
				SelectionID: "sel-home",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.10,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	status, body := postQuote(t, srv, quoteRequestWithBet{Bet: bet, CashoutPercent: 1.0})
	if status != http.StatusOK {
		t.Fatalf("expected 200, got %d (body=%v)", status, body)
	}
	offered := body["offered_value"].(float64)
	if offered == 0 {
		t.Fatalf("legacy bet with PR==0 was zeroed out — backward compatibility broken")
	}

	// Raw formula: 100 * (1/1.10)/(1/2.00) * 0.95 = ~172.73
	expected := 100.0 * (1.0 / 1.10) / (1.0 / 2.00) * 0.95
	expected = math.Round(expected*100) / 100
	if math.Abs(offered-expected) > 0.01 {
		t.Errorf("expected uncapped cashout %.2f for legacy PR==0, got %.2f", expected, offered)
	}
}

// TestQuote_AccumulatorPartialCashout_CapHonored exercises the cap on a
// partial cashout of a 2-leg accumulator. Partial cashouts scale the stake
// down before pricing, so the cap must still operate on the partial offer.
// Here we ask for 50% cashout of an accumulator with a contrived low PR;
// without the cap the partial offer would exceed half the (already low) PR.
func TestQuote_AccumulatorPartialCashout_CapHonored(t *testing.T) {
	srv := integrationTestServer(t)
	defer srv.Close()

	// 2-leg accumulator: original combined odds 1.2*1.5 = 1.80 ⇒ PR=180 if
	// fairly bookkept; we use 115 to deliberately tighten the cap.
	bet := BetSnapshot{
		ID:              "bet-int-acc-partial",
		CPF:             "999.888.777-66",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    1.80,
		PotentialReturn: 115.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-acc-1",
				MarketID:    "mkt-ml",
				SelectionID: "sel-a",
				OddsAtPlace: 1.20,
				CurrentOdds: 1.05,
				EventState:  "live",
				MarketOpen:  true,
			},
			{
				EventID:     "evt-acc-2",
				MarketID:    "mkt-ml",
				SelectionID: "sel-b",
				OddsAtPlace: 1.50,
				CurrentOdds: 1.10,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	// Request 50% partial cashout.
	status, body := postQuote(t, srv, quoteRequestWithBet{Bet: bet, CashoutPercent: 0.50})
	if status != http.StatusOK {
		t.Fatalf("expected 200, got %d (body=%v)", status, body)
	}
	offered := body["offered_value"].(float64)
	// The cap should clamp at PR even though only half the stake is being
	// cashed out — the implementation caps the final value against PR
	// regardless of partial scaling.
	if offered > bet.PotentialReturn {
		t.Errorf("partial cashout %.2f exceeded PotentialReturn %.2f", offered, bet.PotentialReturn)
	}
}

// TestQuote_IneligibleStatus_ReturnsError covers the negative path through the
// HTTP layer: a settled bet must produce a 422, not a quote.
func TestQuote_IneligibleStatus_ReturnsError(t *testing.T) {
	srv := integrationTestServer(t)
	defer srv.Close()

	bet := BetSnapshot{
		ID:              "bet-int-settled",
		CPF:             "000.000.000-00",
		Status:          "settled",
		Stake:           50.00,
		CombinedOdds:    2.00,
		PotentialReturn: 100.00,
		RemainingStake:  50.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-done",
				SelectionID: "sel-x",
				OddsAtPlace: 2.00,
				CurrentOdds: 1.50,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
	}

	status, body := postQuote(t, srv, quoteRequestWithBet{Bet: bet, CashoutPercent: 1.0})
	if status != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422 for settled bet, got %d (body=%v)", status, body)
	}
}
