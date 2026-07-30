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

// ── Enumerations ──────────────────────────────────────────────────────────────

type GameStatus string

const (
	GameStatusActive    GameStatus = "ACTIVE"
	GameStatusCompleted GameStatus = "COMPLETED"
	GameStatusVoided    GameStatus = "VOIDED"
	GameStatusError     GameStatus = "ERROR"
)

type RoundOutcome string

const (
	RoundOutcomeWin  RoundOutcome = "WIN"
	RoundOutcomeLoss RoundOutcome = "LOSS"
	RoundOutcomePush RoundOutcome = "PUSH"
	RoundOutcomeVoid RoundOutcome = "VOID"
)

type GameCategory string

const (
	GameCategorySlots      GameCategory = "SLOTS"
	GameCategoryTableGames GameCategory = "TABLE_GAMES"
	GameCategoryLiveCasino GameCategory = "LIVE_CASINO"
	GameCategoryInstant    GameCategory = "INSTANT_WIN"
)

type RNGCertification string

const (
	RNGCertGLI    RNGCertification = "GLI"
	RNGCertBMM    RNGCertification = "BMM"
	RNGCertECOGRA RNGCertification = "ECOGRA"
	RNGCertITECH  RNGCertification = "ITECH"
)

// ── Game catalog ──────────────────────────────────────────────────────────────

// Game represents a title in the casino catalog.
type Game struct {
	GameID           string           `json:"game_id"`
	Name             string           `json:"name"`
	Provider         string           `json:"provider"`
	Category         GameCategory     `json:"category"`
	RTPPercent       float64          `json:"rtp_percent"`
	MaxWinMultiplier float64          `json:"max_win_multiplier"`
	MinBet           float64          `json:"min_bet_brl"`
	MaxBet           float64          `json:"max_bet_brl"`
	RNGCertified     bool             `json:"rng_certified"`
	CertBody         RNGCertification `json:"cert_body,omitempty"`
	CertNumber       string           `json:"cert_number,omitempty"`
	Active           bool             `json:"active"`
	SIGAPCategory    string           `json:"sigap_category"` // for round-level reporting
}

// ── Sessions ──────────────────────────────────────────────────────────────────

// GameSession tracks an active player session for a specific game.
type GameSession struct {
	SessionID    string            `json:"session_id"`
	GameID       string            `json:"game_id"`
	CPF          string            `json:"cpf"`
	ProviderURL  string            `json:"provider_url"`
	Token        string            `json:"token"`
	Status       GameStatus        `json:"status"`
	LaunchedAt   time.Time         `json:"launched_at"`
	LastActivity time.Time         `json:"last_activity"`
	Metadata     map[string]string `json:"metadata,omitempty"`
}

// LaunchRequest is the payload for POST /games/launch/{game_id}.
type LaunchRequest struct {
	CPF      string            `json:"cpf"`
	Currency string            `json:"currency"` // BRL
	Language string            `json:"language"` // pt-BR
	Metadata map[string]string `json:"metadata,omitempty"`
}

// LaunchResponse returns the game URL and session details.
type LaunchResponse struct {
	SessionID  string `json:"session_id"`
	GameID     string `json:"game_id"`
	CPF        string `json:"cpf"`
	GameURL    string `json:"game_url"`
	LaunchedAt string `json:"launched_at"`
}

// ── Rounds ────────────────────────────────────────────────────────────────────

// Round represents a single bet round (spin / hand / roll).
type Round struct {
	RoundID     string       `json:"round_id"`
	SessionID   string       `json:"session_id"`
	GameID      string       `json:"game_id"`
	CPF         string       `json:"cpf"`
	BetAmount   float64      `json:"bet_amount_brl"`
	WinAmount   float64      `json:"win_amount_brl"`
	Outcome     RoundOutcome `json:"outcome"`
	StartedAt   time.Time    `json:"started_at"`
	CompletedAt time.Time    `json:"completed_at"`
	// SIGAP fields
	GGRContribution float64 `json:"ggr_contribution_brl"` // bet - win
	SIGAPReported   bool    `json:"sigap_reported"`
}

// RoundCompleteRequest is the callback payload sent by the game provider.
type RoundCompleteRequest struct {
	RoundID   string  `json:"round_id"`
	SessionID string  `json:"session_id"`
	GameID    string  `json:"game_id"`
	CPF       string  `json:"cpf"`
	BetAmount float64 `json:"bet_amount_brl"`
	WinAmount float64 `json:"win_amount_brl"`
	Outcome   string  `json:"outcome"`
	Timestamp string  `json:"timestamp"`
	// Provider signature for authenticity verification
	ProviderSignature string `json:"provider_signature"`
}

// ── RNG validation ────────────────────────────────────────────────────────────

// RNGValidationRequest is the payload for POST /games/rng/validate.
type RNGValidationRequest struct {
	GameID     string `json:"game_id"`
	CertBody   string `json:"cert_body"`
	CertNumber string `json:"cert_number"`
}

// RNGValidationResult is the result of an RNG certification check.
type RNGValidationResult struct {
	GameID     string `json:"game_id"`
	Valid      bool   `json:"valid"`
	CertBody   string `json:"cert_body"`
	CertNumber string `json:"cert_number"`
	ExpiresAt  string `json:"expires_at,omitempty"`
	Message    string `json:"message"`
	CheckedAt  string `json:"checked_at"`
}

// ── SIGAP reporting ───────────────────────────────────────────────────────────

// SIGAPRoundRecord is one row in the SIGAP round-level report.
type SIGAPRoundRecord struct {
	RoundID         string  `json:"round_id"`
	GameID          string  `json:"game_id"`
	GameName        string  `json:"game_name"`
	SIGAPCategory   string  `json:"sigap_category"`
	CPF             string  `json:"cpf"`
	BetAmount       float64 `json:"bet_amount_brl"`
	WinAmount       float64 `json:"win_amount_brl"`
	GGRContribution float64 `json:"ggr_contribution_brl"`
	RoundDate       string  `json:"round_date"`
}

// SIGAPRoundReport is the full monthly round-level report.
type SIGAPRoundReport struct {
	ReportID     string             `json:"report_id"`
	Period       string             `json:"period"`
	OperatorCNPJ string             `json:"operator_cnpj"`
	TotalRounds  int                `json:"total_rounds"`
	TotalBet     float64            `json:"total_bet_brl"`
	TotalWin     float64            `json:"total_win_brl"`
	TotalGGR     float64            `json:"total_ggr_brl"`
	Records      []SIGAPRoundRecord `json:"records"`
	GeneratedAt  string             `json:"generated_at"`
}

// ── Health ────────────────────────────────────────────────────────────────────

// HealthStatus is the service health response.
type HealthStatus struct {
	Status    string `json:"status"`
	Service   string `json:"service"`
	Version   string `json:"version"`
	DBUp      bool   `json:"db_up"`
	RedisUp   bool   `json:"redis_up"`
	GameCount int    `json:"game_count"`
	Timestamp string `json:"timestamp"`
}

// ErrorResponse is a standard API error envelope.
type ErrorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message"`
	Code    int    `json:"code"`
}

// newID generates a short UUID string.
func newID() string {
	return uuid.New().String()
}
