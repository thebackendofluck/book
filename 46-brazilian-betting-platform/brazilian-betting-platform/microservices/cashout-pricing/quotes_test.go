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
	"time"
)

func newTestQuoteManager() *QuoteManager {
	logger := testLogger()
	cfg := DefaultPricingConfig()
	engine := NewCashoutPricingEngine(cfg, logger)
	store := NewQuoteStore()
	wallet := NewWalletClient("", logger) // Mock mode
	sigap := NewSIGAPCashoutClient("", "OP-TEST", "", logger)
	return NewQuoteManager(engine, store, wallet, sigap, logger)
}

func testBetSnapshot() BetSnapshot {
	return BetSnapshot{
		ID:              "bet-100",
		CPF:             "123.456.789-09",
		Status:          "accepted",
		Stake:           100.00,
		CombinedOdds:    2.50,
		PotentialReturn: 250.00,
		RemainingStake:  100.00,
		Selections: []SelectionSnapshot{
			{
				EventID:     "evt-001",
				MarketID:    "mkt-001",
				SelectionID: "sel-001",
				OddsAtPlace: 2.50,
				CurrentOdds: 1.80,
				EventState:  "live",
				MarketOpen:  true,
			},
		},
		PlacedAt: time.Now().Add(-1 * time.Hour),
	}
}

func TestGenerateQuote_FullCashout(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, err := qm.GenerateQuote(ctx, bet, 1.0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if quote.Status != QuoteStatusPending {
		t.Errorf("expected status pending, got %s", quote.Status)
	}
	if quote.Type != CashoutTypeFull {
		t.Errorf("expected type full, got %s", quote.Type)
	}
	if quote.CashoutStake != 100.00 {
		t.Errorf("expected cashout stake 100.00, got %.2f", quote.CashoutStake)
	}
	if quote.RemainingStake != 0 {
		t.Errorf("expected remaining stake 0, got %.2f", quote.RemainingStake)
	}
	if quote.OfferedValue <= 0 {
		t.Error("expected positive offered value")
	}
	// testBetSnapshot uses EventState "live", so the shorter LiveTTL applies
	// rather than the 30s pre-match default.
	if quote.TTLSeconds != 3 {
		t.Errorf("expected TTL 3s (live bet), got %d", quote.TTLSeconds)
	}
	if quote.ExpiresAt.Before(time.Now()) {
		t.Error("expected expires_at in the future")
	}
}

func TestGenerateQuote_PartialCashout(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, err := qm.GenerateQuote(ctx, bet, 0.50)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if quote.Type != CashoutTypePartial {
		t.Errorf("expected type partial, got %s", quote.Type)
	}
	if quote.CashoutStake != 50.00 {
		t.Errorf("expected cashout stake 50.00, got %.2f", quote.CashoutStake)
	}
	if quote.RemainingStake != 50.00 {
		t.Errorf("expected remaining stake 50.00, got %.2f", quote.RemainingStake)
	}
}

func TestGenerateQuote_InvalidPartialPercent(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// Below minimum partial percent (10%).
	_, err := qm.GenerateQuote(ctx, bet, 0.05)
	if err == nil {
		t.Error("expected error for partial percent below minimum")
	}

	// Above maximum partial percent (90%).
	_, err = qm.GenerateQuote(ctx, bet, 0.95)
	if err == nil {
		t.Error("expected error for partial percent above maximum")
	}
}

func TestAcceptQuote_Success(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, err := qm.GenerateQuote(ctx, bet, 1.0)
	if err != nil {
		t.Fatalf("generate quote: %v", err)
	}

	resp, err := qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     quote.CPF,
	}, nil)
	if err != nil {
		t.Fatalf("accept quote: %v", err)
	}

	if resp.CreditedAmount != quote.OfferedValue {
		t.Errorf("expected credited %.2f, got %.2f", quote.OfferedValue, resp.CreditedAmount)
	}
	if resp.JournalEntryID == "" {
		t.Error("expected journal entry ID")
	}

	// Verify quote status updated.
	stored, _ := qm.store.GetQuote(ctx, quote.ID)
	if stored.Status != QuoteStatusAccepted {
		t.Errorf("expected stored status accepted, got %s", stored.Status)
	}
}

func TestAcceptQuote_WrongOwner(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, err := qm.GenerateQuote(ctx, bet, 1.0)
	if err != nil {
		t.Fatalf("generate quote: %v", err)
	}

	_, err = qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     "999.999.999-99", // Wrong CPF
	}, nil)
	if err == nil {
		t.Error("expected error for wrong CPF, got nil")
	}
}

func TestAcceptQuote_AlreadyAccepted(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, _ := qm.GenerateQuote(ctx, bet, 1.0)

	// First accept should succeed.
	_, err := qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     quote.CPF,
	}, nil)
	if err != nil {
		t.Fatalf("first accept: %v", err)
	}

	// Second accept should fail.
	_, err = qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     quote.CPF,
	}, nil)
	if err == nil {
		t.Error("expected error on double accept, got nil")
	}
}

func TestAcceptQuote_Expired(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// Generate quote with very short TTL. bet (testBetSnapshot) is live, so
	// LiveTTL is what actually governs this quote's expiry.
	qm.engine.config.LiveTTL = 1
	quote, _ := qm.GenerateQuote(ctx, bet, 1.0)

	// Wait for expiry.
	time.Sleep(1100 * time.Millisecond)

	_, err := qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     quote.CPF,
	}, nil)
	if err == nil {
		t.Error("expected error for expired quote, got nil")
	}

	// Verify stored as expired.
	stored, _ := qm.store.GetQuote(ctx, quote.ID)
	if stored.Status != QuoteStatusExpired {
		t.Errorf("expected stored status expired, got %s", stored.Status)
	}
}

// TestAcceptQuote_StalePrice_Rejected proves that a re-price gate at accept
// time rejects a quote whose OfferedValue has drifted from the bet's current
// fair value by more than the tolerance, instead of silently crediting the
// stale amount.
func TestAcceptQuote_StalePrice_Rejected(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, err := qm.GenerateQuote(ctx, bet, 1.0)
	if err != nil {
		t.Fatalf("generate quote: %v", err)
	}

	// Simulate the market moving sharply in the player's favor between quote
	// generation and acceptance (current odds shorten from 1.80 to 1.10).
	staleBet := bet
	staleBet.Selections = append([]SelectionSnapshot{}, bet.Selections...)
	staleBet.Selections[0].CurrentOdds = 1.10

	_, err = qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     quote.CPF,
	}, &staleBet)
	if err == nil {
		t.Fatal("expected stale quote to be rejected on re-price, got nil error")
	}

	// A rejected accept must not have credited the wallet or changed status.
	stored, _ := qm.store.GetQuote(ctx, quote.ID)
	if stored.Status != QuoteStatusPending {
		t.Errorf("rejected accept must leave quote pending, got %s", stored.Status)
	}
}

// TestAcceptQuote_FreshPrice_Accepted proves the re-price gate does not
// block an accept when the current fair value still matches the offer.
func TestAcceptQuote_FreshPrice_Accepted(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	quote, err := qm.GenerateQuote(ctx, bet, 1.0)
	if err != nil {
		t.Fatalf("generate quote: %v", err)
	}

	// currentBet mirrors bet exactly: no price movement.
	_, err = qm.AcceptQuote(ctx, AcceptQuoteRequest{
		QuoteID: quote.ID,
		BetID:   quote.BetID,
		CPF:     quote.CPF,
	}, &bet)
	if err != nil {
		t.Fatalf("expected accept to succeed with unchanged odds, got error: %v", err)
	}
}

// TestGenerateQuote_CumulativePartialCapExceeded proves that a sequence of
// partial cashouts cannot cumulatively exceed MaxPartialPct of the bet's
// original stake, even when each individual request is within the
// per-request Min/MaxPartialPct bounds.
func TestGenerateQuote_CumulativePartialCapExceeded(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// Simulate R$85 already cashed out via prior partial cashouts (85% of
	// the R$100 original stake), leaving only R$5 of headroom under the
	// default 90% cap. A new 10% partial (R$10) would push the cumulative
	// total to R$95, past the cap, and must be rejected.
	if err := qm.store.SaveJournalEntry(ctx, &CashoutJournalEntry{
		ID:        "jnl-prior-over",
		BetID:     bet.ID,
		Type:      "partial_cashout_credit",
		Amount:    85.00,
		CreatedAt: time.Now(),
	}); err != nil {
		t.Fatalf("seed journal entry: %v", err)
	}

	_, err := qm.GenerateQuote(ctx, bet, 0.10)
	if err == nil {
		t.Fatal("expected cumulative partial cashout cap to reject the quote, got nil error")
	}
}

// TestGenerateQuote_CumulativePartialCapWithinLimit proves the cap does not
// block a partial cashout that stays within the cumulative limit.
func TestGenerateQuote_CumulativePartialCapWithinLimit(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// R$50 already cashed out leaves plenty of headroom under the 90% cap;
	// a new 10% partial (R$10) totals R$60, well within bounds.
	if err := qm.store.SaveJournalEntry(ctx, &CashoutJournalEntry{
		ID:        "jnl-prior-under",
		BetID:     bet.ID,
		Type:      "partial_cashout_credit",
		Amount:    50.00,
		CreatedAt: time.Now(),
	}); err != nil {
		t.Fatalf("seed journal entry: %v", err)
	}

	_, err := qm.GenerateQuote(ctx, bet, 0.10)
	if err != nil {
		t.Fatalf("expected quote within cap to succeed, got error: %v", err)
	}
}

func TestInvalidatePendingQuotes(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// Generate multiple quotes.
	q1, _ := qm.GenerateQuote(ctx, bet, 1.0)
	q2, _ := qm.GenerateQuote(ctx, bet, 0.50)

	count, err := qm.InvalidatePendingQuotesForBet(ctx, bet.ID, "odds_change")
	if err != nil {
		t.Fatalf("invalidate: %v", err)
	}
	if count != 2 {
		t.Errorf("expected 2 invalidated, got %d", count)
	}

	// Verify statuses.
	stored1, _ := qm.store.GetQuote(ctx, q1.ID)
	if stored1.Status != QuoteStatusInvalidated {
		t.Errorf("q1: expected invalidated, got %s", stored1.Status)
	}
	stored2, _ := qm.store.GetQuote(ctx, q2.ID)
	if stored2.Status != QuoteStatusInvalidated {
		t.Errorf("q2: expected invalidated, got %s", stored2.Status)
	}
}

func TestExpireStaleQuotes(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// Short TTL. bet (testBetSnapshot) is live, so LiveTTL governs.
	qm.engine.config.LiveTTL = 1
	qm.GenerateQuote(ctx, bet, 1.0)

	time.Sleep(1100 * time.Millisecond)

	count, err := qm.ExpireStaleQuotes(ctx)
	if err != nil {
		t.Fatalf("expire: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 expired, got %d", count)
	}
}

func TestQuoteStore_JournalEntries(t *testing.T) {
	store := NewQuoteStore()
	ctx := context.Background()

	entry := &CashoutJournalEntry{
		ID:            "jnl-001",
		QuoteID:       "q-001",
		BetID:         "bet-001",
		CPF:           "123.456.789-09",
		Type:          "cashout_credit",
		Amount:        131.94,
		BalanceBefore: 5000.00,
		BalanceAfter:  5131.94,
		Reference:     "cashout:full:q-001",
		CreatedAt:     time.Now(),
	}

	if err := store.SaveJournalEntry(ctx, entry); err != nil {
		t.Fatalf("save journal: %v", err)
	}

	entries, err := store.GetJournalEntriesForBet(ctx, "bet-001")
	if err != nil {
		t.Fatalf("get journals: %v", err)
	}
	if len(entries) != 1 {
		t.Fatalf("expected 1 entry, got %d", len(entries))
	}
	if entries[0].Amount != 131.94 {
		t.Errorf("expected amount 131.94, got %.2f", entries[0].Amount)
	}
}

func TestGenerateQuote_DefaultsToFull(t *testing.T) {
	qm := newTestQuoteManager()
	ctx := context.Background()
	bet := testBetSnapshot()

	// Passing 0 should default to full.
	quote, err := qm.GenerateQuote(ctx, bet, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if quote.Type != CashoutTypeFull {
		t.Errorf("expected full cashout when percent=0, got %s", quote.Type)
	}
}

func TestPartialCashoutSplit(t *testing.T) {
	logger := testLogger()
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), logger)
	pm := NewPartialCashoutManager(engine, logger)

	bet := testBetSnapshot()

	result, err := pm.CalculatePartialSplit(bet, 0.50)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.CashoutStake != 50.00 {
		t.Errorf("expected cashout stake 50.00, got %.2f", result.CashoutStake)
	}
	if result.RemainingStake != 50.00 {
		t.Errorf("expected remaining stake 50.00, got %.2f", result.RemainingStake)
	}
	if result.CashoutValue <= 0 {
		t.Error("expected positive cashout value")
	}
	if result.RemainingReturn <= 0 {
		t.Error("expected positive remaining return")
	}

	// Remaining return should be proportional.
	expectedRemReturn := 50.00 * 2.50
	if result.RemainingReturn != expectedRemReturn {
		t.Errorf("expected remaining return %.2f, got %.2f", expectedRemReturn, result.RemainingReturn)
	}
}

func TestPartialCashoutSplit_InvalidPercent(t *testing.T) {
	logger := testLogger()
	engine := NewCashoutPricingEngine(DefaultPricingConfig(), logger)
	pm := NewPartialCashoutManager(engine, logger)
	bet := testBetSnapshot()

	// 0% not valid for partial.
	_, err := pm.CalculatePartialSplit(bet, 0)
	if err == nil {
		t.Error("expected error for 0%")
	}

	// 100% not valid for partial.
	_, err = pm.CalculatePartialSplit(bet, 1.0)
	if err == nil {
		t.Error("expected error for 100%")
	}

	// Below minimum.
	_, err = pm.CalculatePartialSplit(bet, 0.05)
	if err == nil {
		t.Error("expected error below minimum partial")
	}
}

func TestMaskCPF(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"123.456.789-09", "123.***.***-09"},
		{"12345678909", "123******09"},
		{"abc", "abc"},
		{"", ""},
	}

	for _, tt := range tests {
		got := maskCPF(tt.input)
		if got != tt.expected {
			t.Errorf("maskCPF(%q) = %q, want %q", tt.input, got, tt.expected)
		}
	}
}
