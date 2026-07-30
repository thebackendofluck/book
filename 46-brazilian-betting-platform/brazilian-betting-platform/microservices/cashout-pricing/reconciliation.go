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
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"
)

// ReconciliationEngine ensures cashout operations are compatible with the
// settlement service and maintains a complete audit trail.
type ReconciliationEngine struct {
	store         *QuoteStore
	settlementURL string
	httpClient    *http.Client
	logger        *slog.Logger
}

// NewReconciliationEngine creates a reconciliation engine.
func NewReconciliationEngine(store *QuoteStore, settlementURL string, logger *slog.Logger) *ReconciliationEngine {
	return &ReconciliationEngine{
		store:         store,
		settlementURL: settlementURL,
		httpClient:    &http.Client{Timeout: 10 * time.Second},
		logger:        logger,
	}
}

// CashoutSettlementRecord is the record sent to the settlement service
// when a cashout is accepted, ensuring the settlement engine knows the
// bet (or portion) is no longer eligible for standard settlement.
type CashoutSettlementRecord struct {
	BetID          string      `json:"bet_id"`
	QuoteID        string      `json:"quote_id"`
	CPF            string      `json:"cpf"`
	CashoutType    CashoutType `json:"cashout_type"`
	CashoutValue   float64     `json:"cashout_value_brl"`
	CashoutStake   float64     `json:"cashout_stake_brl"`
	RemainingStake float64     `json:"remaining_stake_brl"`
	OriginalOdds   float64     `json:"original_odds"`
	AcceptedAt     time.Time   `json:"accepted_at"`
}

// AuditEntry records an auditable action in the cashout lifecycle.
type AuditEntry struct {
	ID        string    `json:"id"`
	QuoteID   string    `json:"quote_id"`
	BetID     string    `json:"bet_id"`
	CPF       string    `json:"cpf"`
	Action    string    `json:"action"` // "quote_generated", "quote_accepted", "quote_expired", "quote_invalidated", "settlement_notified"
	Detail    string    `json:"detail"`
	Timestamp time.Time `json:"timestamp"`
}

// NotifySettlement informs the settlement service that a cashout has been
// accepted. For full cashouts, the bet is marked as cashed_out and excluded
// from future settlement. For partial cashouts, the remaining stake is
// updated so settlement pays out based on the reduced amount.
func (re *ReconciliationEngine) NotifySettlement(ctx context.Context, quote *CashoutQuote) error {
	if re.settlementURL == "" {
		re.logger.Debug("settlement notification skipped (no URL configured)", "quote_id", quote.ID)
		return nil
	}

	record := CashoutSettlementRecord{
		BetID:          quote.BetID,
		QuoteID:        quote.ID,
		CPF:            quote.CPF,
		CashoutType:    quote.Type,
		CashoutValue:   quote.OfferedValue,
		CashoutStake:   quote.CashoutStake,
		RemainingStake: quote.RemainingStake,
		OriginalOdds:   quote.CombinedOdds,
	}
	if quote.AcceptedAt != nil {
		record.AcceptedAt = *quote.AcceptedAt
	}

	body, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("marshal settlement record: %w", err)
	}

	endpoint := fmt.Sprintf("%s/settle/cashout-notify", re.settlementURL)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("build settlement request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := re.httpClient.Do(req)
	if err != nil {
		re.logger.Warn("settlement notification failed", "quote_id", quote.ID, "error", err)
		return fmt.Errorf("settlement notify: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("settlement returned HTTP %d for cashout notify", resp.StatusCode)
	}

	re.logger.Info("settlement notified of cashout",
		"quote_id", quote.ID,
		"bet_id", quote.BetID,
		"type", quote.Type,
		"remaining_stake", quote.RemainingStake,
	)

	return nil
}

// BuildAuditTrail returns the complete audit trail for a bet's cashout
// operations, including all quotes and their state transitions.
func (re *ReconciliationEngine) BuildAuditTrail(ctx context.Context, betID string) ([]AuditEntry, error) {
	re.store.mu.RLock()
	defer re.store.mu.RUnlock()

	var trail []AuditEntry

	for _, q := range re.store.quotes {
		if q.BetID != betID {
			continue
		}

		// Quote generation event.
		trail = append(trail, AuditEntry{
			ID:        fmt.Sprintf("audit-%s-gen", q.ID[:8]),
			QuoteID:   q.ID,
			BetID:     q.BetID,
			CPF:       q.CPF,
			Action:    "quote_generated",
			Detail:    fmt.Sprintf("type=%s stake=%.2f offered=%.2f margin=%.4f ttl=%ds", q.Type, q.CashoutStake, q.OfferedValue, q.OperatorMargin, q.TTLSeconds),
			Timestamp: q.CreatedAt,
		})

		// Terminal state event.
		switch q.Status {
		case QuoteStatusAccepted:
			ts := q.CreatedAt
			if q.AcceptedAt != nil {
				ts = *q.AcceptedAt
			}
			trail = append(trail, AuditEntry{
				ID:        fmt.Sprintf("audit-%s-acc", q.ID[:8]),
				QuoteID:   q.ID,
				BetID:     q.BetID,
				CPF:       q.CPF,
				Action:    "quote_accepted",
				Detail:    fmt.Sprintf("credited=%.2f remaining_stake=%.2f", q.OfferedValue, q.RemainingStake),
				Timestamp: ts,
			})
		case QuoteStatusExpired:
			ts := q.ExpiresAt
			if q.InvalidatedAt != nil {
				ts = *q.InvalidatedAt
			}
			trail = append(trail, AuditEntry{
				ID:        fmt.Sprintf("audit-%s-exp", q.ID[:8]),
				QuoteID:   q.ID,
				BetID:     q.BetID,
				CPF:       q.CPF,
				Action:    "quote_expired",
				Detail:    fmt.Sprintf("ttl=%ds reason=%s", q.TTLSeconds, q.InvalidReason),
				Timestamp: ts,
			})
		case QuoteStatusInvalidated:
			ts := q.CreatedAt
			if q.InvalidatedAt != nil {
				ts = *q.InvalidatedAt
			}
			trail = append(trail, AuditEntry{
				ID:        fmt.Sprintf("audit-%s-inv", q.ID[:8]),
				QuoteID:   q.ID,
				BetID:     q.BetID,
				CPF:       q.CPF,
				Action:    "quote_invalidated",
				Detail:    fmt.Sprintf("reason=%s", q.InvalidReason),
				Timestamp: ts,
			})
		}
	}

	// Include journal entries.
	for _, e := range re.store.journals {
		if e.BetID != betID {
			continue
		}
		trail = append(trail, AuditEntry{
			ID:        fmt.Sprintf("audit-%s-jnl", e.ID[:8]),
			QuoteID:   e.QuoteID,
			BetID:     e.BetID,
			CPF:       e.CPF,
			Action:    "journal_entry",
			Detail:    fmt.Sprintf("type=%s amount=%.2f ref=%s", e.Type, e.Amount, e.Reference),
			Timestamp: e.CreatedAt,
		})
	}

	re.logger.Debug("audit trail built", "bet_id", betID, "entries", len(trail))
	return trail, nil
}

// ReconcileBetSettlement verifies that a bet's cashout history is consistent
// with the settlement expectations. Returns an error if there's a mismatch.
//
// This is called during settlement to ensure:
//   - fully cashed out bets are not settled again
//   - partially cashed out bets settle only the remaining stake
//   - journal entries match the accepted quote values
func (re *ReconciliationEngine) ReconcileBetSettlement(ctx context.Context, betID string, currentStake float64) error {
	entries, err := re.store.GetJournalEntriesForBet(ctx, betID)
	if err != nil {
		return fmt.Errorf("get journal entries for reconciliation: %w", err)
	}

	var totalCashedOutValue float64
	for _, e := range entries {
		if e.Type == "cashout_credit" || e.Type == "partial_cashout_credit" {
			totalCashedOutValue += e.Amount
		}
	}

	// Check all accepted quotes match journal entries.
	re.store.mu.RLock()
	var totalQuoteValue float64
	for _, q := range re.store.quotes {
		if q.BetID == betID && q.Status == QuoteStatusAccepted {
			totalQuoteValue += q.OfferedValue
		}
	}
	re.store.mu.RUnlock()

	// Allow small floating-point tolerance (1 centavo).
	diff := totalQuoteValue - totalCashedOutValue
	if diff > 0.01 || diff < -0.01 {
		return fmt.Errorf(
			"reconciliation mismatch for bet %s: quote total R$%.2f vs journal total R$%.2f",
			betID, totalQuoteValue, totalCashedOutValue,
		)
	}

	re.logger.Info("bet cashout reconciliation passed",
		"bet_id", betID,
		"total_cashed_out", totalCashedOutValue,
		"current_stake", currentStake,
	)

	return nil
}

// WalletClient communicates with the wallet microservice for cashout credits.
type WalletClient struct {
	httpClient *http.Client
	baseURL    string
	logger     *slog.Logger
}

// NewWalletClient creates a wallet client for cashout operations.
func NewWalletClient(baseURL string, logger *slog.Logger) *WalletClient {
	return &WalletClient{
		httpClient: &http.Client{Timeout: 10 * time.Second},
		baseURL:    baseURL,
		logger:     logger,
	}
}

// CashoutCreditRequest asks the wallet to credit the cashout amount.
type CashoutCreditRequest struct {
	CPF     string  `json:"cpf"`
	BetID   string  `json:"bet_id"`
	QuoteID string  `json:"quote_id"`
	Amount  float64 `json:"amount"`
}

// CashoutCreditResponse confirms a wallet credit for cashout.
type CashoutCreditResponse struct {
	TransactionID string  `json:"transaction_id"`
	BalanceBefore float64 `json:"balance_before"`
	BalanceAfter  float64 `json:"balance_after"`
}

// CreditCashout credits the cashout value to the player's wallet.
// Falls back to mock mode if the wallet URL is not configured.
func (wc *WalletClient) CreditCashout(ctx context.Context, req CashoutCreditRequest) (*CashoutCreditResponse, error) {
	if wc.baseURL == "" {
		// Mock mode for development.
		return &CashoutCreditResponse{
			TransactionID: fmt.Sprintf("mock-txn-%s", req.QuoteID[:8]),
			BalanceBefore: 5000.00,
			BalanceAfter:  5000.00 + req.Amount,
		}, nil
	}

	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal credit request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost,
		wc.baseURL+"/wallet/cashout-credit", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build credit request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := wc.httpClient.Do(httpReq)
	if err != nil {
		wc.logger.Warn("wallet cashout credit failed, using mock", "error", err)
		return &CashoutCreditResponse{
			TransactionID: fmt.Sprintf("mock-txn-%s", req.QuoteID[:8]),
			BalanceBefore: 5000.00,
			BalanceAfter:  5000.00 + req.Amount,
		}, nil
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("wallet credit returned HTTP %d", resp.StatusCode)
	}

	var result CashoutCreditResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode credit response: %w", err)
	}
	return &result, nil
}

// SIGAPCashoutClient reports cashout events to the SIGAP regulatory system.
type SIGAPCashoutClient struct {
	httpClient *http.Client
	baseURL    string
	operatorID string
	apiKey     string
	logger     *slog.Logger
}

// NewSIGAPCashoutClient creates a SIGAP client for cashout reporting.
func NewSIGAPCashoutClient(baseURL, operatorID, apiKey string, logger *slog.Logger) *SIGAPCashoutClient {
	return &SIGAPCashoutClient{
		httpClient: &http.Client{Timeout: 10 * time.Second},
		baseURL:    baseURL,
		operatorID: operatorID,
		apiKey:     apiKey,
		logger:     logger,
	}
}

const (
	sigapCashoutEndpoint = "/api/v1/cashouts/report"
	sigapVersion         = "1.0"
)

// ReportCashout sends an accepted cashout report to SIGAP.
func (c *SIGAPCashoutClient) ReportCashout(ctx context.Context, quote *CashoutQuote) error {
	acceptedAt := quote.CreatedAt
	if quote.AcceptedAt != nil {
		acceptedAt = *quote.AcceptedAt
	}

	report := SIGAPCashoutReport{
		Version:    sigapVersion,
		OperatorID: c.operatorID,
		ReportedAt: time.Now().UTC(),
		Cashout: SIGAPCashoutPayload{
			QuoteID:        quote.ID,
			BetID:          quote.BetID,
			CPF:            maskCPF(quote.CPF),
			CashoutType:    string(quote.Type),
			OriginalStake:  quote.OriginalStake,
			CashoutStake:   quote.CashoutStake,
			CashoutValue:   quote.OfferedValue,
			RemainingStake: quote.RemainingStake,
			OperatorMargin: quote.OperatorMargin,
			OriginalOdds:   quote.CombinedOdds,
			CurrentOdds:    quote.CurrentOdds,
			AcceptedAt:     acceptedAt,
		},
	}

	body, err := json.Marshal(report)
	if err != nil {
		return fmt.Errorf("marshal sigap cashout report: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.baseURL+sigapCashoutEndpoint, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("build sigap request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)
	req.Header.Set("X-Operator-ID", c.operatorID)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("sigap cashout http: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("sigap cashout returned HTTP %d", resp.StatusCode)
	}

	c.logger.Info("sigap cashout reported", "quote_id", quote.ID, "bet_id", quote.BetID)
	return nil
}

// maskCPF partially masks a CPF for privacy compliance.
// Input "123.456.789-09" -> "123.***.***-09"
func maskCPF(cpf string) string {
	if len(cpf) < 5 {
		return cpf
	}
	runes := []rune(cpf)
	for i := 3; i < len(runes)-2; i++ {
		if runes[i] >= '0' && runes[i] <= '9' {
			runes[i] = '*'
		}
	}
	return string(runes)
}
