"""Usage: python -I tests/tools/load_check.py <base url> [concurrency] [seconds]

A rough load check of an API (the Python one or the TypeScript one, on the same database): `concurrency` clients send a mix of the site's reads for `seconds`
seconds, and the answers are counted by status with the time they took. It shows how an API holds up under many clients at once (the connection pool, the search
concurrency limit); it is not a benchmark of the machine."""

import asyncio
import collections
import statistics
import sys
import time

import httpx

BASE = sys.argv[1].rstrip("/")
CLIENTS = int(sys.argv[2]) if len(sys.argv) > 2 else 50
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 20
PATHS = [
    "/api/health", "/api/v1/items?limit=20", "/api/site/hot", "/api/site/timeline", "/api/site/pool", "/api/site/pool?q=AI", "/api/site/pool?q=model",
    "/api/v1/hot-topics", "/api/site/search/suggestions?q=a", "/robots.txt", "/sitemap.xml",
]  # fmt: skip


async def client(http: httpx.AsyncClient, stop: float, results: list, n: int) -> None:
    i = n
    while time.monotonic() < stop:
        path = PATHS[i % len(PATHS)]
        i += 1
        started = time.monotonic()
        try:
            r = await http.get(path)
            code = r.status_code
        except httpx.HTTPError as error:
            code = type(error).__name__
        results.append((path.split("?")[0], code, time.monotonic() - started))


async def main() -> None:
    results: list = []
    limits = httpx.Limits(max_connections=CLIENTS * 2, max_keepalive_connections=CLIENTS)
    async with httpx.AsyncClient(base_url=BASE, timeout=30, limits=limits) as http:
        stop = time.monotonic() + SECONDS
        started = time.monotonic()
        await asyncio.gather(*(client(http, stop, results, n) for n in range(CLIENTS)))
        elapsed = time.monotonic() - started
    by_code = collections.Counter(code for _, code, _ in results)
    times = sorted(t for _, _, t in results)
    q = lambda p: times[min(len(times) - 1, int(len(times) * p))] * 1000  # noqa: E731
    print(f"{BASE}: {len(results)} requests in {elapsed:.0f}s = {len(results) / elapsed:.0f}/s with {CLIENTS} clients")
    print("statuses:", dict(by_code))
    print(f"time ms: median {statistics.median(times) * 1000:.0f}  p95 {q(0.95):.0f}  p99 {q(0.99):.0f}  max {times[-1] * 1000:.0f}")
    slow = collections.defaultdict(list)
    for path, _, t in results:
        slow[path].append(t)
    for path, ts in sorted(slow.items(), key=lambda kv: -statistics.median(kv[1]))[:4]:
        print(f"  slowest median: {path:<34} {statistics.median(ts) * 1000:>6.0f} ms  ({len(ts)} requests)")


asyncio.run(main())
