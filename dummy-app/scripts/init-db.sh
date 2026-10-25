#!/bin/sh
set -eu
# psql variables quote mock credentials safely; database roles never own each other's tables.
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres \
  -v orders_password="$ORDERS_DB_PASSWORD" \
  -v inventory_password="$INVENTORY_DB_PASSWORD" \
  -v payments_password="$PAYMENTS_DB_PASSWORD" -f /opt/init-db.sql
