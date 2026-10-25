import asyncio
import json
import os
import signal
import time
from collections import Counter, deque

import httpx

from controllers.traffic_generator.mix import TrafficMix
from services.shared.telemetry import utcnow


def json_or_none(response):
    try:
        return response.json()
    except ValueError:
        return None


async def run(env=None, *, transport=None, max_requests=None, stopped=None):
    """Send the planned mix at a steady rate until ``stopped`` is set (or ``max_requests`` were planned).

    Returns the response counts by status. ``transport`` lets tests route requests in-process.
    """
    env = os.environ if env is None else env
    rate = float(env.get("TRAFFIC_RPS", "4"))
    concurrency = int(env.get("TRAFFIC_CONCURRENCY", "16"))
    if not 0.1 <= rate <= 50 or not 1 <= concurrency <= 64:
        raise ValueError("Traffic rate/concurrency exceeds safe local bounds")
    raw_prefix = (
        env.get("TRAFFIC_RUN_PREFIX") or None
    )  # optional: pins attempt references for a repeatable run
    prefix = None if raw_prefix is None else int(raw_prefix)
    if prefix is not None and not 0 <= prefix <= 0xFFFF:
        raise ValueError("TRAFFIC_RUN_PREFIX must be 0-65535")
    mix = TrafficMix(
        env.get("TRAFFIC_FLOW", "legacy"), seed=int(env.get("TRAFFIC_SEED", "42")), run_prefix=prefix
    )
    url = env.get("GATEWAY_URL", "http://gateway:8000")
    stopped, semaphore = stopped or asyncio.Event(), asyncio.Semaphore(concurrency)
    counters, latencies, tasks = Counter(), deque(maxlen=1000), set()
    limits = httpx.Limits(max_connections=concurrency)

    async with httpx.AsyncClient(timeout=12, limits=limits, transport=transport) as client:

        async def request(planned):
            async with semaphore:
                start = time.perf_counter()
                try:
                    response = await client.request(
                        planned.method, url + planned.path, json=planned.json, headers=planned.headers
                    )
                    mix.observe(planned, response.status_code, json_or_none(response))
                    counters[str(response.status_code)] += 1
                except httpx.HTTPError:
                    counters["transport_error"] += 1
                latencies.append((time.perf_counter() - start) * 1000)

        sequence, report_at = 0, time.monotonic() + 10
        while not stopped.is_set() and (max_requests is None or sequence < max_requests):
            # A bounded task set drops excess scheduled work instead of growing an unbounded backlog.
            if len(tasks) < concurrency:
                task = asyncio.create_task(request(mix.next(sequence)))
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
                            "flow": mix.flow,
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
    return dict(counters)


async def main():
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopped.set))
    await run(stopped=stopped)


if __name__ == "__main__":
    asyncio.run(main())
