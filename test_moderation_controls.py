"""Offline regression for the new moderation/privacy controls — run:
    python test_moderation_controls.py

Covers, without importing the full app or loading models:
  • api/pii.py        — PII masking (emails, phones, id numbers) + ref preserve
  • api/safety.py     — repeat-abuse cooldown primitives (arm / expire / isolate)
  • api/anti_patterns — bucketing + theme extraction over synthetic rows
See docs/governance_signoff.md, docs/moderation_plan.md §6.
"""
import time

from api import pii, safety, anti_patterns

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        got={got!r}\n        want={want!r}"
          if not ok else f"PASS  {name}")


def check_true(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# ── PII masking ──────────────────────────────────────────────────────────────
print("── PII masking ──")
check("email masked", pii.mask_pii("email me at juan.delacruz@cvsu.edu.ph please"),
      "email me at [email] please")
check("mobile masked", pii.mask_pii("my number is 09175584673"),
      "my number is [phone]")
check("spaced mobile masked", pii.mask_pii("call 0917 558 4673 daw"),
      "call [phone] daw")
check("landline masked", pii.mask_pii("hotline (046) 430-6332 po"),
      "hotline [phone] po")
check("dashed landline masked", pii.mask_pii("hotline 046-430-0175 po"),
      "hotline [phone] po")          # 3-group 0XX-XXX-XXXX (regression: was leaking)
check("spaced landline masked", pii.mask_pii("call 046 430 0175 daw"),
      "call [phone] daw")            # 3-group spaced (regression: leaked area code)
check("student number masked", pii.mask_pii("student number ko ay 202012345"),
      "student number ko ay [id]")
# References that must be PRESERVED (features depend on them; not PII):
check("ticket ref preserved", pii.mask_pii("track HTKT-07-00001 please"),
      "track HTKT-07-00001 please")
check("doc ref preserved", pii.mask_pii("where is PR-2026-0042"),
      "where is PR-2026-0042")
check("subject code preserved", pii.mask_pii("prereq of COSC 101?"),
      "prereq of COSC 101?")
check("short ticket num preserved", pii.mask_pii("my request #12345 status"),
      "my request #12345 status")
check("year preserved", pii.mask_pii("enrollment for 2026 when"),
      "enrollment for 2026 when")
check_true("idempotent", pii.mask_pii(pii.mask_pii("id 202012345")) == "id [id]")


# ── Repeat-abuse cooldown ────────────────────────────────────────────────────
print("\n── Cooldown primitives ──")
safety.reset_cooldowns()
SID = "sess-A"
# Below threshold (default 3): no cooldown yet.
safety.note_abuse(SID)
safety.note_abuse(SID)
check("2 trips → no cooldown", safety.cooldown_remaining(SID), 0)
# Third trip arms it.
safety.note_abuse(SID)
check_true("3 trips → cooldown armed", safety.cooldown_remaining(SID) > 0)
check_true("cooldown ≈ configured seconds",
           safety.cooldown_remaining(SID) <= int(safety._COOLDOWN_SECONDS) + 1)
# Other sessions are isolated.
check("other session unaffected", safety.cooldown_remaining("sess-B"), 0)
# None session is a no-op (anonymous single-shot can't be tracked).
safety.note_abuse(None)
check("None session no-op", safety.cooldown_remaining(None), 0)
# Expiry is pruned on read.
safety._cooldown_until[SID] = time.time() - 1
check("expired cooldown → 0", safety.cooldown_remaining(SID), 0)
check_true("expired entry pruned", SID not in safety._cooldown_until)
# Response copy names the wait.
check_true("response mentions seconds", "20 second" in safety.cooldown_response(20))
# Counter incremented on the transition into cooldown.
safety.reset_cooldowns()
before = safety._stats.get("cooldown", 0)
for _ in range(3):
    safety.note_abuse("sess-C")
check("cooldown counter +1 on arm", safety._stats.get("cooldown", 0), before + 1)
# Re-arming an already-active cooldown does NOT double-count.
safety.note_abuse("sess-C")
check("re-arm does not double-count", safety._stats.get("cooldown", 0), before + 1)

# snapshot exposes the new fields
snap = safety.snapshot()
check_true("snapshot has cooldown block", "cooldown" in snap and "active_sessions" in snap["cooldown"])
check_true("snapshot reports pii_masking", snap.get("pii_masking") is True)


# ── Anti-pattern mining ──────────────────────────────────────────────────────
print("\n── Anti-pattern report ──")
rows = [
    # unanswered fallbacks clustering on a real theme
    {"user_message": "how do I request my diploma copy", "intent": "nlu_fallback", "confidence": 0.2},
    {"user_message": "where to request diploma", "intent": "nlu_fallback", "confidence": 0.1},
    {"user_message": "diploma request steps po", "intent": "fallback", "confidence": 0.3},
    # off-topic
    {"user_message": "write me a poem about love", "intent": "out_of_scope", "confidence": 1.0},
    {"user_message": "do my math homework", "intent": "off_topic_homework", "confidence": 1.0},
    # low confidence (answered but unsure)
    {"user_message": "shifting to another program", "intent": "shifting_program", "confidence": 0.31},
    # safety
    {"user_message": "gago ka bot", "intent": "safety_abuse", "confidence": 1.0},
    {"user_message": "I want to kill myself", "intent": "safety_self_harm", "confidence": 1.0},
    {"user_message": "take a break message", "intent": "safety_cooldown", "confidence": 1.0},
    # a normal answered message that must be IGNORED
    {"user_message": "what programs are offered", "intent": "courses_offered", "confidence": 0.95},
]
rep = anti_patterns.build_report(rows, low_conf_threshold=0.5)
check("total analyzed excludes the answered row", rep["total_analyzed"], 9)
check("fallback bucket count", rep["buckets"]["unanswered_fallback"]["count"], 3)
check("off_topic bucket count", rep["buckets"]["off_topic"]["count"], 2)
check("low_confidence bucket count", rep["buckets"]["low_confidence"]["count"], 1)
check("abuse bucket count", rep["buckets"]["safety_abuse"]["count"], 1)
check("self_harm counted", rep["buckets"]["safety_self_harm"]["count"], 1)
# self-harm must be count-only: no examples, no themes
check_true("self_harm not themed/quoted",
           "examples" not in rep["buckets"]["safety_self_harm"]
           and "top_terms" not in rep["buckets"]["safety_self_harm"])
# 'diploma' should surface as a fallback theme term
fb_terms = {t["term"] for t in rep["buckets"]["unanswered_fallback"]["top_terms"]}
check_true("'diploma' surfaces as a theme", "diploma" in fb_terms)
# emerging themes only draw from fallback/off-topic/low-conf (never abuse text)
emerging_terms = {t["term"] for t in rep["emerging_themes"]}
check_true("emerging themes exclude abuse tokens", "gago" not in emerging_terms)

# PII mask placeholders must NOT surface as themes (regression: 'id'/'email'
# inner-words were leaking through the dead startswith('[') guard).
rows_masked = [
    {"user_message": "my student number is [id] and email [email] po", "intent": "nlu_fallback", "confidence": 0.2},
    {"user_message": "student number [id] verification", "intent": "nlu_fallback", "confidence": 0.2},
]
rep_m = anti_patterns.build_report(rows_masked)
terms_m = {t["term"] for t in rep_m["buckets"]["unanswered_fallback"]["top_terms"]}
check_true("mask placeholder words not themed",
           "id" not in terms_m and "email" not in terms_m and "phone" not in terms_m)
check_true("real words still themed past masks", "student" in terms_m and "number" in terms_m)


print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
