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
	"time"
)

// VerifiedAccount represents a bank account that has been verified for closed-loop
// payment compliance under Portaria 615/2024, Art. 14.
type VerifiedAccount struct {
	CPF         string    `json:"cpf" db:"cpf"`
	PixKey      string    `json:"pix_key" db:"pix_key"`
	PixKeyType  string    `json:"pix_key_type" db:"pix_key_type"`
	BankISPB    string    `json:"bank_ispb" db:"bank_ispb"`
	BankName    string    `json:"bank_name" db:"bank_name"`
	VerifiedAt  time.Time `json:"verified_at" db:"verified_at"`
	VerifiedBy  string    `json:"verified_by" db:"verified_by"` // method: pix_dict, manual
}

// ClosedLoopEnforcer enforces the closed payment loop requirement:
// players may only withdraw to bank accounts they own and which have
// been verified by the operator (Portaria 615/2024).
type ClosedLoopEnforcer struct {
	store  *Store
	logger *slog.Logger
}

// NewClosedLoopEnforcer creates a new ClosedLoopEnforcer.
func NewClosedLoopEnforcer(store *Store, logger *slog.Logger) *ClosedLoopEnforcer {
	return &ClosedLoopEnforcer{store: store, logger: logger}
}

// Verify checks that the given PIX key is eligible for withdrawal
// under the closed-loop rules for the specified CPF.
//
// Rules (Portaria 615/2024, Art. 14):
//  1. CPF key: must exactly match the player's CPF.
//  2. Non-CPF key: must be pre-registered and verified against BACEN DICT.
//  3. Third-party keys are not permitted (no B2C payments, no P2P forwarding).
func (e *ClosedLoopEnforcer) Verify(ctx context.Context, cpf, pixKey, pixKeyType string) error {
	switch pixKeyType {
	case "cpf":
		clean := stripNonDigits(cpf)
		cleanKey := stripNonDigits(pixKey)
		if clean != cleanKey {
			e.logger.Warn("closed loop violation: CPF key mismatch",
				"player_cpf", maskCPF(cpf),
				"pix_key", maskCPF(pixKey),
			)
			return fmt.Errorf("withdrawal rejected: PIX CPF key must belong to the player (closed loop)")
		}
		return nil

	case "email", "phone", "random":
		verified, err := e.isKeyVerified(ctx, cpf, pixKey)
		if err != nil {
			return fmt.Errorf("verify pix key: %w", err)
		}
		if !verified {
			return fmt.Errorf("PIX key '%s' has not been pre-registered and verified for this account", pixKey)
		}
		return nil

	default:
		return fmt.Errorf("unsupported PIX key type: %s", pixKeyType)
	}
}

// isKeyVerified checks the verified_pix_keys table for a matching record.
// In production this also queries BACEN DICT to confirm key ownership.
func (e *ClosedLoopEnforcer) isKeyVerified(ctx context.Context, cpf, pixKey string) (bool, error) {
	var count int
	err := e.store.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM verified_pix_keys
		WHERE cpf = $1 AND pix_key = $2`, cpf, pixKey,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("query verified_pix_keys: %w", err)
	}
	return count > 0, nil
}

// RegisterKey stores a player's PIX key after BACEN DICT verification.
// This is called during the player onboarding / KYC process.
func (e *ClosedLoopEnforcer) RegisterKey(ctx context.Context, acc *VerifiedAccount) error {
	_, err := e.store.pool.Exec(ctx, `
		INSERT INTO verified_pix_keys (cpf, pix_key, pix_key_type, bank_ispb, bank_name, verified_at, verified_by)
		VALUES ($1,$2,$3,$4,$5,$6,$7)
		ON CONFLICT (cpf, pix_key) DO UPDATE
		SET verified_at = $6, verified_by = $7`,
		acc.CPF, acc.PixKey, acc.PixKeyType,
		acc.BankISPB, acc.BankName,
		time.Now().UTC(), acc.VerifiedBy,
	)
	if err != nil {
		return fmt.Errorf("register pix key: %w", err)
	}
	e.logger.Info("pix key registered",
		"cpf", maskCPF(acc.CPF),
		"pix_key_type", acc.PixKeyType,
		"bank_name", acc.BankName,
	)
	return nil
}
