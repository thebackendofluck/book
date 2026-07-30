-- AcmeToCasino Platform - D1 Database Schema
-- SQLite-compatible (Cloudflare D1)

-- ─── Users ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  email           TEXT    UNIQUE NOT NULL,
  username        TEXT    UNIQUE NOT NULL,
  password_hash   TEXT    NOT NULL,
  first_name      TEXT,
  last_name       TEXT,
  date_of_birth   TEXT,   -- ISO-8601 date string
  country         TEXT,   -- 2-letter ISO code
  currency        TEXT    NOT NULL DEFAULT 'EUR',
  language        TEXT    NOT NULL DEFAULT 'en',
  status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','inactive','suspended','self_excluded')),
  balance         REAL    NOT NULL DEFAULT 0.00,
  role            TEXT    NOT NULL DEFAULT 'player'
                    CHECK (role IN ('player','vip','staff','admin')),
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_users_email    ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_status   ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_country  ON users(country);

-- ─── Games ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS games (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id           TEXT    UNIQUE NOT NULL,
  provider          TEXT    NOT NULL,
  name              TEXT    NOT NULL,
  category          TEXT    NOT NULL,  -- slots, table, live, instant
  type              TEXT    NOT NULL
                      CHECK (type IN ('slots','table','live','instant')),
  rtp               REAL,
  mobile_compatible INTEGER NOT NULL DEFAULT 1,  -- boolean (0/1)
  jurisdictions     TEXT,   -- JSON array of allowed 2-letter country codes; NULL = all
  currencies        TEXT,   -- JSON array of supported currency codes; NULL = all
  thumbnail_url     TEXT,
  is_active         INTEGER NOT NULL DEFAULT 1,
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_games_category  ON games(category);
CREATE INDEX IF NOT EXISTS idx_games_provider  ON games(provider);
CREATE INDEX IF NOT EXISTS idx_games_is_active ON games(is_active);

-- ─── Transactions ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transactions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  type            TEXT    NOT NULL
                    CHECK (type IN ('deposit','withdrawal','bonus','wager','win')),
  amount          REAL    NOT NULL,
  currency        TEXT    NOT NULL,
  status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','completed','failed','cancelled')),
  payment_method  TEXT,
  reference_id    TEXT,
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  processed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_id    ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type       ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_transactions_status     ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at);

-- ─── Bonuses ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bonuses (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id               INTEGER NOT NULL REFERENCES users(id),
  bonus_type            TEXT    NOT NULL,
  amount                REAL    NOT NULL,
  wagering_requirement  REAL    NOT NULL DEFAULT 0,
  wagering_contribution REAL    NOT NULL DEFAULT 0,
  expiry_date           TEXT,
  status                TEXT    NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','completed','expired','cancelled')),
  created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_bonuses_user_id    ON bonuses(user_id);
CREATE INDEX IF NOT EXISTS idx_bonuses_status     ON bonuses(status);
CREATE INDEX IF NOT EXISTS idx_bonuses_expiry     ON bonuses(expiry_date);

-- ─── KYC Records ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kyc_records (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  level           TEXT    NOT NULL
                    CHECK (level IN ('basic','standard','enhanced')),
  status          TEXT    NOT NULL DEFAULT 'not_started'
                    CHECK (status IN ('not_started','pending','approved','rejected','expired')),
  document_type   TEXT
                    CHECK (document_type IN (
                      'passport','national_id','drivers_license',
                      'proof_of_address','source_of_funds'
                    )),
  document_ref    TEXT,   -- R2 object key (private)
  reviewer_notes  TEXT,
  submitted_at    TEXT,
  reviewed_at     TEXT,
  expires_at      TEXT,
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_kyc_user_id ON kyc_records(user_id);
CREATE INDEX IF NOT EXISTS idx_kyc_status  ON kyc_records(status);

-- ─── Responsible Gambling Settings ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS responsible_gambling_settings (
  user_id                   INTEGER PRIMARY KEY REFERENCES users(id),
  daily_deposit_limit       REAL,
  weekly_deposit_limit      REAL,
  monthly_deposit_limit     REAL,
  session_reminder_minutes  INTEGER,
  reality_check_minutes     INTEGER,
  self_exclusion_until      TEXT,
  cool_off_until            TEXT,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ─── Compliance Events (audit log) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS compliance_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  event_type  TEXT    NOT NULL,
  details     TEXT,   -- JSON
  created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_compliance_events_user_id    ON compliance_events(user_id);
CREATE INDEX IF NOT EXISTS idx_compliance_events_event_type ON compliance_events(event_type);
CREATE INDEX IF NOT EXISTS idx_compliance_events_created_at ON compliance_events(created_at);

-- ─── Security Events ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS security_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ip          TEXT,
  event_type  TEXT    NOT NULL,
  details     TEXT,   -- JSON
  severity    INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_security_events_ip         ON security_events(ip);
CREATE INDEX IF NOT EXISTS idx_security_events_event_type ON security_events(event_type);
CREATE INDEX IF NOT EXISTS idx_security_events_created_at ON security_events(created_at);

-- ─── Translations ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS translations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  key         TEXT NOT NULL,
  language    TEXT NOT NULL,
  text        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE(key, language)
);

CREATE INDEX IF NOT EXISTS idx_translations_language ON translations(language);
