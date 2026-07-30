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
	"math"
	"sync"
	"time"

	"github.com/google/uuid"
)

// cashoutRepriceTolerance is the maximum fractional deviation allowed
// between a quote's OfferedValue and the bet's fair value at accept time.
// Beyond this the accept is rejected rather than paying out a stale price.
const cashoutRepriceTolerance = 0.01 // 1%

// QuoteManager handles the lifecycle of cashout quotes: generation,
// acceptance, expiry, and invalidation.
type QuoteManager struct {
	engine *CashoutPricingEngine
	store  *QuoteStore
	wallet *WalletClient
	sigap  *SIGAPCashoutClient
	logger *slog.Logger
}

// NewQuoteManager constructs a QuoteManager with all dependencies.
func NewQuoteManager(engine *CashoutPricingEngine, store *QuoteStore, wallet *WalletClient, sigap *SIGAPCashoutClient, logger *slog.Logger) *QuoteManager {
	return &QuoteManager{
		engine: engine,
		store:  store,
		wallet: wallet,
		sigap:  sigap,
		logger: logger,
	}
}

// GenerateQuote creates a new cashout quote for the given bet.
// If cashoutPercent is 0 or 1.0, a full cashout is offered.
// Otherwise, a partial cashout is generated for the requested portion.
func (qm *QuoteManager) GenerateQuote(ctx context.Context, bet BetSnapshot, cashoutPercent float64) (*CashoutQuote, error) {
	// Default to full cashout.
	if cashoutPercent <= 0 || cashoutPercent > 1.0 {
		cashoutPercent = 1.0
	}

	// Validate partial cashout bounds.
	cfg := qm.engine.config
	if cashoutPercent < 1.0 {
		if cashoutPercent < cfg.MinPartialPct {
			return nil, fmt.Errorf("partial cashout %.0f%% below minimum %.0f%%", cashoutPercent*100, cfg.MinPartialPct*100)
		}
		if cashoutPercent > cfg.MaxPartialPct {
			return nil, fmt.Errorf("partial cashout %.0f%% above maximum %.0f%%", cashoutPercent*100, cfg.MaxPartialPct*100)
		}
	}

	// Calculate the stake portion to cash out.
	cashoutStake := bet.RemainingStake * cashoutPercent

	// Enforce the cumulative partial-cashout cap across the bet's full
	// history (not just this one request): a series of partials that each
	// individually satisfy MinPartialPct/MaxPartialPct could otherwise still
	// drain the stake well beyond MaxPartialPct of the original amount.
	if cashoutPercent < 1.0 {
		if err := checkCumulativePartialCap(ctx, bet, qm.store, cashoutStake, cfg); err != nil {
			return nil, err
		}
	}

	// Price the cashout.
	result, err := qm.engine.CalculateCashoutValue(bet, cashoutStake)
	if err != nil {
		return nil, fmt.Errorf("pricing calculation: %w", err)
	}
	if !result.Eligible {
		return nil, fmt.Errorf("bet not eligible for cashout: %s", result.IneligibleReason)
	}

	// Determine cashout type.
	cashoutType := CashoutTypeFull
	if cashoutPercent < 1.0 {
		cashoutType = CashoutTypePartial
	}

	now := time.Now().UTC()
	// Live markets move too fast for a 30s pre-match TTL to be safe: a
	// quote left outstanding that long risks being accepted well after the
	// market has moved. Selections still in a live event get a much shorter
	// window; pre-match bets keep the longer default.
	ttl := cfg.DefaultTTL
	if ttl <= 0 {
		ttl = 30
	}
	if betHasLiveSelection(bet) {
		ttl = cfg.LiveTTL
		if ttl <= 0 {
			ttl = 3
		}
	}

	// Compute current combined odds from selections.
	currentOdds := 1.0
	for _, sel := range bet.Selections {
		currentOdds *= sel.CurrentOdds
	}

	quote := &CashoutQuote{
		ID:              uuid.New().String(),
		BetID:           bet.ID,
		CPF:             bet.CPF,
		Status:          QuoteStatusPending,
		Type:            cashoutType,
		OriginalStake:   bet.Stake,
		CashoutStake:    cashoutStake,
		RemainingStake:  bet.RemainingStake - cashoutStake,
		OfferedValue:    result.CashoutValue,
		OperatorMargin:  result.MarginApplied,
		OriginalImplied: result.OriginalImplied,
		CurrentImplied:  result.CurrentImplied,
		CombinedOdds:    bet.CombinedOdds,
		CurrentOdds:     currentOdds,
		TTLSeconds:      ttl,
		ExpiresAt:       now.Add(time.Duration(ttl) * time.Second),
		CreatedAt:       now,
	}

	// Persist the quote.
	if err := qm.store.SaveQuote(ctx, quote); err != nil {
		return nil, fmt.Errorf("save quote: %w", err)
	}

	qm.logger.Info("cashout quote generated",
		"quote_id", quote.ID,
		"bet_id", quote.BetID,
		"type", quote.Type,
		"offered_value", quote.OfferedValue,
		"ttl", quote.TTLSeconds,
		"expires_at", quote.ExpiresAt,
	)

	return quote, nil
}

// AcceptQuote validates and accepts an outstanding cashout quote.
// On success, credits the player wallet and creates a journal entry.
//
// currentBet, when non-nil, is a freshly fetched snapshot of the bet's
// current odds, used to re-price the quote immediately before crediting.
// This closes the window between quote generation and acceptance during
// which the market can move: if the quote's OfferedValue has drifted from
// the current fair value by more than cashoutRepriceTolerance, the accept is
// rejected instead of silently paying out the stale amount. Pass nil to
// skip re-pricing (e.g. when a fresh snapshot could not be obtained).
func (qm *QuoteManager) AcceptQuote(ctx context.Context, req AcceptQuoteRequest, currentBet *BetSnapshot) (*AcceptQuoteResponse, error) {
	quote, err := qm.store.GetQuote(ctx, req.QuoteID)
	if err != nil {
		return nil, fmt.Errorf("get quote: %w", err)
	}

	// Validate ownership.
	if quote.BetID != req.BetID || quote.CPF != req.CPF {
		return nil, fmt.Errorf("quote %s does not belong to bet %s / cpf %s", req.QuoteID, req.BetID, req.CPF)
	}

	// Validate status.
	if quote.Status != QuoteStatusPending {
		return nil, fmt.Errorf("quote %s has status %q, cannot accept", req.QuoteID, quote.Status)
	}

	// Check expiry.
	now := time.Now().UTC()
	if now.After(quote.ExpiresAt) {
		// Mark as expired.
		quote.Status = QuoteStatusExpired
		_ = qm.store.UpdateQuoteStatus(ctx, quote.ID, QuoteStatusExpired, "ttl_expired")
		return nil, fmt.Errorf("quote %s expired at %s", req.QuoteID, quote.ExpiresAt.Format(time.RFC3339))
	}

	// Re-price against current market conditions before crediting. TTL
	// alone doesn't guarantee the offer is still fair — re-check it here.
	if currentBet != nil {
		fresh, err := qm.engine.CalculateCashoutValue(*currentBet, quote.CashoutStake)
		if err != nil {
			return nil, fmt.Errorf("re-price quote %s: %w", quote.ID, err)
		}
		if fresh.Eligible && quote.OfferedValue > 0 {
			deviation := math.Abs(fresh.CashoutValue-quote.OfferedValue) / quote.OfferedValue
			if deviation > cashoutRepriceTolerance {
				return nil, fmt.Errorf(
					"quote %s is stale: offered R$%.2f, current fair value R$%.2f (%.2f%% deviation exceeds %.0f%% tolerance); request a new quote",
					quote.ID, quote.OfferedValue, fresh.CashoutValue, deviation*100, cashoutRepriceTolerance*100,
				)
			}
		}
	}

	// Credit the player wallet.
	creditResp, err := qm.wallet.CreditCashout(ctx, CashoutCreditRequest{
		CPF:     quote.CPF,
		BetID:   quote.BetID,
		QuoteID: quote.ID,
		Amount:  quote.OfferedValue,
	})
	if err != nil {
		return nil, fmt.Errorf("wallet credit: %w", err)
	}

	// Mark quote as accepted.
	acceptedAt := time.Now().UTC()
	quote.Status = QuoteStatusAccepted
	quote.AcceptedAt = &acceptedAt

	if err := qm.store.UpdateQuoteAccepted(ctx, quote); err != nil {
		qm.logger.Error("failed to mark quote accepted after wallet credit",
			"quote_id", quote.ID,
			"error", err,
		)
		// The wallet was already credited; log for manual reconciliation.
	}

	// Create journal entry.
	entry := &CashoutJournalEntry{
		ID:            uuid.New().String(),
		QuoteID:       quote.ID,
		BetID:         quote.BetID,
		CPF:           quote.CPF,
		Type:          journalTypeForCashout(quote.Type),
		Amount:        quote.OfferedValue,
		BalanceBefore: creditResp.BalanceBefore,
		BalanceAfter:  creditResp.BalanceAfter,
		Reference:     fmt.Sprintf("cashout:%s:%s", quote.Type, quote.ID),
		CreatedAt:     acceptedAt,
	}
	if err := qm.store.SaveJournalEntry(ctx, entry); err != nil {
		qm.logger.Error("failed to save journal entry", "quote_id", quote.ID, "error", err)
	}

	// Report to SIGAP asynchronously.
	go func() {
		sigapCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := qm.sigap.ReportCashout(sigapCtx, quote); err != nil {
			qm.logger.Error("sigap cashout report failed", "quote_id", quote.ID, "error", err)
		}
	}()

	qm.logger.Info("cashout quote accepted",
		"quote_id", quote.ID,
		"bet_id", quote.BetID,
		"credited_amount", quote.OfferedValue,
		"remaining_stake", quote.RemainingStake,
	)

	return &AcceptQuoteResponse{
		QuoteID:        quote.ID,
		BetID:          quote.BetID,
		CreditedAmount: quote.OfferedValue,
		RemainingStake: quote.RemainingStake,
		JournalEntryID: entry.ID,
	}, nil
}

// InvalidateQuote marks a quote as invalidated due to an external event
// (odds change, event state change, market suspension).
func (qm *QuoteManager) InvalidateQuote(ctx context.Context, quoteID, reason string) error {
	if err := qm.store.UpdateQuoteStatus(ctx, quoteID, QuoteStatusInvalidated, reason); err != nil {
		return fmt.Errorf("invalidate quote: %w", err)
	}
	qm.logger.Info("cashout quote invalidated", "quote_id", quoteID, "reason", reason)
	return nil
}

// InvalidatePendingQuotesForBet invalidates all pending quotes for a given bet.
// Called when odds change, event state changes, or market is suspended.
func (qm *QuoteManager) InvalidatePendingQuotesForBet(ctx context.Context, betID, reason string) (int, error) {
	quotes, err := qm.store.GetPendingQuotesForBet(ctx, betID)
	if err != nil {
		return 0, fmt.Errorf("get pending quotes: %w", err)
	}

	count := 0
	for _, q := range quotes {
		if err := qm.store.UpdateQuoteStatus(ctx, q.ID, QuoteStatusInvalidated, reason); err != nil {
			qm.logger.Error("failed to invalidate quote", "quote_id", q.ID, "error", err)
			continue
		}
		count++
	}

	if count > 0 {
		qm.logger.Info("invalidated pending quotes",
			"bet_id", betID,
			"count", count,
			"reason", reason,
		)
	}

	return count, nil
}

// ExpireStaleQuotes scans for pending quotes past their TTL and marks
// them expired. Intended to run periodically (e.g., every 5 seconds).
func (qm *QuoteManager) ExpireStaleQuotes(ctx context.Context) (int, error) {
	return qm.store.ExpireQuotesPastDeadline(ctx, time.Now().UTC())
}

// betHasLiveSelection reports whether any leg of the bet is currently in a
// live event. Live markets move fast enough to need a much shorter quote TTL
// than pre-match markets.
func betHasLiveSelection(bet BetSnapshot) bool {
	for _, sel := range bet.Selections {
		if sel.EventState == "live" {
			return true
		}
	}
	return false
}

// journalTypeForCashout returns the journal entry type for a cashout.
func journalTypeForCashout(ct CashoutType) string {
	if ct == CashoutTypePartial {
		return "partial_cashout_credit"
	}
	return "cashout_credit"
}

// QuoteStore is an in-memory store for cashout quotes and journal entries.
// In production, this would be backed by PostgreSQL + Redis cache.
type QuoteStore struct {
	mu       sync.RWMutex
	quotes   map[string]*CashoutQuote
	journals map[string]*CashoutJournalEntry
}

// NewQuoteStore creates an empty in-memory quote store.
func NewQuoteStore() *QuoteStore {
	return &QuoteStore{
		quotes:   make(map[string]*CashoutQuote),
		journals: make(map[string]*CashoutJournalEntry),
	}
}

// SaveQuote persists a new cashout quote.
func (s *QuoteStore) SaveQuote(_ context.Context, q *CashoutQuote) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.quotes[q.ID] = q
	return nil
}

// GetQuote retrieves a quote by ID.
func (s *QuoteStore) GetQuote(_ context.Context, id string) (*CashoutQuote, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	q, ok := s.quotes[id]
	if !ok {
		return nil, fmt.Errorf("quote %s not found", id)
	}
	return q, nil
}

// UpdateQuoteStatus sets a quote's status and optional invalidation reason.
func (s *QuoteStore) UpdateQuoteStatus(_ context.Context, id string, status QuoteStatus, reason string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	q, ok := s.quotes[id]
	if !ok {
		return fmt.Errorf("quote %s not found", id)
	}
	q.Status = status
	if reason != "" {
		q.InvalidReason = reason
		now := time.Now().UTC()
		q.InvalidatedAt = &now
	}
	return nil
}

// UpdateQuoteAccepted marks a quote as accepted with a timestamp.
func (s *QuoteStore) UpdateQuoteAccepted(_ context.Context, q *CashoutQuote) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	stored, ok := s.quotes[q.ID]
	if !ok {
		return fmt.Errorf("quote %s not found", q.ID)
	}
	stored.Status = q.Status
	stored.AcceptedAt = q.AcceptedAt
	return nil
}

// GetPendingQuotesForBet returns all pending quotes for a bet.
func (s *QuoteStore) GetPendingQuotesForBet(_ context.Context, betID string) ([]*CashoutQuote, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var result []*CashoutQuote
	for _, q := range s.quotes {
		if q.BetID == betID && q.Status == QuoteStatusPending {
			result = append(result, q)
		}
	}
	return result, nil
}

// ExpireQuotesPastDeadline marks all pending quotes with ExpiresAt before
// the given time as expired. Returns the count of expired quotes.
func (s *QuoteStore) ExpireQuotesPastDeadline(_ context.Context, now time.Time) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	count := 0
	for _, q := range s.quotes {
		if q.Status == QuoteStatusPending && now.After(q.ExpiresAt) {
			q.Status = QuoteStatusExpired
			q.InvalidReason = "ttl_expired"
			invalidAt := now
			q.InvalidatedAt = &invalidAt
			count++
		}
	}
	return count, nil
}

// SaveJournalEntry persists a cashout journal entry.
func (s *QuoteStore) SaveJournalEntry(_ context.Context, entry *CashoutJournalEntry) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.journals[entry.ID] = entry
	return nil
}

// GetJournalEntriesForBet returns all journal entries for a bet.
func (s *QuoteStore) GetJournalEntriesForBet(_ context.Context, betID string) ([]*CashoutJournalEntry, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var result []*CashoutJournalEntry
	for _, e := range s.journals {
		if e.BetID == betID {
			result = append(result, e)
		}
	}
	return result, nil
}
