CREATE ROLE orders_app LOGIN PASSWORD :'orders_password';
CREATE ROLE inventory_app LOGIN PASSWORD :'inventory_password';
CREATE ROLE payments_app LOGIN PASSWORD :'payments_password';
CREATE DATABASE orders_db OWNER orders_app;
CREATE DATABASE inventory_db OWNER inventory_app;
CREATE DATABASE payments_db OWNER payments_app;
REVOKE ALL ON DATABASE orders_db FROM PUBLIC;
REVOKE ALL ON DATABASE inventory_db FROM PUBLIC;
REVOKE ALL ON DATABASE payments_db FROM PUBLIC;
\connect orders_db
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE orders_app;
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
  customer_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','inventory_reserved','completed','failed','reconciliation_required')),
  reservation_id TEXT, payment_id TEXT, error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
RESET ROLE;
\connect inventory_db
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE inventory_app;
CREATE TABLE products (
  product_id TEXT PRIMARY KEY, name TEXT NOT NULL, price_cents INTEGER NOT NULL CHECK(price_cents > 0),
  stock INTEGER NOT NULL CHECK(stock >= 0)
);
CREATE TABLE reservations (
  reservation_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(product_id),
  quantity INTEGER NOT NULL CHECK(quantity > 0), status TEXT NOT NULL CHECK(status IN ('reserved','released'))
);
CREATE TABLE reservation_releases (reservation_id TEXT PRIMARY KEY);
INSERT INTO products VALUES
  ('sku-001','Notebook',1200,1000000), ('sku-002','Mug',1800,1000000),
  ('sku-003','Pen set',600,1000000), ('sku-004','Limited poster',900,0),
  ('sku-005','Sample labels',300,1000000);
RESET ROLE;
\connect payments_db
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE payments_app;
CREATE TABLE payments (
  payment_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
  order_id TEXT NOT NULL, customer_id TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  status TEXT NOT NULL CHECK(status IN ('succeeded','declined')), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
RESET ROLE;
