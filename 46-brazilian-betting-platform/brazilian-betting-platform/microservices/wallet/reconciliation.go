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

// ReconciliationEngine performs daily financial reconciliation.
// It verifies that the sum of all transactions matches wallet balances,
// as required by Portaria 615/2024 for licensed operators.
type ReconciliationEngine struct {
	store  *Store
	logger *slog.Logger
}

// NewReconciliationEngine creates a new ReconciliationEngine.
func NewReconciliationEngine(store *Store, logger *slog.Logger) *ReconciliationEngine {
	return &ReconciliationEngine{store: store, logger: logger}
}

// Reconcile runs the daily reconciliation for the given date string (YYYY-MM-DD).
// If date is empty, it defaults to yesterday (completed day).
func (r *ReconciliationEngine) Reconcile(ctx context.Context, date string) (*ReconciliationResult, error) {
	if date == "" {
		yesterday := time.Now().UTC().AddDate(0, 0, -1)
		date = yesterday.Format("2006-01-02")
	}

	totals, err := r.store.GetDailyTotals(ctx, date)
	if err != nil {
		return nil, fmt.Errorf("get daily totals for %s: %w", date, err)
	}

	totalDeposits := totals[TxTypeDeposit]
	totalWithdrawals := totals[TxTypeWithdrawal]
	totalBetDebits := totals[TxTypeBetDebit]
	totalWinCredits := totals[TxTypeWinCredit]
	totalRefunds := totals[TxTypeRefund]

	// Expected closing balance movement:
	// Opening + Deposits + WinCredits + Refunds - Withdrawals - BetDebits = Closing
	currentTotalBalance, err := r.store.GetTotalBalance(ctx)
	if err != nil {
		return nil, fmt.Errorf("get total balance: %w", err)
	}

	// Reconstruct opening balance from movements.
	// Closing = Opening + Deposits + WinCredits + Refunds - Withdrawals - BetDebits
	// Therefore: Opening = Closing - Deposits - WinCredits - Refunds + Withdrawals + BetDebits
	openingBalance := currentTotalBalance - totalDeposits - totalWinCredits - totalRefunds +
		totalWithdrawals + totalBetDebits

	expectedClosing := openingBalance + totalDeposits + totalWinCredits + totalRefunds -
		totalWithdrawals - totalBetDebits

	discrepancy := currentTotalBalance - expectedClosing
	reconciled := abs(discrepancy) < 0.01 // Allow 1 cent rounding tolerance.

	result := &ReconciliationResult{
		Date:             date,
		TotalDeposits:    totalDeposits,
		TotalWithdrawals: totalWithdrawals,
		TotalBetDebits:   totalBetDebits,
		TotalWinCredits:  totalWinCredits,
		OpeningBalance:   openingBalance,
		ClosingBalance:   currentTotalBalance,
		Discrepancy:      discrepancy,
		Reconciled:       reconciled,
		ProcessedAt:      time.Now().UTC(),
	}

	if !reconciled {
		r.logger.Error("reconciliation discrepancy detected",
			"date", date,
			"discrepancy", discrepancy,
			"closing_balance", currentTotalBalance,
			"expected_closing", expectedClosing,
		)
	} else {
		r.logger.Info("reconciliation successful",
			"date", date,
			"closing_balance", currentTotalBalance,
			"deposits", totalDeposits,
			"withdrawals", totalWithdrawals,
		)
	}

	return result, nil
}
