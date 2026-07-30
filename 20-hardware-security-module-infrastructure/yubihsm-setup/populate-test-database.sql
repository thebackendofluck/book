-- populate-test-database.sql
-- Casino HSM test database schema and seed data.
-- Creates players (50K), wallet_events (200K+), game_rounds (500K).
-- Used by test-tde-postgresql.sh for GDPR Art.32 TDE testing.
-- Compatible with PostgreSQL 18.

-- Players table with PII (plaintext — to be encrypted via Transit TDE)
CREATE TABLE IF NOT EXISTS players (
    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    email          TEXT NOT NULL,
    full_name      TEXT NOT NULL,
    date_of_birth  DATE,
    phone          TEXT,
    address        TEXT,
    jurisdiction   TEXT,
    kyc_status     TEXT DEFAULT 'pending',
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Encrypted PII columns (stores Transit ciphertext)
CREATE TABLE IF NOT EXISTS players_encrypted (
    id                   UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    player_ref           UUID,
    email_cipher         TEXT NOT NULL,
    name_cipher          TEXT NOT NULL,
    phone_cipher         TEXT,
    encryption_key_version INTEGER DEFAULT 1,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- Wallet events
CREATE TABLE IF NOT EXISTS wallet_events (
    id          BIGSERIAL PRIMARY KEY,
    player_id   UUID REFERENCES players(id),
    event_type  TEXT NOT NULL,
    amount      DECIMAL(15,2),
    currency    TEXT DEFAULT 'EUR',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Game rounds with RNG seed hash (for GLI-19 audit)
CREATE TABLE IF NOT EXISTS game_rounds (
    id            BIGSERIAL PRIMARY KEY,
    player_id     UUID REFERENCES players(id),
    game_slug     TEXT,
    bet_amount    DECIMAL(15,2),
    win_amount    DECIMAL(15,2),
    rng_seed_hash TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Populate 50K players
INSERT INTO players (email, full_name, date_of_birth, phone, jurisdiction, kyc_status)
SELECT
    'player_' || i || '@acmetocasino.com',
    'Player ' || i || ' Name',
    '1970-01-01'::date + (random() * 15000)::int,
    '+' || (floor(random() * 9000000000) + 1000000000)::bigint,
    (ARRAY['MGA','UKGC','DGE_NJ','AGCO_ON','GGL_DE'])[floor(random()*5+1)],
    (ARRAY['verified','pending','rejected'])[floor(random()*3+1)]
FROM generate_series(1, 50000) AS i;

-- Populate wallet events (~200K rows via TABLESAMPLE)
INSERT INTO wallet_events (player_id, event_type, amount, currency)
SELECT
    id,
    (ARRAY['DEPOSIT','BET','WIN','WITHDRAWAL'])[floor(random()*4+1)],
    round((random() * 1000)::numeric, 2),
    (ARRAY['EUR','GBP','USD','BRL'])[floor(random()*4+1)]
FROM players TABLESAMPLE BERNOULLI(40)
CROSS JOIN generate_series(1, 4);

-- Populate game rounds (500K rows)
INSERT INTO game_rounds (player_id, game_slug, bet_amount, win_amount, rng_seed_hash)
SELECT
    id,
    (ARRAY['book-of-dead','starburst','gonzo-quest','blackjack','roulette','aviator'])[floor(random()*6+1)],
    round((random() * 100)::numeric, 2),
    round((random() * 200)::numeric, 2),
    md5(random()::text)
FROM players TABLESAMPLE BERNOULLI(100)
CROSS JOIN generate_series(1, 10);

-- Verify counts
SELECT 'players'       AS table_name, COUNT(*) AS row_count FROM players
UNION ALL
SELECT 'wallet_events', COUNT(*)                            FROM wallet_events
UNION ALL
SELECT 'game_rounds',   COUNT(*)                            FROM game_rounds;
