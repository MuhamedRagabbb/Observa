# Schema migrations runbook

Additive, versioned SQL per service database (`<service>/NNN_description.sql`), applied by `services/shared/migrations.py`:
- in order, each file in its own transaction (a failed file rolls back and is not recorded);
- under an advisory lock, so two runners cannot race;
- checksummed in `schema_migrations`, so editing an applied file is refused.

They are **not** applied at service startup. Applying them to the live Compose databases needs explicit approval.

## Hard rules

- **Never delete or overwrite a backup.** Each run writes to a new timestamped folder.
- **Never run `docker compose down -v` or `scripts/reset.*` against the live project (`observa-dummy`).** Both delete the database volumes.
- **Leave legacy `orders` rows alone**, including the stuck `pending`, `inventory_reserved` and `reconciliation_required` ones. No migration or rollback here touches them.
- **Fault scenarios (F1/F2/F3) never run against the live databases.** They saturate the pool and fail payments, which strands legacy orders. Run them only on a throwaway project (see [Fault testing on a throwaway project](#fault-testing-on-a-throwaway-project)). The test suite refuses them unless `RUN_FAULT_TESTS=1` and `COMPOSE_PROJECT_NAME` names a project other than `observa-dummy`.
- **The reconciler stays off** (`RECONCILER_ENABLED=false`, the Compose default) until all migrations are in and a separate approval is given.

## Migrations and their limits

Apply in this order. Orders goes last: once its tables exist the new checkout works, and it needs cart reservations and the payment void fence underneath it.

| # | Service | Version | Change | Live impact | Code compatibility |
|---|---|---|---|---|---|
| 1 | inventory | `001_cart_reservations` | New `cart_reservations` and `cart_reservation_lines` (foreign key to `products`) | Brief lock on `products` (5 rows) | Inventory checks for the table on every call, so it works before and after |
| 2 | payments | `001_payment_void_fence` | Allows `voided` tombstones; customer and amount are nullable **only** for voided rows | **Exclusive lock while the new CHECK scans the whole `payments` table** (about 115k rows; well under a second). Charges wait meanwhile. | Voids return 503 until applied; charges are unaffected |
| 3 | orders | `001_checkout_attempts` | New `checkout_attempts`, `order_items` and `order_number_seq` | New objects only | Legacy flow unaffected |
| 4 | orders | `002_checkout_pricing` | Nullable price, total and failure-detail columns | Catalogue-only change on new tables | Checkout needs it |
| 5 | orders | `003_reconciler` | Scheduling columns (`check_count` default 0, no table rewrite), `trace_parent`, partial index | New table is empty at this point | Checkout (`begin` writes `trace_parent`) and the reconciler need it |

The payments migration replaces the constraint named `payments_status_check` (the name on the live database). If the name ever differs, the migration fails and rolls back rather than leaving the fence half-installed.

**Rollback window.** Each migration can be undone cheaply only while its new objects hold no data:

| Service | Rollback is possible while… | Data that closes the window |
|---|---|---|
| inventory | `cart_reservations` is empty | any cart reservation, even a released one |
| payments | no row has `status = 'voided'` | any void tombstone: it fences a late charge, and deleting it could let that charge through |
| orders | `checkout_attempts` is empty | any checkout attempt, including an `abandoned` fence written by `/resolve` |

Legacy traffic, the traffic generator and the non-fault live tests (`tests/integration/test_live.py`) write none of these, so the window stays open until something uses the new checkout (`POST /api/checkout` or `/resolve`).

## Rehearse first (no live data)

1. Isolated PostgreSQL (throwaway container, never the Compose database). This covers applying, rolling back and re-applying, lock timeouts and the checkout/reconciler scenarios:
   ```powershell
   $env:RUN_DB_TESTS='1'; .venv\Scripts\python.exe -m pytest -q tests/isolated_db
   ```
2. Optional full dress rehearsal on a throwaway Compose project, including the fault suite. See [Fault testing on a throwaway project](#fault-testing-on-a-throwaway-project).

## Live procedure (only after approval)

Run from `dummy-app/` in PowerShell. Stop at the first unexpected result.

### 1. Pre-checks

```powershell
git status -sb                      # clean, on the reviewed commit
docker compose ps                   # all services healthy
curl.exe -s http://127.0.0.1:8090/faults/status   # must be {"active":null,"auto_running":false}
docker compose exec -T orders ls scripts/migrations/orders   # the image contains 001-003
```

The Orders image must be built from the reviewed commit (`docker compose up --build -d --wait` beforehand if not).

Confirm nothing is applied yet. Each command must print an empty line:

```powershell
foreach ($db in 'orders_db','inventory_db','payments_db') {
  docker compose exec -T postgres psql -U postgres -d $db -Atc "SELECT to_regclass('schema_migrations')"
}
docker compose exec -T postgres psql -U postgres -d payments_db -Atc "SELECT conname FROM pg_constraint WHERE conname IN ('payments_status_check','payments_charge_fields')"
# expected: payments_status_check only
```

### 2. Pause traffic

```powershell
docker compose stop traffic-generator
Start-Sleep 15   # let in-flight orders finish; a stop mid-request is what strands legacy rows
```

### 3. Snapshot counts and stuck orders

```powershell
$ts  = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$dir = "D:\Observa-db-backups-$ts"
New-Item -ItemType Directory $dir | Out-Null    # fails if it already exists: never reuse a folder

$counts = @{
  orders_db    = "SELECT 'orders', count(*) FROM orders"
  inventory_db = "SELECT 'products', count(*), sum(stock) FROM products UNION ALL SELECT 'reservations', count(*), NULL FROM reservations UNION ALL SELECT 'reservation_releases', count(*), NULL FROM reservation_releases"
  payments_db  = "SELECT status, count(*) FROM payments GROUP BY status ORDER BY status"
}
foreach ($db in $counts.Keys) {
  docker compose exec -T postgres psql -U postgres -d $db -At -c $counts[$db] | Out-File "$dir\counts-$db-before.txt" -Encoding utf8
}
docker compose exec -T postgres psql -U postgres -d orders_db -At -c "SELECT order_id, status FROM orders WHERE status IN ('pending','inventory_reserved','reconciliation_required') ORDER BY order_id" | Out-File "$dir\stuck-orders-before.txt" -Encoding utf8
```

### 4. Binary-safe backups

Dump **inside** the container, then copy the file out. Never redirect `pg_dump` output through PowerShell (`> file`): Windows PowerShell 5.1 re-encodes native output as text and corrupts a custom-format dump.

```powershell
foreach ($db in 'orders_db','inventory_db','payments_db') {
  docker compose exec -T postgres pg_dump -U postgres -Fc -f "/tmp/$db-$ts.dump" $db
  if ($LASTEXITCODE -ne 0) { throw "pg_dump failed for $db" }
  docker compose exec -T postgres pg_restore --list "/tmp/$db-$ts.dump" | Out-Null   # readable archive
  if ($LASTEXITCODE -ne 0) { throw "unreadable dump for $db" }
  docker compose cp "postgres:/tmp/$db-$ts.dump" "$dir\$db-$ts.dump"
  if ($LASTEXITCODE -ne 0) { throw "copy failed for $db" }
}
Get-FileHash "$dir\*.dump" -Algorithm SHA256 | Select-Object Hash, Path | Export-Csv "$dir\SHA256SUMS.csv" -NoTypeInformation
```

The `/tmp` copies inside the container are working copies, not backups. The backups are the files in `$dir`.

### 5. Verify each backup restores (isolated, never the live database)

The restore runs in a separate throwaway container: tmpfs storage, no published ports, not on the Compose networks, removed when stopped.

```powershell
docker run -d --rm --name observa-restore-check --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=restore-check postgres:16.6-bookworm
if ($LASTEXITCODE -ne 0) { throw "restore container did not start (a leftover one? docker stop observa-restore-check)" }
try {
  # The entrypoint restarts the server after initialising: wait for the second "ready" line.
  do { Start-Sleep 1 } until (((cmd /c "docker logs observa-restore-check 2>&1") | Select-String 'ready to accept connections').Count -ge 2)
  foreach ($db in 'orders_db','inventory_db','payments_db') {
    docker cp "$dir\$db-$ts.dump" "observa-restore-check:/tmp/$db.dump"
    if ($LASTEXITCODE -ne 0) { throw "copy into restore container failed for $db" }
    docker exec observa-restore-check createdb -U postgres $db
    if ($LASTEXITCODE -ne 0) { throw "createdb failed for $db" }
    docker exec observa-restore-check pg_restore -U postgres --no-owner --no-privileges --exit-on-error -d $db "/tmp/$db.dump"
    if ($LASTEXITCODE -ne 0) { throw "restore failed for $db" }
    docker exec observa-restore-check psql -U postgres -d $db -At -c $counts[$db] | Out-File "$dir\counts-$db-restored.txt" -Encoding utf8
    if (Compare-Object (Get-Content "$dir\counts-$db-before.txt") (Get-Content "$dir\counts-$db-restored.txt")) { throw "restored counts differ for $db" }
  }
} finally {
  docker stop observa-restore-check | Out-Null   # --rm removes it and its tmpfs data, also after a failure
}
```

Go on only if every database restored with identical counts.

### 6. Apply, as each service's own role, with bounded lock waits

`?lock_timeout=5s` on the DSN is passed to PostgreSQL as a session setting. If a step cannot get its lock within 5 s (for example behind a long transaction), it fails, rolls back that file and records nothing, instead of queueing live requests behind it.

The command deliberately has no inner double quotes, because Windows PowerShell 5.1 mangles them in native arguments. `set -f` stops `sh` treating the `?` as a wildcard, and the DSN contains no spaces.

```powershell
foreach ($svc in 'inventory','payments','orders') {   # this order
  docker compose exec -T $svc sh -c "set -f; python -m services.shared.migrations --dsn `${DATABASE_URL}?lock_timeout=5s --dir scripts/migrations/$svc"
  if ($LASTEXITCODE -ne 0) { throw "migration failed for ${svc}: stop here (that file was rolled back)" }
}
# expected: applied 001_cart_reservations / 001_payment_void_fence / 001_checkout_attempts, 002_checkout_pricing, 003_reconciler
```

If one fails with `LockNotAvailableError`, find the blocker and retry later. Don't terminate sessions without approval:

```powershell
docker compose exec -T postgres psql -U postgres -At -c "SELECT pid, datname, state, now()-xact_start AS open_for, left(query, 60) FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY xact_start"
```

### 7. Post-checks (before any new checkout data exists)

```powershell
foreach ($db in 'orders_db','inventory_db','payments_db') {
  docker compose exec -T postgres psql -U postgres -d $db -At -c "SELECT version FROM schema_migrations ORDER BY version"
  docker compose exec -T postgres psql -U postgres -d $db -At -c $counts[$db] | Out-File "$dir\counts-$db-after.txt" -Encoding utf8
  if (Compare-Object (Get-Content "$dir\counts-$db-before.txt") (Get-Content "$dir\counts-$db-after.txt")) { throw "counts changed in $db" }
}
docker compose exec -T postgres psql -U postgres -d orders_db -At -c "SELECT order_id, status FROM orders WHERE status IN ('pending','inventory_reserved','reconciliation_required') ORDER BY order_id" | Out-File "$dir\stuck-orders-after.txt" -Encoding utf8
if (Compare-Object (Get-Content "$dir\stuck-orders-before.txt") (Get-Content "$dir\stuck-orders-after.txt")) { throw "legacy stuck orders changed" }
docker compose exec -T postgres psql -U postgres -d orders_db -At -c "SELECT count(*) FROM checkout_attempts"   # 0
docker compose exec -T postgres psql -U postgres -d inventory_db -At -c "SELECT count(*) FROM cart_reservations"  # 0
docker compose exec -T postgres psql -U postgres -d payments_db -At -c "SELECT count(*) FROM payments WHERE status='voided'"  # 0
```

### 8. Resume and watch

```powershell
docker compose start traffic-generator
$env:RUN_LIVE_TESTS='1'; $env:POSTGRES_PORT='5433'   # POSTGRES_PORT from .env (5433 on this machine)
.venv\Scripts\python.exe -m pytest -q tests/integration/test_live.py   # non-fault live checks only
```

Watch Grafana/Prometheus for 15 minutes against the pre-migration baseline: request error rate, `db.pool.timeouts` and p95 latency. **Roll back** (below) on a migration error, a count or stuck-order difference, a failing legacy live test, or an error rate clearly above the baseline.

**The first new-checkout write ends the rollback window.** That includes a manual smoke `POST /api/checkout`, any `/resolve`, or enabling the reconciler. Do it only once you are satisfied with the steps above.

## Undo

### A. Rollback before new checkout data exists

Use the guarded scripts in `<service>/rollback/` (the runner never reads that folder). Each one sets `lock_timeout`, **refuses and changes nothing if its window has closed**, drops only what its migration added, and deletes its `schema_migrations` row so the migration can be re-applied later. Isolated tests prove that each script restores the original schema exactly.

Apply in reverse order (orders 003 → 002 → 001, then payments, then inventory), each as its service role in one transaction:

```powershell
foreach ($step in @(
  @('orders','003_reconciler'), @('orders','002_checkout_pricing'), @('orders','001_checkout_attempts'),
  @('payments','001_payment_void_fence'), @('inventory','001_cart_reservations'))) {
  $svc, $version = $step
  $tracked = docker compose exec -T postgres psql -U postgres -d "${svc}_db" -Atc "SELECT to_regclass('schema_migrations')"
  $applied = if ($tracked) { docker compose exec -T postgres psql -U postgres -d "${svc}_db" -Atc "SELECT 1 FROM schema_migrations WHERE version = '$version'" }
  if (-not $applied) { "skip ${svc} ${version}: not applied"; continue }
  Get-Content "scripts\migrations\$svc\rollback\$version.sql" -Raw |
    docker compose exec -T postgres psql -U "${svc}_app" -d "${svc}_db" --single-transaction -v ON_ERROR_STOP=1 -f -
  if ($LASTEXITCODE -ne 0) { throw "rollback refused or failed at ${svc} ${version}: stop and use recovery (B)" }
}
```

The loop skips versions that were never applied, so it also undoes a partial apply. The scripts are plain ASCII on purpose, because Windows PowerShell 5.1 pipes text to native programs as ASCII. The inline rollback notes in the migration files' own headers are historical, and these scripts supersede them. Applied migration files are never edited, because the runner checksums them. The running code stays compatible: inventory and payments detect the missing objects, and Orders keeps serving the legacy flow (the new checkout returns errors, as before migration). Disable the reconciler first if it was enabled.

### B. Recovery after new data

Don't roll back. The migrations are additive and the legacy flow doesn't use the new objects, so leaving them in place is safe:

1. Stop new writes: disable the reconciler (`RECONCILER_ENABLED=false`, then `docker compose up -d --no-deps orders`) and stop the traffic or client that uses the new checkout.
2. **Fix forward** with a new, reviewed migration and code change. Never drop the new tables, never delete `voided` payment rows (they fence late charges) and never delete `abandoned` attempts (they fence late checkouts).
3. **Restoring a dump is a last resort that needs its own approval.** It discards every write made after the dump in that database, including legacy orders and payments. If it is ever approved, restore into a **new** database name, verify it, then point `DATABASE_URL` at it. Never drop or overwrite the original database or its volume.

## Reconciler activation (separate approval, after a stable window)

```powershell
# in .env (or the shell):  RECONCILER_ENABLED=true
docker compose up -d --no-deps orders
docker compose logs orders --since 2m | Select-String 'reconciler_disabled|reconciliation_cycle_failed'   # expect nothing
```

Then watch `orders.reconciliation.stuck` (aggregate with max, since every Orders process reports the database-wide count) and the `reconciliation_*` logs. To switch it off, set `RECONCILER_ENABLED=false` (or remove it) and run `docker compose up -d --no-deps orders`.

## Fault testing on a throwaway project

The full live suite, including the F1/F2/F3 scenarios and the Collector outage, runs against a separate Compose project with its own volumes. `COMPOSE_PROJECT_NAME` (not `-p`) is used so the test's own `docker compose stop collector` also targets the throwaway project. The live project is only stopped: its containers and volumes stay as they are.

```powershell
docker compose stop                                   # live project observa-dummy: stop only, volumes kept
$env:COMPOSE_PROJECT_NAME='observa-faulttest'
$env:GROUND_TRUTH_DIR="$env:TEMP\observa-faulttest-ground-truth"   # keep the live incident journal untouched
New-Item -ItemType Directory -Force $env:GROUND_TRUTH_DIR | Out-Null
docker compose up --build -d --wait                   # new volumes observa-faulttest_*
# optional dress rehearsal: run step 6 here, against the throwaway databases
$env:RUN_LIVE_TESTS='1'; $env:RUN_FAULT_TESTS='1'; $env:TEST_COLLECTOR_OUTAGE='1'; $env:POSTGRES_PORT='5433'
.venv\Scripts\python.exe -m pytest -q
docker compose down                                   # throwaway containers and networks only
Remove-Item Env:COMPOSE_PROJECT_NAME, Env:RUN_FAULT_TESTS, Env:GROUND_TRUTH_DIR
docker compose start                                  # live project again
docker volume ls --format '{{.Name}}' | Select-String 'observa-'   # observa-dummy_* must still be listed
```

**Alongside the live project (no stop needed):** publish the throwaway project on other host ports and point the tests at them. Every published port is a Compose variable, and the live tests read `TEST_*_URL` and `POSTGRES_PORT`:

```powershell
$env:COMPOSE_PROJECT_NAME='observa-faulttest'
$env:GROUND_TRUTH_DIR="$env:TEMP\observa-faulttest-ground-truth"; New-Item -ItemType Directory -Force $env:GROUND_TRUTH_DIR | Out-Null
$env:GATEWAY_PORT='18000'; $env:ORDERS_PORT='18001'; $env:INVENTORY_PORT='18002'; $env:PAYMENTS_PORT='18003'; $env:CONTROLLER_PORT='18090'
$env:POSTGRES_PORT='15433'; $env:PROMETHEUS_PORT='19090'; $env:JAEGER_PORT='26686'; $env:LOKI_PORT='13100'
$env:OTLP_GRPC_PORT='14317'; $env:OTLP_HTTP_PORT='14318'; $env:COLLECTOR_METRICS_PORT='18889'; $env:GRAFANA_PORT='13000'
$env:TRAFFIC_FLOW='checkout'   # optional: background traffic on the new checkout
docker compose up --build -d --wait postgres gateway orders inventory payments fault-injector collector prometheus jaeger loki telemetry-health
# apply step 6 here (throwaway databases), then: docker compose up -d --no-deps traffic-generator
$env:TEST_GATEWAY_URL='http://localhost:18000'; $env:TEST_ORDERS_URL='http://localhost:18001'; $env:TEST_INVENTORY_URL='http://localhost:18002'
$env:TEST_PAYMENTS_URL='http://localhost:18003'; $env:TEST_CONTROLLER_URL='http://localhost:18090'
$env:TEST_PROMETHEUS_URL='http://localhost:19090'; $env:TEST_JAEGER_URL='http://localhost:26686'; $env:TEST_LOKI_URL='http://localhost:13100'
$env:RUN_LIVE_TESTS='1'; $env:RUN_FAULT_TESTS='1'; $env:TEST_COLLECTOR_OUTAGE='1'
.venv\Scripts\python.exe -m pytest -q tests/integration tests/scenarios
docker compose down   # throwaway containers and networks only; COMPOSE_PROJECT_NAME is still set
```

Both projects build the shared image tag `observa-dummy:local`; running live containers keep the image they were created from until they are recreated.

Notes:
- The variant above that reuses the default ports needs the live project stopped while it runs.
- `GROUND_TRUTH_DIR` sends the throwaway controller's incident journal to a scratch folder, so fault runs never append to the live `ground-truth/incidents.jsonl`.
- The guard trusts the environment: setting both variables while the live project runs on the same ports would still reach it. Always stop the live project first.
- Both projects share the image tag `observa-dummy:local`. Build the throwaway from the same commit that is live.
- Remove the throwaway volumes only by their exact names (`docker volume rm observa-faulttest_postgres-data observa-faulttest_prometheus-data observa-faulttest_loki-data`), after checking the listing. Never with `down -v` while `COMPOSE_PROJECT_NAME` might be unset.
