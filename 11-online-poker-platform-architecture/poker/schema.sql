-- Chapter 4: Online Poker Platform Architecture
-- Database Schema
--
-- Complete PostgreSQL schema for the poker platform including:
--   - players: Player accounts, balances, verification status
--   - game_sessions: Table sessions with game type and rake tracking
--   - hands: Individual hand history with community cards and winners
--   - player_actions: Full action log for each hand (fold/call/raise/etc.)
--   - transactions: Financial transaction ledger for deposits/withdrawals
--
-- Reference: Chapter 4 - Data Management section

-- Players table
CREATE TABLE players (
    id UUID PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    account_balance DECIMAL(15, 2) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    verification_status VARCHAR(20),
    country_code VARCHAR(2)
);

-- Game sessions table
CREATE TABLE game_sessions (
    id UUID PRIMARY KEY,
    table_id UUID NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    game_type VARCHAR(20) NOT NULL,
    stakes VARCHAR(20) NOT NULL,
    rake DECIMAL(10, 2),
    total_pot DECIMAL(15, 2)
);

-- Hands history table
CREATE TABLE hands (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES game_sessions(id),
    hand_number INT NOT NULL,
    dealer_position INT NOT NULL,
    community_cards VARCHAR(20),
    pot_size DECIMAL(15, 2),
    rake DECIMAL(10, 2),
    winners JSONB,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Player actions table
CREATE TABLE player_actions (
    id UUID PRIMARY KEY,
    hand_id UUID REFERENCES hands(id),
    player_id UUID REFERENCES players(id),
    action_type VARCHAR(20) NOT NULL,
    amount DECIMAL(15, 2),
    position INT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action_order INT NOT NULL
);

-- Transactions table
CREATE TABLE transactions (
    id UUID PRIMARY KEY,
    player_id UUID REFERENCES players(id),
    type VARCHAR(20) NOT NULL,
    amount DECIMAL(15, 2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    payment_method VARCHAR(30),
    reference_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);
