#!/usr/bin/env python3
"""Propose, apply, and drift-check per-intent source bindings.

For every active intent, retrieves the best-matching Citizens' Charter page
and official-site URL from the same TF-IDF indexes the RAG tiers use, and
writes them as *proposals* with evidence snippets for review:

    python scripts/bind_intent_sources.py              # → data/intent_sources_proposed.json
    python scripts/bind_intent_sources.py --apply      # promote proposals → data/intent_sources.json
    python scripts/bind_intent_sources.py --check      # drift-check live bindings

--apply honors data/intent_sources_review.json when present — a
{tag: {"charter": bool, "site": bool}} verdict map produced by review — so
only accepted bindings are promoted. Without a review file, every proposal
above threshold is promoted.

The query per intent is its patterns (how users ask) plus its first curated
responses (what the answer says — curated FROM these sources, so the strongest
lexical bridge back to them).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from intents_db import load_intents  # noqa: E402
from api import charter_rag, intent_grounding, site_rag  # noqa: E402

PROPOSED_PATH = ROOT / "data" / "intent_sources_proposed.json"
BINDINGS_PATH = ROOT / "data" / "intent_sources.json"
REVIEW_PATH = ROOT / "data" / "intent_sources_review.json"


def propose() -> dict:
    if charter_rag.get_index() is None and site_rag.get_index() is None:
        sys.exit("Neither the charter nor the site index is available — nothing to bind against.")

    proposals: dict[str, dict] = {}
    skipped_conversational, skipped_no_match = [], []
    for intent in load_intents():
        tag = intent["tag"]
        if not intent.get("active", True):
            continue
        if tag in intent_grounding.EXCLUDED_TAGS:
            skipped_conversational.append(tag)
            continue
        # Same derivation the drift check re-runs — see api/intent_grounding.
        entry = intent_grounding.propose_for(intent)
        if entry:
            entry["description"] = intent.get("description", "")
            entry["sample_patterns"] = intent.get("patterns", [])[:5]
            entry["sample_response"] = (intent.get("responses") or [""])[0][:300]
            proposals[tag] = entry
        else:
            skipped_no_match.append(tag)

    PROPOSED_PATH.write_text(
        json.dumps({"proposals": proposals}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] {len(proposals)} intents got a proposed binding → {PROPOSED_PATH}")
    print(f"     skipped (conversational): {len(skipped_conversational)} — {sorted(skipped_conversational)}")
    print(f"     skipped (no match above floor): {len(skipped_no_match)} — {sorted(skipped_no_match)}")
    return proposals


def apply_proposals() -> None:
    raw = json.loads(PROPOSED_PATH.read_text(encoding="utf-8"))["proposals"]
    review = {}
    if REVIEW_PATH.exists():
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        review.pop("_meta", None)
        print(f"[i] review is a strict allowlist ({len(review)} intents); "
              "refs without an explicit true verdict are dropped")
    bindings: dict[str, list[dict]] = {}
    rejected = 0
    for tag, entry in raw.items():
        refs = []
        for kind in ("charter", "site"):
            ref = entry.get(kind)
            if not ref:
                continue
            # With a review file present it is authoritative: a ref is
            # promoted ONLY when the review explicitly accepted it (true).
            # Missing tag/kind → drop (unverified is not the same as accepted).
            verdict = review.get(tag, {}).get(kind, False) if review else True
            if not verdict:
                rejected += 1
                continue
            refs.append({k: ref[k] for k in ("kind", "locator", "label", "score")})
        if refs:
            bindings[tag] = refs
    BINDINGS_PATH.write_text(
        json.dumps({"bindings": bindings}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] {len(bindings)} intents bound ({rejected} refs dropped) → {BINDINGS_PATH}")


def check() -> None:
    report = intent_grounding.check_drift(load_intents())
    print(json.dumps(report, indent=1, ensure_ascii=False))
    n_bad = len(report.get("drifted", [])) + len(report.get("stale", []))
    if n_bad:
        print(f"\n[DRIFT] {n_bad} binding(s) no longer match the corpus — "
              "re-run the binder and re-verify before trusting these citations.")
        sys.exit(1)
    print(f"\n[OK] all {report.get('checked', 0)} bound refs still re-derive to the same source.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="promote proposals to live bindings")
    ap.add_argument("--check", action="store_true", help="drift-check live bindings")
    args = ap.parse_args()
    if args.check:
        check()
    elif args.apply:
        apply_proposals()
    else:
        propose()
