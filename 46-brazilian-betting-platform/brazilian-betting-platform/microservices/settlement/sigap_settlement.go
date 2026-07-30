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

const (
	sigapSettlementEndpoint = "/api/v1/settlements/report"
	sigapGGREndpoint        = "/api/v1/ggr/report"
	sigapVersion            = "1.0"
)

// SIGAPSettlementClient reports settlement data to the SIGAP regulatory system.
type SIGAPSettlementClient struct {
	httpClient *http.Client
	baseURL    string
	operatorID string
	apiKey     string
	logger     *slog.Logger
}

// SIGAPSettlementReport is the payload for a single bet settlement report.
type SIGAPSettlementReport struct {
	Version      string                 `json:"version"`
	OperatorID   string                 `json:"operator_id"`
	ReportedAt   time.Time              `json:"reported_at"`
	Settlement   SIGAPSettlementPayload `json:"settlement"`
}

// SIGAPSettlementPayload is the data fields for a settlement report.
type SIGAPSettlementPayload struct {
	BetID         string     `json:"bet_id"`
	CPF           string     `json:"cpf"` // Full CPF — SIGAP requires it as the regulatory primary key; mask only in internal logs
	EventID       string     `json:"event_id"`
	Outcome       string     `json:"outcome"`
	Stake         float64    `json:"stake_brl"`
	GrossReturn   float64    `json:"gross_return_brl"`
	TaxWithheld   float64    `json:"tax_withheld_brl"`
	NetPayout     float64    `json:"net_payout_brl"`
	GGRContrib    float64    `json:"ggr_contrib_brl"`
	SettledAt     time.Time  `json:"settled_at"`
}

// SIGAPGGRReport is the payload for a daily GGR report submission.
type SIGAPGGRPayload struct {
	Version           string    `json:"version"`
	OperatorID        string    `json:"operator_id"`
	ReportDate        string    `json:"report_date"`
	TotalBetsAmount   float64   `json:"total_bets_brl"`
	TotalPrizesPaid   float64   `json:"total_prizes_brl"`
	TotalTaxWithheld  float64   `json:"total_tax_withheld_brl"`
	GGR               float64   `json:"ggr_brl"`
	TotalBetCount     int       `json:"total_bet_count"`
	UniquePlayerCount int       `json:"unique_player_count"`
	SubmittedAt       time.Time `json:"submitted_at"`
}

// NewSIGAPSettlementClient creates a new SIGAPSettlementClient.
func NewSIGAPSettlementClient(baseURL, operatorID, apiKey string, logger *slog.Logger) *SIGAPSettlementClient {
	return &SIGAPSettlementClient{
		httpClient: &http.Client{Timeout: 10 * time.Second},
		baseURL:    baseURL,
		operatorID: operatorID,
		apiKey:     apiKey,
		logger:     logger,
	}
}

// ReportSettlement sends a single bet settlement to SIGAP.
func (c *SIGAPSettlementClient) ReportSettlement(ctx context.Context, s *Settlement) error {
	report := SIGAPSettlementReport{
		Version:    sigapVersion,
		OperatorID: c.operatorID,
		ReportedAt: time.Now().UTC(),
		Settlement: SIGAPSettlementPayload{
			BetID:       s.BetID,
			CPF:         s.CPF, // full, unmasked — see field comment on SIGAPSettlementPayload.CPF
			EventID:     s.EventID,
			Outcome:     string(s.Outcome),
			Stake:       s.Stake,
			GrossReturn: s.GrossReturn,
			TaxWithheld: s.TaxWithheld,
			NetPayout:   s.NetPayout,
			GGRContrib:  s.GGRContrib,
			SettledAt:   s.SettledAt,
		},
	}
	return c.post(ctx, sigapSettlementEndpoint, report)
}

// ReportGGR submits a daily GGR report to SIGAP.
func (c *SIGAPSettlementClient) ReportGGR(ctx context.Context, r *GGRReport) error {
	payload := SIGAPGGRPayload{
		Version:           sigapVersion,
		OperatorID:        c.operatorID,
		ReportDate:        r.ReportDate,
		TotalBetsAmount:   r.TotalBetsAmount,
		TotalPrizesPaid:   r.TotalPrizesPaid,
		TotalTaxWithheld:  r.TotalTaxWithheld,
		GGR:               r.GGR,
		TotalBetCount:     r.TotalBetCount,
		UniquePlayerCount: r.UniquePlayerCount,
		SubmittedAt:       time.Now().UTC(),
	}
	return c.post(ctx, sigapGGREndpoint, payload)
}

func (c *SIGAPSettlementClient) post(ctx context.Context, endpoint string, payload any) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal sigap payload: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+endpoint, bytes.NewReader(data))
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)
	req.Header.Set("X-Operator-ID", c.operatorID)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("http do: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("sigap returned HTTP %d for %s", resp.StatusCode, endpoint)
	}

	c.logger.Debug("sigap report submitted", "endpoint", endpoint, "status", resp.StatusCode)
	return nil
}
