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
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/rs/zerolog/log"
)

// Handlers wires all HTTP route handlers to their dependencies.
type Handlers struct {
	catalog   []Game
	sessions  map[string]*GameSession // sessionID -> session
	rounds    map[string]*Round       // roundID -> round
	providers *ProviderRegistry
	rng       *RNGValidator
	sigap     *SIGAPRoundsReporter
}

// NewHandlers returns initialised Handlers with a demo game catalog.
func NewHandlers(providers *ProviderRegistry, rng *RNGValidator) *Handlers {
	catalog := seedCatalog()
	return &Handlers{
		catalog:   catalog,
		sessions:  make(map[string]*GameSession),
		rounds:    make(map[string]*Round),
		providers: providers,
		rng:       rng,
		sigap:     NewSIGAPRoundsReporter(catalog),
	}
}

// ── Health ────────────────────────────────────────────────────────────────────

// Health handles GET /health.
func (h *Handlers) Health(w http.ResponseWriter, r *http.Request) {
	respond(w, http.StatusOK, HealthStatus{
		Status:    "UP",
		Service:   "casino-aggregation",
		Version:   "1.0.0",
		DBUp:      true, // TODO: real DB ping
		RedisUp:   true, // TODO: real Redis ping
		GameCount: len(h.catalog),
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	})
}

// ── Catalog ───────────────────────────────────────────────────────────────────

// GetCatalog handles GET /games/catalog.
func (h *Handlers) GetCatalog(w http.ResponseWriter, r *http.Request) {
	// Filter to active games only
	active := make([]Game, 0, len(h.catalog))
	for _, g := range h.catalog {
		if g.Active {
			active = append(active, g)
		}
	}
	respond(w, http.StatusOK, map[string]interface{}{
		"games": active,
		"total": len(active),
	})
}

// ── Launch ────────────────────────────────────────────────────────────────────

// LaunchGame handles POST /games/launch/{game_id}.
func (h *Handlers) LaunchGame(w http.ResponseWriter, r *http.Request) {
	gameID := chi.URLParam(r, "game_id")

	var req LaunchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		respondErr(w, http.StatusBadRequest, "invalid request body", err.Error())
		return
	}

	if req.CPF == "" {
		respondErr(w, http.StatusBadRequest, "cpf is required", "")
		return
	}

	// Find game in catalog
	game := h.findGame(gameID)
	if game == nil {
		respondErr(w, http.StatusNotFound, "game not found", gameID)
		return
	}

	if !game.Active {
		respondErr(w, http.StatusConflict, "game is not currently available", gameID)
		return
	}

	// Verify RNG certification
	if !h.rng.IsGameCertified(gameID) {
		log.Warn().Str("game_id", gameID).Msg("Launch blocked: RNG certificate invalid or expired")
		respondErr(w, http.StatusForbidden, "game RNG certificate is not valid", gameID)
		return
	}

	// Select provider
	provider, ok := h.providers.Get(game.Provider)
	if !ok {
		respondErr(w, http.StatusServiceUnavailable, "game provider unavailable", game.Provider)
		return
	}

	currency := req.Currency
	if currency == "" {
		currency = "BRL"
	}
	language := req.Language
	if language == "" {
		language = "pt-BR"
	}

	session := &GameSession{
		SessionID:    newID(),
		GameID:       gameID,
		CPF:          req.CPF,
		Token:        newID(),
		Status:       GameStatusActive,
		LaunchedAt:   time.Now().UTC(),
		LastActivity: time.Now().UTC(),
		Metadata:     req.Metadata,
	}

	gameURL, err := provider.BuildGameURL(session, game, currency, language)
	if err != nil {
		log.Error().Err(err).Str("game_id", gameID).Msg("Failed to build game URL")
		respondErr(w, http.StatusInternalServerError, "failed to build game URL", err.Error())
		return
	}

	session.ProviderURL = gameURL
	h.sessions[session.SessionID] = session

	log.Info().
		Str("session_id", session.SessionID).
		Str("game_id", gameID).
		Str("cpf", req.CPF).
		Str("provider", game.Provider).
		Msg("Game session launched")

	respond(w, http.StatusCreated, LaunchResponse{
		SessionID:  session.SessionID,
		GameID:     gameID,
		CPF:        req.CPF,
		GameURL:    gameURL,
		LaunchedAt: session.LaunchedAt.Format(time.RFC3339),
	})
}

// ── Round complete callback ───────────────────────────────────────────────────

// RoundComplete handles POST /games/round/complete.
//
// This endpoint receives signed callbacks from game providers when a round ends.
func (h *Handlers) RoundComplete(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 64*1024))
	if err != nil {
		respondErr(w, http.StatusBadRequest, "failed to read request body", err.Error())
		return
	}

	// Find the matching provider and verify the callback signature
	var req RoundCompleteRequest
	if err := json.Unmarshal(body, &req); err != nil {
		respondErr(w, http.StatusBadRequest, "invalid JSON payload", err.Error())
		return
	}

	// Validate session exists
	session, ok := h.sessions[req.SessionID]
	if !ok {
		respondErr(w, http.StatusNotFound, "session not found", req.SessionID)
		return
	}

	// Verify provider signature
	game := h.findGame(req.GameID)
	if game != nil {
		provider, pok := h.providers.Get(game.Provider)
		if pok && !provider.VerifyCallback(r, body) {
			log.Warn().
				Str("session_id", req.SessionID).
				Str("game_id", req.GameID).
				Msg("Round callback signature verification failed")
			respondErr(w, http.StatusUnauthorized, "invalid callback signature", "")
			return
		}
	}

	outcome := RoundOutcome(req.Outcome)
	now := time.Now().UTC()

	round := &Round{
		RoundID:     req.RoundID,
		SessionID:   req.SessionID,
		GameID:      req.GameID,
		CPF:         req.CPF,
		BetAmount:   req.BetAmount,
		WinAmount:   req.WinAmount,
		Outcome:     outcome,
		StartedAt:   now.Add(-5 * time.Second), // approximate
		CompletedAt: now,
	}

	h.rounds[round.RoundID] = round
	session.LastActivity = now

	// Feed into SIGAP reporter
	h.sigap.RecordRound(*round)

	log.Info().
		Str("round_id", round.RoundID).
		Str("session_id", round.SessionID).
		Str("cpf", round.CPF).
		Float64("bet", round.BetAmount).
		Float64("win", round.WinAmount).
		Str("outcome", string(outcome)).
		Msg("Round completed")

	respond(w, http.StatusOK, map[string]string{
		"round_id": round.RoundID,
		"status":   "ACCEPTED",
	})
}

// ── Sessions ──────────────────────────────────────────────────────────────────

// GetSessions handles GET /games/sessions/{cpf}.
func (h *Handlers) GetSessions(w http.ResponseWriter, r *http.Request) {
	cpf := chi.URLParam(r, "cpf")

	var active []*GameSession
	for _, s := range h.sessions {
		if s.CPF == cpf && s.Status == GameStatusActive {
			active = append(active, s)
		}
	}

	respond(w, http.StatusOK, map[string]interface{}{
		"cpf":      cpf,
		"sessions": active,
		"total":    len(active),
	})
}

// ── RNG validation ────────────────────────────────────────────────────────────

// ValidateRNG handles POST /games/rng/validate.
func (h *Handlers) ValidateRNG(w http.ResponseWriter, r *http.Request) {
	var req RNGValidationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		respondErr(w, http.StatusBadRequest, "invalid request body", err.Error())
		return
	}

	if req.GameID == "" {
		respondErr(w, http.StatusBadRequest, "game_id is required", "")
		return
	}

	result := h.rng.Validate(req)
	status := http.StatusOK
	if !result.Valid {
		status = http.StatusUnprocessableEntity
	}
	respond(w, status, result)
}

// ── SIGAP report ──────────────────────────────────────────────────────────────

// GetSIGAPReport handles GET /games/sigap-report?period=YYYY-MM.
func (h *Handlers) GetSIGAPReport(w http.ResponseWriter, r *http.Request) {
	period := r.URL.Query().Get("period")
	if period == "" {
		// Default to previous month
		now := time.Now().UTC()
		prev := now.AddDate(0, -1, 0)
		period = prev.Format("2006-01")
	}

	report, err := h.sigap.GenerateReport(period)
	if err != nil {
		respondErr(w, http.StatusBadRequest, "invalid period", err.Error())
		return
	}

	respond(w, http.StatusOK, report)
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func (h *Handlers) findGame(gameID string) *Game {
	for i := range h.catalog {
		if h.catalog[i].GameID == gameID {
			return &h.catalog[i]
		}
	}
	return nil
}

func respond(w http.ResponseWriter, status int, payload interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		log.Error().Err(err).Msg("Failed to encode response")
	}
}

func respondErr(w http.ResponseWriter, status int, msg, detail string) {
	respond(w, status, ErrorResponse{
		Error:   http.StatusText(status),
		Message: msg + ifNotEmpty(": ", detail),
		Code:    status,
	})
}

func ifNotEmpty(prefix, s string) string {
	if s == "" {
		return ""
	}
	return prefix + s
}

// seedCatalog returns a representative set of demo games.
func seedCatalog() []Game {
	return []Game{
		{
			GameID: "GAME-SLOTS-001", Name: "Amazônia Fortune",
			Provider: "PRAGMATIC", Category: GameCategorySlots,
			RTPPercent: 96.5, MaxWinMultiplier: 5000,
			MinBet: 0.20, MaxBet: 500.00,
			RNGCertified: true, CertBody: RNGCertBMM, CertNumber: "BMM-BR-2024-045",
			Active: true, SIGAPCategory: "CASINO_SLOTS",
		},
		{
			GameID: "GAME-SLOTS-002", Name: "Carnival Jackpot",
			Provider: "NETENT", Category: GameCategorySlots,
			RTPPercent: 97.1, MaxWinMultiplier: 10000,
			MinBet: 0.10, MaxBet: 200.00,
			RNGCertified: true, CertBody: RNGCertGLI, CertNumber: "GLI-BR-2024-001",
			Active: true, SIGAPCategory: "CASINO_SLOTS",
		},
		{
			GameID: "GAME-TABLE-001", Name: "Blackjack Brasil",
			Provider: "EVOLUTION", Category: GameCategoryTableGames,
			RTPPercent: 99.5, MaxWinMultiplier: 3,
			MinBet: 5.00, MaxBet: 5000.00,
			RNGCertified: true, CertBody: RNGCertECOGRA, CertNumber: "ECOGRA-BR-2024-010",
			Active: true, SIGAPCategory: "CASINO_TABLE",
		},
		{
			GameID: "GAME-LIVE-001", Name: "Live Roleta VIP",
			Provider: "EVOLUTION", Category: GameCategoryLiveCasino,
			RTPPercent: 97.3, MaxWinMultiplier: 35,
			MinBet: 10.00, MaxBet: 10000.00,
			RNGCertified: true, CertBody: RNGCertGLI, CertNumber: "GLI-BR-2024-002",
			Active: true, SIGAPCategory: "LIVE_CASINO",
		},
		{
			GameID: "GAME-INSTANT-001", Name: "Raspadinha Online",
			Provider: "PRAGMATIC", Category: GameCategoryInstant,
			RTPPercent: 95.0, MaxWinMultiplier: 1000,
			MinBet: 1.00, MaxBet: 50.00,
			RNGCertified: true, CertBody: RNGCertITECH, CertNumber: "ITECH-BR-2024-099",
			Active: true, SIGAPCategory: "INSTANT_WIN",
		},
	}
}
