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
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store handles all PostgreSQL operations for the wallet service.
// Balance mutations use SERIALIZABLE isolation to prevent race conditions.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a new Store.
func NewStore(pool *pgxpool.Pool) *Store {
	return &Store{pool: pool}
}

// GetWallet retrieves a wallet by CPF.
func (s *Store) GetWallet(ctx context.Context, cpf string) (*Wallet, error) {
	w := &Wallet{}
	err := s.pool.QueryRow(ctx, `
		SELECT cpf, balance, bonus_balance, pending_debits, blocked, block_reason, created_at, updated_at
		FROM wallets WHERE cpf = $1`, cpf,
	).Scan(&w.CPF, &w.Balance, &w.BonusBalance, &w.PendingDebits,
		&w.Blocked, &w.BlockReason, &w.CreatedAt, &w.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("get wallet: %w", err)
	}
	return w, nil
}

// CreateWallet creates a new zero-balance wallet for a player.
func (s *Store) CreateWallet(ctx context.Context, cpf string) (*Wallet, error) {
	now := time.Now().UTC()
	_, err := s.pool.Exec(ctx, `
		INSERT INTO wallets (cpf, balance, bonus_balance, pending_debits, blocked, created_at, updated_at)
		VALUES ($1, 0, 0, 0, false, $2, $2)
		ON CONFLICT (cpf) DO NOTHING`, cpf, now)
	if err != nil {
		return nil, fmt.Errorf("create wallet: %w", err)
	}
	return s.GetWallet(ctx, cpf)
}

// Credit adds the given amount to a wallet balance atomically.
// Uses SERIALIZABLE isolation to prevent concurrent balance corruption.
func (s *Store) Credit(ctx context.Context, cpf string, amount float64, tx *Transaction) error {
	dbTx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.Serializable})
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer dbTx.Rollback(ctx) //nolint:errcheck

	var currentBalance float64
	err = dbTx.QueryRow(ctx,
		`SELECT balance FROM wallets WHERE cpf = $1 FOR UPDATE`, cpf,
	).Scan(&currentBalance)
	if err != nil {
		return fmt.Errorf("lock wallet: %w", err)
	}

	newBalance := currentBalance + amount
	_, err = dbTx.Exec(ctx,
		`UPDATE wallets SET balance = $1, updated_at = $2 WHERE cpf = $3`,
		newBalance, time.Now().UTC(), cpf)
	if err != nil {
		return fmt.Errorf("update balance: %w", err)
	}

	tx.BalanceBefore = currentBalance
	tx.BalanceAfter = newBalance
	tx.Status = TxStatusCompleted

	_, err = dbTx.Exec(ctx, `
		INSERT INTO transactions (id, cpf, type, status, amount, balance_before, balance_after, reference_id, description, created_at, updated_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)`,
		tx.ID, tx.CPF, tx.Type, tx.Status, tx.Amount,
		tx.BalanceBefore, tx.BalanceAfter, tx.ReferenceID, tx.Description, time.Now().UTC(),
	)
	if err != nil {
		return fmt.Errorf("insert transaction: %w", err)
	}

	return dbTx.Commit(ctx)
}

// Debit removes the given amount from a wallet balance atomically.
// Returns an error if the wallet has insufficient funds.
func (s *Store) Debit(ctx context.Context, cpf string, amount float64, tx *Transaction) error {
	dbTx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.Serializable})
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer dbTx.Rollback(ctx) //nolint:errcheck

	var currentBalance float64
	err = dbTx.QueryRow(ctx,
		`SELECT balance FROM wallets WHERE cpf = $1 FOR UPDATE`, cpf,
	).Scan(&currentBalance)
	if err != nil {
		return fmt.Errorf("lock wallet: %w", err)
	}

	if currentBalance < amount {
		return ErrInsufficientFunds
	}

	newBalance := currentBalance - amount
	_, err = dbTx.Exec(ctx,
		`UPDATE wallets SET balance = $1, updated_at = $2 WHERE cpf = $3`,
		newBalance, time.Now().UTC(), cpf)
	if err != nil {
		return fmt.Errorf("update balance: %w", err)
	}

	tx.BalanceBefore = currentBalance
	tx.BalanceAfter = newBalance
	tx.Status = TxStatusCompleted

	_, err = dbTx.Exec(ctx, `
		INSERT INTO transactions (id, cpf, type, status, amount, balance_before, balance_after, reference_id, description, created_at, updated_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)`,
		tx.ID, tx.CPF, tx.Type, tx.Status, tx.Amount,
		tx.BalanceBefore, tx.BalanceAfter, tx.ReferenceID, tx.Description, time.Now().UTC(),
	)
	if err != nil {
		return fmt.Errorf("insert transaction: %w", err)
	}

	return dbTx.Commit(ctx)
}

// GetTransactions returns the transaction history for a player.
func (s *Store) GetTransactions(ctx context.Context, cpf string, limit, offset int) ([]Transaction, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, cpf, type, status, amount, balance_before, balance_after,
		       reference_id, description, created_at, updated_at
		FROM transactions WHERE cpf = $1
		ORDER BY created_at DESC
		LIMIT $2 OFFSET $3`, cpf, limit, offset,
	)
	if err != nil {
		return nil, fmt.Errorf("query transactions: %w", err)
	}
	defer rows.Close()

	var txs []Transaction
	for rows.Next() {
		var t Transaction
		if err := rows.Scan(&t.ID, &t.CPF, &t.Type, &t.Status, &t.Amount,
			&t.BalanceBefore, &t.BalanceAfter, &t.ReferenceID,
			&t.Description, &t.CreatedAt, &t.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan transaction: %w", err)
		}
		txs = append(txs, t)
	}
	return txs, rows.Err()
}

// CreatePIXPayment persists a new PIX payment record.
func (s *Store) CreatePIXPayment(ctx context.Context, p *PIXPayment) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO pix_payments (id, cpf, type, amount, status, e2e_id, qr_code, qr_code_base64, copy_paste_code, pix_key, bank_account, expires_at, created_at, updated_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)`,
		p.ID, p.CPF, p.Type, p.Amount, p.Status, p.E2EID,
		p.QRCode, p.QRCodeBase64, p.CopyPasteCode, p.PixKey, p.BankAccount,
		p.ExpiresAt, time.Now().UTC(),
	)
	return err
}

// GetPIXPayment retrieves a PIX payment by ID.
func (s *Store) GetPIXPayment(ctx context.Context, id string) (*PIXPayment, error) {
	p := &PIXPayment{}
	err := s.pool.QueryRow(ctx, `
		SELECT id, cpf, type, amount, status, e2e_id, qr_code, qr_code_base64, copy_paste_code, pix_key, bank_account, transaction_id, expires_at, confirmed_at, created_at, updated_at
		FROM pix_payments WHERE id = $1`, id,
	).Scan(&p.ID, &p.CPF, &p.Type, &p.Amount, &p.Status, &p.E2EID,
		&p.QRCode, &p.QRCodeBase64, &p.CopyPasteCode, &p.PixKey,
		&p.BankAccount, &p.TransactionID, &p.ExpiresAt, &p.ConfirmedAt,
		&p.CreatedAt, &p.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("get pix payment: %w", err)
	}
	return p, nil
}

// ConfirmPIXDeposit marks a PIX payment as confirmed and credits the wallet atomically.
func (s *Store) ConfirmPIXDeposit(ctx context.Context, pixID, e2eID string, amount float64, cpf string) error {
	dbTx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.Serializable})
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer dbTx.Rollback(ctx) //nolint:errcheck

	now := time.Now().UTC()

	// Check idempotency — do not double-credit.
	var status PIXStatus
	err = dbTx.QueryRow(ctx,
		`SELECT status FROM pix_payments WHERE id = $1 FOR UPDATE`, pixID,
	).Scan(&status)
	if err != nil {
		return fmt.Errorf("lock pix payment: %w", err)
	}
	if status == PIXStatusConfirmed {
		return nil // Already processed — idempotent.
	}

	_, err = dbTx.Exec(ctx, `
		UPDATE pix_payments SET status = $1, e2e_id = $2, confirmed_at = $3, updated_at = $3
		WHERE id = $4`, PIXStatusConfirmed, e2eID, now, pixID)
	if err != nil {
		return fmt.Errorf("update pix payment: %w", err)
	}

	// Credit wallet.
	var currentBalance float64
	err = dbTx.QueryRow(ctx,
		`SELECT balance FROM wallets WHERE cpf = $1 FOR UPDATE`, cpf,
	).Scan(&currentBalance)
	if err != nil {
		return fmt.Errorf("lock wallet: %w", err)
	}
	newBalance := currentBalance + amount
	_, err = dbTx.Exec(ctx,
		`UPDATE wallets SET balance = $1, updated_at = $2 WHERE cpf = $3`,
		newBalance, now, cpf)
	if err != nil {
		return fmt.Errorf("credit wallet: %w", err)
	}

	return dbTx.Commit(ctx)
}

// GetDailyTotals returns aggregated transaction totals for a given date (YYYY-MM-DD).
func (s *Store) GetDailyTotals(ctx context.Context, date string) (map[TransactionType]float64, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT type, COALESCE(SUM(amount), 0)
		FROM transactions
		WHERE DATE(created_at) = $1 AND status = 'completed'
		GROUP BY type`, date)
	if err != nil {
		return nil, fmt.Errorf("get daily totals: %w", err)
	}
	defer rows.Close()

	totals := make(map[TransactionType]float64)
	for rows.Next() {
		var txType TransactionType
		var sum float64
		if err := rows.Scan(&txType, &sum); err != nil {
			return nil, err
		}
		totals[txType] = sum
	}
	return totals, rows.Err()
}

// GetTotalBalance returns the sum of all wallet balances (for reconciliation).
func (s *Store) GetTotalBalance(ctx context.Context) (float64, error) {
	var total float64
	err := s.pool.QueryRow(ctx, `SELECT COALESCE(SUM(balance), 0) FROM wallets WHERE NOT blocked`).Scan(&total)
	return total, err
}
