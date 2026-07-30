-- init.sql — Schema and test data for coupon_generator.php testing

CREATE TYPE campaign_type_enum AS ENUM ('welcome', 'reload', 'cashback', 'freeplay', 'loyalty');
CREATE TYPE bonus_type_enum AS ENUM ('match', 'fixed', 'freeplay', 'freespin');
CREATE TYPE campaign_status_enum AS ENUM ('active', 'paused', 'expired', 'cancelled');

CREATE TABLE IF NOT EXISTS players (
    player_id BIGSERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS promotion_campaigns (
    campaign_id SERIAL PRIMARY KEY,
    campaign_name VARCHAR(255) NOT NULL,
    campaign_type campaign_type_enum NOT NULL,
    bonus_amount DECIMAL(10,2) NOT NULL,
    bonus_currency CHAR(3) DEFAULT 'EUR',
    bonus_type bonus_type_enum NOT NULL,
    wagering_requirement INT DEFAULT 35,
    min_deposit DECIMAL(10,2) DEFAULT 10.00,
    max_redemptions INT DEFAULT 50000,
    codes_per_batch INT DEFAULT 1000,
    code_prefix VARCHAR(10) DEFAULT '',
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
    valid_until TIMESTAMP WITH TIME ZONE NOT NULL,
    target_brand VARCHAR(100) DEFAULT 'all',
    auto_generate BOOLEAN DEFAULT TRUE,
    status campaign_status_enum DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_status_valid ON promotion_campaigns (status, valid_until);
CREATE INDEX idx_brand ON promotion_campaigns (target_brand);

CREATE TABLE IF NOT EXISTS coupon_codes (
    code_id BIGSERIAL PRIMARY KEY,
    campaign_id INT NOT NULL,
    code VARCHAR(20) NOT NULL UNIQUE,
    bonus_amount DECIMAL(10,2) NOT NULL,
    bonus_currency CHAR(3) DEFAULT 'EUR',
    bonus_type bonus_type_enum NOT NULL,
    wagering_requirement INT DEFAULT 35,
    min_deposit DECIMAL(10,2) DEFAULT 10.00,
    valid_until TIMESTAMP WITH TIME ZONE NOT NULL,
    brand VARCHAR(100) DEFAULT 'all',
    redeemed BOOLEAN DEFAULT FALSE,
    redeemed_by BIGINT NULL,
    redeemed_at TIMESTAMP WITH TIME ZONE NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (campaign_id) REFERENCES promotion_campaigns(campaign_id)
);

CREATE INDEX idx_campaign ON coupon_codes (campaign_id);
CREATE INDEX idx_code ON coupon_codes (code);
CREATE INDEX idx_redeemed ON coupon_codes (redeemed, valid_until);

-- Test data: 3 active campaigns
INSERT INTO promotion_campaigns
    (campaign_name, campaign_type, bonus_amount, bonus_currency, bonus_type,
     wagering_requirement, min_deposit, max_redemptions, codes_per_batch,
     code_prefix, valid_from, valid_until, target_brand, auto_generate, status)
VALUES
    ('Welcome Bonus 100%', 'welcome', 100.00, 'EUR', 'match',
     35, 20.00, 10000, 50, 'WEL',
     NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days',
     'PlayGrand', TRUE, 'active'),

    ('Weekend Reload 50%', 'reload', 50.00, 'EUR', 'match',
     30, 15.00, 5000, 25, 'RLD',
     NOW() - INTERVAL '1 day', NOW() + INTERVAL '7 days',
     'Diamond7', TRUE, 'active'),

    ('Free Spins Friday', 'freeplay', 0.00, 'EUR', 'freespin',
     40, 10.00, 20000, 100, 'FSF',
     NOW() - INTERVAL '1 day', NOW() + INTERVAL '14 days',
     'all', TRUE, 'active');

-- Expired campaign (should NOT be processed)
INSERT INTO promotion_campaigns
    (campaign_name, campaign_type, bonus_amount, bonus_currency, bonus_type,
     wagering_requirement, min_deposit, max_redemptions, codes_per_batch,
     code_prefix, valid_from, valid_until, target_brand, auto_generate, status)
VALUES
    ('Old Promo', 'cashback', 25.00, 'EUR', 'fixed',
     25, 10.00, 1000, 100, 'OLD',
     NOW() - INTERVAL '60 days', NOW() - INTERVAL '1 day',
     'all', TRUE, 'active');

-- Test player
INSERT INTO players (username, email) VALUES ('testplayer1', 'test@example.com');
