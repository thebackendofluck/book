# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
FastAPI service for the regulator-revenue dashboard.

Endpoints (all read-only, public):
  GET /health
  GET /api/regulator-reports/summary       overall stats (states, totals, last run)
  GET /api/regulator-reports/timeseries    runs over time per state
  GET /api/regulator-reports/breakdown     latest run grouped by vertical / cadence / format

Every endpoint goes through the Redis cache decorator (TTL = CACHE_TTL_SECONDS).
The cache key is the path; cache invalidation happens implicitly via TTL expiry,
which is fine because the underlying data only changes once a week when the
collector runs.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
log = structlog.get_logger()

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))
ALLOW_ORIGINS = [o.strip() for o in os.environ.get("ALLOW_ORIGINS", "*").split(",")]

# ---------------------------------------------------------------------------
# Channel filter — online vs retail/land-based gambling verticals.
# The metric_facts table mixes online (igaming, sports-wagering, online-poker…)
# and land-based (commercial-casino, retail-*, video-gaming, lottery…) GGR.
# Market-sizing charts accept ?channel=online|retail|all (default "all" keeps
# the all-gaming view this dashboard is titled for). channel=online serves the
# iGaming-only figures the book cites — without it, e.g. Nevada's ~$16B
# land-based casino win dwarfs the actual online market.
# ---------------------------------------------------------------------------
_CHANNEL_VERTICALS: dict[str, tuple[str, ...]] = {
    "online": (
        "igaming", "sports-wagering", "online-poker",
        "bingo-remote", "online-casino", "combined",
    ),
    "retail": (
        "commercial-casino", "retail-betting", "retail-casino",
        "land-based-betting", "land-based-casino", "video-gaming",
        "vgt", "vlt", "slot-arcades", "arcades", "lottery",
        "horse-racing", "bingo",
    ),
}


_VALID_CHANNELS = {"online", "retail", "all"}


def _channel_sql(channel: str) -> str:
    """Return a safe ' AND vertical IN (...)' fragment, or '' for all/unknown.

    The vertical list is a fixed server-side constant (no user input reaches
    the SQL), so inlining it as a literal is injection-safe.
    """
    verticals = _CHANNEL_VERTICALS.get((channel or "all").lower())
    if not verticals:
        return ""
    quoted = ",".join("'" + v + "'" for v in verticals)
    return f" AND vertical IN ({quoted})"

engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None
redis: Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, SessionLocal, redis
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    log.info("api.startup", db=DATABASE_URL.split("@")[-1], redis=REDIS_URL)
    try:
        yield
    finally:
        await redis.close()
        await engine.dispose()


app = FastAPI(title="Regulator Revenue Reports API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _session_factory() -> async_sessionmaker[AsyncSession]:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="DB session not initialized")
    return SessionLocal


def _redis() -> Redis:
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis not initialized")
    return redis


async def cached(key: str, ttl: int, loader):
    """Tiny Redis cache wrapper: read-through with JSON serialization."""
    if redis is None:
        return await loader()
    cached_value = await redis.get(key)
    if cached_value:
        return json.loads(cached_value)
    value = await loader()
    await redis.setex(key, ttl, json.dumps(value, default=str))
    return value


# ---------------- endpoints ----------------

@app.get("/health")
async def health() -> dict[str, Any]:
    out = {"status": "ok", "db": False, "redis": False}
    try:
        async with _session_factory()() as s:
            await s.execute(text("SELECT 1"))
        out["db"] = True
    except Exception as e:
        out["db_error"] = str(e)
    try:
        out["redis"] = bool(await redis.ping())
    except Exception as e:
        out["redis_error"] = str(e)
    return out


@app.get("/api/regulator-reports/summary")
async def summary() -> dict[str, Any]:
    async def load():
        async with _session_factory()() as s:
            res = await s.execute(text("""
                WITH latest_per_state AS (
                    SELECT DISTINCT ON (state) state, regulator, source_url,
                           collected_at, report_count, success_count,
                           failure_count, total_bytes
                    FROM collection_runs
                    ORDER BY state, collected_at DESC
                )
                SELECT * FROM latest_per_state ORDER BY state
            """))
            states = [dict(r._mapping) for r in res.all()]

            totals_res = await s.execute(text("""
                SELECT
                    COUNT(*)                                      AS total_runs,
                    COUNT(DISTINCT state)                         AS state_count,
                    MAX(collected_at)                             AS last_run,
                    COALESCE(SUM(success_count), 0)               AS total_success,
                    COALESCE(SUM(report_count),  0)               AS total_attempted,
                    COALESCE(SUM(total_bytes),   0)               AS total_bytes
                FROM collection_runs
            """))
            totals = dict(totals_res.one()._mapping)

            # [FIX #2] Expose the real number of states that have actual GGR
            # data in metric_facts — distinct from collection_runs state_count
            # which reflects states we *collected* from, not states with parsed
            # numeric data. These two can diverge (e.g. a state whose collector
            # ran but whose parser emitted nothing yet).
            mf_states_res = await s.execute(text("""
                SELECT COUNT(DISTINCT state) AS metric_facts_states
                FROM metric_facts
                WHERE metric_name = 'ggr'
            """))
            mf_states = int(mf_states_res.scalar() or 0)
            totals["metric_facts_states"] = mf_states

        return {"totals": totals, "states": states}

    return await cached("rrc:summary", CACHE_TTL, load)


@app.get("/api/regulator-reports/timeseries")
async def timeseries(state: str | None = None, days: int = 365) -> dict[str, Any]:
    state_filter = " AND state = :state" if state else ""
    days = max(1, min(days, 3650))

    async def load():
        async with _session_factory()() as s:
            params = {"days": days}
            if state:
                params["state"] = state.upper()
            res = await s.execute(text(f"""
                SELECT state,
                       date_trunc('day', collected_at) AS day,
                       SUM(success_count)              AS success,
                       SUM(report_count)               AS attempted,
                       SUM(total_bytes)                AS bytes
                FROM collection_runs
                WHERE collected_at >= NOW() - make_interval(days => :days)
                {state_filter}
                GROUP BY state, day
                ORDER BY day, state
            """), params)
            rows = [
                {
                    "state": r.state,
                    "day": r.day.isoformat(),
                    "success": int(r.success or 0),
                    "attempted": int(r.attempted or 0),
                    "bytes": int(r.bytes or 0),
                }
                for r in res.all()
            ]
        return {"days": days, "state": state, "points": rows}

    return await cached(f"rrc:timeseries:{state or 'all'}:{days}", CACHE_TTL, load)


# ---------------- executive metric endpoints (USD-denominated) ----------------

@app.get("/api/regulator-reports/metrics/us-totals")
async def us_totals(channel: str = "all") -> dict[str, Any]:
    if channel not in _VALID_CHANNELS:
        raise HTTPException(400, "invalid channel")
    chan = _channel_sql(channel)

    async def load():
        async with _session_factory()() as s:
            res = await s.execute(text(f"""
                WITH quorum_periods AS (
                    -- Only treat a period as valid latest if >= 8 distinct states
                    -- have reported real (ggr > 0) data for that period (online channel).
                    -- This prevents a sparse just-published month from becoming 'latest'.
                    SELECT period
                    FROM metric_facts
                    WHERE metric_name = 'ggr'
                      AND value_usd_cents > 0
                      AND period <= to_char(CURRENT_DATE, 'YYYY-MM')
                      {chan}
                    GROUP BY period
                    HAVING COUNT(DISTINCT state) >= 8
                ),
                latest AS (
                    SELECT MAX(period) AS p
                    FROM quorum_periods
                )
                SELECT
                    (SELECT p FROM latest)                                   AS latest_period,
                    COALESCE(SUM(value_usd_cents) FILTER
                        (WHERE metric_name='ggr' AND period = (SELECT p FROM latest)), 0)
                                                                              AS ggr_latest,
                    COALESCE(SUM(value_usd_cents) FILTER
                        (WHERE metric_name='ggr'
                         AND period >= to_char(date_trunc('year', CURRENT_DATE), 'YYYY-MM')), 0)
                                                                              AS ggr_ytd,
                    COALESCE(SUM(value_usd_cents) FILTER
                        (WHERE metric_name='tax_paid'
                         AND period >= to_char(date_trunc('year', CURRENT_DATE), 'YYYY-MM')), 0)
                                                                              AS tax_ytd,
                    COALESCE(SUM(value_usd_cents) FILTER (WHERE metric_name='ggr'), 0)
                                                                              AS ggr_all_time,
                    COUNT(DISTINCT operator) FILTER (WHERE metric_name='ggr')
                                                                              AS operator_count,
                    COUNT(DISTINCT state)    FILTER (WHERE metric_name='ggr')
                                                                              AS state_count,
                    COUNT(DISTINCT period)   FILTER (WHERE metric_name='ggr')
                                                                              AS month_count
                FROM metric_facts
                WHERE TRUE{chan}
            """))
            row = dict(res.one()._mapping)
            # Fix #1: asyncpg returns PostgreSQL numeric as Decimal/string — coerce to int
            for field in ("ggr_latest", "ggr_ytd", "tax_ytd", "ggr_all_time"):
                if row[field] is not None:
                    row[field] = int(row[field])
            for field in ("operator_count", "state_count", "month_count"):
                if row[field] is not None:
                    row[field] = int(row[field])
            row["channel"] = channel
        return row

    return await cached(f"rrc:metrics:us-totals:{channel}", CACHE_TTL, load)


@app.get("/api/regulator-reports/metrics/by-state")
async def by_state(months: int = 12, channel: str = "all") -> dict[str, Any]:
    if channel not in _VALID_CHANNELS:
        raise HTTPException(400, "invalid channel")
    months = max(1, min(months, 60))
    chan = _channel_sql(channel)

    async def load():
        async with _session_factory()() as s:
            res = await s.execute(text(f"""
                SELECT state,
                       -- Fix #3: clamp negative-GGR months so tax_ytd can't exceed ggr_ytd
                       GREATEST(SUM(value_usd_cents) FILTER (WHERE metric_name='ggr'), 0)
                                                                                  AS ggr_cents,
                       -- Fix #6: COALESCE handle/tax to 0 for states with no data
                       COALESCE(SUM(value_usd_cents) FILTER (WHERE metric_name='handle'),   0)
                                                                                  AS handle_cents,
                       COALESCE(SUM(value_usd_cents) FILTER (WHERE metric_name='tax_paid'), 0)
                                                                                  AS tax_cents,
                       COUNT(DISTINCT period)                                     AS months,
                       COUNT(DISTINCT operator)                                   AS operators
                FROM metric_facts
                WHERE period >= to_char(date_trunc('month', CURRENT_DATE) - make_interval(months => :months), 'YYYY-MM')
                  AND period <= to_char(CURRENT_DATE, 'YYYY-MM')
                  -- Fix #2: exclude future zero-value periods (e.g. PA pre-populated xlsx tabs)
                  AND value_usd_cents > 0
                  {chan}
                GROUP BY state
                -- Fix #7: exclude states with no real GGR (e.g. GE/NV null-only rows)
                HAVING SUM(value_usd_cents) FILTER (WHERE metric_name='ggr') > 0
                ORDER BY ggr_cents DESC NULLS LAST
            """), {"months": months})
            rows = []
            for r in res.all():
                row = dict(r._mapping)
                # Fix #1: coerce PostgreSQL numeric → int
                for field in ("ggr_cents", "handle_cents", "tax_cents"):
                    if row[field] is not None:
                        row[field] = int(row[field])
                for field in ("months", "operators"):
                    if row[field] is not None:
                        row[field] = int(row[field])
                rows.append(row)
        return {"months": months, "channel": channel, "states": rows}

    return await cached(f"rrc:metrics:by-state:{months}:{channel}", CACHE_TTL, load)


@app.get("/api/regulator-reports/metrics/leaderboard")
async def leaderboard(months: int = 12, limit: int = 15, channel: str = "all") -> dict[str, Any]:
    if channel not in _VALID_CHANNELS:
        raise HTTPException(400, "invalid channel")
    months = max(1, min(months, 60))
    limit  = max(1, min(limit, 50))
    chan = _channel_sql(channel)

    async def load():
        async with _session_factory()() as s:
            res = await s.execute(text(f"""
                SELECT state, operator, vertical,
                       SUM(value_usd_cents) AS ggr_cents,
                       COUNT(DISTINCT period) AS months
                FROM metric_facts
                WHERE metric_name = 'ggr'
                  AND period >= to_char(date_trunc('month', CURRENT_DATE) - make_interval(months => :months), 'YYYY-MM')
                  AND value_usd_cents > 0
                  {chan}
                GROUP BY state, operator, vertical
                ORDER BY ggr_cents DESC NULLS LAST
                LIMIT :lim
            """), {"months": months, "lim": limit})
            rows = []
            for r in res.all():
                row = dict(r._mapping)
                # Fix #1: coerce PostgreSQL numeric → int
                if row.get("ggr_cents") is not None:
                    row["ggr_cents"] = int(row["ggr_cents"])
                if row.get("months") is not None:
                    row["months"] = int(row["months"])
                rows.append(row)
        return {"months": months, "limit": limit, "channel": channel, "operators": rows}

    return await cached(f"rrc:metrics:leaderboard:{months}:{limit}:{channel}", CACHE_TTL, load)


@app.get("/api/regulator-reports/metrics/monthly-trend")
async def monthly_trend(metric: str = "ggr", months: int = 24, channel: str = "all") -> dict[str, Any]:
    if metric not in {"ggr", "handle", "tax_paid", "patron_winnings"}:
        raise HTTPException(400, "invalid metric")
    if channel not in _VALID_CHANNELS:
        raise HTTPException(400, "invalid channel")
    months = max(1, min(months, 120))
    chan = _channel_sql(channel)

    async def load():
        async with _session_factory()() as s:
            res = await s.execute(text(f"""
                SELECT state, period,
                       SUM(value_usd_cents) AS value_cents
                FROM metric_facts
                WHERE metric_name = :metric
                  AND period >= to_char(date_trunc('month', CURRENT_DATE) - make_interval(months => :months), 'YYYY-MM')
                  -- Fix #2: exclude future zero-value periods (PA pre-populated xlsx future tabs)
                  AND period <= to_char(CURRENT_DATE, 'YYYY-MM')
                  AND value_usd_cents > 0
                  {chan}
                GROUP BY state, period
                ORDER BY period ASC, state ASC
            """), {"metric": metric, "months": months})
            rows = [
                # Fix #1: value_cents already int()-cast here; guard against None
                {"state": r.state, "period": r.period,
                 "value_cents": int(r.value_cents) if r.value_cents is not None else 0}
                for r in res.all()
            ]
        return {"metric": metric, "months": months, "channel": channel, "points": rows}

    return await cached(f"rrc:metrics:trend:{metric}:{months}:{channel}", CACHE_TTL, load)


@app.get("/api/regulator-reports/breakdown")
async def breakdown() -> dict[str, Any]:
    async def load():
        async with _session_factory()() as s:
            # Use a CTE so the "latest run id per state" is computed in-query.
            # Avoids passing a Python list as :ids — asyncpg's binding for
            # ANY(:ids) is unreliable through SQLAlchemy text() and silently
            # returned 0 rows, leaving the dashboard charts empty.
            latest_cte = """
                WITH latest AS (
                  SELECT DISTINCT ON (state) id, state
                  FROM collection_runs ORDER BY state, collected_at DESC
                )
            """
            # [FIX #1] vertical list: use metric_facts (WHERE metric_name='ggr')
            # instead of report_snapshots. report_snapshots counts collection
            # artifacts (one row per fetched URL) so the vertical distribution
            # reflected the number of *files* downloaded per vertical, not the
            # actual volume of operator-month data. metric_facts GGR rows give
            # the real distribution that matches what the charts aggregate.
            vert_res = await s.execute(text("""
                SELECT vertical, COUNT(*) AS n
                FROM metric_facts
                WHERE metric_name = 'ggr'
                GROUP BY vertical ORDER BY n DESC
            """))
            cad_res = await s.execute(text(latest_cte + """
                SELECT cadence, COUNT(*) AS n
                FROM report_snapshots
                WHERE run_id IN (SELECT id FROM latest) AND fetch_error IS NULL
                GROUP BY cadence ORDER BY n DESC
            """))
            fmt_res = await s.execute(text(latest_cte + """
                SELECT format, COUNT(*) AS n
                FROM report_snapshots
                WHERE run_id IN (SELECT id FROM latest) AND fetch_error IS NULL
                GROUP BY format ORDER BY n DESC
            """))
            # by_state_vertical also sourced from metric_facts for consistency
            stk_res = await s.execute(text("""
                SELECT state, vertical, COUNT(*) AS n
                FROM metric_facts
                WHERE metric_name = 'ggr'
                GROUP BY state, vertical ORDER BY state, vertical
            """))

        return {
            "vertical": [{"label": r.vertical, "value": r.n} for r in vert_res.all()],
            "cadence":  [{"label": r.cadence,  "value": r.n} for r in cad_res.all()],
            "format":   [{"label": r.format,   "value": r.n} for r in fmt_res.all()],
            "by_state_vertical": [
                {"state": r.state, "vertical": r.vertical, "value": r.n}
                for r in stk_res.all()
            ],
        }

    return await cached("rrc:breakdown", CACHE_TTL, load)
