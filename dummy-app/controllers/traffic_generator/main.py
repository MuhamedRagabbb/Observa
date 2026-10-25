import asyncio
import json
import os
import random
import signal
import time
from collections import Counter, deque

import httpx

from services.shared.telemetry import utcnow


async def run():
    rate = float(os.getenv("TRAFFIC_RPS", "4"))
    concurrency = int(os.getenv("TRAFFIC_CONCURRENCY", "16"))
    if not 0.1 <= rate <= 50 or not 1 <= concurrency <= 64:
        raise ValueError("Traffic rate/concurrency exceeds safe local bounds")
    rng = random.Random(int(os.getenv("TRAFFIC_SEED", "42")))
    url = os.getenv("GATEWAY_URL", "http://gateway:8000")
    stopped, semaphore = asyncio.Event(), asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopped.set))
    orders, counters, latencies, tasks = deque(maxlen=500), Counter(), deque(maxlen=1000), set()

    async with httpx.AsyncClient(timeout=12, limits=httpx.Limits(max_connections=concurrency)) as client:

        async def request(sequence, choice):
            async with semaphore:
                start = time.perf_counter()
                try:
                    if choice < 0.65:
                        product = (
                            "sku-999"
                            if choice < 0.02
                            else "sku-004"
                            if choice < 0.04
                            else f"sku-{sequence % 3 + 1:03d}"
                        )
                        quantity = 0 if 0.04 <= choice < 0.06 else sequence % 3 + 1
                        customer = (
                            f"decline-{sequence}" if 0.06 <= choice < 0.08 else f"load-user-{sequence % 50}"
                        )
                        response = await client.post(
                            url + "/api/orders",
                            json={
                                "product_id": product,
                                "quantity": quantity,
                                "customer_id": customer,
                            },
                        )
                        if response.status_code == 201:
                            orders.append(response.json()["order_id"])
                    elif choice < 0.85:
                        response = await client.get(url + "/api/products")
                    elif choice < 0.98 and orders:
                        response = await client.get(url + "/api/orders/" + orders[sequence % len(orders)])
                    else:
                        response = await client.get(url + "/unavailable")
                    counters[str(response.status_code)] += 1
                except httpx.HTTPError:
                    counters["transport_error"] += 1
                latencies.append((time.perf_counter() - start) * 1000)

        sequence, report_at = 0, time.monotonic() + 10
        while not stopped.is_set():
            # A bounded task set drops excess scheduled work instead of growing an unbounded backlog.
            if len(tasks) < concurrency:
                task = asyncio.create_task(request(sequence, rng.random()))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            else:
                counters["dropped"] += 1
            sequence += 1
            if time.monotonic() >= report_at:
                values = sorted(latencies)
                print(
                    json.dumps(
                        {
                            "timestamp": utcnow(),
                            "level": "INFO",
                            "service.name": "traffic-generator",
                            "event": "traffic_summary",
                            "request_id": "system",
                            "counts": dict(counters),
                            "latency_p50_ms": round(values[len(values) // 2], 2) if values else None,
                            "latency_p95_ms": round(values[int(len(values) * 0.95)], 2) if values else None,
                        }
                    ),
                    flush=True,
                )
                report_at = time.monotonic() + 10
            try:
                await asyncio.wait_for(stopped.wait(), 1 / rate)
            except TimeoutError:
                pass
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
