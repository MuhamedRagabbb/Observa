"""One reconciler process for the two-process test: runs cycles until the database is idle.

Usage: python -m tests.isolated_db.reconciler_worker DSN INVENTORY_URL PAYMENTS_URL
Prints ``RESULT {"claimed": n}`` on its own line when done (logs also go to stdout).
"""

import asyncio
import json
import sys

from services.orders.reconciler import Reconciler
from services.orders.saga import CheckoutSaga
from services.shared.attempts import AttemptStore, ClaimPolicy
from services.shared.client import DependencyClient
from services.shared.config import Settings
from services.shared.database import Database
from services.shared.runtime import Runtime
from services.shared.telemetry import Telemetry


async def main(dsn, inventory_url, payments_url):
    settings = Settings(
        dsn=dsn,
        otlp_endpoint="",
        pool_max=4,
        pool_timeout=5,
        inventory_url=inventory_url,
        payments_url=payments_url,
    )
    telemetry = Telemetry("orders-reconciler-test", settings)
    runtime = Runtime(telemetry)
    db = Database(settings, telemetry, runtime)
    await db.open()
    client = DependencyClient(telemetry, settings)
    attempts = AttemptStore(db)
    saga = CheckoutSaga(attempts, client, settings, telemetry, runtime, resume_after=0)
    worker = Reconciler(
        attempts,
        saga,
        telemetry,
        policy=ClaimPolicy(stall_seconds=1, lease_seconds=30),
    )  # default clock: the database's, shared by both processes
    claimed, idle = 0, 0
    try:
        while idle < 5:
            report = await worker.run_cycle()
            claimed += report.claimed
            idle = idle + 1 if report.claimed == 0 else 0
            await asyncio.sleep(0.1)
    finally:
        await client.close()
        await runtime.close()
        await db.close()
        telemetry.close()
    print("RESULT " + json.dumps({"claimed": claimed}), flush=True)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
