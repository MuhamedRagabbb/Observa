-- Additive: persistent multi-item checkout attempts. The legacy `orders` table is untouched
-- and keeps serving the single-item flow (including its six stuck legacy rows).
-- Contact details are never stored. Reconciler scheduling and line prices arrive in later
-- additive migrations, with the code that uses them. Rollback (only before any attempt exists):
--   DROP TABLE order_items; DROP TABLE checkout_attempts; DROP SEQUENCE order_number_seq;
CREATE SEQUENCE order_number_seq START 1000;
CREATE TABLE checkout_attempts (
  customer_id TEXT NOT NULL,
  attempt_ref TEXT NOT NULL CHECK (attempt_ref ~ '^ATT-[0-9A-F]{4}-[0-9]{4}$'),
  request_hash TEXT CHECK (request_hash IS NOT NULL OR state = 'abandoned'),
  order_id TEXT UNIQUE,
  order_number TEXT UNIQUE,
  state TEXT NOT NULL CHECK (state IN (
    'received', 'reserving', 'reserved', 'charging', 'completing', 'payment_unknown',
    'releasing', 'releasing_declined', 'releasing_voided',
    'completed', 'payment_declined', 'payment_failed', 'not_placed', 'failed', 'abandoned')),
  version INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, attempt_ref)
);
CREATE TABLE order_items (
  order_id TEXT NOT NULL REFERENCES checkout_attempts(order_id),
  product_id TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 10),
  PRIMARY KEY (order_id, product_id)
);
