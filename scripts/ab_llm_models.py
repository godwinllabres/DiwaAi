"""A/B two (or more) Ollama models on the gold eval — ADR-004a.

Motivation (2026-08 research synthesis): FilBench evidence puts SEA-LION
v3.5 8B on the efficiency frontier for Filipino at this size class, but the
standing rule is that model swaps are EVAL-GATED, never vibes-gated. This
script is that gate: it hot-swaps the responding LLM through the existing
POST /admin/llm endpoint, replays training/run_gold_eval.py per model, and
prints a side-by-side with per-language slices so the Filipino/Taglish
deltas — the reason to consider SEA-LION at all — are visible, not averaged
away. The previously configured provider/model is restored afterward.

Prereqs: a running API with DASHBOARD_PIN set, and each candidate pulled in
Ollama first (e.g. `ollama pull aisingapore/Llama-SEA-LION-v3.5-8B-R`).

Usage:
    python scripts/ab_llm_models.py \
        --base-url http://127.0.0.1:8009 \
        --models qwen3:8b aisingapore/Llama-SEA-LION-v3.5-8B-R

    # PIN comes from --pin or the DASHBOARD_PIN env var.

Informational by design: exit is non-zero only when a run ERRORS, not when a
model loses — adopting the winner stays a human decision recorded in the
handoff/ADR, with thresholds enforced by CI's own gold-eval step.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _api(base_url: str, path: str, pin: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json", "X-Admin-Pin": pin},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 — local server
        return json.loads(r.read().decode("utf-8"))


def _slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", model)


def _run_eval(base_url: str, eval_file: str | None, out_path: Path) -> int:
    cmd = [sys.executable, str(REPO / "training" / "run_gold_eval.py"),
           "--base-url", base_url,
           # Informational run: thresholds off, the JSON carries the numbers.
           "--min-accuracy", "0", "--max-oos-accept", "1",
           "--json-out", str(out_path)]
    if eval_file:
        cmd += ["--eval-file", eval_file]
    return subprocess.run(cmd, cwd=REPO, check=False).returncode


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8009")
    p.add_argument("--provider", default="ollama")
    p.add_argument("--models", nargs="+", required=True,
                   help="two or more Ollama model names to compare")
    p.add_argument("--pin", default=os.environ.get("DASHBOARD_PIN", ""),
                   help="admin PIN (default: DASHBOARD_PIN env)")
    p.add_argument("--eval-file", default=None,
                   help="override the gold eval set (default: training/gold_eval.jsonl)")
    p.add_argument("--out-dir", default=str(REPO / "logs" / "ab_llm"))
    args = p.parse_args()

    if not args.pin:
        print("No admin PIN — pass --pin or set DASHBOARD_PIN.", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    original = _api(args.base_url, "/admin/llm", args.pin)
    print(f"current LLM: provider={original.get('provider')} "
          f"model={original.get('model')} (will be restored)")

    runs: dict[str, dict] = {}
    failed = False
    try:
        for model in args.models:
            print(f"\n=== {model} ===")
            status = _api(args.base_url, "/admin/llm", args.pin,
                          {"provider": args.provider, "model": model})
            if not status.get("available"):
                print(f"  !! provider reports unavailable for {model} "
                      f"(pulled in Ollama?) — skipping")
                failed = True
                continue
            out_path = out_dir / f"gold_{_slug(model)}.json"
            rc = _run_eval(args.base_url, args.eval_file, out_path)
            if rc not in (0, 1):  # 1 = thresholds (disabled) — anything else is an error
                print(f"  !! eval errored (rc={rc}) for {model}")
                failed = True
                continue
            runs[model] = json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        # Best-effort restore of whatever was configured before the A/B.
        try:
            _api(args.base_url, "/admin/llm", args.pin,
                 {"provider": original.get("provider") or "none",
                  "model": original.get("model")})
        except Exception as exc:  # noqa: BLE001
            print(f"  !! could not restore original LLM config: {exc}", file=sys.stderr)
            failed = True

    if len(runs) < 2:
        print("\nFewer than two successful runs — nothing to compare.")
        return 1 if failed else 0

    # ── Side-by-side ────────────────────────────────────────────────────
    langs = sorted({lang for r in runs.values()
                    for lang in r["metrics"].get("per_language", {})})
    rows = ([("intent accuracy", "intent_accuracy"),
             ("OOS false-accept", "oos_false_accept"),
             ("citation rate", "citation_rate"),
             ("clarify-or-assumption", "clarify_or_assumption_rate")]
            + [(f"accuracy [{lang}]", ("per_language", lang)) for lang in langs])

    name_w = max(len(m) for m in runs) + 2
    print("\n" + "=" * (26 + name_w * len(runs)))
    print("  GOLD EVAL A/B — LLM tier")
    print("=" * (26 + name_w * len(runs)))
    print(f"{'metric':24}" + "".join(f"{m:>{name_w}}" for m in runs))
    for label, key in rows:
        vals = []
        for r in runs.values():
            m = r["metrics"]
            v = (m.get(key[0], {}).get(key[1]) if isinstance(key, tuple)
                 else m.get(key))
            vals.append("--" if v is None else f"{v:.3f}")
        print(f"{label:24}" + "".join(f"{v:>{name_w}}" for v in vals))
    llm_share = {m: sum(v for k, v in r["metrics"]["tier_distribution"].items()
                        if "llm" in k.lower())
                 for m, r in runs.items()}
    print(f"{'LLM-tier answers':24}"
          + "".join(f"{llm_share[m]:>{name_w}}" for m in runs))

    best = max(runs, key=lambda m: (runs[m]["metrics"]["intent_accuracy"],
                                    -runs[m]["metrics"]["oos_false_accept"]))
    print(f"\nleader on accuracy/OOS: {best}")
    print("Adopt only via OLLAMA_MODEL in the deployment env + a handoff note; "
          "this script changes nothing permanently.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
