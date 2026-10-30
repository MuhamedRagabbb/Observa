-- Rollback of orders 001_checkout_attempts, ONLY before any checkout attempt exists (abandoned fences count).
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
DROP TABLE order_items;
DROP TABLE checkout_attempts;
DROP SEQUENCE order_number_seq;
DELETE FROM schema_migrations WHERE version = '001_checkout_attempts';
