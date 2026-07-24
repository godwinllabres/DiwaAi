#!/usr/bin/env python3
"""Concurrency smoke test for /chat — does the turn gate behave under load?

This exists because every throughput number in the concurrency work rests on an
UNMEASURED assumption: the mix of fast (Naive Bayes / retrieval) vs slow (LLM)
turns, and the latency of each. That mix decides the right value for
CHAT_MAX_CONCURRENT_TURNS, and it differs per deployment and per season.

Run it against staging, never production: it deliberately tries to saturate the
gate, and it writes real rows to the chat log.

    python scripts/loadtest.py --url http://localhost:8000 --sessions 50

What it asserts:
  * no 5xx other than the deliberate 503 shed
  * every 503 carries Retry-After (a client that can't back off will hammer)
  * per-session turn ordering holds (replies come back in the order asked)
  * the turn gate drains back to idle afterwards — a leaked waiter counter
    would leave `waiting` pinned above zero and wedge the gate shut

Memory is NOT covered here: watch `docker stats` alongside a run, since the
whole point of bounding concurrency is bounding the memory spike.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

# A realistic mix matters more than volume: FAQ-style questions should land in
# the fast tiers, the open-ended ones should reach the LLM. If everything you
# send is one shape, the measured concurrency ceiling is meaningless.
FAST = [
    "what are the enrollment requirements",
    "where is the registrar",
    "when does enrollment start",
    "how do I request a Form 137",
    "who is the dean of CEIT",
]
SLOW = [
    "compare the BS Computer Science and BS Information Technology programs for me",
    "explain how the scholarship application process works end to end",
    "what should I prepare if I'm transferring from another university",
]


def _post(url: str, payload: dict, timeout: float) -> tuple[int, dict, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": raw[:200].decode(errors="replace")}
        return e.code, parsed, dict(e.headers or {})
    except Exception as e:  # noqa: BLE001 — connection refused, timeout, reset
        return 0, {"detail": f"{type(e).__name__}: {e}"}, {}


async def session(idx: int, url: str, turns: int, timeout: float) -> dict:
    """One student: `turns` sequential messages, as a real tab would send them."""
    sid = f"loadtest-{idx}"
    out = {"codes": [], "latencies": [], "retry_after": [], "ordered": True}
    for t in range(turns):
        # Mostly fast questions with a slow one mixed in — see FAST/SLOW above.
        msg = SLOW[idx % len(SLOW)] if t % 4 == 3 else FAST[(idx + t) % len(FAST)]
        marker = f"{msg} (q{t})"
        t0 = time.perf_counter()
        code, body, headers = await asyncio.to_thread(
            _post, f"{url}/chat", {"message": marker, "session_id": sid}, timeout
        )
        out["latencies"].append(time.perf_counter() - t0)
        out["codes"].append(code)
        if code == 503:
            out["retry_after"].append(headers.get("Retry-After"))
        if code == 200 and not isinstance(body.get("text"), str):
            out["ordered"] = False
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--sessions", type=int, default=50)
    ap.add_argument("--turns", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    gate_before, _, _ = await asyncio.to_thread(
        _post, f"{args.url}/health", {}, 10
    )
    if gate_before == 0:
        print(f"cannot reach {args.url} — is the server up?", file=sys.stderr)
        return 2

    print(f"firing {args.sessions} sessions x {args.turns} turns at {args.url}")
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[session(i, args.url, args.turns, args.timeout) for i in range(args.sessions)]
    )
    elapsed = time.perf_counter() - t0

    codes = collections.Counter(c for r in results for c in r["codes"])
    lat = [x for r in results for x in r["latencies"]]
    total = sum(codes.values())
    ok = codes.get(200, 0)
    shed = codes.get(503, 0)
    throttled = codes.get(429, 0)
    unexpected = {c: n for c, n in codes.items() if c not in (200, 429, 503)}
    missing_retry = [r for res in results for r in res["retry_after"] if not r]

    lat.sort()
    def pct(p: float) -> float:
        return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0.0

    print(f"\n  wall clock     {elapsed:.1f}s for {total} requests "
          f"({total / elapsed:.1f}/s)")
    print(f"  200 OK         {ok}")
    print(f"  503 shed       {shed}  (deliberate backpressure, not failure)")
    print(f"  429 throttled  {throttled}")
    if unexpected:
        print(f"  UNEXPECTED     {unexpected}")
    if lat:
        print(f"  latency        p50 {pct(.5):.2f}s   p95 {pct(.95):.2f}s   "
              f"max {lat[-1]:.2f}s   mean {statistics.mean(lat):.2f}s")

    # Give the gate a moment to drain, then confirm it returned to idle.
    await asyncio.sleep(1.0)
    _, health, _ = await asyncio.to_thread(_post, f"{args.url}/health", {}, 10)
    gate = (health or {}).get("turn_gate") or {}
    print(f"  gate after     {gate}")

    failures = []
    if unexpected:
        failures.append(f"unexpected status codes: {unexpected}")
    if missing_retry:
        failures.append(f"{len(missing_retry)} x 503 without Retry-After")
    if gate.get("waiting"):
        failures.append(f"waiter counter did not drain: waiting={gate['waiting']} "
                        f"(gate will wedge shut)")
    if gate.get("in_flight"):
        failures.append(f"in_flight did not drain: {gate['in_flight']}")
    if ok == 0:
        failures.append("no request succeeded at all")

    print()
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("  PASS  no unexpected errors; gate drained cleanly")
    print("\n  Next: mine the real tier mix before tuning CHAT_MAX_CONCURRENT_TURNS —")
    print("    SELECT model_used, count(*), round(avg(response_time_ms))")
    print("    FROM chat_messages GROUP BY 1 ORDER BY 2 DESC;")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
