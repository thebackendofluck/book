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
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// ── Test setup ────────────────────────────────────────────────────────────────

func setupTestApp() (http.Handler, *Handlers) {
	providers := NewProviderRegistry()
	rng := NewRNGValidator()
	h := NewHandlers(providers, rng)

	r := chi.NewRouter()
	r.Get("/health", h.Health)
	r.Route("/games", func(r chi.Router) {
		r.Get("/catalog", h.GetCatalog)
		r.Post("/launch/{game_id}", h.LaunchGame)
		r.Post("/round/complete", h.RoundComplete)
		r.Get("/sessions/{cpf}", h.GetSessions)
		r.Post("/rng/validate", h.ValidateRNG)
		r.Get("/sigap-report", h.GetSIGAPReport)
	})

	return r, h
}

func doRequest(t *testing.T, handler http.Handler, method, path string, body interface{}) *httptest.ResponseRecorder {
	t.Helper()
	var reqBody *bytes.Buffer
	if body != nil {
		b, err := json.Marshal(body)
		require.NoError(t, err)
		reqBody = bytes.NewBuffer(b)
	} else {
		reqBody = &bytes.Buffer{}
	}
	req := httptest.NewRequest(method, path, reqBody)
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	return rr
}

// ── Health ────────────────────────────────────────────────────────────────────

func TestHealth_ReturnsOK(t *testing.T) {
	app, _ := setupTestApp()
	rr := doRequest(t, app, "GET", "/health", nil)
	assert.Equal(t, http.StatusOK, rr.Code)

	var hs HealthStatus
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &hs))
	assert.Equal(t, "UP", hs.Status)
	assert.Equal(t, "casino-aggregation", hs.Service)
}

func TestHealth_ContainsGameCount(t *testing.T) {
	app, _ := setupTestApp()
	rr := doRequest(t, app, "GET", "/health", nil)
	var hs HealthStatus
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &hs))
	assert.Greater(t, hs.GameCount, 0)
}

// ── Catalog ───────────────────────────────────────────────────────────────────

func TestGetCatalog_ReturnsActiveGames(t *testing.T) {
	app, _ := setupTestApp()
	rr := doRequest(t, app, "GET", "/games/catalog", nil)
	assert.Equal(t, http.StatusOK, rr.Code)

	var result map[string]interface{}
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &result))
	total := int(result["total"].(float64))
	assert.Greater(t, total, 0)
}

// ── Game launch ───────────────────────────────────────────────────────────────

func TestLaunchGame_ValidGame(t *testing.T) {
	app, _ := setupTestApp()
	req := LaunchRequest{CPF: "12345678909", Currency: "BRL", Language: "pt-BR"}
	rr := doRequest(t, app, "POST", "/games/launch/GAME-SLOTS-001", req)
	assert.Equal(t, http.StatusCreated, rr.Code)

	var resp LaunchResponse
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &resp))
	assert.NotEmpty(t, resp.SessionID)
	assert.NotEmpty(t, resp.GameURL)
	assert.Equal(t, "12345678909", resp.CPF)
}

func TestLaunchGame_NonExistentGame_Returns404(t *testing.T) {
	app, _ := setupTestApp()
	req := LaunchRequest{CPF: "12345678909"}
	rr := doRequest(t, app, "POST", "/games/launch/GAME-NONEXISTENT", req)
	assert.Equal(t, http.StatusNotFound, rr.Code)
}

func TestLaunchGame_MissingCPF_Returns400(t *testing.T) {
	app, _ := setupTestApp()
	rr := doRequest(t, app, "POST", "/games/launch/GAME-SLOTS-001", LaunchRequest{})
	assert.Equal(t, http.StatusBadRequest, rr.Code)
}

func TestLaunchGame_InvalidRNG_Returns403(t *testing.T) {
	app, h := setupTestApp()
	// Add a game with no cert registered
	h.catalog = append(h.catalog, Game{
		GameID: "GAME-UNCERTIFIED", Name: "Uncertified Slot",
		Provider: "PRAGMATIC", Category: GameCategorySlots,
		Active: true,
	})
	req := LaunchRequest{CPF: "99988877766"}
	rr := doRequest(t, app, "POST", "/games/launch/GAME-UNCERTIFIED", req)
	assert.Equal(t, http.StatusForbidden, rr.Code)
}

// ── Round complete ────────────────────────────────────────────────────────────

func TestRoundComplete_ValidRound(t *testing.T) {
	app, h := setupTestApp()

	// Create a session first
	sess := &GameSession{
		SessionID: "sess-001", GameID: "GAME-SLOTS-001", CPF: "12345678909",
		Token: "tok-001", Status: GameStatusActive,
		LaunchedAt: time.Now(), LastActivity: time.Now(),
	}
	h.sessions["sess-001"] = sess

	req := RoundCompleteRequest{
		RoundID:   "round-001",
		SessionID: "sess-001",
		GameID:    "GAME-SLOTS-001",
		CPF:       "12345678909",
		BetAmount: 10.00,
		WinAmount: 25.00,
		Outcome:   "WIN",
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
	payload, err := json.Marshal(req)
	require.NoError(t, err)

	mac := hmac.New(sha256.New, []byte("demo-secret-pragmatic"))
	mac.Write(payload)
	signature := hex.EncodeToString(mac.Sum(nil))

	httpReq := httptest.NewRequest(http.MethodPost, "/games/round/complete", bytes.NewReader(payload))
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("X-GAL-Signature", signature)

	rr := httptest.NewRecorder()
	app.ServeHTTP(rr, httpReq)
	assert.Equal(t, http.StatusOK, rr.Code)
}

func TestRoundComplete_UnknownSession_Returns404(t *testing.T) {
	app, _ := setupTestApp()
	req := RoundCompleteRequest{
		RoundID:   "round-999",
		SessionID: "nonexistent-session",
		GameID:    "GAME-SLOTS-001",
		CPF:       "12345678909",
		BetAmount: 5.00,
		WinAmount: 0.00,
		Outcome:   "LOSS",
	}
	rr := doRequest(t, app, "POST", "/games/round/complete", req)
	assert.Equal(t, http.StatusNotFound, rr.Code)
}

// ── Sessions ──────────────────────────────────────────────────────────────────

func TestGetSessions_NoActiveSessions(t *testing.T) {
	app, _ := setupTestApp()
	rr := doRequest(t, app, "GET", "/games/sessions/00000000000", nil)
	assert.Equal(t, http.StatusOK, rr.Code)

	var result map[string]interface{}
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &result))
	assert.Equal(t, float64(0), result["total"])
}

func TestGetSessions_ReturnsActiveSessions(t *testing.T) {
	app, h := setupTestApp()
	h.sessions["s1"] = &GameSession{
		SessionID: "s1", CPF: "77766655544", GameID: "GAME-SLOTS-001",
		Status: GameStatusActive, LaunchedAt: time.Now(), LastActivity: time.Now(),
	}
	rr := doRequest(t, app, "GET", "/games/sessions/77766655544", nil)
	assert.Equal(t, http.StatusOK, rr.Code)
	var result map[string]interface{}
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &result))
	assert.Equal(t, float64(1), result["total"])
}

// ── RNG validation ────────────────────────────────────────────────────────────

func TestValidateRNG_KnownCertifiedGame(t *testing.T) {
	app, _ := setupTestApp()
	req := RNGValidationRequest{GameID: "GAME-SLOTS-001"}
	rr := doRequest(t, app, "POST", "/games/rng/validate", req)
	assert.Equal(t, http.StatusOK, rr.Code)

	var result RNGValidationResult
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &result))
	assert.True(t, result.Valid)
	assert.Equal(t, "RNG certification valid", result.Message)
}

func TestValidateRNG_UnknownGame_ReturnsInvalid(t *testing.T) {
	app, _ := setupTestApp()
	req := RNGValidationRequest{GameID: "GAME-UNKNOWN"}
	rr := doRequest(t, app, "POST", "/games/rng/validate", req)
	assert.Equal(t, http.StatusUnprocessableEntity, rr.Code)

	var result RNGValidationResult
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &result))
	assert.False(t, result.Valid)
}

func TestValidateRNG_ExpiredCert(t *testing.T) {
	_, h := setupTestApp()
	past := time.Now().Add(-24 * time.Hour)
	h.rng.certs["GAME-EXPIRED"] = certRecord{
		GameID: "GAME-EXPIRED", CertBody: RNGCertGLI,
		CertNumber: "GLI-EXPIRED", IssuedAt: past.Add(-365 * 24 * time.Hour),
		ExpiresAt: past, Active: true,
	}
	result := h.rng.Validate(RNGValidationRequest{GameID: "GAME-EXPIRED"})
	assert.False(t, result.Valid)
	assert.Contains(t, result.Message, "expired")
}

// ── SIGAP round report ────────────────────────────────────────────────────────

func TestSIGAPReport_ReturnsReportForPeriod(t *testing.T) {
	app, h := setupTestApp()

	// Inject a completed round
	now := time.Now().UTC()
	h.sigap.RecordRound(Round{
		RoundID:     "sigap-round-001",
		SessionID:   "sess-sigap",
		GameID:      "GAME-SLOTS-001",
		CPF:         "12345678909",
		BetAmount:   50.00,
		WinAmount:   20.00,
		Outcome:     RoundOutcomeLoss,
		StartedAt:   now.Add(-10 * time.Second),
		CompletedAt: now,
	})

	period := now.Format("2006-01")
	rr := doRequest(t, app, "GET", "/games/sigap-report?period="+period, nil)
	assert.Equal(t, http.StatusOK, rr.Code)

	var report SIGAPRoundReport
	require.NoError(t, json.Unmarshal(rr.Body.Bytes(), &report))
	assert.Equal(t, period, report.Period)
	assert.GreaterOrEqual(t, report.TotalRounds, 1)
}

func TestSIGAPReport_InvalidPeriod_Returns400(t *testing.T) {
	app, _ := setupTestApp()
	rr := doRequest(t, app, "GET", "/games/sigap-report?period=not-a-period", nil)
	assert.Equal(t, http.StatusBadRequest, rr.Code)
}

// ── RNG validator unit tests ──────────────────────────────────────────────────

func TestRNGValidator_ValidCert(t *testing.T) {
	v := NewRNGValidator()
	result := v.Validate(RNGValidationRequest{GameID: "GAME-SLOTS-001"})
	assert.True(t, result.Valid)
}

func TestRNGValidator_IsGameCertified(t *testing.T) {
	v := NewRNGValidator()
	assert.True(t, v.IsGameCertified("GAME-LIVE-001"))
	assert.False(t, v.IsGameCertified("GAME-DOES-NOT-EXIST"))
}

// ── SIGAP round reporter unit tests ──────────────────────────────────────────

func TestSIGAPRoundsReporter_GGRComputation(t *testing.T) {
	reporter := NewSIGAPRoundsReporter(seedCatalog())
	reporter.RecordRound(Round{
		RoundID: "ggr-01", GameID: "GAME-SLOTS-001", CPF: "CPF01",
		BetAmount: 100.00, WinAmount: 30.00, Outcome: RoundOutcomeLoss,
		CompletedAt: time.Now().UTC(),
	})
	period := time.Now().UTC().Format("2006-01")
	rep, err := reporter.GenerateReport(period)
	require.NoError(t, err)
	assert.Equal(t, roundFloat(70.00), rep.TotalGGR)
}

func TestSIGAPRoundsReporter_EmptyPeriod(t *testing.T) {
	reporter := NewSIGAPRoundsReporter(seedCatalog())
	rep, err := reporter.GenerateReport("2000-01")
	require.NoError(t, err)
	assert.Equal(t, 0, rep.TotalRounds)
}
