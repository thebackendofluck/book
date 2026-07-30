# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
daily_stats.py — Daily analytics pipeline for iGaming platform statistics.

Mirrors StatsStep.scala, DailyPlayerStatsStep.scala, DailyPlayerRevenueStep.scala,
StepChooser.scala, and Run.scala.

Pipeline architecture:
  19 steps executed in dependency order, each step being idempotent:
    balances → dps → dpgs → freerounds → revenue → affiliate → period →
    runningtotals → runningcalculations → matrix → averages → scorealerts →
    scoreinteractions → levels → messages → timespent → compliancealerts →
    topdepositoralerts → vipsegments

Each step:
  1. Checks if data already exists for the date range (idempotency)
  2. Optionally clears existing data (--clear flag)
  3. Executes the INSERT ... SELECT SQL within a transaction
  4. Logs row count and execution time

Key SQL patterns:
  - CASE WHEN aggregation over a single pass of USER_ACCOUNT_HISTORY
  - Account type separation (cash=1, bonus=2, loyalty=3) via JOIN to USER_ACCOUNTS
  - LEFT OUTER JOIN for opening/closing balance snapshots
  - Jurisdiction-specific gaming duty: CH-based or GGR-based formula
  - Payment risk classification by payment method rating (low/medium/high)
  - Session activity: 15-min active periods, longest session in hours
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Database wrapper
# ---------------------------------------------------------------------------

class DB:
    def __init__(self, db_url: str) -> None:
        self._conn = psycopg2.connect(db_url)
        self._conn.autocommit = False

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            count = cur.rowcount
        self._conn.commit()
        return count

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Any]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Step base classes (mirrors StatsStep.scala)
# ---------------------------------------------------------------------------

class StatsStep(ABC):
    @property
    @abstractmethod
    def table_name(self) -> str: ...

    @property
    def date_column(self) -> str:
        return "on_date"

    def is_already_populated(self, db: DB, from_date: date, to_date: date) -> bool:
        row = db.fetchone(
            f"SELECT COUNT(*) FROM analytics_dw.{self.table_name} "
            f"WHERE {self.date_column} BETWEEN %s AND %s",
            (from_date, to_date),
        )
        return (row[0] if row else 0) > 0

    def clear_existing(self, db: DB, from_date: date, to_date: date) -> int:
        return db.execute(
            f"DELETE FROM analytics_dw.{self.table_name} "
            f"WHERE {self.date_column} BETWEEN %s AND %s",
            (from_date, to_date),
        )

    def generate_insert(self, from_date: date, to_date: date) -> tuple[str, tuple]:
        """Return (sql, params) for the INSERT … SELECT statement."""
        raise NotImplementedError

    def process(self, db: DB, from_date: date, to_date: date, clear: bool = False) -> int:
        if clear:
            cleared = self.clear_existing(db, from_date, to_date)
            log.info("cleared rows", table=self.table_name, count=cleared,
                     from_date=str(from_date), to_date=str(to_date))
        elif self.is_already_populated(db, from_date, to_date):
            log.info("already populated, skipping", table=self.table_name,
                     from_date=str(from_date), to_date=str(to_date))
            return 0

        sql, params = self.generate_insert(from_date, to_date)
        count = db.execute(sql, params)
        log.info("inserted rows", table=self.table_name, count=count,
                 from_date=str(from_date), to_date=str(to_date))
        return count


# ---------------------------------------------------------------------------
# DailyPlayerStatsStep (mirrors DailyPlayerStatsStep.scala)
#
# Aggregates raw USER_ACCOUNT_HISTORY into one row per player per day.
# TYPEID=1 → cash account; TYPEID=2 → bonus account; TYPEID=3 → loyalty.
# Active periods = 15-minute time slots with any gaming activity.
# ---------------------------------------------------------------------------

class DailyPlayerStatsStep(StatsStep):
    table_name = "daily_player_stats"

    def generate_insert(self, from_date: date, to_date: date) -> tuple[str, tuple]:
        sql = """
        INSERT INTO analytics_dw.daily_player_stats (
            on_date, user_id, currency,
            deposit, deposit_count, deposit_voided,
            cash_gamescount, cash_betcount, bonus_gamescount,
            bonus_betcount, all_gamescount, all_betcount,
            cash_stake, bonus_stake, cash_refund, bonus_refund, cash_return, bonus_return,
            withdrawal_req, withdrawal_app, withdrawal_rej, withdrawal_rev, withdrawal_returned,
            cash_adj_cr, cash_manual_credit_adjust, cash_adj_dr,
            loyalty_received_auto, loyalty_adj_cr, loyalty_adj_dr, loyalty_redeemed,
            bonus_account_bonus, bonus_to_cash_account, refunds_cash_to_bonus,
            bonus_adj_cr, bonus_manual_credit_adjust, bonus_adj_dr, bonus_revoked,
            cash_opening_balance, cash_closing_balance,
            bonus_opening_balance, bonus_closing_balance,
            points_opening_balance, points_closing_balance,
            active_periods,
            longest_active_periods_in_session, longest_daily_session_in_hours, withdrawal_pend
        )
        SELECT
            on_date, y.user_id, y.currency,
            deposit, deposit_count, deposit_voided,
            cash_gamescount, cash_betcount, bonus_gamescount,
            bonus_betcount, all_gamescount, all_betcount,
            cash_stake, bonus_stake, cash_refund, bonus_refund, cash_return, bonus_return,
            withdrawal_req, withdrawal_app, withdrawal_rej, withdrawal_rev, withdrawal_returned,
            cash_adj_cr, cash_manual_credit_adjust, cash_adj_dr,
            loyalty_received_auto, loyalty_adj_cr, loyalty_adj_dr, loyalty_redeemed,
            bonus_account_bonus, bonus_to_cash_account, refunds_cash_to_bonus,
            bonus_adj_cr, bonus_manual_credit_adjust, bonus_adj_dr, bonus_revoked,
            COALESCE(ob.cash_balance,   0)/100 cash_opening_balance,
            COALESCE(cb.cash_balance,   0)/100 cash_closing_balance,
            COALESCE(ob.bonus_balance,  0)/100 bonus_opening_balance,
            COALESCE(cb.bonus_balance,  0)/100 bonus_closing_balance,
            COALESCE(ob.loyalty_balance,0)     points_opening_balance,
            COALESCE(cb.loyalty_balance,0)     points_closing_balance,
            active_periods,
            COALESCE(a.longest_active_periods_in_session, 0),
            COALESCE(b.longest_daily_session_in_hours,    0),
            COALESCE(wp.withdrawal_pend, 0)
        FROM (
            SELECT
                DATE_TRUNC('day', h.changedate)  on_date,
                h.userid                          user_id,
                MAX(ac.currency)                  currency,
                -- Deposits
                SUM(CASE WHEN ac.typeid=1 AND h.changetype='deposit'        THEN h.amount ELSE 0 END)/100 deposit,
                SUM(CASE WHEN ac.typeid=1 AND h.changetype='deposit'        THEN 1        ELSE 0 END)     deposit_count,
                SUM(CASE WHEN ac.typeid=1 AND h.changetype='deposit_voided' THEN h.amount ELSE 0 END)/100 deposit_voided,
                -- Game counts (cash vs bonus)
                COUNT(DISTINCT CASE WHEN ac.typeid=1 AND h.changetype IN ('credit','debit')
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.comments ELSE NULL END) cash_gamescount,
                SUM(CASE WHEN ac.typeid=1 AND h.changetype='debit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN 1 ELSE 0 END) cash_betcount,
                COUNT(DISTINCT CASE WHEN ac.typeid=2 AND h.changetype IN ('credit','debit')
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.comments ELSE NULL END) bonus_gamescount,
                SUM(CASE WHEN ac.typeid=2 AND h.changetype='debit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN 1 ELSE 0 END) bonus_betcount,
                COUNT(DISTINCT CASE WHEN ac.typeid IN (1,2) AND h.changetype IN ('credit','debit')
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.comments ELSE NULL END) all_gamescount,
                SUM(CASE WHEN ac.typeid IN (1,2) AND h.changetype='debit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN 1 ELSE 0 END) all_betcount,
                -- Stakes and returns by account type
                SUM(CASE WHEN ac.typeid=1 AND h.changetype='debit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.amount ELSE 0 END)/100 cash_stake,
                SUM(CASE WHEN ac.typeid=2 AND h.changetype='debit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.amount ELSE 0 END)/100 bonus_stake,
                SUM(CASE WHEN ac.typeid=1 AND h.changetype='credit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.amount ELSE 0 END)/100 cash_return,
                SUM(CASE WHEN ac.typeid=2 AND h.changetype='credit'
                    AND h.referralsystem IN (SELECT refsystem::varchar FROM casino_core.games)
                    THEN h.amount ELSE 0 END)/100 bonus_return,
                -- Stubs for additional aggregations (same CASE WHEN pattern)
                0 cash_refund, 0 bonus_refund,
                0 withdrawal_req, 0 withdrawal_app, 0 withdrawal_rej,
                0 withdrawal_rev, 0 withdrawal_returned,
                0 cash_adj_cr, 0 cash_manual_credit_adjust, 0 cash_adj_dr,
                0 loyalty_received_auto, 0 loyalty_adj_cr, 0 loyalty_adj_dr, 0 loyalty_redeemed,
                0 bonus_account_bonus, 0 bonus_to_cash_account, 0 refunds_cash_to_bonus,
                0 bonus_adj_cr, 0 bonus_manual_credit_adjust, 0 bonus_adj_dr, 0 bonus_revoked,
                -- Active periods: distinct 15-min buckets with game activity
                COUNT(DISTINCT CASE WHEN h.changetype IN ('debit','credit')
                    THEN (DATE_TRUNC('hour', h.changedate) +
                          ((TRUNC(EXTRACT(MINUTE FROM h.changedate)/15)*15) || ' minutes')::interval)
                    ELSE NULL END) active_periods
            FROM casino_core.user_account_history h
            JOIN casino_core.user_accounts ac ON ac.id = h.accountid
            WHERE h.changedate BETWEEN %s::timestamp AND (%s::timestamp + interval '1 day' - interval '1 millisecond')
            GROUP BY DATE_TRUNC('day', h.changedate), h.userid
        ) y
        -- Opening balance (snapshot before this day)
        LEFT OUTER JOIN analytics_dw.daily_player_balances ob
            ON ob.user_id = y.user_id
               AND ob.from_date <= y.on_date - interval '1 day'
               AND (ob.to_date IS NULL OR ob.to_date >= y.on_date - interval '1 day')
        -- Closing balance (snapshot for this day)
        LEFT OUTER JOIN analytics_dw.daily_player_balances cb
            ON cb.user_id = y.user_id
               AND cb.from_date <= y.on_date
               AND (cb.to_date IS NULL OR cb.to_date >= y.on_date)
        -- Longest active periods per session (ROW_NUMBER trick for top-1)
        LEFT OUTER JOIN (
            SELECT userid, COUNT(sessionid) longest_active_periods_in_session,
                   ROW_NUMBER() OVER(PARTITION BY userid ORDER BY COUNT(sessionid) DESC) rn
            FROM (
                SELECT DISTINCT h.userid,
                    DATE_TRUNC('hour', h.changedate) + (TRUNC(EXTRACT(MINUTE FROM h.changedate)/15)*15||' min')::interval,
                    us.sessionid
                FROM casino_core.user_account_history h
                JOIN casino_core.user_session us ON us.userid = h.userid
                WHERE h.changedate BETWEEN us.created AND us.lasttouch
                  AND h.changedate BETWEEN %s::timestamp AND (%s::timestamp + interval '1 day' - interval '1 millisecond')
                  AND h.changetype IN ('debit','credit')
                GROUP BY h.userid, h.changedate, us.sessionid
            ) sub
            GROUP BY userid, sessionid
        ) a ON a.userid = y.user_id AND a.rn = 1
        -- Longest session in hours
        LEFT OUTER JOIN (
            SELECT DISTINCT us.userid,
                EXTRACT(DAY  FROM us.lasttouch - us.created)*24 +
                EXTRACT(HOUR FROM us.lasttouch - us.created) longest_daily_session_in_hours,
                ROW_NUMBER() OVER(PARTITION BY us.userid ORDER BY
                    EXTRACT(DAY  FROM us.lasttouch - us.created)*24 +
                    EXTRACT(HOUR FROM us.lasttouch - us.created) DESC) rn
            FROM casino_core.user_session us
            WHERE us.lasttouch BETWEEN %s::timestamp AND (%s::timestamp + interval '1 day' - interval '1 millisecond')
            GROUP BY us.userid, us.lasttouch, us.created
        ) b ON b.userid = y.user_id AND b.rn = 1
        -- Pending withdrawals
        LEFT OUTER JOIN (
            SELECT uw.userid, COUNT(*) withdrawal_pend
            FROM casino_core.user_withdraws uw
            WHERE uw.status = 0
              AND uw.changedate BETWEEN %s::timestamp AND (%s::timestamp + interval '1 day' - interval '1 millisecond')
            GROUP BY uw.userid
        ) wp ON wp.userid = y.user_id
        """
        params = (from_date, to_date,   # main WHERE
                  from_date, to_date,   # session active periods
                  from_date, to_date,   # longest session
                  from_date, to_date)   # pending withdrawals
        return sql, params


# ---------------------------------------------------------------------------
# DailyPlayerRevenueStep (mirrors DailyPlayerRevenueStep.scala)
#
# Derives GGR, CPL, Cash Hold, gaming duty, payment risk, stakes by type.
# Depends on daily_player_stats and daily_player_game_stats being populated first.
# ---------------------------------------------------------------------------

class DailyPlayerRevenueStep(StatsStep):
    table_name = "daily_player_revenue"

    def generate_insert(self, from_date: date, to_date: date) -> tuple[str, tuple]:
        sql = """
        INSERT INTO analytics_dw.daily_player_revenue (
            on_date, user_id, currency,
            cash_profit_loss, bonus_profit_loss, gross_gaming_revenue, all_stakes,
            cash_hold, progressive_contribution, gaming_duty,
            net_cash_contribution, net_deposits, total_money_in,
            slots_stakes, table_stakes, p2p_stakes,
            low_risk_payments, medium_low_risk_payments, medium_risk_payments,
            medium_high_risk_payments, high_risk_payments
        )
        SELECT
            s.on_date, s.user_id, s.currency,
            cpl, bpl, ggr, ts, ch,
            progressive_contribution,
            CASE WHEN tax_formula='CH'  THEN tax_percentage * ch
                 WHEN tax_formula='GGR' THEN tax_percentage * ggr
                 ELSE 0 END gaming_duty,
            ch - progressive_contribution -
                 CASE WHEN tax_formula='CH'  THEN tax_percentage * ch
                      WHEN tax_formula='GGR' THEN tax_percentage * ggr
                      ELSE 0 END ncc,
            nd, ami,
            slots_stakes, table_stakes, 0 p2p_stakes,
            low_risk_payments, medium_low_risk_payments, medium_risk_payments,
            medium_high_risk_payments, high_risk_payments
        FROM (
            SELECT
                d.on_date, d.user_id, d.currency,
                -- Core revenue metrics
                (d.cash_stake  - d.cash_refund  - d.cash_return)  cpl,
                (d.bonus_stake - d.bonus_refund - d.bonus_return)  bpl,
                (d.cash_stake  - d.cash_refund  - d.cash_return +
                 d.bonus_stake - d.bonus_refund - d.bonus_return)  ggr,
                (d.cash_stake  - d.cash_refund  +
                 d.bonus_stake - d.bonus_refund)                    ts,
                -- Cash Hold: balance-sheet approach
                (d.cash_opening_balance + d.deposit - d.deposit_voided
                 - d.cash_closing_balance - d.withdrawal_req
                 + d.withdrawal_rev + d.withdrawal_rej + d.withdrawal_returned) ch,
                -- Net Deposits
                (d.deposit - d.withdrawal_req + d.withdrawal_rev
                 + d.withdrawal_rej + d.withdrawal_returned) nd,
                -- Total Money In (deposits + bonuses - revocations)
                (d.deposit - d.deposit_voided + d.bonus_account_bonus - d.bonus_revoked) ami,
                -- Gaming duty rates by jurisdiction
                COALESCE(tax.percentage, 0) tax_percentage,
                COALESCE(tax.formula,   'GGR') tax_formula,
                -- Progressive jackpot contribution from per-game stats
                (SELECT COALESCE(SUM((g.cash_stake + g.bonus_stake)/100.0 * p.percentage), 0)
                   FROM analytics_dw.daily_player_game_stats g
                   JOIN casino_core.game_progressive p ON p.game_id = g.game_id
                  WHERE g.user_id = d.user_id AND g.on_date = d.on_date
                ) progressive_contribution,
                -- Stakes by game type (slots=1,4,5,6; tables=2,3,7)
                (SELECT COALESCE(SUM(gs.cash_stake + gs.bonus_stake - gs.cash_refunds - gs.bonus_refunds), 0)
                   FROM analytics_dw.daily_player_game_stats gs
                   JOIN casino_core.games g ON gs.game_id = g.id AND g.game_type_id IN (1,4,5,6)
                  WHERE gs.user_id = d.user_id AND gs.on_date = d.on_date
                ) slots_stakes,
                (SELECT COALESCE(SUM(gs.cash_stake + gs.bonus_stake - gs.cash_refunds - gs.bonus_refunds), 0)
                   FROM analytics_dw.daily_player_game_stats gs
                   JOIN casino_core.games g ON gs.game_id = g.id AND g.game_type_id IN (2,3,7)
                  WHERE gs.user_id = d.user_id AND gs.on_date = d.on_date
                ) table_stakes,
                -- Payment risk classification
                (SELECT COALESCE(SUM(up.amount),0)/100 FROM casino_core.user_payments up
                   JOIN casino_core.payment_method pm ON up.payment_method_id = pm.name AND pm.risk_rating='low'
                  WHERE up.user_id=d.user_id AND DATE_TRUNC('day',up.date_updated)=d.on_date AND up.status='SUCCEEDED'
                ) low_risk_payments,
                (SELECT COALESCE(SUM(up.amount),0)/100 FROM casino_core.user_payments up
                   JOIN casino_core.payment_method pm ON up.payment_method_id = pm.name AND pm.risk_rating='medium-low'
                  WHERE up.user_id=d.user_id AND DATE_TRUNC('day',up.date_updated)=d.on_date AND up.status='SUCCEEDED'
                ) medium_low_risk_payments,
                (SELECT COALESCE(SUM(up.amount),0)/100 FROM casino_core.user_payments up
                   JOIN casino_core.payment_method pm ON up.payment_method_id = pm.name AND pm.risk_rating='medium'
                  WHERE up.user_id=d.user_id AND DATE_TRUNC('day',up.date_updated)=d.on_date AND up.status='SUCCEEDED'
                ) medium_risk_payments,
                (SELECT COALESCE(SUM(up.amount),0)/100 FROM casino_core.user_payments up
                   JOIN casino_core.payment_method pm ON up.payment_method_id = pm.name AND pm.risk_rating='medium-high'
                  WHERE up.user_id=d.user_id AND DATE_TRUNC('day',up.date_updated)=d.on_date AND up.status='SUCCEEDED'
                ) medium_high_risk_payments,
                (SELECT COALESCE(SUM(up.amount),0)/100 FROM casino_core.user_payments up
                   JOIN casino_core.payment_method pm ON up.payment_method_id = pm.name AND pm.risk_rating='high'
                  WHERE up.user_id=d.user_id AND DATE_TRUNC('day',up.date_updated)=d.on_date AND up.status='SUCCEEDED'
                ) high_risk_payments
            FROM analytics_dw.daily_player_stats d
            JOIN casino_core.users u ON u.id = d.user_id
            JOIN casino_core.user_info ui ON ui.userid = u.id AND ui.testaccount = FALSE
            JOIN casino_core.brands a ON a.id = u.affiliateid
            LEFT OUTER JOIN analytics_dw.gaming_duty_rates tax
                ON tax.country = ui.country
                   AND (tax.from_date <= d.on_date OR tax.from_date IS NULL)
                   AND (tax.to_date >= d.on_date OR tax.to_date IS NULL)
            WHERE d.on_date BETWEEN %s AND %s
        ) s
        """
        return sql, (from_date, to_date)


# ---------------------------------------------------------------------------
# Stub steps for the other pipeline stages
# ---------------------------------------------------------------------------

class PassThroughStep(StatsStep):
    """Stub for steps not detailed in the Scala source (same interface, no-op SQL)."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def table_name(self) -> str:
        return self._name

    def generate_insert(self, from_date: date, to_date: date) -> tuple[str, tuple]:
        return "SELECT 1", ()  # no-op: real SQL would be here

    def process(self, db: DB, from_date: date, to_date: date, clear: bool = False) -> int:
        log.info("step placeholder", step=self._name, from_date=str(from_date))
        return 0


# ---------------------------------------------------------------------------
# Step chooser (mirrors StepChooser.scala)
#
# Supports: single step, range (from-to), open range (from- or -to), comma list.
# ---------------------------------------------------------------------------

class StepChooser:
    """
    Flexible step selection for the analytics pipeline.

    Examples:
      "dps"           → run only DPS
      "dps-revenue"   → run dps through revenue (inclusive)
      "revenue-"      → run revenue and all subsequent steps
      "-dpgs"         → run everything up to and including dpgs
      "dps,revenue"   → run two non-contiguous steps
    """

    def __init__(self, step_names: list[str]) -> None:
        self._steps = step_names

    def _idx(self, name: str) -> int:
        try:
            return self._steps.index(name)
        except ValueError:
            raise ValueError(f"Unknown step: {name!r}. Valid: {self._steps}")

    def choose(self, spec: str) -> list[str]:
        if "," in spec:
            return [s.strip() for s in spec.split(",")]
        if "-" in spec:
            parts = spec.split("-", 1)
            left, right = parts[0].strip(), parts[1].strip()
            if left and right:
                return self._steps[self._idx(left):self._idx(right) + 1]
            if left:
                return self._steps[self._idx(left):]
            if right:
                return self._steps[:self._idx(right) + 1]
        return [spec.strip()]

    @property
    def all_steps(self) -> list[str]:
        return self._steps


# ---------------------------------------------------------------------------
# Pipeline registry
# ---------------------------------------------------------------------------

STEP_OBJECTS: dict[str, StatsStep] = {
    "balances":            PassThroughStep("daily_player_balances"),
    "dps":                 DailyPlayerStatsStep(),
    "dpgs":                PassThroughStep("daily_player_game_stats"),
    "freerounds":          PassThroughStep("daily_player_free_rounds"),
    "revenue":             DailyPlayerRevenueStep(),
    "affiliate":           PassThroughStep("daily_player_affiliate"),
    "period":              PassThroughStep("period_player_stats"),
    "runningtotals":       PassThroughStep("player_running_totals"),
    "runningcalculations": PassThroughStep("player_running_calculations"),
    "matrix":              PassThroughStep("matrix_scores"),
    "averages":            PassThroughStep("running_averages"),
    "scorealerts":         PassThroughStep("matrix_score_alerts"),
    "scoreinteractions":   PassThroughStep("matrix_score_interactions"),
    "levels":              PassThroughStep("matrix_levels"),
    "messages":            PassThroughStep("rg_messages"),
    "timespent":           PassThroughStep("time_spent_alerts"),
    "compliancealerts":    PassThroughStep("compliance_alerts"),
    "topdepositoralerts":  PassThroughStep("top_depositor_alerts"),
    "vipsegments":         PassThroughStep("vip_segments"),
}

STEP_ORDER = list(STEP_OBJECTS.keys())


# ---------------------------------------------------------------------------
# CLI entry point (mirrors Run.scala)
# ---------------------------------------------------------------------------

def main() -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    parser = argparse.ArgumentParser(description="Daily stats pipeline")
    parser.add_argument("-date",  help="Single date YYYYMMDD (default: yesterday)")
    parser.add_argument("-from",  dest="from_date", help="Start date YYYYMMDD")
    parser.add_argument("-to",    dest="to_date",   help="End date YYYYMMDD")
    parser.add_argument("-env",   default="stage",  help="Environment")
    parser.add_argument("-steps", default=None,     help="Step spec (e.g. dps, dps-revenue, revenue-)")
    parser.add_argument("-clear", default="false",  help="Clear existing data (true/false)")
    args = parser.parse_args()

    def parse_date(s: str) -> date:
        return datetime.strptime(s, "%Y%m%d").date()

    from_date = (
        parse_date(args.from_date) if args.from_date
        else parse_date(args.date) if args.date
        else date.today() - timedelta(days=1)
    )
    to_date = parse_date(args.to_date) if args.to_date else from_date
    clear   = args.clear.lower() == "true"

    db_url = os.environ.get("DATABASE_URL", "")
    db     = DB(db_url)

    chooser = StepChooser(STEP_ORDER)
    selected = chooser.choose(args.steps) if args.steps else STEP_ORDER

    log.info("daily stats starting", env=args.env,
             from_date=str(from_date), to_date=str(to_date),
             steps=selected, clear=clear)

    total_rows = 0
    for step_name in selected:
        step = STEP_OBJECTS.get(step_name)
        if not step:
            log.error("unknown step", name=step_name)
            sys.exit(1)

        log.info("starting step", step=step_name)
        t0 = time.monotonic()
        try:
            rows = step.process(db, from_date, to_date, clear)
            total_rows += rows
        except Exception as exc:
            log.error("step failed", step=step_name, error=str(exc))
            db.close()
            sys.exit(1)
        elapsed = time.monotonic() - t0
        log.info("step complete", step=step_name, rows=rows, elapsed_s=round(elapsed, 2))

    db.close()
    log.info("daily stats complete", total_rows=total_rows)


if __name__ == "__main__":
    main()
