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
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// --- Unit tests for PIX helpers ---

func TestStripNonDigits(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"123.456.789-09", "12345678909"},
		{"(11) 99999-8888", "11999998888"},
		{"abc123", "123"},
		{"", ""},
		{"000.000.000-00", "00000000000"},
	}
	for _, tc := range tests {
		got := stripNonDigits(tc.input)
		if got != tc.want {
			t.Errorf("stripNonDigits(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

func TestMaskCPF(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"123.456.789-09", "123.***.***-09"},
		{"12", "12"},
		{"", ""},
	}
	for _, tc := range tests {
		got := maskCPF(tc.input)
		if got != tc.want {
			t.Errorf("maskCPF(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

func TestAbs(t *testing.T) {
	tests := []struct {
		input float64
		want  float64
	}{
		{-5.0, 5.0},
		{5.0, 5.0},
		{0.0, 0.0},
		{-0.001, 0.001},
	}
	for _, tc := range tests {
		got := abs(tc.input)
		if got != tc.want {
			t.Errorf("abs(%.4f) = %.4f, want %.4f", tc.input, got, tc.want)
		}
	}
}

// --- PIX amount validation tests ---

func TestPIXDepositMinimum(t *testing.T) {
	client := &PIXClient{store: nil, logger: noopLogger()}
	_, err := client.InitiateDeposit(context.Background(), "12345678909", 0.50)
	if err == nil {
		t.Error("expected error for amount below minimum")
	}
}

func TestPIXDepositMaximum(t *testing.T) {
	client := &PIXClient{store: nil, logger: noopLogger()}
	_, err := client.InitiateDeposit(context.Background(), "12345678909", 100000.00)
	if err == nil {
		t.Error("expected error for amount above maximum")
	}
}

func TestPIXWithdrawalMinimum(t *testing.T) {
	client := &PIXClient{store: nil, logger: noopLogger()}
	req := &WithdrawRequest{Amount: 5.00, PixKey: "12345678909", PixKeyType: "cpf"}
	_, err := client.InitiateWithdrawal(context.Background(), "12345678909", req)
	if err == nil {
		t.Error("expected error for withdrawal below minimum")
	}
}

func TestPIXWithdrawalMaximum(t *testing.T) {
	client := &PIXClient{store: nil, logger: noopLogger()}
	req := &WithdrawRequest{Amount: 50000.00, PixKey: "12345678909", PixKeyType: "cpf"}
	_, err := client.InitiateWithdrawal(context.Background(), "12345678909", req)
	if err == nil {
		t.Error("expected error for withdrawal above maximum")
	}
}

func TestPIXWithdrawalMissingKey(t *testing.T) {
	client := &PIXClient{store: nil, logger: noopLogger()}
	req := &WithdrawRequest{Amount: 100.00, PixKey: "", PixKeyType: "cpf"}
	_, err := client.InitiateWithdrawal(context.Background(), "12345678909", req)
	if err == nil {
		t.Error("expected error for missing pix_key")
	}
}

// --- Closed loop enforcement tests ---

func TestClosedLoop_CPFKeyMatch(t *testing.T) {
	enforcer := &ClosedLoopEnforcer{store: nil, logger: noopLogger()}
	// Matching CPF should pass.
	err := enforcer.Verify(context.Background(), "123.456.789-09", "12345678909", "cpf")
	if err != nil {
		t.Errorf("expected no error for matching CPF key, got: %v", err)
	}
}

func TestClosedLoop_CPFKeyMismatch(t *testing.T) {
	enforcer := &ClosedLoopEnforcer{store: nil, logger: noopLogger()}
	// Different CPF should be rejected.
	err := enforcer.Verify(context.Background(), "123.456.789-09", "98765432100", "cpf")
	if err == nil {
		t.Error("expected error for mismatched CPF key")
	}
}

func TestClosedLoop_UnsupportedKeyType(t *testing.T) {
	enforcer := &ClosedLoopEnforcer{store: nil, logger: noopLogger()}
	err := enforcer.Verify(context.Background(), "12345678909", "somekey", "cnpj")
	if err == nil {
		t.Error("expected error for unsupported PIX key type")
	}
}

// --- Reconciliation logic tests ---

func TestReconciliation_Balanced(t *testing.T) {
	// Simulate reconciliation with a balanced ledger.
	closing := 10000.0
	opening := 9000.0
	deposits := 2000.0
	withdrawals := 1000.0
	betDebits := 500.0
	winCredits := 500.0
	refunds := 0.0

	expected := opening + deposits + winCredits + refunds - withdrawals - betDebits
	discrepancy := closing - expected

	if abs(discrepancy) < 0.01 {
		// Balanced
	} else {
		t.Errorf("reconciliation should be balanced, discrepancy = %.4f", discrepancy)
	}
}

func TestReconciliation_Discrepancy(t *testing.T) {
	closing := 10000.0
	opening := 9000.0
	deposits := 2000.0
	withdrawals := 1000.0
	betDebits := 500.0
	winCredits := 400.0  // intentional discrepancy
	refunds := 0.0

	expected := opening + deposits + winCredits + refunds - withdrawals - betDebits
	discrepancy := closing - expected

	// 10000 - (9000 + 2000 + 400 + 0 - 1000 - 500) = 10000 - 9900 = 100
	if abs(discrepancy) < 0.01 {
		t.Error("expected discrepancy but got balanced ledger")
	}
}

// --- ErrInsufficientFunds tests ---

func TestInsufficientFundsError(t *testing.T) {
	err := ErrInsufficientFunds
	if !errors.Is(err, ErrInsufficientFunds) {
		t.Error("ErrInsufficientFunds should match itself")
	}
	if err.Error() != "insufficient funds" {
		t.Errorf("error message = %q", err.Error())
	}
}

// --- PIX QR code generation helpers ---

func TestGenerateTxID_NotEmpty(t *testing.T) {
	id := generateTxID()
	if id == "" {
		t.Error("expected non-empty tx ID")
	}
}

func TestGenerateTxID_Unique(t *testing.T) {
	ids := make(map[string]struct{})
	for i := 0; i < 100; i++ {
		id := generateTxID()
		if _, exists := ids[id]; exists {
			t.Errorf("duplicate tx ID generated: %s", id)
		}
		ids[id] = struct{}{}
	}
}

func TestBuildBRCode_NotEmpty(t *testing.T) {
	code := buildBRCode("00000000000000", 100.00, "txid1234")
	if code == "" {
		t.Error("BR code should not be empty")
	}
}

// --- HTTP handler boundary tests ---

func TestDeposit_InvalidBody(t *testing.T) {
	store := &Store{}
	client := &PIXClient{store: store, logger: noopLogger()}
	handler := Deposit(store, client, noopLogger())

	req := httptest.NewRequest(http.MethodPost, "/wallet/12345678909/deposit", bytes.NewBufferString("{invalid"))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", rr.Code)
	}
}

func TestWithdraw_MissingPixKey(t *testing.T) {
	store := &Store{}
	client := &PIXClient{store: store, logger: noopLogger()}
	handler := Withdraw(store, client, noopLogger())

	body := `{"amount":100.00,"pix_key":""}`
	req := httptest.NewRequest(http.MethodPost, "/wallet/12345678909/withdraw", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing pix_key, got %d", rr.Code)
	}
}

func TestHealthCheck_ReturnsOK(t *testing.T) {
	handler := HealthCheck()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("health: expected 200, got %d", rr.Code)
	}
	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body)
	if body["status"] != "ok" {
		t.Errorf("health status = %v", body["status"])
	}
}

func TestPIXWebhook_MissingFields(t *testing.T) {
	client := &PIXClient{store: nil, logger: noopLogger()}
	handler := PIXWebhook(client, noopLogger())

	body := `{"amount":100.0}`
	req := httptest.NewRequest(http.MethodPost, "/wallet/webhook/pix", bytes.NewBufferString(body))
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", rr.Code)
	}
}

func TestReconcile_InvalidDateFormat(t *testing.T) {
	engine := &ReconciliationEngine{store: nil, logger: noopLogger()}
	handler := Reconcile(engine, noopLogger())

	req := httptest.NewRequest(http.MethodPost, "/wallet/reconcile?date=01-01-2025", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid date format, got %d", rr.Code)
	}
}

// --- Transaction type constants ---

func TestTransactionTypes(t *testing.T) {
	types := []TransactionType{
		TxTypeDeposit, TxTypeWithdrawal, TxTypeBetDebit,
		TxTypeWinCredit, TxTypeRefund, TxTypeTax, TxTypeBonus, TxTypeAdjustment,
	}
	for _, tt := range types {
		if string(tt) == "" {
			t.Errorf("transaction type %v is empty", tt)
		}
	}
}

func TestPIXStatus_Values(t *testing.T) {
	statuses := []PIXStatus{PIXStatusPending, PIXStatusConfirmed, PIXStatusExpired, PIXStatusFailed, PIXStatusRefunded}
	for _, s := range statuses {
		if string(s) == "" {
			t.Errorf("PIX status %v is empty", s)
		}
	}
}

// --- helpers ---

func noopLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(nopWriter{}, nil))
}

type nopWriter struct{}

func (nopWriter) Write(p []byte) (int, error) { return len(p), nil }

var _ = time.Now // suppress unused import if needed
