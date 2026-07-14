"""Aggregate the judge workflow's per-batch verdict files into a scored report
and a failures list (the redesign work-list).

Reads : data/eval/verdicts/batch_XX.json  (list of {uid, verdict, intent_ok,
         grounded_ok, severity, issue, fix_hint, ...})
        data/eval/responses.json           (to join back question/response)
Writes: data/eval/scored.json              (full joined verdicts)
        data/eval/failures.json            (verdicts that are not a clean pass)
Prints: pass rate, breakdown by verdict / category / source, MISSING intents,
        and the ranked failure list.

Usage: python scripts/eval_merge_verdicts.py
"""
import glob
import json
import os
from collections import Counter, defaultdict

VDIR = "data/eval/verdicts"
RESP = "data/eval/responses.json"
SCORED = "data/eval/scored.json"
FAIL = "data/eval/failures.json"

PASS_VERDICTS = {"correct", "refused_ok", "fallback_ok"}


def main():
    resp = {it["uid"]: it for it in json.load(open(RESP, encoding="utf-8"))}
    verdicts = {}
    for f in sorted(glob.glob(os.path.join(VDIR, "batch_*.json"))):
        try:
            arr = json.load(open(f, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[BAD] {os.path.basename(f)}: {e}")
            continue
        for v in arr:
            if isinstance(v, dict) and "uid" in v:
                verdicts[v["uid"]] = v

    scored, failures = [], []
    by_verdict, by_cat, by_source = Counter(), Counter(), Counter()
    fail_by_cat = Counter()
    missing_intents = Counter()
    ungraded = []

    for uid, item in resp.items():
        v = verdicts.get(uid)
        if not v:
            ungraded.append(uid)
            continue
        r = item.get("response", {})
        row = {
            "uid": uid,
            "question": item["question"],
            "expected_intent": item["expected_intent"],
            "got_intent": r.get("intent"),
            "confidence": r.get("confidence"),
            "source": r.get("source"),
            "category": item.get("category"),
            "subtype": item.get("subtype"),
            "verdict": v.get("verdict"),
            "intent_ok": v.get("intent_ok"),
            "grounded_ok": v.get("grounded_ok"),
            "severity": v.get("severity"),
            "issue": v.get("issue"),
            "fix_hint": v.get("fix_hint"),
            "gold_answer": item["gold_answer"],
            "got_text": r.get("text"),
        }
        scored.append(row)
        by_verdict[v.get("verdict")] += 1
        by_cat[item.get("category")] += 1
        by_source[r.get("source")] += 1
        if str(item.get("expected_intent", "")).startswith("MISSING"):
            missing_intents[item["expected_intent"]] += 1
        if v.get("verdict") not in PASS_VERDICTS:
            failures.append(row)
            fail_by_cat[item.get("category")] += 1

    json.dump(scored, open(SCORED, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    sev_rank = {"high": 0, "med": 1, "medium": 1, "low": 2, None: 3}
    failures.sort(key=lambda r: sev_rank.get(r.get("severity"), 3))
    json.dump(failures, open(FAIL, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    total = len(scored)
    passed = sum(by_verdict[v] for v in PASS_VERDICTS if v in by_verdict)
    print(f"\n===== VALIDATION SCORE =====")
    print(f"graded: {total}   ungraded: {len(ungraded)}")
    if total:
        print(f"PASS: {passed}/{total} = {passed/total*100:.1f}%   FAIL: {len(failures)}")
    print(f"\nby verdict: {dict(by_verdict)}")
    print(f"by source : {dict(by_source)}")
    print(f"\nfailures by category: {dict(fail_by_cat)}")
    if missing_intents:
        print(f"\nMISSING intents flagged in dataset:")
        for k, n in missing_intents.most_common():
            print(f"  {n:2d}  {k}")

    # fix-hint clustering
    hints = Counter(r.get("fix_hint", "").strip()[:80] for r in failures if r.get("fix_hint"))
    print(f"\ntop fix hints:")
    for h, n in hints.most_common(15):
        print(f"  {n:2d}  {h}")

    print(f"\nHIGH-severity failures:")
    for r in failures:
        if r.get("severity") == "high":
            print(f"  [{r['category']}] {r['question'][:60]!r} exp={r['expected_intent']} got={r['got_intent']} :: {str(r.get('issue'))[:80]}")

    print(f"\nwrote {SCORED} and {FAIL}")


if __name__ == "__main__":
    main()
