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
	"crypto/tls"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"time"
)

const (
	sigapMaxRetries    = 3
	sigapRetryBase     = 500 * time.Millisecond
	sigapTimeout       = 10 * time.Second
	sigapBetEndpoint   = "/api/v1/bets/report"
	sigapEventType     = "BET_PLACED"
	sigapVersion       = "1.0"
)

// SIGAPClient is responsible for reporting bets to the Secretaria de Prêmios e Apostas
// (SPA/SIGAP) system as required by Lei 14.790/2023 and Portaria 1207/2024.
type SIGAPClient struct {
	httpClient  *http.Client
	baseURL     string
	operatorID  string
	apiKey      string
	logger      *slog.Logger
}

// SIGAPBetReport is the canonical JSON schema for bet-level reporting to SIGAP.
type SIGAPBetReport struct {
	Version      string              `json:"version"`
	OperatorID   string              `json:"operator_id"`
	EventType    string              `json:"event_type"`
	ReportedAt   time.Time           `json:"reported_at"`
	Bet          SIGAPBetPayload     `json:"bet"`
}

// SIGAPBetPayload contains the bet data fields mandated by Portaria 1207/2024.
type SIGAPBetPayload struct {
	BetID           string              `json:"bet_id"`
	CPF             string              `json:"cpf"`            // Full CPF — SIGAP is the regulator's own system and uses CPF as the primary key (Portaria 1207/2024); do not mask here, only in internal logs
	BetType         string              `json:"bet_type"`
	Stake           float64             `json:"stake_brl"`
	PotentialReturn float64             `json:"potential_return_brl"`
	CombinedOdds    float64             `json:"combined_odds"`
	PlacedAt        time.Time           `json:"placed_at"`
	Selections      []SIGAPSelection    `json:"selections"`
	Channel         string              `json:"channel"` // online, app, retail
	IPAddress       string              `json:"ip_address,omitempty"`
}

// SIGAPSelection describes one leg of a reported bet.
type SIGAPSelection struct {
	EventID       string  `json:"event_id"`
	MarketType    string  `json:"market_type"`
	SelectionName string  `json:"selection_name"`
	OddsValue     float64 `json:"odds_value"`
}

// SIGAPResponse is the acknowledgement returned by the SIGAP API.
type SIGAPResponse struct {
	ReportID  string    `json:"report_id"`
	Status    string    `json:"status"`
	Timestamp time.Time `json:"timestamp"`
	Message   string    `json:"message,omitempty"`
}

// NewSIGAPClient constructs a SIGAPClient with mTLS support.
// certFile/keyFile paths are empty in test environments (stub mode).
func NewSIGAPClient(baseURL, operatorID, apiKey, certFile, keyFile string, logger *slog.Logger) (*SIGAPClient, error) {
	tlsCfg := &tls.Config{
		MinVersion: tls.VersionTLS13,
	}

	// Load mTLS certificate if provided (production mode).
	if certFile != "" && keyFile != "" {
		cert, err := tls.LoadX509KeyPair(certFile, keyFile)
		if err != nil {
			return nil, fmt.Errorf("load mTLS cert: %w", err)
		}
		tlsCfg.Certificates = []tls.Certificate{cert}
	}

	transport := &http.Transport{TLSClientConfig: tlsCfg}
	client := &http.Client{
		Timeout:   sigapTimeout,
		Transport: transport,
	}

	return &SIGAPClient{
		httpClient: client,
		baseURL:    baseURL,
		operatorID: operatorID,
		apiKey:     apiKey,
		logger:     logger,
	}, nil
}

// ReportBet sends a bet-placed report to SIGAP with exponential backoff retry.
// Returns the SIGAP report ID on success.
func (c *SIGAPClient) ReportBet(ctx context.Context, bet *Bet) (string, error) {
	report := c.buildReport(bet)

	payload, err := json.Marshal(report)
	if err != nil {
		return "", fmt.Errorf("marshal sigap report: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt <= sigapMaxRetries; attempt++ {
		if attempt > 0 {
			backoff := sigapRetryBase * time.Duration(1<<uint(attempt-1))
			// Add jitter to prevent thundering herd.
			jitter := time.Duration(rand.Int63n(int64(backoff / 2)))
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			case <-time.After(backoff + jitter):
			}
		}

		reportID, err := c.doReport(ctx, payload)
		if err == nil {
			c.logger.Info("sigap bet reported",
				"bet_id", bet.ID,
				"report_id", reportID,
				"attempt", attempt+1,
			)
			return reportID, nil
		}

		lastErr = err
		c.logger.Warn("sigap report attempt failed",
			"bet_id", bet.ID,
			"attempt", attempt+1,
			"error", err,
		)
	}

	return "", fmt.Errorf("sigap report failed after %d attempts: %w", sigapMaxRetries+1, lastErr)
}

func (c *SIGAPClient) doReport(ctx context.Context, payload []byte) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.baseURL+sigapBetEndpoint, bytes.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)
	req.Header.Set("X-Operator-ID", c.operatorID)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("http do: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("sigap returned HTTP %d", resp.StatusCode)
	}

	var sigapResp SIGAPResponse
	if err := json.NewDecoder(resp.Body).Decode(&sigapResp); err != nil {
		return "", fmt.Errorf("decode sigap response: %w", err)
	}
	return sigapResp.ReportID, nil
}

// buildReport constructs a SIGAPBetReport from a Bet.
func (c *SIGAPClient) buildReport(bet *Bet) SIGAPBetReport {
	selections := make([]SIGAPSelection, 0, len(bet.Selections))
	for _, sel := range bet.Selections {
		selections = append(selections, SIGAPSelection{
			EventID:       sel.EventID,
			MarketType:    sel.MarketName,
			SelectionName: sel.SelectionName,
			OddsValue:     sel.OddsValue,
		})
	}

	return SIGAPBetReport{
		Version:    sigapVersion,
		OperatorID: c.operatorID,
		EventType:  sigapEventType,
		ReportedAt: time.Now().UTC(),
		Bet: SIGAPBetPayload{
			BetID:           bet.ID,
			CPF:             bet.CPF, // full, unmasked — see field comment on SIGAPBetPayload.CPF
			BetType:         string(bet.Type),
			Stake:           bet.Stake,
			PotentialReturn: bet.PotentialReturn,
			CombinedOdds:    bet.CombinedOdds,
			PlacedAt:        bet.PlacedAt,
			Selections:      selections,
			Channel:         "online",
			IPAddress:       bet.IPAddress,
		},
	}
}

// maskCPF partially masks a CPF for internal logging only. It must never be
// applied to the payload sent to SIGAP: the regulator requires the full CPF
// as the bet's primary key.
// Input "123.456.789-09" → "123.***.***-09"
func maskCPF(cpf string) string {
	if len(cpf) < 5 {
		return cpf
	}
	// Keep first 3 and last 2 digits visible.
	runes := []rune(cpf)
	for i := 3; i < len(runes)-2; i++ {
		if runes[i] >= '0' && runes[i] <= '9' {
			runes[i] = '*'
		}
	}
	return string(runes)
}

// SIGAPStub is a no-op SIGAP client used in testing and staging environments.
type SIGAPStub struct {
	logger *slog.Logger
}

// ReportBet on the stub returns a fake report ID without making HTTP calls.
func (s *SIGAPStub) ReportBet(_ context.Context, bet *Bet) (string, error) {
	reportID := fmt.Sprintf("STUB-%s-%d", bet.ID[:8], time.Now().UnixMilli())
	s.logger.Debug("sigap stub: report bet", "bet_id", bet.ID, "report_id", reportID)
	return reportID, nil
}
