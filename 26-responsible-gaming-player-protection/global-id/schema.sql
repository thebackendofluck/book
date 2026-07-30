-- =============================================================================
-- REGULATORY REQUIREMENT: Self-Exclusion Propagation + KYC Deduplication
-- Regulation:  UKGC LCCP SR Code 3.5.1 — self-exclusion must be applied across
--              all brands operated by the same licensee;
--              UKGC RTS 14 — technical standards for self-exclusion systems;
--              MGA Player Protection Directive §8 — self-exclusion must be group-wide;
--              NJ N.J.A.C. 13:69C-11 — self-exclusion program requirements;
--              GDPR Art. 9 (special category data) — health-adjacent RG flags
--              require explicit legal basis (legitimate interest + player protection);
--              Sweden Spellagen §6 kap. 6 § — operators must check Spelpaus for
--              ALL brands in group before onboarding a player;
--              Ontario AGCO Standard 2.14.1 — Centralized Self-Exclusion Program
--              (launching H1 2026; ≤1 hour to add excluded player to registry)
-- Purpose:     Cross-brand identity graph linking all player accounts to a single
--              global identity (GID). Essential for:
--                (1) Self-exclusion propagation — when a player self-excludes on
--                    one brand, the flag must propagate to ALL brands in the group.
--                    Failure to do this is one of the most common UKGC enforcement
--                    cases, resulting in multi-million pound fines.
--                (2) KYC deduplication — prevents duplicate accounts and
--                    multi-accounting fraud.
--                (3) Responsible gaming risk aggregation — a player's RG profile
--                    must be assessed across all brands, not per-brand.
-- Retention:   Self-exclusion records must be retained indefinitely (UKGC RTS 14;
--              MGA PPD §8) — they CANNOT be erased even on GDPR erasure requests.
--              The self-exclusion IS the player's instruction not to be admitted.
-- Penalty:     UKGC: fines have ranged from £500K to £19M for self-exclusion failures
--              across multi-brand operations; MGA: directive violation;
--              NJ DGE: licence conditions require self-exclusion system integrity
-- Last Verified: March 2026
--
-- Applicable jurisdictions (all require group-wide SE propagation):
--   UKGC      — GamStop national SE + group-wide SE (RTS 14) — CRITICAL
--   MGA       — Group-wide SE mandatory (PPD §8)
--   NJ DGE    — N.J.A.C. 13:69C-11 (self-exclusion program)
--   Sweden    — Spelpaus national SE + group propagation (Spellagen §6 kap. 6 §)
--              WARNING 2026: Spelinspektionen proposed stronger Spelpaus ID
--              verification (Actor IDs + API keys); expected August 2026.
--   Netherlands KSA — CRUKS national SE (Wet Koa Art. 4.7); operators must check
--              CRUKS before registration AND on each login session. New licence
--              renewal process from Jan 2026 includes CRUKS integration re-test.
--   Ontario AGCO — CSE standard 2.14.1 (launching H1 2026): registration within
--              1 hour; 6-month, 1-year, 5-year options
--   Brazil    — SIGAP National Register of Prohibited Persons (2026 requirement:
--              check before any wager or deposit; penalty 20% of revenue)
--   PA PGCB   — 58 Pa. Code §436a.10 (self-exclusion program)
--   MI MGCB   — MGCB responsible gaming regulations (SE group propagation)
--
-- References:
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC Remote Technical Standards: https://www.gamblingcommission.gov.uk/standards/remote-technical-standards
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   GDPR Full Text: https://gdpr-info.eu/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   Spelinspektionen: https://www.spelinspektionen.se/en/
--   KSA (Kansspelautoriteit): https://kansspelautoriteit.nl/
--   CRUKS: https://kansspelautoriteit.nl/cruks/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   iGaming Ontario: https://igamingontario.ca/en/operators
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
-- =============================================================================
-- Global ID Schema - Cross-brand identity matching database
-- PostgreSQL schema for linking player accounts across multiple brands
-- under a single operator group.
--
-- Design notes:
-- - The GID table is the identity anchor: one row per unique person
-- - USERS maps (global_id, user_id) -- a person can have multiple user_ids across brands
-- - USER_INFO stores PII used for identity matching (see MatchRules.scala)
-- - USER_FLAGS stores responsible gaming flags at the global level
--   (flags are per-person, not per-account)

CREATE SCHEMA IF NOT EXISTS global_id;

-- Master identity table: one row per unique real-world person
CREATE TABLE global_id.gid (
  id          SERIAL PRIMARY KEY,
  created_on  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Links user accounts to global identities
-- A single person (global_id) may have accounts on multiple brands (brand_id)
CREATE TABLE global_id.users (
  global_id     BIGINT REFERENCES global_id.gid(id),
  user_id       BIGINT UNIQUE NOT NULL,
  brand_id      INT NOT NULL,
  created_on    TIMESTAMP NOT NULL DEFAULT NOW(),
  enabled       BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(global_id, user_id)
);

-- PII used for identity matching across brands
-- Fields here correspond to MatchRules fields (email, name, dob, postcode, etc.)
CREATE TABLE global_id.user_info (
  global_id     BIGINT REFERENCES global_id.gid(id),
  user_id       BIGINT REFERENCES global_id.users(user_id),
  email         VARCHAR(255),
  first_name    VARCHAR(255) NOT NULL,
  last_name     VARCHAR(255) NOT NULL,
  postcode      VARCHAR(20) NOT NULL,
  dob           DATE NOT NULL,
  ip            VARCHAR(45),
  phone         VARCHAR(20),
  cookie_value  VARCHAR(255),
  country       VARCHAR(3),
  ssn           VARCHAR(20),
  PRIMARY KEY(global_id, user_id)
);

-- Responsible gaming and compliance flags at the global (person) level
-- When a flag is set here, PropagationRules determine which linked accounts
-- receive the flag via Kafka messages
CREATE TABLE global_id.user_flags (
  global_id     BIGINT REFERENCES global_id.gid(id),
  flag_type     VARCHAR(50) NOT NULL,
  flag_value    BOOLEAN NOT NULL,
  set_on        TIMESTAMP NOT NULL DEFAULT NOW(),
  original_user_id BIGINT,           -- which user triggered this flag
  last_audit_id    BIGINT,           -- link to audit trail
  PRIMARY KEY(global_id, flag_type)
);

-- Audit trail for flag changes (immutable append-only)
CREATE TABLE global_id.user_flags_audit (
  id            BIGSERIAL PRIMARY KEY,
  global_id     BIGINT NOT NULL,
  flag_type     VARCHAR(50) NOT NULL,
  old_value     BOOLEAN,
  new_value     BOOLEAN NOT NULL,
  date_changed  TIMESTAMP NOT NULL DEFAULT NOW(),
  old_user_id   BIGINT,
  new_user_id   BIGINT NOT NULL,
  comment       TEXT
);

-- Track last flag update per user (for time-based propagation rules)
CREATE TABLE global_id.user_flag_lastupdate (
  user_id       BIGINT NOT NULL,
  flag_type     VARCHAR(50) NOT NULL,
  last_update   TIMESTAMP NOT NULL,
  last_update_user BIGINT NOT NULL,
  PRIMARY KEY(user_id, flag_type)
);

-- Audit trail for user-to-GID reassignments
CREATE TABLE global_id.users_audit (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL,
  old_global_id BIGINT NOT NULL,
  change_date   TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Track cases where a user matches multiple existing GIDs
-- (requires manual review to determine which GID is correct)
CREATE TABLE global_id.multiple_matches_audit (
  user_id       BIGINT NOT NULL,
  first_match   BIGINT NOT NULL,
  second_match  BIGINT NOT NULL,
  created_on    TIMESTAMP NOT NULL DEFAULT NOW(),
  merged        BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(user_id, first_match, second_match)
);

-- Partial matches: users who share some but not all identity fields
-- Used by compliance team to review potential identity links
CREATE TABLE global_id.partial_matches (
  first_user_id     BIGINT NOT NULL,
  second_user_id    BIGINT NOT NULL,
  email_match       BOOLEAN NOT NULL DEFAULT FALSE,
  phone_number_match BOOLEAN NOT NULL DEFAULT FALSE,
  postcode_match    BOOLEAN NOT NULL DEFAULT FALSE,
  last_name_match   BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(first_user_id, second_user_id)
);

-- Performance indexes for identity matching queries
CREATE INDEX idx_user_info_email ON global_id.user_info (lower(btrim(email)));
CREATE INDEX idx_user_info_ssn ON global_id.user_info (btrim(ssn)) WHERE ssn IS NOT NULL;
CREATE INDEX idx_user_info_name_dob ON global_id.user_info (
  left(lower(regexp_replace(first_name, '\s|\W', '', 'g')), 1),
  lower(regexp_replace(last_name, '\s|\W', '', 'g')),
  dob,
  lower(replace(postcode, ' ', ''))
);
CREATE INDEX idx_user_info_phone_dob ON global_id.user_info (
  right(replace(phone, ' ', ''), 9),
  dob,
  country
) WHERE phone IS NOT NULL;
CREATE INDEX idx_users_global_id ON global_id.users (global_id);
CREATE INDEX idx_user_flags_global_id ON global_id.user_flags (global_id);
