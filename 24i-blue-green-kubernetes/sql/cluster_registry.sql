-- cluster_registry.sql — single-writer ownership for the shared player database
--
-- WHY A LEASE AND NOT A SESSION ADVISORY LOCK
--
-- An earlier revision of this file granted primary status with
-- pg_try_advisory_lock(12345). Advisory locks taken that way are *session*
-- scoped: the lock disappears the moment the claiming connection closes. The
-- caller (python/wallet_startup.py) claimed the lock at startup and then closed
-- its cursor and connection in the same function, so the mutual exclusion it
-- appeared to provide lasted microseconds. After a switchover the old colour is
-- only cordoned and drained and its pods stay alive for up to an hour, so any
-- wallet pod that restarted inside that window found the lock free, declared
-- itself primary, cleared its read-only flag, and started writing player
-- balances into the same database the live colour was writing to. That is a
-- double-spend and balance-corruption path, not a consistency nit.
--
-- A session lock is also the wrong granularity. wallet-service runs with
-- replicas: 3, so at most one of the three pods could ever have held a session
-- lock; the other two would have been permanently read-only.
--
-- The model below is a leased row with a heartbeat and a fencing token:
--
--   * The lease is owned by a cluster COLOUR, not by a connection or a pod.
--     Every pod of the holding colour may write; pods of any other colour may
--     not. Nothing depends on connection lifetime, so connection pools,
--     PgBouncer, and pod restarts are all safe.
--   * The lease has an explicit expiry. A holder must renew before it expires
--     (renew_primary_lease). A holder that cannot reach the database therefore
--     stops being a holder when its own deadline passes and must fail closed.
--   * Every handover bumps a monotonic epoch, taken from a sequence. The epoch
--     is the fencing token: a balance-mutating transaction calls
--     assert_primary_lease(colour, epoch) as its first statement, so a writer
--     that was demoted while a request was already in flight has its
--     transaction aborted by the database rather than committing a stale write.
--
-- Advisory lock 12345 is still used, but only as pg_advisory_xact_lock inside
-- the claim and release paths, to serialise handovers. A transaction-scoped
-- lock is released at commit by definition, so it cannot outlive the work it
-- protects.

-- ── table ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cluster_registry (
    id               SERIAL PRIMARY KEY,
    cluster_color    VARCHAR(10) NOT NULL,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_primary       BOOLEAN NOT NULL DEFAULT FALSE,
    cluster_node     VARCHAR(255) NOT NULL,
    lease_epoch      BIGINT,
    lease_expires_at TIMESTAMPTZ
);

-- Upgrade path for registries created before the lease columns existed.
ALTER TABLE cluster_registry ADD COLUMN IF NOT EXISTS lease_epoch      BIGINT;
ALTER TABLE cluster_registry ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

-- One row per colour. The old code inserted a new row on every claim with
-- "ON CONFLICT DO NOTHING" and no conflict target, which cannot conflict with
-- anything and therefore accumulated one dead row per rotation. If this index
-- fails to create you already have duplicate rows: collapse them to the newest
-- row per colour before rerunning this file.
CREATE UNIQUE INDEX IF NOT EXISTS cluster_registry_color_unique
    ON cluster_registry (cluster_color);

-- At most one primary row, enforced by the database rather than by convention.
CREATE UNIQUE INDEX IF NOT EXISTS cluster_registry_primary_unique
    ON cluster_registry (is_primary)
    WHERE is_primary = TRUE;

-- Monotonic fencing token. Never reused, never reset.
CREATE SEQUENCE IF NOT EXISTS cluster_primary_epoch_seq AS BIGINT START 1;

-- ── claim ────────────────────────────────────────────────────────────────────

-- Return type changed from BOOLEAN, so the old signature has to go first.
DROP FUNCTION IF EXISTS claim_primary_cluster(VARCHAR, VARCHAR);

-- Acquire the primary lease for p_color, or report who holds it.
--
-- Returns acquired = TRUE only when the caller's colour owns a live lease
-- afterwards. A claim by a colour that already holds the lease is a renewal by
-- a sibling pod: it extends the expiry and keeps the existing epoch, so
-- starting a fourth replica does not fence the other three.
CREATE OR REPLACE FUNCTION claim_primary_cluster(
    p_color       VARCHAR(10),
    p_node        VARCHAR(255),
    p_ttl_seconds INTEGER DEFAULT 20
) RETURNS TABLE (
    acquired         BOOLEAN,
    lease_epoch      BIGINT,
    lease_expires_at TIMESTAMPTZ,
    holder_color     VARCHAR(10)
) AS $$
DECLARE
    v_holder  cluster_registry;
    v_expiry  TIMESTAMPTZ;
    v_epoch   BIGINT;
BEGIN
    IF p_ttl_seconds IS NULL OR p_ttl_seconds < 5 THEN
        RAISE EXCEPTION 'primary lease TTL must be at least 5 seconds, got %', p_ttl_seconds;
    END IF;

    -- Serialise concurrent claims. Transaction scoped: released at COMMIT.
    PERFORM pg_advisory_xact_lock(12345);

    SELECT * INTO v_holder
    FROM cluster_registry
    WHERE is_primary = TRUE
    FOR UPDATE;

    v_expiry := NOW() + make_interval(secs => p_ttl_seconds);

    IF FOUND AND v_holder.cluster_color <> p_color
       AND v_holder.lease_expires_at IS NOT NULL
       AND v_holder.lease_expires_at > NOW() THEN
        -- Someone else holds a live lease. Do not steal it; the switchover
        -- releases it explicitly and then waits out the TTL.
        RETURN QUERY SELECT FALSE,
                            v_holder.lease_epoch,
                            v_holder.lease_expires_at,
                            v_holder.cluster_color;
        RETURN;
    END IF;

    IF FOUND AND v_holder.cluster_color = p_color THEN
        -- Sibling pod of the current holder: extend, keep the epoch.
        UPDATE cluster_registry
        SET lease_expires_at = v_expiry,
            last_heartbeat   = NOW(),
            cluster_node     = p_node
        WHERE id = v_holder.id
        RETURNING cluster_registry.lease_epoch INTO v_epoch;

        RETURN QUERY SELECT TRUE, v_epoch, v_expiry, p_color;
        RETURN;
    END IF;

    -- No holder, or the holder's lease has expired: this is a handover, so the
    -- fencing token advances.
    v_epoch := nextval('cluster_primary_epoch_seq');

    UPDATE cluster_registry
    SET is_primary       = FALSE,
        lease_expires_at = LEAST(cluster_registry.lease_expires_at, NOW())
    WHERE is_primary = TRUE;

    INSERT INTO cluster_registry (
        cluster_color, registered_at, last_heartbeat,
        is_primary, cluster_node, lease_epoch, lease_expires_at
    )
    VALUES (p_color, NOW(), NOW(), TRUE, p_node, v_epoch, v_expiry)
    ON CONFLICT (cluster_color) DO UPDATE
    SET registered_at    = NOW(),
        last_heartbeat   = NOW(),
        is_primary       = TRUE,
        cluster_node     = EXCLUDED.cluster_node,
        lease_epoch      = EXCLUDED.lease_epoch,
        lease_expires_at = EXCLUDED.lease_expires_at;

    RETURN QUERY SELECT TRUE, v_epoch, v_expiry, p_color;
END;
$$ LANGUAGE plpgsql;

-- ── renew (this is the heartbeat) ─────────────────────────────────────────────

-- Extend a lease the caller already holds. Renewal deliberately fails once the
-- lease has expired: an expired holder must go through claim_primary_cluster
-- again, which bumps the epoch and fences any write it had in flight. That is
-- what stops a pod that was partitioned from the database for a minute from
-- quietly resuming as primary.
CREATE OR REPLACE FUNCTION renew_primary_lease(
    p_color       VARCHAR(10),
    p_node        VARCHAR(255),
    p_epoch       BIGINT,
    p_ttl_seconds INTEGER DEFAULT 20
) RETURNS TABLE (
    renewed          BOOLEAN,
    lease_expires_at TIMESTAMPTZ
) AS $$
DECLARE
    v_expiry TIMESTAMPTZ;
BEGIN
    UPDATE cluster_registry
    SET lease_expires_at = NOW() + make_interval(secs => p_ttl_seconds),
        last_heartbeat   = NOW(),
        cluster_node     = p_node
    WHERE cluster_color = p_color
      AND is_primary = TRUE
      AND lease_epoch = p_epoch
      AND cluster_registry.lease_expires_at > NOW()
    RETURNING cluster_registry.lease_expires_at INTO v_expiry;

    IF v_expiry IS NULL THEN
        RETURN QUERY SELECT FALSE, NULL::TIMESTAMPTZ;
    ELSE
        RETURN QUERY SELECT TRUE, v_expiry;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Kept for the cluster health monitor, which only wants "am I still primary".
-- Return type changed from VOID, so the old signature has to go first.
DROP FUNCTION IF EXISTS update_cluster_heartbeat(VARCHAR);

CREATE OR REPLACE FUNCTION update_cluster_heartbeat(
    p_color VARCHAR(10)
) RETURNS BOOLEAN AS $$
DECLARE
    v_ok BOOLEAN;
BEGIN
    UPDATE cluster_registry
    SET last_heartbeat = NOW()
    WHERE cluster_color = p_color
      AND is_primary = TRUE
      AND lease_expires_at > NOW()
    RETURNING TRUE INTO v_ok;

    -- A heartbeat from a colour that does not hold a live lease is a lie, and
    -- the old version of this function recorded it as if it were the truth.
    RETURN COALESCE(v_ok, FALSE);
END;
$$ LANGUAGE plpgsql;

-- ── release ──────────────────────────────────────────────────────────────────

-- Give up the lease. Called by the switchover before the new colour claims, and
-- by a wallet pod on graceful shutdown. p_epoch NULL means "release whatever
-- this colour holds", which is what an operator running the switchover wants.
CREATE OR REPLACE FUNCTION release_primary_cluster(
    p_color VARCHAR(10),
    p_epoch BIGINT DEFAULT NULL
) RETURNS BOOLEAN AS $$
DECLARE
    v_released BOOLEAN;
BEGIN
    PERFORM pg_advisory_xact_lock(12345);

    UPDATE cluster_registry
    SET is_primary       = FALSE,
        lease_expires_at = NOW()
    WHERE cluster_color = p_color
      AND is_primary = TRUE
      AND (p_epoch IS NULL OR lease_epoch = p_epoch)
    RETURNING TRUE INTO v_released;

    RETURN COALESCE(v_released, FALSE);
END;
$$ LANGUAGE plpgsql;

-- ── fence ────────────────────────────────────────────────────────────────────

-- First statement of every balance-mutating transaction. Raises, and therefore
-- aborts the transaction, unless the caller's colour and epoch still own a live
-- lease.
--
-- FOR SHARE is load bearing: it blocks release_primary_cluster and
-- claim_primary_cluster (both take FOR UPDATE on the same row) until this
-- transaction commits, so a handover cannot land in the middle of a wallet
-- write. Keep wallet transactions short.
CREATE OR REPLACE FUNCTION assert_primary_lease(
    p_color VARCHAR(10),
    p_epoch BIGINT
) RETURNS VOID AS $$
DECLARE
    v_holder cluster_registry;
BEGIN
    SELECT * INTO v_holder
    FROM cluster_registry
    WHERE is_primary = TRUE
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no cluster holds the primary lease; refusing write from % epoch %',
            p_color, p_epoch;
    END IF;

    IF v_holder.cluster_color <> p_color OR v_holder.lease_epoch <> p_epoch THEN
        RAISE EXCEPTION 'fenced: primary lease is held by % epoch %, caller is % epoch %',
            v_holder.cluster_color, v_holder.lease_epoch, p_color, p_epoch;
    END IF;

    IF v_holder.lease_expires_at IS NULL OR v_holder.lease_expires_at <= NOW() THEN
        RAISE EXCEPTION 'primary lease for % epoch % expired at %',
            p_color, p_epoch, v_holder.lease_expires_at;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ── inspection ───────────────────────────────────────────────────────────────

-- Used by switchover.sh to verify a handover actually happened, and by the
-- rotation dashboards. Read only.
CREATE OR REPLACE FUNCTION current_primary_lease()
RETURNS TABLE (
    cluster_color     VARCHAR(10),
    cluster_node      VARCHAR(255),
    lease_epoch       BIGINT,
    lease_expires_at  TIMESTAMPTZ,
    seconds_remaining NUMERIC
) AS $$
    SELECT r.cluster_color,
           r.cluster_node,
           r.lease_epoch,
           r.lease_expires_at,
           ROUND(EXTRACT(EPOCH FROM (r.lease_expires_at - NOW()))::NUMERIC, 1)
    FROM cluster_registry r
    WHERE r.is_primary = TRUE;
$$ LANGUAGE sql STABLE;
