-- Brazilian Betting Platform - minimal D1 schema for chapter 46 Workers
-- Derived from the runtime queries used by the Cloudflare worker set.

CREATE TABLE IF NOT EXISTS players (
  id TEXT PRIMARY KEY,
  cpf TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL UNIQUE,
  full_name TEXT,
  password_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  kyc_status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  kyc_updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_players_cpf ON players(cpf);
CREATE INDEX IF NOT EXISTS idx_players_email ON players(email);
CREATE INDEX IF NOT EXISTS idx_players_status ON players(status);

CREATE TABLE IF NOT EXISTS bets (
  id TEXT PRIMARY KEY,
  player_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  selection TEXT NOT NULL,
  odds_at_placement REAL NOT NULL,
  stake_amount_centavos INTEGER NOT NULL,
  potential_return_centavos INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'placed',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bets_player_id_created_at ON bets(player_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bets_created_at ON bets(created_at);

CREATE TABLE IF NOT EXISTS pix_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  txid TEXT NOT NULL UNIQUE,
  player_id TEXT NOT NULL,
  amount_centavos INTEGER NOT NULL,
  status TEXT NOT NULL,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  end_to_end_id TEXT UNIQUE,
  confirmed_at TEXT,
  psp_payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_pix_transactions_txid ON pix_transactions(txid);
CREATE INDEX IF NOT EXISTS idx_pix_transactions_end_to_end_id ON pix_transactions(end_to_end_id);
CREATE INDEX IF NOT EXISTS idx_pix_transactions_player_id ON pix_transactions(player_id);

-- Application-managed outbox for PIX receipts forwarded to the authoritative
-- AWS core. This is delivery state, not a wallet ledger.
CREATE TABLE IF NOT EXISTS pix_origin_notifications (
  notification_id TEXT PRIMARY KEY,
  txid TEXT NOT NULL UNIQUE,
  end_to_end_id TEXT NOT NULL UNIQUE,
  player_id TEXT NOT NULL,
  amount_centavos INTEGER NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_attempt_at TEXT,
  delivered_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pix_origin_notifications_pending
  ON pix_origin_notifications(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS wallet_transactions (
  id TEXT PRIMARY KEY,
  player_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  amount_centavos INTEGER NOT NULL,
  balance_after_centavos INTEGER NOT NULL,
  reference TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wallet_transactions_player_id_created_at
  ON wallet_transactions(player_id, created_at DESC);

-- Delivery metadata for already prepared, XSD-validated, e-CNPJ-signed SIGAP
-- documents. The signed document is carried by Queue, not stored as event rows.
CREATE TABLE IF NOT EXISTS sigap_delivery_ledger (
  batch_id TEXT PRIMARY KEY NOT NULL,
  operator_id TEXT NOT NULL,
  document_family TEXT NOT NULL CHECK (document_family IN ('bettor','wallet','operator_daily','operator_monthly','sports_bets','online_games')),
  reference_date TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  reception_path TEXT NOT NULL,
  record_count INTEGER NOT NULL CHECK (record_count BETWEEN 1 AND 7500),
  compressed_size_bytes INTEGER NOT NULL CHECK (compressed_size_bytes BETWEEN 1 AND 3145728),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  delivery_status TEXT NOT NULL DEFAULT 'pending' CHECK (delivery_status IN ('pending','delivered')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_http_status INTEGER,
  last_response_body TEXT,
  sigap_movement_id TEXT,
  reconciliation_status TEXT NOT NULL DEFAULT 'not_started' CHECK (reconciliation_status IN ('not_started','pending','matched','divergent','failed')),
  last_attempt_at TEXT,
  delivered_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sigap_delivery_status_updated
  ON sigap_delivery_ledger(delivery_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_sigap_delivery_reference
  ON sigap_delivery_ledger(reference_date, document_family);

CREATE TABLE IF NOT EXISTS sigap_ggr_reports (
  id TEXT PRIMARY KEY,
  report_date TEXT NOT NULL UNIQUE,
  total_stake_centavos INTEGER NOT NULL,
  total_payout_centavos INTEGER NOT NULL,
  ggr_centavos INTEGER NOT NULL,
  submitted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sigap_ggr_reports_report_date ON sigap_ggr_reports(report_date);

CREATE TABLE IF NOT EXISTS request_log (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_request_log_created_at ON request_log(created_at);

CREATE TABLE IF NOT EXISTS security_events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_security_events_created_at ON security_events(created_at);
