"""Postgres-only DDL for fast "closest offers to this CV" search (pgvector).

`job_offers.embedding` stays the source of truth (JSON, works everywhere,
incl. the SQLite test database). On Postgres with the `vector` extension, a
trigger mirrors it into `embedding_vec`, so every existing write path keeps
working untouched and the matching module can ask the database for the
nearest offers instead of loading every offer into memory.

Used by the Alembic migration and by the Postgres-only tests.
"""

from __future__ import annotations

EXTENSION_AVAILABLE_SQL = "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"

UPGRADE_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "ALTER TABLE job_offers ADD COLUMN IF NOT EXISTS embedding_vec vector",
    """
    CREATE OR REPLACE FUNCTION job_offers_sync_embedding_vec() RETURNS trigger AS $$
    BEGIN
      -- Never let a bad embedding block an offer from being saved: anything
      -- unusable simply leaves the mirror column empty.
      BEGIN
        IF NEW.embedding IS NOT NULL
           AND jsonb_typeof(NEW.embedding) = 'array'
           AND jsonb_array_length(NEW.embedding) > 0 THEN
          NEW.embedding_vec := NEW.embedding::text::vector;
        ELSE
          NEW.embedding_vec := NULL;
        END IF;
      EXCEPTION WHEN others THEN
        NEW.embedding_vec := NULL;
      END;
      RETURN NEW;
    END
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_job_offers_embedding_vec ON job_offers",
    """
    CREATE TRIGGER trg_job_offers_embedding_vec
    BEFORE INSERT OR UPDATE OF embedding ON job_offers
    FOR EACH ROW EXECUTE FUNCTION job_offers_sync_embedding_vec()
    """,
    # Backfill the offers that already have an embedding (re-assigning the
    # column fires the trigger above).
    "UPDATE job_offers SET embedding = embedding WHERE embedding IS NOT NULL",
]

DOWNGRADE_STATEMENTS = [
    "DROP TRIGGER IF EXISTS trg_job_offers_embedding_vec ON job_offers",
    "DROP FUNCTION IF EXISTS job_offers_sync_embedding_vec()",
    "ALTER TABLE job_offers DROP COLUMN IF EXISTS embedding_vec",
]
