-- pg_aegis 0.1.0 — schema and bootstrap SQL.
-- pgrx appends generated function definitions from src/lib.rs after this.

-- Key storage (wrapped by master key or YubiHSM).
CREATE TABLE IF NOT EXISTS pg_aegis_keys (
    key_name       TEXT PRIMARY KEY,
    key_version    INT NOT NULL DEFAULT 1,
    encrypted_key  BYTEA NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rotated_at     TIMESTAMPTZ
);

-- Optional audit log of decrypt operations. Application code can
-- INSERT to this on every aegis_decrypt() call via a wrapper function.
CREATE TABLE IF NOT EXISTS pg_aegis_audit (
    id          BIGSERIAL PRIMARY KEY,
    key_name    TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT current_user,
    client_addr INET,
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    aad         TEXT
);

CREATE INDEX IF NOT EXISTS pg_aegis_audit_at_idx ON pg_aegis_audit (at DESC);
CREATE INDEX IF NOT EXISTS pg_aegis_audit_key_idx ON pg_aegis_audit (key_name, at DESC);
