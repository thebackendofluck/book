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
	"math"
	"time"

	"github.com/google/uuid"
)

const (
	// BrazilianTaxRate is the fixed income tax rate on gambling winnings.
	// Source: Lei 14.790/2023, Art. 31, §1.
	BrazilianTaxRate = 0.15

	// MonthlyExemptionThreshold is the net winnings threshold, aggregated per
	// CPF per calendar month, below which income tax is not withheld.
	// R$2,112.00 for 2024. Note this is a MONTHLY threshold, not annual: a
	// player's net prizes are netted across the whole month before the
	// exemption and 15% rate are applied.
	// Source: Lei 14.790/2023, Art. 31, §2; Instrução Normativa RFB 2.178/2024.
	MonthlyExemptionThreshold = 2112.00
)

// TaxCalculator handles Brazilian income tax computation and withholding.
type TaxCalculator struct {
	store  *Store
	logger *slog.Logger
}

// NewTaxCalculator creates a TaxCalculator.
func NewTaxCalculator(store *Store, logger *slog.Logger) *TaxCalculator {
	return &TaxCalculator{store: store, logger: logger}
}

// toCentavos converts a BRL amount to integer centavos, rounding to the
// nearest centavo. Comparisons against the exemption threshold are done in
// integer centavos (not float64) so that amounts landing exactly on the
// threshold boundary aren't misclassified by floating-point error. Amounts
// throughout this file are conceptually centavos even though they are
// carried as float64 BRL for compatibility with callers.
func toCentavos(amountBRL float64) int64 {
	return int64(math.Round(amountBRL * 100))
}

// applyMonthlyExemption computes the taxable amount and tax due on a net
// amount (BRL), applying the monthly exemption threshold shared by both
// CalculateTax and CalculateMonthlyTax below.
func applyMonthlyExemption(netAmountBRL float64) (taxableAmount, taxAmount float64) {
	netCentavos := toCentavos(netAmountBRL)
	thresholdCentavos := toCentavos(MonthlyExemptionThreshold)
	if netCentavos <= thresholdCentavos {
		return 0, 0
	}
	taxableAmount = float64(netCentavos-thresholdCentavos) / 100
	taxAmount = taxableAmount * BrazilianTaxRate
	return
}

// CalculateTax computes the income tax due on a single settled bet, treating
// the bet's profit as a standalone monthly net winnings figure and applying
// the same MonthlyExemptionThreshold used by CalculateMonthlyTax. Returns
// the tax amount and net payout after tax.
//
// Because the exemption is legally assessed on the CPF's aggregated net
// winnings for the whole calendar month (not per bet), this per-bet
// withholding is a conservative estimate: most individual bets fall under
// the threshold and are settled with no withholding here. The authoritative
// monthly figure is reconciled via WithholdTax/CalculateMonthlyTax, which
// nets all of a player's bets for the period and withholds any tax still
// owed once the aggregated exemption is exceeded.
func CalculateTax(stake, grossReturn float64) (taxAmount, netPayout float64) {
	profit := grossReturn - stake
	if profit <= 0 {
		return 0, grossReturn
	}
	_, taxAmount = applyMonthlyExemption(profit)
	netPayout = grossReturn - taxAmount
	return
}

// CalculateMonthlyTax computes the tax owed on a player's aggregated net
// winnings for a single calendar month, applying the R$2,112 monthly
// exemption threshold. This is the authoritative calculation used for
// monthly IRPF withholding via WithholdTax; CalculateTax above applies the
// same logic per-bet as a conservative real-time estimate.
func CalculateMonthlyTax(monthlyNetWinnings float64) (taxableAmount, taxAmount float64) {
	if monthlyNetWinnings <= 0 {
		return 0, 0
	}
	return applyMonthlyExemption(monthlyNetWinnings)
}

// WithholdTax processes income tax withholding for a CPF for a single
// calendar month. period must identify one calendar month (e.g. "2024-03"),
// matching the monthly aggregation the exemption threshold is legally
// assessed against — see MonthlyExemptionThreshold.
// Persists the withholding record and returns it.
func (t *TaxCalculator) WithholdTax(ctx context.Context, cpf, period string) (*TaxWithholding, error) {
	// Fetch total winnings and stakes for the period.
	totals, err := t.store.GetPlayerPeriodTotals(ctx, cpf, period)
	if err != nil {
		return nil, fmt.Errorf("get player period totals: %w", err)
	}

	totalWinnings := totals["total_winnings"]
	totalStake := totals["total_stake"]
	netWinnings := totalWinnings - totalStake

	taxableAmount, taxAmount := CalculateMonthlyTax(netWinnings)

	now := time.Now().UTC()
	withholding := &TaxWithholding{
		ID:            uuid.NewString(),
		CPF:           cpf,
		Period:        period,
		TotalWinnings: totalWinnings,
		TotalStake:    totalStake,
		NetWinnings:   netWinnings,
		TaxableAmount: taxableAmount,
		TaxRate:       BrazilianTaxRate,
		TaxAmount:     taxAmount,
		Withheld:      taxAmount > 0,
		CreatedAt:     now,
	}

	if taxAmount > 0 {
		withholding.WithheldAt = &now
	}

	if err := t.store.SaveTaxWithholding(ctx, withholding); err != nil {
		return nil, fmt.Errorf("save tax withholding: %w", err)
	}

	t.logger.Info("tax withholding processed",
		"cpf", maskCPF(cpf),
		"period", period,
		"net_winnings", netWinnings,
		"taxable_amount", taxableAmount,
		"tax_amount", taxAmount,
	)

	return withholding, nil
}

// maskCPF partially masks a CPF for logging privacy.
func maskCPF(cpf string) string {
	if len(cpf) < 5 {
		return cpf
	}
	runes := []rune(cpf)
	for i := 3; i < len(runes)-2; i++ {
		if runes[i] >= '0' && runes[i] <= '9' {
			runes[i] = '*'
		}
	}
	return string(runes)
}
