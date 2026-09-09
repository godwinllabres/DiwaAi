"""Offline eval for the RAG grounding gate (site + charter tiers).

The gate is the decision "may this retrieved passage be handed to the LLM as
evidence (AUGMENT), or quoted verbatim to the user (QUOTE)?". Until 2026-09 it
was an absolute coverage-weighted-cosine threshold (site_rag.AUGMENT_MIN_SCORE /
QUOTE_MIN_SCORE). That admitted "wow, you can answer questions like that?" at
0.126 (a GAD workshop page, parroted by the LLM with a citation — UAT
2026-09-02) and "can you answer questions?" at 0.214 with a bigram hit (quoted
VERBATIM on the no-LLM path), while rejecting "when does enrollment start" at
0.074. Score is not relevance. This harness measures any candidate gate on the
same three axes so the replacement is chosen by numbers, not taste:

  gold coverage   mirror_qa factual items (gold source_url in the corpus):
                  the gold document is among the AUGMENT-admitted site passages
  trap safety     mirror_qa 'trap' items + gold_eval out-of-scope items:
                  NOTHING is admitted (augment) / quoted (quote)
  probe safety    data/eval/grounding_probes.json — conversational, meta,
                  feedback, acknowledgement and other-tier utterances that
                  must never reach either corpus

Gate contract (pluggable, so candidates can be compared without touching the
serving code):

    gate(query: str, index, passages: list[Passage]) -> GateVerdict
    GateVerdict(augment=[Passage...] (best-first, <= 2), quote=Passage | None)

`passages` is index.retrieve(query, k=8) — the gate may re-rank, re-score or
reject; it must not fetch more. Pass --gate module:function to evaluate a
candidate; the default is the production gate in api.grounding_gate (or the
legacy absolute thresholds with --gate legacy).

Run:  python scripts/eval_grounding_gate.py [--gate legacy] [--json out.json] [--verbose]
Pure module run — no TensorFlow, no LLM, no server. ~20s on the 4.7k-chunk corpus.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from api import charter_rag, site_rag  # noqa: E402

MIRROR_QA = os.path.join(ROOT, "data", "eval", "mirror_qa.json")
PROBES = os.path.join(ROOT, "data", "eval", "grounding_probes.json")
GOLD_EVAL = os.path.join(ROOT, "training", "gold_eval.jsonl")
RETRIEVE_K = 8


@dataclass
class GateVerdict:
    augment: list = field(default_factory=list)   # passages handed to the LLM
    quote: Optional[object] = None                # passage quoted verbatim, if any
    reason: str = ""                              # free-text, for --verbose


Gate = Callable[[str, object, list], GateVerdict]


def legacy_site_gate(query: str, index, passages: list) -> GateVerdict:
    """The pre-2026-09 absolute thresholds, exactly as _gather_grounding and
    _verbatim_document_reply applied them to the site tier."""
    augment = [p for p in passages[:2] if p.score >= site_rag.AUGMENT_MIN_SCORE]
    quote = None
    if passages and passages[0].score >= site_rag.QUOTE_MIN_SCORE and passages[0].bigram_hits >= 1:
        quote = passages[0]
    return GateVerdict(augment=augment, quote=quote, reason="absolute-threshold")


def legacy_charter_gate(query: str, index, passages: list) -> GateVerdict:
    augment = [p for p in passages[:2] if p.score >= charter_rag.AUGMENT_MIN_SCORE]
    quote = None
    if passages and passages[0].score >= charter_rag.QUOTE_MIN_SCORE and passages[0].bigram_hits >= 1:
        quote = passages[0]
    return GateVerdict(augment=augment, quote=quote, reason="absolute-threshold")


def _production_gate(query: str, index, passages: list) -> GateVerdict:
    """api.grounding_gate.decide adapted to the harness contract."""
    from api import grounding_gate
    v = grounding_gate.decide(query, index, passages)
    return GateVerdict(augment=[ps for ps, _ in v.augment],
                       quote=v.quote[0] if v.quote else None, reason=v.reason)


def _load_gate(spec: str) -> tuple[Gate, Gate]:
    """'legacy' | 'api.grounding_gate' | 'module:function' | 'module' (site_gate/charter_gate)."""
    if spec == "legacy":
        return legacy_site_gate, legacy_charter_gate
    if spec == "api.grounding_gate":
        return _production_gate, _production_gate
    if ":" in spec:
        mod_name, fn_name = spec.split(":", 1)
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, fn_name)
        return fn, fn
    mod = importlib.import_module(spec)
    return getattr(mod, "site_gate"), getattr(mod, "charter_gate")


def _path(url: str) -> str:
    return urlsplit((url or "").strip()).path.strip("/").lower()


def _load_items():
    mirror = json.load(open(MIRROR_QA, encoding="utf-8"))
    corpus_paths = set()
    with open(site_rag._PATH, encoding="utf-8") as fh:
        for m in site_rag._DOC_MARKER_RE.finditer(fh.read()):
            corpus_paths.add(_path(m.group(2)))
    factual, traps = [], []
    for it in mirror:
        if it.get("category") == "trap":
            traps.append({"text": it["question"], "src": "mirror_qa:trap",
                          "expected": it.get("expected_intent", "")})
        elif _path(it.get("source_url", "")) in corpus_paths:
            factual.append({"text": it["question"], "gold": _path(it["source_url"]),
                            "lang": it.get("language", ""), "category": it.get("category", "")})
    with open(GOLD_EVAL, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("expected_behavior") == "refuse" or row.get("expected_intent") == "out_of_scope":
                traps.append({"text": row["question"], "src": "gold_eval:oos",
                              "expected": row.get("expected_intent", "")})
    probes = json.load(open(PROBES, encoding="utf-8"))["items"]
    return factual, traps, probes


def run(site_gate: Gate, charter_gate: Gate, verbose: bool = False) -> dict:
    site = site_rag.get_index()
    charter = charter_rag.get_index()
    if site is None:
        raise SystemExit("site index unavailable — docs/site_corpus.txt missing?")
    factual, traps, probes = _load_items()

    # ── gold coverage ────────────────────────────────────────────────────
    cov_hits, quote_hits, details = 0, 0, []
    for it in factual:
        ps = site.retrieve(it["text"], k=RETRIEVE_K)
        v = site_gate(it["text"], site, ps)
        admitted = [p for p in v.augment]
        hit = any(_path(p.url) == it["gold"] for p in admitted)
        qhit = v.quote is not None and _path(v.quote.url) == it["gold"]
        # Was the gold doc even retrievable (top-8) — separates gate loss from retrieval loss.
        retrievable = any(_path(p.url) == it["gold"] for p in ps)
        cov_hits += hit
        quote_hits += qhit
        details.append({"q": it["text"], "lang": it["lang"], "hit": hit, "quote_hit": qhit,
                        "retrievable": retrievable, "n_admitted": len(admitted),
                        "top": (ps[0].title, round(ps[0].score, 3)) if ps else None,
                        "reason": v.reason})
    retrievable_n = sum(1 for d in details if d["retrievable"])

    # ── trap + probe safety ──────────────────────────────────────────────
    def safety(items, key):
        aug_safe = quote_safe = aug_n = 0
        leaks = []
        for it in items:
            text = it[key]
            ps = site.retrieve(text, k=RETRIEVE_K)
            v = site_gate(text, site, ps)
            cps = charter.retrieve(text, k=RETRIEVE_K) if charter else []
            cv = charter_gate(text, charter, cps) if charter else GateVerdict()
            # allow_augment: underspecified topical asks — excerpts may reach
            # the LLM, only a verbatim quote counts as a leak.
            a_leak = (bool(v.augment) or bool(cv.augment)) and not it.get("allow_augment")
            q_leak = v.quote is not None or cv.quote is not None
            if not it.get("allow_augment"):
                aug_n += 1
                aug_safe += not a_leak
            quote_safe += not q_leak
            if a_leak or q_leak:
                what = []
                if v.augment:
                    what.append(f"site-augment {v.augment[0].title[:45]!r}@{v.augment[0].score:.3f}")
                if v.quote:
                    what.append(f"SITE-QUOTE {v.quote.title[:45]!r}@{v.quote.score:.3f}")
                if cv.augment:
                    what.append(f"charter-augment p{cv.augment[0].page}@{cv.augment[0].score:.3f}")
                if cv.quote:
                    what.append(f"CHARTER-QUOTE p{cv.quote.page}@{cv.quote.score:.3f}")
                leaks.append({"q": text, "leak": what, "kind": it.get("kind") or it.get("src")})
        return aug_safe, aug_n, quote_safe, leaks

    t_aug, t_aug_n, t_quote, t_leaks = safety(traps, "text")
    p_aug, p_aug_n, p_quote, p_leaks = safety(probes, "text")

    by_lang = {}
    for d in details:
        b = by_lang.setdefault(d["lang"] or "?", [0, 0])
        b[0] += d["hit"]
        b[1] += 1
    out = {
        "factual": {"n": len(factual), "retrievable_top8": retrievable_n,
                    "augment_coverage": cov_hits, "quote_coverage": quote_hits,
                    "augment_coverage_pct": round(100 * cov_hits / max(1, len(factual)), 1),
                    "quote_coverage_pct": round(100 * quote_hits / max(1, len(factual)), 1),
                    "augment_coverage_by_lang": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_lang.items())}},
        "traps": {"n": len(traps), "augment_n": t_aug_n, "augment_safe": t_aug, "quote_safe": t_quote,
                  "augment_safe_pct": round(100 * t_aug / max(1, t_aug_n), 1),
                  "quote_safe_pct": round(100 * t_quote / max(1, len(traps)), 1),
                  "leaks": t_leaks},
        "probes": {"n": len(probes), "augment_n": p_aug_n, "augment_safe": p_aug, "quote_safe": p_quote,
                   "augment_safe_pct": round(100 * p_aug / max(1, p_aug_n), 1),
                   "quote_safe_pct": round(100 * p_quote / max(1, len(probes)), 1),
                   "leaks": p_leaks},
    }
    if verbose:
        out["factual"]["details"] = details
    return out


HELDOUT = os.path.join(ROOT, "data", "eval", "grounding_heldout.json")


def _expected(item: dict, passage) -> bool:
    """Does this passage belong to the document the held-out item names?"""
    if item.get("expect_slug"):
        return item["expect_slug"].lower() in (getattr(passage, "url", "") or "").lower()
    if item.get("expect_pages"):
        return getattr(passage, "page", None) in set(item["expect_pages"])
    if item.get("expect_regex"):
        import re
        return bool(re.search(item["expect_regex"], passage.text))
    if item.get("expect_text"):
        return item["expect_text"].lower() in passage.text.lower()
    return False


def run_heldout(site_gate: Gate, charter_gate: Gate, verbose: bool = False) -> dict:
    """Score the held-out set (built from the critics' fresh utterances — never
    used to tune a threshold). Classes: never / noquote / gold / must_augment."""
    site = site_rag.get_index()
    charter = charter_rag.get_index()
    items = json.load(open(HELDOUT, encoding="utf-8"))["items"]
    gates = {"site": (site, site_gate), "charter": (charter, charter_gate)}

    def verdicts(text):
        out = {}
        for name, (idx, gate) in gates.items():
            if idx is None:
                continue
            ps = idx.retrieve(text, k=RETRIEVE_K)
            out[name] = (ps, gate(text, idx, ps))
        return out

    c = {"never": [0, 0, 0], "noquote": [0, 0], "gold": [0, 0, 0, 0, 0], "must_augment": [0, 0]}
    leaks, blocks = [], []
    for it in items:
        cls, text = it["cls"], it["text"]
        vs = verdicts(text)
        aug_leak = any(v.augment for _, v in vs.values())
        q_leak = any(v.quote is not None for _, v in vs.values())
        if cls == "never":
            c["never"][0] += 1
            c["never"][1] += not aug_leak
            c["never"][2] += not q_leak
            if q_leak or aug_leak:
                leaks.append({"cls": cls, "kind": it.get("kind"), "q": text, "quote": q_leak,
                              "what": [f"{n}:{(v.quote.title if hasattr(v.quote,'title') else 'p'+str(getattr(v.quote,'page',''))) if v.quote else ''}|aug={len(v.augment)}" for n, (_, v) in vs.items()]})
        elif cls == "noquote":
            c["noquote"][0] += 1
            c["noquote"][1] += not q_leak
            if q_leak:
                leaks.append({"cls": cls, "kind": it.get("kind"), "q": text, "quote": True,
                              "what": [f"{n}:{(v.quote.title if hasattr(v.quote,'title') else 'p'+str(getattr(v.quote,'page',''))) if v.quote else ''}" for n, (_, v) in vs.items() if v.quote]})
        elif cls == "gold":
            corpus = it.get("corpus", "site")
            if corpus not in vs:
                continue
            ps, v = vs[corpus]
            retrievable = any(_expected(it, p) for p in ps)
            hit = any(_expected(it, p) for p in v.augment)
            c["gold"][0] += 1
            c["gold"][1] += retrievable
            c["gold"][2] += hit
            if v.quote is not None:
                c["gold"][3] += 1
                c["gold"][4] += not _expected(it, v.quote)
            if retrievable and not hit:
                blocks.append({"q": text, "lang": it.get("lang"), "corpus": corpus, "reason": v.reason[:160]})
        elif cls == "must_augment":
            c["must_augment"][0] += 1
            c["must_augment"][1] += aug_leak
            if not aug_leak:
                blocks.append({"q": text, "lang": "", "corpus": "any", "reason": "followup-opener zeroed"})
    n, a, q = c["never"]
    gn, gr, gh, gq, gw = c["gold"]
    out = {
        "never": {"n": n, "augment_safe": a, "quote_safe": q,
                  "augment_safe_pct": round(100 * a / max(1, n), 1), "quote_safe_pct": round(100 * q / max(1, n), 1)},
        "noquote": {"n": c["noquote"][0], "quote_safe": c["noquote"][1],
                    "quote_safe_pct": round(100 * c["noquote"][1] / max(1, c["noquote"][0]), 1)},
        "gold": {"n": gn, "retrievable_top8": gr, "admitted": gh,
                 "admitted_pct_of_retrievable": round(100 * gh / max(1, gr), 1),
                 "quotes_issued": gq, "wrong_doc_quotes": gw},
        "must_augment": {"n": c["must_augment"][0], "admitted": c["must_augment"][1]},
        "leaks": leaks, "blocks": blocks,
    }
    return out


def print_heldout(h: dict) -> None:
    print("-" * 72)
    print(f"HELD-OUT never   n={h['never']['n']}  augment-safe={h['never']['augment_safe']} "
          f"({h['never']['augment_safe_pct']}%)  quote-safe={h['never']['quote_safe']} ({h['never']['quote_safe_pct']}%)")
    print(f"HELD-OUT noquote n={h['noquote']['n']}  quote-safe={h['noquote']['quote_safe']} ({h['noquote']['quote_safe_pct']}%)")
    g = h["gold"]
    print(f"HELD-OUT gold    n={g['n']}  retrievable@8={g['retrievable_top8']}  admitted={g['admitted']} "
          f"({g['admitted_pct_of_retrievable']}% of retrievable)  quotes={g['quotes_issued']} wrong-doc={g['wrong_doc_quotes']}")
    m = h["must_augment"]
    print(f"HELD-OUT opener-first-questions n={m['n']}  admitted={m['admitted']}")
    print("-" * 72)
    for l in h["leaks"]:
        if l.get("quote"):
            print(f"  HELD-OUT QUOTE LEAK [{l['cls']}/{l['kind']}] {l['q'][:70]!r}: {l['what']}")


def print_report(r: dict) -> None:
    f, t, p = r["factual"], r["traps"], r["probes"]
    print("=" * 72)
    print(f"GOLD COVERAGE   n={f['n']}  retrievable@8={f['retrievable_top8']}  "
          f"augment={f['augment_coverage']} ({f['augment_coverage_pct']}%)  "
          f"quote={f['quote_coverage']} ({f['quote_coverage_pct']}%)  "
          f"by-lang={f['augment_coverage_by_lang']}")
    print(f"TRAP SAFETY     n={t['n']}  augment-safe={t['augment_safe']}/{t['augment_n']} ({t['augment_safe_pct']}%)  "
          f"quote-safe={t['quote_safe']}/{t['n']} ({t['quote_safe_pct']}%)")
    print(f"PROBE SAFETY    n={p['n']}  augment-safe={p['augment_safe']}/{p['augment_n']} ({p['augment_safe_pct']}%)  "
          f"quote-safe={p['quote_safe']}/{p['n']} ({p['quote_safe_pct']}%)")
    print("=" * 72)
    for label, leaks in (("trap", t["leaks"]), ("probe", p["leaks"])):
        for l in leaks:
            print(f"  LEAK [{label}/{l['kind']}] {l['q'][:60]!r}: " + "; ".join(l["leak"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default="api.grounding_gate",
                    help="'legacy' | module (site_gate/charter_gate) | module:function")
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--verbose", action="store_true", help="include per-item factual details")
    ap.add_argument("--min-coverage", type=float, default=None, help="fail if augment coverage %% below")
    ap.add_argument("--min-probe-safe", type=float, default=None, help="fail if probe augment-safe %% below")
    args = ap.parse_args()
    try:
        site_gate, charter_gate = _load_gate(args.gate)
    except (ImportError, AttributeError) as exc:
        print(f"gate {args.gate!r} not loadable ({exc}); falling back to legacy", file=sys.stderr)
        site_gate, charter_gate = legacy_site_gate, legacy_charter_gate
    report = run(site_gate, charter_gate, verbose=args.verbose)
    report["gate"] = args.gate
    print_report(report)
    if os.path.exists(HELDOUT):
        report["heldout"] = run_heldout(site_gate, charter_gate)
        print_heldout(report["heldout"])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"wrote {args.json}")
    rc = 0
    if args.min_coverage is not None and report["factual"]["augment_coverage_pct"] < args.min_coverage:
        print(f"FAIL: coverage {report['factual']['augment_coverage_pct']} < {args.min_coverage}")
        rc = 1
    if args.min_probe_safe is not None and report["probes"]["augment_safe_pct"] < args.min_probe_safe:
        print(f"FAIL: probe safety {report['probes']['augment_safe_pct']} < {args.min_probe_safe}")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
