-- Additive: background reconciler scheduling (P1-D) and the trace that started each attempt.
-- check_count numbers each reconciler claim and doubles as the claim token for releasing it.
-- Rollback (stop the reconciler first):
--   DROP INDEX checkout_attempts_open;
--   ALTER TABLE checkout_attempts DROP COLUMN next_check_at, DROP COLUMN lease_until,
--     DROP COLUMN check_count, DROP COLUMN trace_parent;
ALTER TABLE checkout_attempts
  ADD COLUMN next_check_at TIMESTAMPTZ,
  ADD COLUMN lease_until TIMESTAMPTZ,
  ADD COLUMN check_count INTEGER NOT NULL DEFAULT 0 CHECK (check_count >= 0),
  ADD COLUMN trace_parent TEXT CHECK (trace_parent ~ '^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$');
CREATE INDEX checkout_attempts_open ON checkout_attempts (updated_at)
  WHERE state NOT IN ('completed', 'payment_declined', 'payment_failed', 'not_placed', 'failed', 'abandoned');
