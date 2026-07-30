-- V2__setup.sql
-- VIP Rule Processor: seed 12 VIP tier definitions
-- Chapter 37: Marketing Technology and CRM -- VIP rule processor
--
-- Tier model: 2D boundary (30-day deposits × 30-day bet volume)
-- 12 tiers with qualifiers:
--   STANDARD (3 tiers): deposits AND bets meet thresholds
--   ND (No Deposit, 3 tiers): high bets, low deposits
--   NW (No Wager, 3 tiers): high deposits, low wagering
--   POT (Potential, 3 tiers): approaching Standard thresholds
--
-- Amounts in EUR cents (e.g., 50000 = EUR 500.00)

INSERT INTO rules (
    name, level, qualifier,
    min_deposit_30d, max_deposit_30d,
    min_bet_30d, max_bet_30d,
    bet_weight, base_score,
    benefits
) VALUES

-- ----------------------------------------------------------------
-- STANDARD tiers: balanced deposit + wager activity
-- ----------------------------------------------------------------
(
    'Standard 1', 1, 'STANDARD',
    50000,          -- EUR 500 min deposits
    499999,         -- EUR 4,999.99 max deposits
    100000,         -- EUR 1,000 min bets
    NULL,           -- no bet cap
    1.00, 1000,
    '{"vip_manager": false, "monthly_bonus_eur": 0, "withdrawal_priority": "standard", "cashback_pct": 0}'
),
(
    'Standard 2', 4, 'STANDARD',
    500000,         -- EUR 5,000
    1999999,        -- EUR 19,999.99
    500000,         -- EUR 5,000
    NULL,
    1.00, 4000,
    '{"vip_manager": false, "monthly_bonus_eur": 50, "withdrawal_priority": "elevated", "cashback_pct": 5}'
),
(
    'Standard 3', 7, 'STANDARD',
    2000000,        -- EUR 20,000
    NULL,           -- no upper bound
    2000000,        -- EUR 20,000
    NULL,
    1.00, 7000,
    '{"vip_manager": true, "monthly_bonus_eur": 200, "withdrawal_priority": "vip", "cashback_pct": 10}'
),

-- ----------------------------------------------------------------
-- ND (No Deposit) tiers: high bet volume, low deposit volume
-- Players who wager heavily from bonus or transferred funds
-- ----------------------------------------------------------------
(
    'ND 1', 2, 'ND',
    0,              -- any deposit amount
    49999,          -- EUR 499.99 deposits (below Standard threshold)
    200000,         -- EUR 2,000 min bets
    999999,         -- EUR 9,999.99 max bets
    1.30, 2000,     -- higher bet weight for no-deposit activity
    '{"vip_manager": false, "monthly_bonus_eur": 25, "withdrawal_priority": "standard", "cashback_pct": 3}'
),
(
    'ND 2', 5, 'ND',
    0,
    49999,
    1000000,        -- EUR 10,000 min bets
    4999999,        -- EUR 49,999.99 max bets
    1.30, 5000,
    '{"vip_manager": false, "monthly_bonus_eur": 75, "withdrawal_priority": "elevated", "cashback_pct": 6}'
),
(
    'ND 3', 9, 'ND',
    0,
    49999,
    5000000,        -- EUR 50,000+
    NULL,
    1.50, 9000,
    '{"vip_manager": true, "monthly_bonus_eur": 300, "withdrawal_priority": "vip", "cashback_pct": 12}'
),

-- ----------------------------------------------------------------
-- NW (No Wager) tiers: high deposits, low wagering (withdrawal risk)
-- ----------------------------------------------------------------
(
    'NW 1', 3, 'NW',
    500000,         -- EUR 5,000 min deposits
    1999999,        -- EUR 19,999.99 max deposits
    0,              -- any bet amount
    99999,          -- EUR 999.99 max bets (below Standard threshold)
    0.50, 3000,     -- lower bet weight — deposit focus
    '{"vip_manager": false, "monthly_bonus_eur": 0, "withdrawal_priority": "elevated", "cashback_pct": 2, "withdrawal_limit_override_eur": 10000}'
),
(
    'NW 2', 6, 'NW',
    2000000,        -- EUR 20,000
    9999999,        -- EUR 99,999.99
    0,
    999999,         -- EUR 9,999.99 max bets
    0.50, 6000,
    '{"vip_manager": true, "monthly_bonus_eur": 0, "withdrawal_priority": "vip", "cashback_pct": 0, "withdrawal_limit_override_eur": 50000}'
),
(
    'NW 3', 10, 'NW',
    10000000,       -- EUR 100,000+
    NULL,
    0,
    9999999,        -- EUR 99,999.99 max bets
    0.50, 10000,
    '{"vip_manager": true, "monthly_bonus_eur": 0, "withdrawal_priority": "instant", "cashback_pct": 0, "withdrawal_limit_override_eur": 200000, "dedicated_account_manager": true}'
),

-- ----------------------------------------------------------------
-- POT (Potential) tiers: approaching Standard thresholds
-- Players to nurture toward full VIP — proactive outreach
-- ----------------------------------------------------------------
(
    'POT 1', 8, 'POT',
    200000,         -- EUR 2,000 (approaching Standard 2 at EUR 5,000)
    499999,
    200000,         -- EUR 2,000 bets
    499999,
    1.00, 8000,
    '{"vip_manager": false, "monthly_bonus_eur": 30, "withdrawal_priority": "standard", "cashback_pct": 3, "pot_outreach": true}'
),
(
    'POT 2', 11, 'POT',
    1000000,        -- EUR 10,000 (approaching Standard 3 at EUR 20,000)
    1999999,
    1000000,
    1999999,
    1.00, 11000,
    '{"vip_manager": false, "monthly_bonus_eur": 100, "withdrawal_priority": "elevated", "cashback_pct": 7, "pot_outreach": true}'
),
(
    'POT 3', 12, 'POT',
    5000000,        -- EUR 50,000 (approaching NW 3 at EUR 100,000)
    9999999,
    0,
    4999999,
    0.75, 12000,
    '{"vip_manager": true, "monthly_bonus_eur": 150, "withdrawal_priority": "vip", "cashback_pct": 5, "pot_outreach": true, "whale_watch": true}'
);

-- ----------------------------------------------------------------
-- Insert default scheduler entry
-- ----------------------------------------------------------------
INSERT INTO scheduler (job_name, status, next_run_at)
VALUES ('vip_batch_recalculation', 'PENDING', NOW() + INTERVAL '1 hour');
