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

import "time"

// TransactionType classifies a wallet movement.
type TransactionType string

const (
	TxTypeDeposit    TransactionType = "deposit"
	TxTypeWithdrawal TransactionType = "withdrawal"
	TxTypeBetDebit   TransactionType = "bet_debit"
	TxTypeWinCredit  TransactionType = "win_credit"
	TxTypeRefund     TransactionType = "refund"
	TxTypeTax        TransactionType = "tax_withholding"
	TxTypeBonus      TransactionType = "bonus"
	TxTypeAdjustment TransactionType = "adjustment"
)

// TransactionStatus represents the lifecycle of a transaction.
type TransactionStatus string

const (
	TxStatusPending   TransactionStatus = "pending"
	TxStatusCompleted TransactionStatus = "completed"
	TxStatusFailed    TransactionStatus = "failed"
	TxStatusReversed  TransactionStatus = "reversed"
)

// PIXStatus represents the lifecycle of a PIX payment.
type PIXStatus string

const (
	PIXStatusPending   PIXStatus = "pending"
	PIXStatusConfirmed PIXStatus = "confirmed"
	PIXStatusExpired   PIXStatus = "expired"
	PIXStatusFailed    PIXStatus = "failed"
	PIXStatusRefunded  PIXStatus = "refunded"
)

// Wallet represents a player's financial account.
type Wallet struct {
	CPF           string    `json:"cpf" db:"cpf"`
	Balance       float64   `json:"balance" db:"balance"`           // Available balance in BRL
	BonusBalance  float64   `json:"bonus_balance" db:"bonus_balance"` // Non-withdrawable bonus
	PendingDebits float64   `json:"pending_debits" db:"pending_debits"` // Held for pending bets
	Blocked       bool      `json:"blocked" db:"blocked"`
	BlockReason   string    `json:"block_reason,omitempty" db:"block_reason"`
	CreatedAt     time.Time `json:"created_at" db:"created_at"`
	UpdatedAt     time.Time `json:"updated_at" db:"updated_at"`
}

// Transaction records a single wallet movement.
type Transaction struct {
	ID            string            `json:"id" db:"id"`
	CPF           string            `json:"cpf" db:"cpf"`
	Type          TransactionType   `json:"type" db:"type"`
	Status        TransactionStatus `json:"status" db:"status"`
	Amount        float64           `json:"amount" db:"amount"`
	BalanceBefore float64           `json:"balance_before" db:"balance_before"`
	BalanceAfter  float64           `json:"balance_after" db:"balance_after"`
	ReferenceID   string            `json:"reference_id,omitempty" db:"reference_id"` // Bet ID, PIX ID, etc.
	Description   string            `json:"description,omitempty" db:"description"`
	Metadata      map[string]string `json:"metadata,omitempty" db:"-"`
	CreatedAt     time.Time         `json:"created_at" db:"created_at"`
	UpdatedAt     time.Time         `json:"updated_at" db:"updated_at"`
}

// PIXPayment represents a PIX payment (deposit or withdrawal).
type PIXPayment struct {
	ID              string    `json:"id" db:"id"`
	CPF             string    `json:"cpf" db:"cpf"`
	Type            string    `json:"type" db:"type"` // deposit, withdrawal
	Amount          float64   `json:"amount" db:"amount"`
	Status          PIXStatus `json:"status" db:"status"`
	E2EID           string    `json:"e2e_id,omitempty" db:"e2e_id"` // Banco Central end-to-end ID
	QRCode          string    `json:"qr_code,omitempty" db:"qr_code"`
	QRCodeBase64    string    `json:"qr_code_base64,omitempty" db:"qr_code_base64"`
	CopyPasteCode   string    `json:"copy_paste_code,omitempty" db:"copy_paste_code"`
	PixKey          string    `json:"pix_key,omitempty" db:"pix_key"`        // Destination key for withdrawal
	BankAccount     string    `json:"bank_account,omitempty" db:"bank_account"` // Verified bank account
	TransactionID   string    `json:"transaction_id,omitempty" db:"transaction_id"`
	ExpiresAt       time.Time `json:"expires_at" db:"expires_at"`
	ConfirmedAt     *time.Time `json:"confirmed_at,omitempty" db:"confirmed_at"`
	CreatedAt       time.Time `json:"created_at" db:"created_at"`
	UpdatedAt       time.Time `json:"updated_at" db:"updated_at"`
}

// DepositRequest is the incoming payload for a PIX deposit.
type DepositRequest struct {
	Amount      float64 `json:"amount"`
	CallbackURL string  `json:"callback_url,omitempty"`
}

// WithdrawRequest is the incoming payload for a PIX withdrawal.
type WithdrawRequest struct {
	Amount     float64 `json:"amount"`
	PixKey     string  `json:"pix_key"`
	PixKeyType string  `json:"pix_key_type"` // cpf, email, phone, random
}

// PIXWebhookPayload is the webhook body sent by the PSP on payment confirmation.
type PIXWebhookPayload struct {
	PIXPaymentID string    `json:"pix_payment_id"`
	E2EID        string    `json:"e2e_id"`
	Amount       float64   `json:"amount"`
	Status       string    `json:"status"`
	PaidAt       time.Time `json:"paid_at"`
	PSPCode      string    `json:"psp_code,omitempty"`
}

// ReconciliationResult summarises a daily reconciliation run.
type ReconciliationResult struct {
	Date             string    `json:"date"`
	TotalDeposits    float64   `json:"total_deposits"`
	TotalWithdrawals float64   `json:"total_withdrawals"`
	TotalBetDebits   float64   `json:"total_bet_debits"`
	TotalWinCredits  float64   `json:"total_win_credits"`
	OpeningBalance   float64   `json:"opening_balance"`
	ClosingBalance   float64   `json:"closing_balance"`
	Discrepancy      float64   `json:"discrepancy"`
	Reconciled       bool      `json:"reconciled"`
	ProcessedAt      time.Time `json:"processed_at"`
}
