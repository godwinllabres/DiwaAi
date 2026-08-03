"""Gold-set evaluation harness (P1-4, HANDOFF-QUALITY 2026-08-03).

Drives POST /chat on a RUNNING server with training/gold_eval.jsonl and
reports the quality metrics the handoff makes gating:

    intent accuracy          answerable items whose returned `intent` matches
                             expected_intent (or an allowed alt_intents entry)
    OOS false-accept rate    out-of-scope items the bot ANSWERED instead of
                             refusing/deflecting/falling back
    per-language slices      eng / fil / taglish accuracy
    per-tier distribution    which ResponseSource answered each item
    clarify-vs-guess rate    ambiguous items that got a clarification rather
                             than a guessed answer
    citation presence        bound-intent items whose reply carries `sources`
                             (or an appended "Source:" block)

Exit code is non-zero when intent accuracy < --min-accuracy or the OOS
false-accept rate > --max-oos-accept, so a retrain that regresses either
fails loudly in CI.

Usage:
    python -m uvicorn api.app:app --port 8009        # in another shell
    python training/run_gold_eval.py --base-url http://127.0.0.1:8009

Run after every retrain. The gold set is ground truth — when the bot and the
set disagree, fix the bot (or, for a genuinely wrong label, fix the set in
its own commit so metric moves stay attributable).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

try:
    import httpx as _http
except ImportError:  # pragma: no cover — stdlib fallback
    _http = None
    import urllib.request

# Intents whose curated reply IS the correct out-of-scope handling — a
# deflection, not an answer. Counted as "refuse" behavior.
REFUSAL_INTENTS = {"out_of_scope", "off_topic_homework", "compare_to_other_school"}

# Sources that mean "no substantive answer was produced".
NON_ANSWER_SOURCES = {"refusal", "fallback", "llm_unavailable"}


def post_chat(base_url: str, message: str, session_id: str, timeout: float) -> dict:
    payload = {"message": message, "session_id": session_id, "user_id": "gold-eval"}
    url = f"{base_url.rstrip('/')}/chat"
    if _http is not None:
        resp = _http.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — local server
        return json.loads(r.read().decode("utf-8"))


def classify_behavior(reply: dict) -> str:
    """Map a ChatResponse to answer | clarify | refuse | fallback."""
    intent = reply.get("intent") or ""
    source = reply.get("source") or ""
    if intent.endswith("_disambiguation") or intent == "campus_disambiguation":
        return "clarify"
    if source == "refusal" or reply.get("refusal_reason") or intent in REFUSAL_INTENTS:
        return "refuse"
    if source in NON_ANSWER_SOURCES:
        return "fallback"
    return "answer"


def has_citation(reply: dict) -> bool:
    if reply.get("sources"):
        return True
    return "Source:" in (reply.get("text") or "")


def run(args: argparse.Namespace) -> int:
    items = [json.loads(line) for line in
             Path(args.eval_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    run_id = uuid.uuid4().hex[:8]
    results = []
    t0 = time.time()

    for i, it in enumerate(items):
        session_id = f"gold-{run_id}-{i}"
        try:
            reply = post_chat(args.base_url, it["question"], session_id, args.timeout)
            error = None
        except Exception as exc:  # noqa: BLE001 — a dead server fails every item the same way
            reply, error = {}, f"{type(exc).__name__}: {exc}"
        behavior = classify_behavior(reply) if not error else "error"
        text = (reply.get("text") or "")
        got_intent = reply.get("intent")
        accepted = {it["expected_intent"], *(it.get("alt_intents") or [])}
        head = text[:160].lower()
        results.append({
            **it,
            "got_intent": got_intent,
            "got_source": reply.get("source"),
            "got_behavior": behavior,
            "intent_ok": got_intent in accepted,
            "content_ok": all(a.lower() in text.lower() for a in it.get("must_contain", [])),
            "cited": has_citation(reply),
            # P2-8 stated-assumption branch: an answer that OPENS by naming
            # its assumed campus/level counts as handled ambiguity.
            "assumed": ("assuming" in head) or ("ipagpalagay" in head),
            "error": error,
            "text_head": text[:160],
        })

    elapsed = time.time() - t0

    # ── Metrics ──────────────────────────────────────────────────────────
    answerable = [r for r in results
                  if r["expected_behavior"] == "answer"
                  and r["expected_intent"] not in ("OUT_OF_SCOPE", "AMBIGUOUS")]
    oos = [r for r in results if r["expected_intent"] == "OUT_OF_SCOPE"]
    ambiguous = [r for r in results if r["ambiguous"]]
    cite_items = [r for r in results if r.get("must_cite")]
    errors = [r for r in results if r["error"]]

    def rate(hits: int, total: int) -> float:
        return hits / total if total else 0.0

    acc_hits = sum(1 for r in answerable if r["intent_ok"] and r["got_behavior"] == "answer")
    accuracy = rate(acc_hits, len(answerable))
    content_hits = sum(1 for r in answerable if r["content_ok"])
    oos_accepts = [r for r in oos if r["got_behavior"] == "answer"]
    oos_accept_rate = rate(len(oos_accepts), len(oos))
    clarified = [r for r in ambiguous if r["got_behavior"] == "clarify"]
    clarify_rate = rate(len(clarified), len(ambiguous))
    handled_amb = clarified + [r for r in ambiguous
                               if r["got_behavior"] == "answer" and r.get("assumed")]
    handled_amb_rate = rate(len(handled_amb), len(ambiguous))
    cited_hits = sum(1 for r in cite_items if r["cited"] and r["got_behavior"] == "answer")

    by_lang: dict[str, list] = defaultdict(list)
    for r in answerable:
        by_lang[r["language"]].append(r)
    tier_dist = Counter(r["got_source"] or "error" for r in results)

    # ── Report ───────────────────────────────────────────────────────────
    w = print
    w("=" * 66)
    w(f"  GOLD EVAL — {len(items)} items in {elapsed:.1f}s   run={run_id}")
    w("=" * 66)
    w(f"intent accuracy      : {accuracy:.3f}  ({acc_hits}/{len(answerable)})"
      f"   [threshold >= {args.min_accuracy}]")
    w(f"OOS false-accept     : {oos_accept_rate:.3f}  ({len(oos_accepts)}/{len(oos)})"
      f"   [threshold <= {args.max_oos_accept}]")
    w(f"content (must_contain): {rate(content_hits, len(answerable)):.3f}  ({content_hits}/{len(answerable)})  [informational]")
    w(f"clarify-vs-guess     : {clarify_rate:.3f}  ({len(clarified)}/{len(ambiguous)})  [informational]")
    w(f"clarify-or-assumption: {handled_amb_rate:.3f}  ({len(handled_amb)}/{len(ambiguous)})  [P2 DoD target >= 0.80]")
    w(f"citation presence    : {rate(cited_hits, len(cite_items)):.3f}  ({cited_hits}/{len(cite_items)})  [informational]")
    if errors:
        w(f"transport errors     : {len(errors)}")
    w("\nper-language accuracy:")
    for lang, rs in sorted(by_lang.items()):
        hits = sum(1 for r in rs if r["intent_ok"] and r["got_behavior"] == "answer")
        w(f"  {lang:8s}: {rate(hits, len(rs)):.3f}  ({hits}/{len(rs)})")
    w("\nper-tier distribution:")
    for src, n in tier_dist.most_common():
        w(f"  {src or '<none>':22s}: {n}")

    misses = [r for r in answerable if not (r["intent_ok"] and r["got_behavior"] == "answer")]
    if misses:
        w("\nintent misses:")
        for r in misses:
            w(f"  [{r['language']}] {r['question'][:58]!r}"
              f"\n      expected {r['expected_intent']!r}  got {r['got_intent']!r}"
              f" ({r['got_source']}, {r['got_behavior']})")
    if oos_accepts:
        w("\nOOS false-accepts:")
        for r in oos_accepts:
            w(f"  {r['question'][:58]!r} -> {r['got_intent']!r} ({r['got_source']})")
    guessed = [r for r in ambiguous if r["got_behavior"] == "answer"]
    if guessed:
        w("\nambiguous items answered as a guess (not clarified):")
        for r in guessed:
            w(f"  {r['question'][:58]!r} -> {r['got_intent']!r}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({
                "run_id": run_id, "elapsed_s": elapsed,
                "metrics": {
                    "intent_accuracy": accuracy,
                    "oos_false_accept": oos_accept_rate,
                    "clarify_rate": clarify_rate,
                    "clarify_or_assumption_rate": handled_amb_rate,
                    "citation_rate": rate(cited_hits, len(cite_items)),
                    "per_language": {
                        lang: rate(sum(1 for r in rs if r["intent_ok"] and r["got_behavior"] == "answer"), len(rs))
                        for lang, rs in by_lang.items()
                    },
                    "tier_distribution": dict(tier_dist),
                },
                "results": results,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        w(f"\nper-item results -> {args.json_out}")

    failed = []
    if errors:
        failed.append(f"{len(errors)} transport errors")
    if accuracy < args.min_accuracy:
        failed.append(f"intent accuracy {accuracy:.3f} < {args.min_accuracy}")
    if oos_accept_rate > args.max_oos_accept:
        failed.append(f"OOS false-accept {oos_accept_rate:.3f} > {args.max_oos_accept}")
    if failed:
        w("\nFAIL: " + "; ".join(failed))
        return 1
    w("\nPASS: thresholds met")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8009")
    p.add_argument("--eval-file", default=str(Path(__file__).parent / "gold_eval.jsonl"))
    p.add_argument("--min-accuracy", type=float, default=0.85)
    p.add_argument("--max-oos-accept", type=float, default=0.10)
    p.add_argument("--timeout", type=float, default=120.0,
                   help="per-request timeout; generous for a cold local LLM tier")
    p.add_argument("--json-out", default=None,
                   help="write full per-item results as JSON for diffing runs")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
