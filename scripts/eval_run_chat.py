"""Phase B — drive the live Sevi chat API with every dataset question and record
the raw response for validation.

Each question runs in its OWN session (fresh user_id/session_id) so intent
routing is judged per-message, not contaminated by conversation stickiness.
Reads   : data/eval/mirror_qa.json      (the grounded dataset)
Writes  : data/eval/responses.json      (dataset item + live chat response)

Usage: python scripts/eval_run_chat.py [--endpoint URL] [--workers N] [--limit N]
"""
import argparse
import concurrent.futures as cf
import json
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_ENDPOINT = "http://127.0.0.1:8090/api/chat"
DATASET = "data/eval/mirror_qa.json"
OUT = "data/eval/responses.json"


def ask(endpoint: str, message: str, retries: int = 3) -> dict:
    """POST one message in a fresh session; return parsed response or error dict.

    Retries transient RemoteDisconnected/timeout (the single-worker server makes
    blocking LLM calls, so it drops connections under concurrency)."""
    last = None
    for attempt in range(retries):
        sid = uuid.uuid4().hex
        body = json.dumps({
            "message": message,
            "user_id": f"eval-{sid}",
            "session_id": sid,
        }).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8"))
            return {
                "ok": True,
                "text": d.get("text"),
                "intent": d.get("intent"),
                "confidence": d.get("confidence"),
                "source": d.get("source"),
                "refusal_reason": d.get("refusal_reason"),
                "suggestions": d.get("suggestions", []),
                "latency_ms": round((time.time() - t0) * 1000),
            }
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}: {e.read()[:200]!r}"}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": last}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dataset", default=DATASET)
    args = ap.parse_args()

    data = json.load(open(args.dataset, encoding="utf-8"))
    if args.limit:
        data = data[: args.limit]
    print(f"asking {len(data)} questions -> {args.endpoint} ({args.workers} workers)")

    results = [None] * len(data)

    def work(i_item):
        i, item = i_item
        resp = ask(args.endpoint, item["question"])
        return i, {**item, "response": resp}

    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, merged in ex.map(work, list(enumerate(data))):
            results[i] = merged
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(data)}")

    json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    errs = sum(1 for r in results if not r["response"].get("ok"))
    print(f"done: {len(results)} responses ({errs} request errors) -> {args.out}")


if __name__ == "__main__":
    main()
