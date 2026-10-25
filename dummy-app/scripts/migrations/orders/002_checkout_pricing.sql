-- Additive: server-computed prices and totals, plus structured failure details
-- (for example unavailable items) so an idempotent replay returns the same outcome.
-- Rollback (only before any attempt is priced):
--   ALTER TABLE order_items DROP COLUMN unit_price_cents;
--   ALTER TABLE checkout_attempts DROP COLUMN total_cents, DROP COLUMN failure_details;
ALTER TABLE order_items ADD COLUMN unit_price_cents INTEGER CHECK (unit_price_cents > 0);
ALTER TABLE checkout_attempts
  ADD COLUMN total_cents INTEGER CHECK (total_cents > 0),
  ADD COLUMN failure_details JSONB;
