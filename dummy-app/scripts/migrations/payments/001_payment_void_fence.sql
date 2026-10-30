-- Relaxing, non-destructive: allow 'voided' tombstones that permanently fence an idempotency
-- key against a late charge. Real charges must still carry customer and amount.
-- Rollback (only while no voided rows exist): restore the original status CHECK and NOT NULLs.
ALTER TABLE payments DROP CONSTRAINT payments_status_check;
ALTER TABLE payments ADD CONSTRAINT payments_status_check
  CHECK (status IN ('succeeded', 'declined', 'voided'));
ALTER TABLE payments ALTER COLUMN customer_id DROP NOT NULL, ALTER COLUMN amount_cents DROP NOT NULL;
ALTER TABLE payments ADD CONSTRAINT payments_charge_fields
  CHECK (status = 'voided' OR (customer_id IS NOT NULL AND amount_cents IS NOT NULL));
