-- Bulk re-encryption: pgcrypto AES-256-CBC  ->  pg_aegis AEGIS-128L.
--
-- Assumptions:
--   * source column is BYTEA produced by pgp_sym_encrypt(...)
--   * table has a PK column named `id` (used as AAD to bind ciphertext
--     to its row, preventing ciphertext swap attacks)
--   * target key already exists (aegis_generate_key(...))
--   * column is rewritten in place
--
-- Usage:
--   SELECT migrate_pgcrypto_to_aegis('players', 'email_enc', 'old-pgp-passphrase', 'player_pii_key');

CREATE OR REPLACE FUNCTION migrate_pgcrypto_to_aegis(
    p_table       TEXT,
    p_column      TEXT,
    p_pgp_pass    TEXT,
    p_aegis_key   TEXT,
    p_batch_size  INT DEFAULT 5000
) RETURNS BIGINT AS $$
DECLARE
    v_total    BIGINT := 0;
    v_count    BIGINT;
    v_sql      TEXT;
    v_aad_expr TEXT;
BEGIN
    -- Safety: ensure pgcrypto is available.
    PERFORM 1 FROM pg_extension WHERE extname = 'pgcrypto';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'pgcrypto extension is required for migration';
    END IF;

    -- Bind AAD to "table:column:id" so a ciphertext can't be moved between rows.
    v_aad_expr := format('%L || '':'' || %L || '':'' || t.id::text', p_table, p_column);

    LOOP
        v_sql := format($f$
            WITH batch AS (
                SELECT id
                FROM %I
                WHERE %I IS NOT NULL
                  AND get_byte(%I, 0) != 1  -- skip rows already in pg_aegis wire format
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE %I t
            SET %I = aegis_encrypt_aad(
                         pgp_sym_decrypt(t.%I, %L),
                         %L,
                         %s
                     )
            FROM batch
            WHERE t.id = batch.id
        $f$, p_table, p_column, p_column, p_batch_size,
             p_table, p_column, p_column, p_pgp_pass,
             p_aegis_key, v_aad_expr);

        EXECUTE v_sql;
        GET DIAGNOSTICS v_count = ROW_COUNT;
        v_total := v_total + v_count;
        EXIT WHEN v_count = 0;
        RAISE NOTICE 'pg_aegis migration: % rows done (total %)', v_count, v_total;
        COMMIT;  -- only valid in a PROCEDURE; see procedure variant below.
    END LOOP;

    RETURN v_total;
END;
$$ LANGUAGE plpgsql;

-- Procedure variant that commits per batch (recommended for large tables).
CREATE OR REPLACE PROCEDURE migrate_pgcrypto_to_aegis_proc(
    p_table       TEXT,
    p_column      TEXT,
    p_pgp_pass    TEXT,
    p_aegis_key   TEXT,
    p_batch_size  INT DEFAULT 5000
) AS $$
DECLARE
    v_total    BIGINT := 0;
    v_count    BIGINT;
    v_sql      TEXT;
    v_aad_expr TEXT;
BEGIN
    v_aad_expr := format('%L || '':'' || %L || '':'' || t.id::text', p_table, p_column);
    LOOP
        v_sql := format($f$
            WITH batch AS (
                SELECT id
                FROM %I
                WHERE %I IS NOT NULL
                  AND get_byte(%I, 0) != 1
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE %I t
            SET %I = aegis_encrypt_aad(
                         pgp_sym_decrypt(t.%I, %L),
                         %L,
                         %s
                     )
            FROM batch
            WHERE t.id = batch.id
        $f$, p_table, p_column, p_column, p_batch_size,
             p_table, p_column, p_column, p_pgp_pass,
             p_aegis_key, v_aad_expr);

        EXECUTE v_sql;
        GET DIAGNOSTICS v_count = ROW_COUNT;
        v_total := v_total + v_count;
        EXIT WHEN v_count = 0;
        RAISE NOTICE 'pg_aegis migration: % rows done (total %)', v_count, v_total;
        COMMIT;
    END LOOP;
    RAISE NOTICE 'pg_aegis migration complete: % rows migrated', v_total;
END;
$$ LANGUAGE plpgsql;
