-- Rollback of inventory 001_cart_reservations, ONLY before any cart reservation exists.
-- Run in one transaction (psql --single-transaction). Refuses, changing nothing, once data exists:
-- after that, follow "Recovery after new data" in scripts/migrations/README.md instead.
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cart_reservations) THEN
    RAISE EXCEPTION 'cart_reservations has rows: use recovery, not rollback';
  END IF;
END $$;
DROP TABLE cart_reservation_lines;
DROP TABLE cart_reservations;
DELETE FROM schema_migrations WHERE version = '001_cart_reservations';
