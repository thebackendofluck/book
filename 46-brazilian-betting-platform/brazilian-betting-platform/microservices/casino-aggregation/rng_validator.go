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
	"fmt"
	"strings"
	"time"
)

// RNGValidator checks that a game's RNG certificate is valid per Brazilian
// gambling regulations (Portaria MF 615/2023, Anexo II — technical standards).
//
// Accepted certification bodies:
//   GLI   — Gaming Laboratories International
//   BMM   — BMM Testlabs
//   ECOGRA — eCOGRA
//   ITECH  — iTech Labs
//
// Certificates must not be expired and must reference a known issuer.
// In production, the certificate database is populated by the compliance team
// via the SPA (Secretaria de Prêmios e Apostas) certification registry.

// certRecord holds a single certification entry.
type certRecord struct {
	GameID     string
	CertBody   RNGCertification
	CertNumber string
	IssuedAt   time.Time
	ExpiresAt  time.Time
	Active     bool
}

// RNGValidator maintains the in-memory cert registry and validates game RNGs.
type RNGValidator struct {
	certs map[string]certRecord // key: gameID
}

// NewRNGValidator initialises the validator with a seed registry.
func NewRNGValidator() *RNGValidator {
	v := &RNGValidator{
		certs: make(map[string]certRecord),
	}
	v.seedRegistry()
	return v
}

// RegisterCert adds or updates a certification record for a game.
func (v *RNGValidator) RegisterCert(
	gameID, certBody, certNumber string,
	issuedAt, expiresAt time.Time,
) {
	v.certs[gameID] = certRecord{
		GameID:     gameID,
		CertBody:   RNGCertification(strings.ToUpper(certBody)),
		CertNumber: certNumber,
		IssuedAt:   issuedAt,
		ExpiresAt:  expiresAt,
		Active:     true,
	}
}

// Validate checks whether a game's RNG certification is valid at the current time.
func (v *RNGValidator) Validate(req RNGValidationRequest) RNGValidationResult {
	now := time.Now().UTC()

	record, found := v.certs[req.GameID]
	if !found {
		return RNGValidationResult{
			GameID:     req.GameID,
			Valid:      false,
			CertBody:   req.CertBody,
			CertNumber: req.CertNumber,
			Message:    "Game not found in RNG certification registry",
			CheckedAt:  now.Format(time.RFC3339),
		}
	}

	if !record.Active {
		return RNGValidationResult{
			GameID:     req.GameID,
			Valid:      false,
			CertBody:   string(record.CertBody),
			CertNumber: record.CertNumber,
			Message:    "Certification has been revoked",
			CheckedAt:  now.Format(time.RFC3339),
		}
	}

	if now.After(record.ExpiresAt) {
		return RNGValidationResult{
			GameID:     req.GameID,
			Valid:      false,
			CertBody:   string(record.CertBody),
			CertNumber: record.CertNumber,
			ExpiresAt:  record.ExpiresAt.Format(time.RFC3339),
			Message:    fmt.Sprintf("Certification expired on %s", record.ExpiresAt.Format("2006-01-02")),
			CheckedAt:  now.Format(time.RFC3339),
		}
	}

	if req.CertBody != "" && !strings.EqualFold(req.CertBody, string(record.CertBody)) {
		return RNGValidationResult{
			GameID:     req.GameID,
			Valid:      false,
			CertBody:   string(record.CertBody),
			CertNumber: record.CertNumber,
			Message:    fmt.Sprintf("Cert body mismatch: expected %s, got %s", record.CertBody, req.CertBody),
			CheckedAt:  now.Format(time.RFC3339),
		}
	}

	if req.CertNumber != "" && req.CertNumber != record.CertNumber {
		return RNGValidationResult{
			GameID:     req.GameID,
			Valid:      false,
			CertBody:   string(record.CertBody),
			CertNumber: record.CertNumber,
			Message:    "Certificate number mismatch",
			CheckedAt:  now.Format(time.RFC3339),
		}
	}

	return RNGValidationResult{
		GameID:     req.GameID,
		Valid:      true,
		CertBody:   string(record.CertBody),
		CertNumber: record.CertNumber,
		ExpiresAt:  record.ExpiresAt.Format(time.RFC3339),
		Message:    "RNG certification valid",
		CheckedAt:  now.Format(time.RFC3339),
	}
}

// IsGameCertified returns true if the game has a valid (non-expired) certificate.
func (v *RNGValidator) IsGameCertified(gameID string) bool {
	r, ok := v.certs[gameID]
	if !ok {
		return false
	}
	return r.Active && time.Now().UTC().Before(r.ExpiresAt)
}

// seedRegistry populates demo certification records.
func (v *RNGValidator) seedRegistry() {
	now := time.Now().UTC()
	twoyears := now.Add(2 * 365 * 24 * time.Hour)

	seeds := []struct {
		gameID, certBody, certNumber string
	}{
		{"GAME-SLOTS-001", "GLI", "GLI-BR-2024-001"},
		{"GAME-SLOTS-002", "BMM", "BMM-BR-2024-045"},
		{"GAME-TABLE-001", "ECOGRA", "ECOGRA-BR-2024-010"},
		{"GAME-LIVE-001", "GLI", "GLI-BR-2024-002"},
		{"GAME-INSTANT-001", "ITECH", "ITECH-BR-2024-099"},
	}

	for _, s := range seeds {
		v.RegisterCert(s.gameID, s.certBody, s.certNumber, now, twoyears)
	}
}
