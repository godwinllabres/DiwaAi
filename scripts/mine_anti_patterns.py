"""Print the anti-pattern mining report from the chat logs.

Clusters recent unanswered fallbacks, off-topic / homework / comparison
refusals, safety trips, and low-confidence answers into emerging themes with
representative examples — the input to the monthly "what intents to add / what
lexicon gaps to close" review (docs/governance_signoff.md §4).

Intended for a periodic cron:
    # first of every month, 07:00
    0 7 1 * *  cd /srv/seviai && python scripts/mine_anti_patterns.py --days 30

Usage:
    python scripts/mine_anti_patterns.py [--days 30] [--limit 2000] [--json]

Reads the same DB the API uses (logs/chat_history.db, or DATABASE_URL). The
messages it reads are already PII-masked at write time (api/pii.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import anti_patterns  # noqa: E402
from api.logger import ChatLogger  # noqa: E402

_BUCKET_LABELS = {
    "unanswered_fallback": "Unanswered (fallback)",
    "off_topic": "Off-topic / refused",
    "low_confidence": "Low-confidence answers",
    "safety_abuse": "Abuse trips",
    "safety_threat": "Threat trips",
    "safety_self_harm": "Self-harm referrals (count only)",
    "safety_cooldown": "Repeat-abuse cooldowns",
}


def _print_human(report: dict) -> None:
    # ASCII-only so it renders in cron logs and cp1252 Windows consoles alike.
    print("=" * 70)
    print(f"  Sevi anti-pattern report - last {report.get('window_days', '?')} days")
    print(f"  {report['total_analyzed']} messages analyzed"
          f"  (low-confidence < {report['low_conf_threshold']})")
    print("=" * 70)

    for name, label in _BUCKET_LABELS.items():
        b = report["buckets"].get(name, {})
        count = b.get("count", 0)
        if not count:
            continue
        print(f"\n> {label}: {count}")
        for term in b.get("top_terms", []):
            print(f"    - {term['term']:<28} x{term['count']}")
        for ex in b.get("examples", []):
            print(f'      "{ex}"')

    emerging = report.get("emerging_themes", [])
    if emerging:
        print("\n" + "-" * 70)
        print("  Emerging themes to act on (fallback / off-topic / low-conf):")
        for t in emerging:
            print(f"    - {t['term']:<28} x{t['count']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Sevi anti-pattern log mining")
    ap.add_argument("--days", type=int, default=30, help="look-back window (days)")
    ap.add_argument("--limit", type=int, default=2000, help="max rows to analyze")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead")
    args = ap.parse_args()

    logger = ChatLogger(log_dir="logs", db_path="logs/chat_history.db")
    rows = logger.get_anti_pattern_rows(days=args.days, limit=args.limit)
    report = anti_patterns.build_report(rows)
    report["window_days"] = args.days

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
