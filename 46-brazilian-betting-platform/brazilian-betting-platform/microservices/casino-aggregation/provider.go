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
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/rs/zerolog/log"
)

// GALProvider implements integration with the GAL (Game Aggregation Layer)
// protocol used by Brazilian-licensed game providers.
//
// The GAL protocol defines:
//   - Session token generation (JWT or opaque, provider-dependent)
//   - Round notification callbacks (HMAC-SHA256 signed)
//   - Game URL construction
//   - Wallet debit/credit notifications
//
// This implementation wraps multiple concrete providers behind a common
// interface, selecting the right one based on the provider ID embedded in
// the game catalog entry.

// ── Provider interface ────────────────────────────────────────────────────────

// Provider is the interface that all game provider integrations must satisfy.
type Provider interface {
	// BuildGameURL constructs the launch URL for a game session.
	BuildGameURL(session *GameSession, game *Game, currency, language string) (string, error)
	// VerifyCallback validates an inbound round-complete callback signature.
	VerifyCallback(r *http.Request, payload []byte) bool
	// Name returns the provider identifier string.
	Name() string
}

// ── GAL HTTP provider ─────────────────────────────────────────────────────────

// GALProvider implements the GAL protocol over HTTPS.
type GALProvider struct {
	name       string
	baseURL    string
	operatorID string
	secretKey  []byte
	httpClient *http.Client
}

// NewGALProvider creates a new GAL-protocol provider.
func NewGALProvider(name, baseURL, operatorID, secretKey string) *GALProvider {
	return &GALProvider{
		name:       name,
		baseURL:    baseURL,
		operatorID: operatorID,
		secretKey:  []byte(secretKey),
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
}

// BuildGameURL constructs a signed launch URL per the GAL spec.
//
// URL format:
//
//	https://{provider-base}/launch?
//	  operator={operatorID}
//	  &game={gameID}
//	  &session={sessionToken}
//	  &currency={currency}
//	  &lang={language}
//	  &ts={unix_timestamp}
//	  &sig={hmac_sha256}
func (p *GALProvider) BuildGameURL(session *GameSession, game *Game, currency, language string) (string, error) {
	ts := fmt.Sprintf("%d", time.Now().Unix())
	sigPayload := strings.Join([]string{
		p.operatorID,
		game.GameID,
		session.SessionID,
		session.Token,
		ts,
	}, ":")

	sig := p.sign(sigPayload)

	url := fmt.Sprintf(
		"%s/launch?operator=%s&game=%s&session=%s&currency=%s&lang=%s&ts=%s&sig=%s",
		p.baseURL,
		p.operatorID,
		game.GameID,
		session.Token,
		currency,
		language,
		ts,
		sig,
	)

	log.Debug().
		Str("provider", p.name).
		Str("game_id", game.GameID).
		Str("session_id", session.SessionID).
		Msg("Built game URL")

	return url, nil
}

// VerifyCallback validates the HMAC-SHA256 signature on an inbound callback.
// The provider sends the signature in the X-GAL-Signature header.
func (p *GALProvider) VerifyCallback(r *http.Request, payload []byte) bool {
	gotSig := r.Header.Get("X-GAL-Signature")
	if gotSig == "" {
		return false
	}
	expected := p.sign(string(payload))
	return hmac.Equal([]byte(gotSig), []byte(expected))
}

// Name returns the provider identifier.
func (p *GALProvider) Name() string { return p.name }

// sign computes HMAC-SHA256 over the given message.
func (p *GALProvider) sign(msg string) string {
	mac := hmac.New(sha256.New, p.secretKey)
	mac.Write([]byte(msg))
	return hex.EncodeToString(mac.Sum(nil))
}

// ── Provider registry ─────────────────────────────────────────────────────────

// ProviderRegistry maps provider names to Provider implementations.
type ProviderRegistry struct {
	providers map[string]Provider
}

// NewProviderRegistry creates a registry and registers default providers.
func NewProviderRegistry() *ProviderRegistry {
	reg := &ProviderRegistry{providers: make(map[string]Provider)}

	// Register providers from environment configuration
	providerCfg := []struct{ name, baseURL, opID, secret string }{
		{
			name:    "EVOLUTION",
			baseURL: envOr("EVOLUTION_BASE_URL", "https://evolution-demo.example.com"),
			opID:    envOr("EVOLUTION_OPERATOR_ID", "BRAZILBET"),
			secret:  envOr("EVOLUTION_SECRET", "demo-secret-evolution"),
		},
		{
			name:    "PRAGMATIC",
			baseURL: envOr("PRAGMATIC_BASE_URL", "https://pragmatic-demo.example.com"),
			opID:    envOr("PRAGMATIC_OPERATOR_ID", "BRAZILBET"),
			secret:  envOr("PRAGMATIC_SECRET", "demo-secret-pragmatic"),
		},
		{
			name:    "NETENT",
			baseURL: envOr("NETENT_BASE_URL", "https://netent-demo.example.com"),
			opID:    envOr("NETENT_OPERATOR_ID", "BRAZILBET"),
			secret:  envOr("NETENT_SECRET", "demo-secret-netent"),
		},
	}

	for _, cfg := range providerCfg {
		reg.Register(NewGALProvider(cfg.name, cfg.baseURL, cfg.opID, cfg.secret))
	}

	return reg
}

// Register adds a provider to the registry.
func (r *ProviderRegistry) Register(p Provider) {
	r.providers[strings.ToUpper(p.Name())] = p
	log.Info().Str("provider", p.Name()).Msg("Game provider registered")
}

// Get retrieves a provider by name (case-insensitive).
func (r *ProviderRegistry) Get(name string) (Provider, bool) {
	p, ok := r.providers[strings.ToUpper(name)]
	return p, ok
}

// envOr returns the env variable value or the fallback.
func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
