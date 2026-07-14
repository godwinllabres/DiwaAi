"""Merge the per-doc QA files produced by the dataset workflow into one
validated eval set.

Reads   : <qa_dir>/doc_*.json + <qa_dir>/traps.json
Writes  : data/eval/mirror_qa.json
Reports : per-doc counts, missing/malformed docs, category & expected-intent
          distribution, and the set of suggested MISSING:* intents (redesign input).

Usage: python scripts/eval_merge_dataset.py --qa-dir <path>
"""
import argparse
import glob
import json
import os
import re
from collections import Counter

REQUIRED = ["question", "gold_answer", "expected_intent"]
OUT = "data/eval/mirror_qa.json"


def norm(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def load_array(path: str):
    try:
        raw = open(path, encoding="utf-8").read().strip()
        # tolerate accidental markdown fences
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception as e:  # noqa: BLE001
        print(f"  [BAD JSON] {os.path.basename(path)}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa-dir", required=True)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.qa_dir, "doc_*.json")))
    trap = os.path.join(args.qa_dir, "traps.json")
    if os.path.exists(trap):
        files.append(trap)

    merged, seen, malformed, empty = [], set(), 0, []
    per_source = Counter()
    for f in files:
        arr = load_array(f)
        base = os.path.basename(f)
        if arr is None:
            empty.append(base)
            continue
        kept = 0
        for it in arr:
            if not isinstance(it, dict) or any(k not in it for k in REQUIRED):
                malformed += 1
                continue
            key = norm(it["question"])
            if not key or key in seen:
                continue
            seen.add(key)
            it.setdefault("category", "misc")
            it.setdefault("language", "en")
            it.setdefault("volatility", "stable")
            it.setdefault("source_url", "")
            it["uid"] = f"q{len(merged):04d}"
            it["src_file"] = base
            merged.append(it)
            kept += 1
        per_source[base] = kept

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(merged, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---- report ----
    print(f"\nMERGED {len(merged)} unique QA items -> {OUT}")
    print(f"files read: {len(files)}  malformed items dropped: {malformed}")
    if empty:
        print(f"UNREADABLE/EMPTY files ({len(empty)}): {empty}")
    docs_expected = {f"doc_{i:02d}.json" for i in range(31)}
    got = {b for b in per_source}
    missing = sorted(docs_expected - got)
    if missing:
        print(f"MISSING doc files: {missing}")
    print("\nper-source counts:")
    for b in sorted(per_source):
        print(f"  {b:16s} {per_source[b]}")

    cats = Counter(it.get("category") for it in merged)
    langs = Counter(it.get("language") for it in merged)
    vol = Counter(it.get("volatility") for it in merged)
    print(f"\ncategories: {dict(cats)}")
    print(f"languages : {dict(langs)}")
    print(f"volatility: {dict(vol)}")

    missing_intents = Counter(
        it["expected_intent"] for it in merged
        if str(it.get("expected_intent", "")).startswith("MISSING")
    )
    refuse = sum(1 for it in merged if it.get("expected_intent") == "REFUSE")
    print(f"\nexpected REFUSE items: {refuse}")
    print(f"suggested MISSING intents ({sum(missing_intents.values())} items):")
    for k, v in missing_intents.most_common():
        print(f"  {v:2d}  {k}")


if __name__ == "__main__":
    main()
