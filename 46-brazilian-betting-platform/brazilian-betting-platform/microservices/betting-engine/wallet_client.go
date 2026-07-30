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

// WalletClient communicates with the wallet microservice to reserve and
// release funds during bet placement.
type WalletClient struct {
	httpClient *http.Client
	baseURL    string
	logger     *slog.Logger
}

// NewWalletClient creates a client for the wallet service.
func NewWalletClient(baseURL string, logger *slog.Logger) *WalletClient {
	return &WalletClient{
		httpClient: &http.Client{Timeout: 10 * time.Second},
		baseURL:    baseURL,
		logger:     logger,
	}
}

// WalletBalance holds the player's current wallet state.
type WalletBalance struct {
	CPF              string  `json:"cpf"`
	AvailableBalance float64 `json:"available_balance"`
	ReservedBalance  float64 `json:"reserved_balance"`
	Currency         string  `json:"currency"`
}

// ReservationRequest asks the wallet to hold funds for a bet.
type ReservationRequest struct {
	CPF       string  `json:"cpf"`
	BetID     string  `json:"bet_id"`
	Amount    float64 `json:"amount"`
	Reference string  `json:"reference"` // e.g., "bet_placement"
}

// ReservationResponse confirms or rejects a fund reservation.
type ReservationResponse struct {
	ReservationID string  `json:"reservation_id"`
	Status        string  `json:"status"` // "reserved", "insufficient_balance", "error"
	Amount        float64 `json:"amount"`
	Message       string  `json:"message,omitempty"`
}

// ReleaseRequest asks the wallet to release previously reserved funds.
type ReleaseRequest struct {
	ReservationID string `json:"reservation_id"`
	CPF           string `json:"cpf"`
	BetID         string `json:"bet_id"`
	Reason        string `json:"reason"` // "bet_accepted", "bet_rejected", "bet_cancelled"
}

// GetBalance retrieves the current wallet balance for a player.
// In mock mode (empty baseURL) it returns a development balance; in
// configured mode it fails closed if the wallet service is unreachable.
func (wc *WalletClient) GetBalance(ctx context.Context, cpf string) (*WalletBalance, error) {
	if wc.baseURL == "" {
		// Mock mode: return a default balance for development.
		return &WalletBalance{
			CPF:              cpf,
			AvailableBalance: 10000.00,
			ReservedBalance:  0,
			Currency:         "BRL",
		}, nil
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		fmt.Sprintf("%s/wallet/balance/%s", wc.baseURL, cpf), nil)
	if err != nil {
		return nil, fmt.Errorf("build balance request: %w", err)
	}

	resp, err := wc.httpClient.Do(req)
	if err != nil {
		// Fail CLOSED: a wallet outage must not fabricate a balance. Returning
		// a phantom balance here would let bets be accepted against funds that
		// were never checked.
		return nil, fmt.Errorf("wallet service unavailable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("wallet balance returned status %d", resp.StatusCode)
	}

	var balance WalletBalance
	if err := json.NewDecoder(resp.Body).Decode(&balance); err != nil {
		return nil, fmt.Errorf("decode balance: %w", err)
	}
	return &balance, nil
}

// ReserveFunds places a hold on the player's wallet for the bet amount.
// The reservation must be confirmed (debit) or released after placement.
func (wc *WalletClient) ReserveFunds(ctx context.Context, reservation ReservationRequest) (*ReservationResponse, error) {
	if wc.baseURL == "" {
		// Mock mode: always succeed.
		return &ReservationResponse{
			ReservationID: fmt.Sprintf("res-%s", reservation.BetID),
			Status:        "reserved",
			Amount:        reservation.Amount,
		}, nil
	}

	body, err := json.Marshal(reservation)
	if err != nil {
		return nil, fmt.Errorf("marshal reservation: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		wc.baseURL+"/wallet/reserve", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build reserve request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := wc.httpClient.Do(req)
	if err != nil {
		// Fail CLOSED: never report funds as reserved when the wallet service
		// could not be reached. A phantom reservation accepts a bet with no
		// real hold on the player's money.
		return nil, fmt.Errorf("wallet reserve unavailable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		return nil, fmt.Errorf("wallet reserve returned status %d", resp.StatusCode)
	}

	var result ReservationResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode reservation response: %w", err)
	}
	return &result, nil
}

// ReleaseFunds releases a previously reserved amount back to the player.
func (wc *WalletClient) ReleaseFunds(ctx context.Context, release ReleaseRequest) error {
	if wc.baseURL == "" {
		return nil // Mock mode.
	}

	body, err := json.Marshal(release)
	if err != nil {
		return fmt.Errorf("marshal release: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		wc.baseURL+"/wallet/release", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("build release request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := wc.httpClient.Do(req)
	if err != nil {
		wc.logger.Warn("wallet release failed", "error", err)
		return nil // Non-critical in mock mode.
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("wallet release returned HTTP %d", resp.StatusCode)
	}
	return nil
}
