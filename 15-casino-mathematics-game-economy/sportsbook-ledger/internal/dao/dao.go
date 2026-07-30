// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package dao provides PostgreSQL data access for the sportsbook ledger.
// Mirrors BmcFeedDao and BetsDao from the Scala source.
package dao

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"sportsbook-ledger/internal/models"
)

// FeedDAO persists and queries FeedMessage records (analytics_dw.feed_messages).
type FeedDAO struct {
	db *sql.DB
}

// NewFeedDAO creates a FeedDAO backed by the given *sql.DB.
func NewFeedDAO(db *sql.DB) *FeedDAO { return &FeedDAO{db: db} }

// Insert upserts a batch of FeedMessages using PostgreSQL ON CONFLICT DO UPDATE.
func (d *FeedDAO) Insert(msgs []models.FeedMessage) error {
	if len(msgs) == 0 {
		return nil
	}
	for _, m := range msgs {
		_, err := d.db.Exec(`
			INSERT INTO analytics_dw.feed_messages
			  (id, customer_player_id, update_date, placed_date,
			   bet_status_id, operator_stake_transaction_id, combination_ref,
			   event_group_id, message_type, stake, payout, event_group,
			   is_combination, is_cancellation_void, acme_void_type, json, created)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
			ON CONFLICT (id) DO UPDATE SET
			  update_date = EXCLUDED.update_date,
			  bet_status_id = EXCLUDED.bet_status_id,
			  acme_void_type = EXCLUDED.acme_void_type,
			  json = EXCLUDED.json`,
			m.MessageID, m.CustomerPlayerID, m.UpdatedDate, m.PlacedDate,
			m.BetStatusID, m.OperatorStakeTransactionID, m.CombinationRef,
			m.EventGroupID, m.MessageType, m.Stake, m.Payout, m.EventGroup,
			m.IsCombination, m.IsCancellationVoid, voidTypeStr(m.AcmeVoidType),
			m.JSON, m.Created)
		if err != nil {
			return fmt.Errorf("upsert feed message %d: %w", m.MessageID, err)
		}
	}
	return nil
}

// GetLastMessage returns the most recent FeedMessage by messageId (for resuming).
func (d *FeedDAO) GetLastMessage() (*models.FeedMessage, error) {
	row := d.db.QueryRow(`
		SELECT id FROM analytics_dw.feed_messages
		ORDER BY id DESC LIMIT 1`)
	var id int64
	if err := row.Scan(&id); err == sql.ErrNoRows {
		return nil, nil
	} else if err != nil {
		return nil, err
	}
	return &models.FeedMessage{MessageID: id}, nil
}

// Ping checks database connectivity (used by health check).
func (d *FeedDAO) Ping() error { return d.db.Ping() }

// BetsDAO persists and queries Bet records (analytics_dw.bets).
type BetsDAO struct {
	db *sql.DB
}

// NewBetsDAO creates a BetsDAO backed by the given *sql.DB.
func NewBetsDAO(db *sql.DB) *BetsDAO { return &BetsDAO{db: db} }

// Insert upserts a batch of Bet records.
func (d *BetsDAO) Insert(bets []models.Bet) error {
	if len(bets) == 0 {
		return nil
	}
	for _, b := range bets {
		_, err := d.db.Exec(`
			INSERT INTO analytics_dw.bets
			  (bet_id, combination_ref, live, event_id, event_name, meeting,
			   criterion_name, sport_id, status, odds, outcome_label,
			   message_id, selection_type)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
			ON CONFLICT (bet_id) DO UPDATE SET
			  status = EXCLUDED.status,
			  odds = EXCLUDED.odds`,
			b.BetID, b.CombinationRef, b.Live, b.EventID, b.EventName,
			b.Meeting, b.CriterionName, b.SportID, b.Status, b.Odds,
			b.OutcomeLabel, b.MessageID, b.SelectionType)
		if err != nil {
			return fmt.Errorf("upsert bet %s: %w", b.BetID, err)
		}
	}
	return nil
}

// FindByCombinationRef returns all bets belonging to a given combination reference.
func (d *BetsDAO) FindByCombinationRef(ref int64) ([]models.Bet, error) {
	rows, err := d.db.Query(`
		SELECT bet_id, combination_ref, live, event_id, event_name, meeting,
		       criterion_name, sport_id, status, odds, outcome_label,
		       message_id, selection_type
		FROM analytics_dw.bets
		WHERE combination_ref = $1`, ref)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var bets []models.Bet
	for rows.Next() {
		var b models.Bet
		if err := rows.Scan(&b.BetID, &b.CombinationRef, &b.Live, &b.EventID,
			&b.EventName, &b.Meeting, &b.CriterionName, &b.SportID,
			&b.Status, &b.Odds, &b.OutcomeLabel, &b.MessageID, &b.SelectionType); err != nil {
			return nil, err
		}
		bets = append(bets, b)
	}
	return bets, rows.Err()
}

// ---- helpers ----------------------------------------------------------------

func voidTypeStr(v *models.AcmeVoidType) *string {
	if v == nil {
		return nil
	}
	s := string(*v)
	return &s
}

// CreateSchema ensures the required tables exist (idempotent).
func CreateSchema(db *sql.DB) error {
	stmts := []string{
		`CREATE SCHEMA IF NOT EXISTS analytics_dw`,
		`CREATE TABLE IF NOT EXISTS analytics_dw.feed_messages (
			id                           BIGINT PRIMARY KEY,
			customer_player_id           TEXT,
			update_date                  TIMESTAMPTZ,
			placed_date                  TIMESTAMPTZ,
			bet_status_id                INTEGER,
			operator_stake_transaction_id TEXT,
			combination_ref              BIGINT,
			event_group_id               BIGINT,
			message_type                 TEXT,
			stake                        DOUBLE PRECISION,
			payout                       DOUBLE PRECISION,
			event_group                  TEXT,
			is_combination               BOOLEAN,
			is_cancellation_void         BOOLEAN,
			acme_void_type               TEXT,
			json                         JSONB,
			created                      TIMESTAMPTZ
		)`,
		`CREATE TABLE IF NOT EXISTS analytics_dw.bets (
			bet_id          TEXT PRIMARY KEY,
			combination_ref BIGINT,
			live            BOOLEAN,
			event_id        BIGINT,
			event_name      TEXT,
			meeting         TEXT,
			criterion_name  TEXT,
			sport_id        TEXT,
			status          TEXT,
			odds            DOUBLE PRECISION,
			outcome_label   TEXT,
			message_id      BIGINT,
			selection_type  TEXT
		)`,
	}
	for _, s := range stmts {
		if _, err := db.Exec(s); err != nil {
			return fmt.Errorf("exec schema stmt: %w\nSQL: %s", err, truncate(s, 60))
		}
	}
	return nil
}

func truncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// suppress unused import warning
var _ = time.Now
