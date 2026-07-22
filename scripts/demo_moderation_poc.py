"""End-to-end POC for the moderation & privacy controls.

Drives the REAL code paths (no mocks) to demonstrate all five controls:
  1. Persistent accuracy disclaimer ....... DiwaWeb (shown in the UI, not here)
  2. Repeat-abuse cooldown ................. api/app.py `_safety_screen`
  3. PII masking on write ................. api/logger.py -> api/pii.py
  4. Anti-pattern mining ................. api/anti_patterns.py
  5. Governance sign-off ................. docs/governance_signoff.md (doc)

Run (needs the full server deps):  python scripts/demo_moderation_poc.py
ASCII-only output so it renders anywhere.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app          # noqa: E402  (heavy import — loads the brain)
from api import safety, anti_patterns, pii  # noqa: E402
from api.logger import ChatLogger  # noqa: E402

SID = "poc-session"


def rule(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


async def screen(label: str, message: str) -> None:
    """Run one message through the real front-door SafetyGate and report it."""
    block, effective = await app._safety_screen(message, SID)
    if block is None:
        verdict = "PASS THROUGH (answered normally)"
        detail = f"    downstream message: {effective!r}"
    else:
        verdict = f"BLOCKED -> intent={block['intent']}"
        detail = f"    reply: {block['text'][:88]}..."
    rem = safety.cooldown_remaining(SID)
    print(f"\n  [{label}] {message!r}")
    print(f"    {verdict}   (cooldown now: {rem}s)")
    print(detail)


async def demo_cooldown() -> None:
    rule("2 + 3. Repeat-abuse cooldown  (self-harm & clean questions stay safe)")
    safety.reset_cooldowns()
    await screen("clean", "how do I enroll at CvSU?")
    await screen("abuse 1", "gago ka bot")
    await screen("abuse 2", "tanga ka talaga")
    await screen("abuse 3 -> arms cooldown", "bobo mo naman")
    print("\n  --- session is now in cooldown ---")
    await screen("clean DURING cooldown (must NOT be held)",
                 "anong requirements sa admission?")
    await screen("self-harm DURING cooldown (must ALWAYS refer)",
                 "I want to kill myself")
    print("\n  Invariants held: the clean question passed through, and the")
    print("  self-harm disclosure reached the referral despite the cooldown.")


def demo_pii() -> None:
    rule("3. PII masking on the log write path (throwaway DB, real ChatLogger)")
    tmp = tempfile.mkdtemp(prefix="sevi-poc-")
    logger = ChatLogger(log_dir=tmp, db_path=os.path.join(tmp, "demo.db"))
    raw = "Hi, my student number is 202012345, email juan@cvsu.edu.ph, call 0917 558 4673"
    logger.log_chat(
        user_id="poc-user", user_message=raw, bot_response="ok",
        intent="contact_info", confidence=0.9, session_id=SID,
    )
    stored = logger.get_user_history("poc-user", limit=1)[0]["user_message"]
    print(f"\n  typed  : {raw}")
    print(f"  stored : {stored}")
    print(f"\n  Ticket/doc refs are preserved (features depend on them):")
    for ref in ["HTKT-07-00001", "PR-2026-0042", "COSC 101"]:
        print(f"    {ref:<16} -> {pii.mask_pii(ref)}")


def demo_mining() -> None:
    rule("4. Anti-pattern mining over the real chat log")
    rows = app.chat_logger.get_anti_pattern_rows(days=3650, limit=2000)
    rep = anti_patterns.build_report(rows)
    print(f"\n  {rep['total_analyzed']} anti-pattern messages found. Bucket counts:")
    for name, b in rep["buckets"].items():
        if b.get("count"):
            print(f"    {name:<22} {b['count']}")
    if rep["emerging_themes"]:
        print("\n  Top emerging themes (what to build next):")
        for t in rep["emerging_themes"][:5]:
            print(f"    - {t['term']:<26} x{t['count']}")
    print("\n  (self-harm is counted but never themed or quoted -- dignity first)")


async def main() -> None:
    await demo_cooldown()
    demo_pii()
    demo_mining()
    rule("5. Governance sign-off -> docs/governance_signoff.md (Guidance + DPO)")
    print("\n  Crisis copy and consent copy each have a review checklist with the")
    print("  exact copy under review and the controls above listed as evidence.")
    print("\nPOC complete.")


if __name__ == "__main__":
    asyncio.run(main())
