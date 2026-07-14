"""Compare baseline vs redesigned eval: pass rate, verdict mix, category deltas.

Baseline : data/eval/responses.json      + data/eval/verdicts/
Redesign : data/eval/responses_new.json  + data/eval/verdicts_new/

Usage: python scripts/eval_compare.py
"""
import glob
import json
import os
from collections import Counter

PASS = {"correct", "refused_ok", "fallback_ok"}


def load_verdicts(vdir):
    v = {}
    for f in sorted(glob.glob(os.path.join(vdir, "batch_*.json"))):
        try:
            for row in json.load(open(f, encoding="utf-8")):
                if isinstance(row, dict) and "uid" in row:
                    v[row["uid"]] = row
        except Exception as e:  # noqa: BLE001
            print(f"[bad] {f}: {e}")
    return v


def score(resp_path, vdir, label):
    resp = {it["uid"]: it for it in json.load(open(resp_path, encoding="utf-8"))}
    v = load_verdicts(vdir)
    by_verdict = Counter()
    passed = 0
    fail_by_cat = Counter()
    cat_total = Counter()
    graded = 0
    per_uid = {}
    for uid, item in resp.items():
        vv = v.get(uid)
        cat_total[item.get("category")] += 1
        if not vv:
            continue
        graded += 1
        verdict = vv.get("verdict")
        by_verdict[verdict] += 1
        per_uid[uid] = verdict
        if verdict in PASS:
            passed += 1
        else:
            fail_by_cat[item.get("category")] += 1
    print(f"\n===== {label} =====")
    print(f"graded {graded}/{len(resp)}  PASS {passed} = {passed/graded*100:.1f}%")
    print(f"verdicts: {dict(by_verdict.most_common())}")
    return {"passed": passed, "graded": graded, "by_verdict": by_verdict,
            "fail_by_cat": fail_by_cat, "cat_total": cat_total, "per_uid": per_uid}


def main():
    base = score("data/eval/responses.json", "data/eval/verdicts", "BASELINE (live, old models)")
    new = score("data/eval/responses_new.json", "data/eval/verdicts_new", "REDESIGN (in-process, new models)")

    bp = base["passed"] / base["graded"] * 100
    np_ = new["passed"] / new["graded"] * 100
    print("\n############ HEADLINE ############")
    print(f"  pass rate: {bp:.1f}%  ->  {np_:.1f}%   ({np_-bp:+.1f} pts)")

    print("\ncategory pass-rate (baseline -> redesign):")
    cats = sorted(set(base["cat_total"]) | set(new["cat_total"]))
    for c in cats:
        bt = base["cat_total"].get(c, 0); nt = new["cat_total"].get(c, 0)
        bf = base["fail_by_cat"].get(c, 0); nf = new["fail_by_cat"].get(c, 0)
        bpass = (bt - bf) / bt * 100 if bt else 0
        npass = (nt - nf) / nt * 100 if nt else 0
        print(f"  {c:16s} {bpass:5.0f}% -> {npass:5.0f}%   (n={nt})")

    # movement: how many baseline-fails are now passes, and any regressions
    improved = regressed = 0
    for uid, nv in new["per_uid"].items():
        bv = base["per_uid"].get(uid)
        if bv is None:
            continue
        was_pass = bv in PASS
        now_pass = nv in PASS
        if now_pass and not was_pass:
            improved += 1
        elif was_pass and not now_pass:
            regressed += 1
    print(f"\nmovement: {improved} newly PASS, {regressed} regressed (was pass, now fail)")


if __name__ == "__main__":
    main()
