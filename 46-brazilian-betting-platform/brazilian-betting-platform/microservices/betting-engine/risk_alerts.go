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

// NOTE: This component is defined but intentionally not wired into main.go.
// It is presented in the book as an extension point — readers can integrate it
// by registering its handlers/listeners in cmd/main.go. See chapter 46 §X.

import (
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// AlertSeverity classifies the urgency of a risk alert.
type AlertSeverity string

const (
	AlertSeverityInfo     AlertSeverity = "info"
	AlertSeverityWarning  AlertSeverity = "warning"
	AlertSeverityCritical AlertSeverity = "critical"
)

// RiskAlertType classifies what triggered the risk alert.
type RiskAlertType string

const (
	RiskAlertTypeExposureThreshold RiskAlertType = "exposure_threshold"
	RiskAlertTypeSuspiciousPattern RiskAlertType = "suspicious_pattern"
	RiskAlertTypeRapidBetting      RiskAlertType = "rapid_betting"
	RiskAlertTypeLargeStake        RiskAlertType = "large_stake"
	RiskAlertTypeConcentratedBets  RiskAlertType = "concentrated_bets"
	RiskAlertTypeLateGoalBetting   RiskAlertType = "late_goal_betting"
	RiskAlertTypeArbitrage         RiskAlertType = "arbitrage_suspected"
)

// RiskAlert is a single alert raised by the risk engine.
type RiskAlert struct {
	ID           string        `json:"id"`
	Type         RiskAlertType `json:"type"`
	Severity     AlertSeverity `json:"severity"`
	EventID      string        `json:"event_id,omitempty"`
	MarketID     string        `json:"market_id,omitempty"`
	SelectionID  string        `json:"selection_id,omitempty"`
	CPF          string        `json:"cpf,omitempty"`
	Description  string        `json:"description"`
	Value        float64       `json:"value,omitempty"` // threshold value that triggered the alert
	Threshold    float64       `json:"threshold,omitempty"`
	Acknowledged bool          `json:"acknowledged"`
	CreatedAt    time.Time     `json:"created_at"`
}

// RiskThresholds defines configurable limits that trigger alerts.
type RiskThresholds struct {
	// LargeStakeThreshold: single bet above this amount triggers an alert (BRL).
	LargeStakeThreshold float64

	// RapidBetWindow: time window for rapid betting detection.
	RapidBetWindow time.Duration
	// RapidBetCount: number of bets within the window to trigger alert.
	RapidBetCount int

	// ConcentrationThreshold: if a single player has more than this % of
	// total stake on a selection, flag it.
	ConcentrationThreshold float64

	// LateGoalMinute: minute after which pre-goal bets are flagged as suspicious.
	LateGoalMinute int

	// ExposureAlertLevel: minimum exposure level to generate alerts.
	ExposureAlertLevel ExposureLevel
}

// DefaultRiskThresholds returns production defaults for Brazilian market.
func DefaultRiskThresholds() RiskThresholds {
	return RiskThresholds{
		LargeStakeThreshold:    10000.00, // R$10,000
		RapidBetWindow:         2 * time.Minute,
		RapidBetCount:          10,
		ConcentrationThreshold: 0.30, // 30% of selection stake from one player
		LateGoalMinute:         80,
		ExposureAlertLevel:     ExposureLevelHigh,
	}
}

// playerBetRecord tracks recent bet activity per player for pattern detection.
type playerBetRecord struct {
	CPF       string
	EventID   string
	Stake     float64
	Odds      float64
	Timestamp time.Time
}

// RiskEngine monitors bets for suspicious patterns and threshold breaches.
type RiskEngine struct {
	mu         sync.Mutex
	tracker    *ExposureTracker
	thresholds RiskThresholds
	alerts     []RiskAlert
	playerBets map[string][]playerBetRecord // CPF -> recent bets
	alertSeq   int
	logger     *slog.Logger
}

// NewRiskEngine creates a risk engine wired to the exposure tracker.
func NewRiskEngine(tracker *ExposureTracker, thresholds RiskThresholds, logger *slog.Logger) *RiskEngine {
	return &RiskEngine{
		tracker:    tracker,
		thresholds: thresholds,
		alerts:     make([]RiskAlert, 0),
		playerBets: make(map[string][]playerBetRecord),
		logger:     logger,
	}
}

// EvaluateBet checks a newly placed bet against all risk rules and generates alerts.
func (re *RiskEngine) EvaluateBet(
	cpf, eventID, marketID, selectionID, sport string,
	stake, odds float64,
	eventMinute int,
) []RiskAlert {
	re.mu.Lock()
	defer re.mu.Unlock()

	now := time.Now().UTC()
	var newAlerts []RiskAlert

	// Record the bet for pattern tracking.
	record := playerBetRecord{
		CPF:       cpf,
		EventID:   eventID,
		Stake:     stake,
		Odds:      odds,
		Timestamp: now,
	}
	re.playerBets[cpf] = append(re.playerBets[cpf], record)

	// 1. Large stake check.
	if stake >= re.thresholds.LargeStakeThreshold {
		alert := re.createAlert(RiskAlertTypeLargeStake, AlertSeverityWarning,
			fmt.Sprintf("Large stake R$%.2f from CPF %s on event %s", stake, riskMaskCPF(cpf), eventID),
			eventID, marketID, selectionID, cpf,
			stake, re.thresholds.LargeStakeThreshold,
		)
		newAlerts = append(newAlerts, alert)
	}

	// 2. Rapid betting pattern.
	recentBets := re.countRecentBets(cpf, now)
	if recentBets >= re.thresholds.RapidBetCount {
		alert := re.createAlert(RiskAlertTypeRapidBetting, AlertSeverityWarning,
			fmt.Sprintf("Rapid betting: %d bets in %s from CPF %s",
				recentBets, re.thresholds.RapidBetWindow, riskMaskCPF(cpf)),
			eventID, "", "", cpf,
			float64(recentBets), float64(re.thresholds.RapidBetCount),
		)
		newAlerts = append(newAlerts, alert)
	}

	// 3. Late goal minute betting (football-specific).
	if eventMinute >= re.thresholds.LateGoalMinute && isFootballSport(sport) {
		alert := re.createAlert(RiskAlertTypeLateGoalBetting, AlertSeverityWarning,
			fmt.Sprintf("Late-match bet at minute %d from CPF %s on event %s",
				eventMinute, riskMaskCPF(cpf), eventID),
			eventID, marketID, selectionID, cpf,
			float64(eventMinute), float64(re.thresholds.LateGoalMinute),
		)
		newAlerts = append(newAlerts, alert)
	}

	// 4. Exposure threshold alerts.
	bucket := re.tracker.GetBucket(eventID, marketID, selectionID)
	if bucket != nil && isAtOrAboveLevel(bucket.Level, re.thresholds.ExposureAlertLevel) {
		alert := re.createAlert(RiskAlertTypeExposureThreshold, AlertSeverityCritical,
			fmt.Sprintf("Exposure level %s on selection %s (liability R$%.2f)",
				bucket.Level, selectionID, bucket.MaxLiability),
			eventID, marketID, selectionID, "",
			bucket.MaxLiability, 0,
		)
		newAlerts = append(newAlerts, alert)
	}

	// 5. Bet concentration check.
	if bucket != nil && bucket.TotalStake > 0 {
		playerStakeOnSelection := re.playerStakeOnSelection(cpf, eventID, selectionID)
		concentration := playerStakeOnSelection / bucket.TotalStake
		if concentration >= re.thresholds.ConcentrationThreshold {
			alert := re.createAlert(RiskAlertTypeConcentratedBets, AlertSeverityWarning,
				fmt.Sprintf("Player CPF %s holds %.0f%% of stake on selection %s",
					riskMaskCPF(cpf), concentration*100, selectionID),
				eventID, marketID, selectionID, cpf,
				concentration, re.thresholds.ConcentrationThreshold,
			)
			newAlerts = append(newAlerts, alert)
		}
	}

	re.alerts = append(re.alerts, newAlerts...)

	for _, a := range newAlerts {
		re.logger.Warn("risk alert generated",
			"alert_id", a.ID,
			"type", a.Type,
			"severity", a.Severity,
			"event_id", a.EventID,
			"description", a.Description,
		)
	}

	return newAlerts
}

// createAlert builds a new RiskAlert.
func (re *RiskEngine) createAlert(
	alertType RiskAlertType,
	severity AlertSeverity,
	description string,
	eventID, marketID, selectionID, cpf string,
	value, threshold float64,
) RiskAlert {
	re.alertSeq++
	return RiskAlert{
		ID:          fmt.Sprintf("RISK-%06d", re.alertSeq),
		Type:        alertType,
		Severity:    severity,
		EventID:     eventID,
		MarketID:    marketID,
		SelectionID: selectionID,
		CPF:         cpf,
		Description: description,
		Value:       value,
		Threshold:   threshold,
		CreatedAt:   time.Now().UTC(),
	}
}

// countRecentBets returns how many bets a player placed within the rapid-bet window.
func (re *RiskEngine) countRecentBets(cpf string, now time.Time) int {
	records := re.playerBets[cpf]
	cutoff := now.Add(-re.thresholds.RapidBetWindow)
	count := 0
	for _, r := range records {
		if r.Timestamp.After(cutoff) {
			count++
		}
	}
	return count
}

// playerStakeOnSelection totals a player's stake on a specific selection.
func (re *RiskEngine) playerStakeOnSelection(cpf, eventID, selectionID string) float64 {
	var total float64
	for _, r := range re.playerBets[cpf] {
		if r.EventID == eventID {
			// Simplified: in production, selection-level filtering would use
			// a separate index. For now, attribute all event bets proportionally.
			total += r.Stake
		}
	}
	return total
}

// GetAlerts returns alerts, optionally filtered by severity.
func (re *RiskEngine) GetAlerts(minSeverity AlertSeverity, limit int) []RiskAlert {
	re.mu.Lock()
	defer re.mu.Unlock()

	order := map[AlertSeverity]int{
		AlertSeverityInfo:     0,
		AlertSeverityWarning:  1,
		AlertSeverityCritical: 2,
	}
	minOrder := order[minSeverity]

	var filtered []RiskAlert
	// Return most recent first.
	for i := len(re.alerts) - 1; i >= 0; i-- {
		a := re.alerts[i]
		if order[a.Severity] >= minOrder {
			filtered = append(filtered, a)
		}
		if limit > 0 && len(filtered) >= limit {
			break
		}
	}
	return filtered
}

// AcknowledgeAlert marks an alert as reviewed.
func (re *RiskEngine) AcknowledgeAlert(alertID string) bool {
	re.mu.Lock()
	defer re.mu.Unlock()

	for i := range re.alerts {
		if re.alerts[i].ID == alertID {
			re.alerts[i].Acknowledged = true
			return true
		}
	}
	return false
}

// AlertCount returns the total number of unacknowledged alerts.
func (re *RiskEngine) AlertCount() int {
	re.mu.Lock()
	defer re.mu.Unlock()
	count := 0
	for _, a := range re.alerts {
		if !a.Acknowledged {
			count++
		}
	}
	return count
}

// --- helpers ---

// riskMaskCPF returns a partially masked CPF for logging (e.g., "123.***.*56-78").
func riskMaskCPF(cpf string) string {
	if len(cpf) < 6 {
		return "***"
	}
	return cpf[:3] + ".***.***-" + cpf[len(cpf)-2:]
}

// isFootballSport returns true for football-related sport identifiers.
func isFootballSport(sport string) bool {
	switch sport {
	case "futebol", "brasileirao", "copa-brasil", "libertadores", "sul-americana":
		return true
	default:
		return false
	}
}

// isAtOrAboveLevel checks if the actual level meets the minimum threshold.
func isAtOrAboveLevel(actual, minimum ExposureLevel) bool {
	order := map[ExposureLevel]int{
		ExposureLevelNormal:   0,
		ExposureLevelElevated: 1,
		ExposureLevelHigh:     2,
		ExposureLevelCritical: 3,
	}
	return order[actual] >= order[minimum]
}

// --- HTTP handlers ---

// GetRiskAlerts handles GET /risk/alerts.
func GetRiskAlerts(engine *RiskEngine) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		severity := AlertSeverity(r.URL.Query().Get("severity"))
		if severity == "" {
			severity = AlertSeverityInfo
		}

		alerts := engine.GetAlerts(severity, 100)
		writeJSON(w, http.StatusOK, map[string]any{
			"alerts":         alerts,
			"count":          len(alerts),
			"unacknowledged": engine.AlertCount(),
			"timestamp":      time.Now().UTC(),
		})
	}
}

// AcknowledgeAlertHandler handles POST /risk/alerts/{id}/ack.
func AcknowledgeAlertHandler(engine *RiskEngine) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		alertID := r.URL.Query().Get("id")
		if alertID == "" {
			// Try chi param.
			alertID = r.PathValue("id")
		}
		if alertID == "" {
			writeError(w, http.StatusBadRequest, "alert id required")
			return
		}

		if engine.AcknowledgeAlert(alertID) {
			writeJSON(w, http.StatusOK, map[string]any{
				"status":   "acknowledged",
				"alert_id": alertID,
			})
		} else {
			writeError(w, http.StatusNotFound, "alert not found")
		}
	}
}

// GetRiskSummary handles GET /risk/summary.
// Returns a high-level risk summary for the operations dashboard.
func GetRiskSummary(engine *RiskEngine, tracker *ExposureTracker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		criticalBuckets := tracker.GetBucketsAboveLevel(ExposureLevelCritical)
		highBuckets := tracker.GetBucketsAboveLevel(ExposureLevelHigh)

		writeJSON(w, http.StatusOK, map[string]any{
			"unacknowledged_alerts": engine.AlertCount(),
			"critical_exposures":    len(criticalBuckets),
			"high_exposures":        len(highBuckets),
			"thresholds":            engine.thresholds,
			"timestamp":             time.Now().UTC(),
		})
	}
}
