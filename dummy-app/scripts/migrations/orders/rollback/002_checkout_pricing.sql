-- Rollback of orders 002_checkout_pricing, ONLY before any checkout attempt exists (abandoned fences count).
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
ALTER TABLE order_items DROP COLUMN unit_price_cents;
ALTER TABLE checkout_attempts DROP COLUMN total_cents, DROP COLUMN failure_details;
DELETE FROM schema_migrations WHERE version = '002_checkout_pricing';
