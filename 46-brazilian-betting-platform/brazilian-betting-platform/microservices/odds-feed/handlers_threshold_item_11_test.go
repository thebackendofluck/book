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
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestUpdateOdds_ThresholdBoundary_item_11 pins the 1.01 inclusive lower
// boundary for odds accepted by POST /odds/update:
//
//   - 0.99     -> 400 (below 1.00, clearly invalid)
//   - 1.00     -> 400 (boundary, certain payout — never a real market)
//   - 1.005    -> 400 (between 1.00 and 1.01, still below minimum)
//   - 1.01     -> ACCEPTED (first valid value at the inclusive boundary)
//   - 1.5      -> ACCEPTED (typical market odds)
//   - 10.0     -> ACCEPTED (high odds)
//   - 100.0    -> ACCEPTED (very high odds — still valid)
//
// For ACCEPTED rows we cannot expect HTTP 200 because the test Cache uses
// a nil Redis client and cache.UpdateOdds will panic when it tries to hit
// the network. We `recover()` and assert the threshold check let the
// request through (status code != 400 OR a panic was triggered downstream
// of the threshold check). This matches the existing pattern in
// TestUpdateOdds_Exactly1_01_Accepted.
//
// This is the rollout DoD for item 11: each boundary value is pinned so
// any drift in the `update.NewOdds < 1.01` guard fails loudly.
func TestUpdateOdds_ThresholdBoundary_item_11(t *testing.T) {
	cases := []struct {
		name       string
		newOdds    float64
		wantReject bool // true -> expect HTTP 400; false -> expect non-400 / accepted-path
	}{
		{"0.99-rejected", 0.99, true},
		{"1.00-rejected-boundary", 1.00, true},
		{"1.005-rejected-mid", 1.005, true},
		{"1.01-accepted-boundary", 1.01, false},
		{"1.5-accepted", 1.5, false},
		{"10.0-accepted", 10.0, false},
		{"100.0-accepted", 100.0, false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			cache := &Cache{client: nil}
			handler := UpdateOdds(cache, nil, noopLogger())

			body := fmt.Sprintf(
				`{"event_id":"ev1","market_id":"m1","selection_id":"s1","new_odds":%v,"source":"test"}`,
				c.newOdds,
			)
			req := httptest.NewRequest(http.MethodPost, "/odds/update", strings.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			rr := httptest.NewRecorder()

			// Accepted-path will panic in cache.UpdateOdds(nil client); recover
			// so the test reports threshold behavior, not the unrelated panic.
			func() {
				defer func() {
					if r := recover(); r != nil && c.wantReject {
						t.Errorf("threshold check let odds=%v through (panic in cache layer) but row expects 400",
							c.newOdds)
					}
				}()
				handler.ServeHTTP(rr, req)
			}()

			if c.wantReject {
				if rr.Code != http.StatusBadRequest {
					t.Errorf("odds=%v: expected 400 (rejected), got %d", c.newOdds, rr.Code)
				}
				return
			}
			// Accepted path: must NOT be 400. Anything else (200, 500, or the
			// recovered panic) proves the threshold check passed.
			if rr.Code == http.StatusBadRequest {
				t.Errorf("odds=%v: expected non-400 (accepted by threshold), got 400", c.newOdds)
			}
		})
	}
}
