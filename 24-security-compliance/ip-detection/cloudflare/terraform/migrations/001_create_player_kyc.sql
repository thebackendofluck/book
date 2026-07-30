-- Chapter 24 - IP Detection Pipeline
-- D1 migration: create the player_kyc table.
--
-- Run with:
--   wrangler d1 execute casino-player-db \
--     --file=./terraform/migrations/001_create_player_kyc.sql \
--     --env production

CREATE TABLE IF NOT EXISTS player_kyc (
  player_id   TEXT        NOT NULL PRIMARY KEY,
  status      TEXT        NOT NULL DEFAULT 'none'
                          CHECK (status IN ('none', 'pending', 'approved', 'rejected', 'frozen')),
  tier        INTEGER     NOT NULL DEFAULT 0
                          CHECK (tier IN (0, 1, 2)),
  reviewed_at TEXT,           -- ISO 8601 timestamp, nullable
  reviewer    TEXT,           -- email or system ID of reviewer, nullable
  notes       TEXT,           -- compliance notes, nullable
  created_at  TEXT        NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT        NOT NULL DEFAULT (datetime('now'))
);

-- Index for status-based queries (e.g., "show all pending KYC cases")
CREATE INDEX IF NOT EXISTS idx_player_kyc_status
  ON player_kyc (status);

-- Index for reviewer workload queries
CREATE INDEX IF NOT EXISTS idx_player_kyc_reviewer
  ON player_kyc (reviewer)
  WHERE reviewer IS NOT NULL;

-- Trigger to auto-update updated_at on every row modification
CREATE TRIGGER IF NOT EXISTS trg_player_kyc_updated_at
  AFTER UPDATE ON player_kyc
  FOR EACH ROW
BEGIN
  UPDATE player_kyc
     SET updated_at = datetime('now')
   WHERE player_id = OLD.player_id;
END;
