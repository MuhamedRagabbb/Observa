-- Additive: multi-item (cart) reservations alongside the legacy single-item `reservations` table.
-- Legacy rows and the shared `reservation_releases` tombstones are untouched.
-- Rollback (only before any cart reservation exists):
--   DROP TABLE cart_reservation_lines; DROP TABLE cart_reservations;
CREATE TABLE cart_reservations (
  reservation_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('reserved', 'released')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE cart_reservation_lines (
  reservation_id TEXT NOT NULL REFERENCES cart_reservations(reservation_id),
  product_id TEXT NOT NULL REFERENCES products(product_id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (reservation_id, product_id)
);
