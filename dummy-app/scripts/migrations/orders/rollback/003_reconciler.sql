-- Rollback of orders 003_reconciler, ONLY before any checkout attempt exists (abandoned fences count).
-- Apply rollbacks newest first: 003, then 002, then 001.
-- Run in one transaction (psql --single-transaction). Refuses, changing nothing, once data exists:
-- after that, follow "Recovery after new data" in scripts/migrations/README.md instead.
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM checkout_attempts) THEN
    RAISE EXCEPTION 'checkout_attempts has rows: use recovery, not rollback';
  END IF;
END $$;
DROP INDEX checkout_attempts_open;
ALTER TABLE checkout_attempts
  DROP COLUMN next_check_at, DROP COLUMN lease_until, DROP COLUMN check_count, DROP COLUMN trace_parent;
DELETE FROM schema_migrations WHERE version = '003_reconciler';
