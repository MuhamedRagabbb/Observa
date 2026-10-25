# Schema migrations

Additive, versioned SQL per service database (`<service>/NNN_description.sql`). They are applied by `services/shared/migrations.py`:
- in order, each in its own transaction;
- under an advisory lock;
- checksummed, so editing an applied file is refused.

They are **not** applied automatically at service startup.

| Service | Version | Change | Rollback |
|---|---|---|---|
| inventory | `001_cart_reservations` | New `cart_reservations` + `cart_reservation_lines` tables; legacy tables and tombstones untouched | Drop both new tables, only while no cart reservation exists |
| orders | `001_checkout_attempts` | New `checkout_attempts`, `order_items` and `order_number_seq`; legacy `orders` untouched | Drop the two tables and the sequence, only while no attempt exists |
| payments | `001_payment_void_fence` | Allows `voided` tombstones; customer/amount nullable **only** for voided rows | Restore the original status CHECK and NOT NULLs, only while no voided row exists |

The payments migration replaces the auto-named `payments_status_check` constraint, which is the name on the live database. If that name ever differs, the migration fails and rolls back rather than leaving the fence half-installed.

## Verify on an isolated database (no live data touched)

```powershell
$env:RUN_DB_TESTS='1'; .venv\Scripts\python.exe -m pytest tests/isolated_db
```

## Apply to the local Compose database (only after review and approval)

1. Take a backup and keep it. Never overwrite an existing dump:
   ```powershell
   docker compose exec -T postgres pg_dump -U postgres -Fc inventory_db > ..\..\backups\inventory_db-<timestamp>.dump
   ```
2. Apply as the owning service role, inside the inventory container:
   ```powershell
   docker compose exec -T inventory sh -c 'python -m services.shared.migrations --dsn "$DATABASE_URL" --dir scripts/migrations/inventory'
   ```
3. Check: `SELECT version FROM schema_migrations;` returns `001_cart_reservations`.
