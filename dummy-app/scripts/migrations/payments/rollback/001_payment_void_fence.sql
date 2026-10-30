-- Rollback of payments 001_payment_void_fence, ONLY before any voided row exists.
-- A voided row is a fence against a late charge; deleting one could let a charge through.
-- Run in one transaction (psql --single-transaction). Refuses, changing nothing, once data exists:
-- after that, follow "Recovery after new data" in scripts/migrations/README.md instead.
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM payments WHERE status = 'voided') THEN
    RAISE EXCEPTION 'payments has voided rows: use recovery, not rollback';
  END IF;
END $$;
ALTER TABLE payments DROP CONSTRAINT payments_charge_fields;
ALTER TABLE payments DROP CONSTRAINT payments_status_check;
ALTER TABLE payments ADD CONSTRAINT payments_status_check CHECK (status IN ('succeeded', 'declined'));
ALTER TABLE payments ALTER COLUMN customer_id SET NOT NULL, ALTER COLUMN amount_cents SET NOT NULL;
DELETE FROM schema_migrations WHERE version = '001_payment_void_fence';
