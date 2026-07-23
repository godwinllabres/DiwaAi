"""Offline regression for the Conversation Recap tier + 2026-07 gate fixes.

Run: python test_conversation_recap.py

Two-sided, like test_safety_gate.py: every recap phrasing must be answered
from session history (never by chitchat or the LLM), and every content ask
that merely contains "summarize"/"discussed" must fall through untouched.
Also pins the NonsenseGate/ScopeGate fixes: Filipino particles and short
social tokens are not refused, and "how much is the tuition" is not math.

No model artifacts or LLM needed — the recap tier and both gates are pure
Python, so the tier method and gates are exercised directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")

from api.hybrid_chatbot import HybridChatbot, NonsenseGate, ScopeGate

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: {detail}")
        FAILURES.append(name)


def make_bot():
    """A bot instance without loading any model artifacts."""
    bot = HybridChatbot.__new__(HybridChatbot)
    bot.conversation_history = {}
    bot.model_usage_stats = {"conversation_recap_used": 0}
    return bot


def seed(bot, user_id, questions):
    bot.conversation_history[user_id] = [
        {"user_message": q, "bot_response": "…", "intent": "x",
         "confidence": 0.9, "model_used": "t", "session_id": None,
         "entities": {}, "is_follow_up": False}
        for q in questions
    ]


# ---- recap phrasings must be caught -------------------------------------
RECAP_ASKS = [
    "can you summarize our conversation",
    "summarize this conversation please",
    "Summarise our chat",
    "recap our conversation",
    "summarize what I asked you",
    "summarize what we discussed",
    "what did we talk about so far",
    "what did we discuss",
    "what have we discussed so far",
    "what did I ask you",
    "ano ang napag-usapan natin",
    "anong pinag-usapan natin kanina",
    "buod ng usapan natin",
]

# ---- content asks that must NOT be caught -------------------------------
CONTENT_ASKS = [
    "can you summarize the admission requirements",
    "summarize the enrollment procedure",
    "summarize the student handbook",
    "what did the registrar say about form 137",
    "recap of the academic calendar",
    "what should I ask the registrar",
    "what are the requirements we need for enrollment",
    "buod ng history ng CvSU",
    "ano ang requirements sa enrollment",
    "what do we need to enroll",
]


def test_recap_phrasings_match():
    bot = make_bot()
    seed(bot, "u1", ["what are the admission requirements"])
    for ask in RECAP_ASKS:
        out = bot._conversation_recap_result(ask, "u1")
        check(f"recap catches: {ask!r}", out is not None)


def test_content_asks_fall_through():
    bot = make_bot()
    seed(bot, "u1", ["what are the admission requirements"])
    for ask in CONTENT_ASKS:
        out = bot._conversation_recap_result(ask, "u1")
        check(f"recap ignores: {ask!r}", out is None, f"matched: {out!r}")


def test_empty_history_has_canned_reply():
    bot = make_bot()
    out = bot._conversation_recap_result("what did we talk about so far", "new-user")
    check("empty history: reply exists", out is not None)
    check("empty history: says nothing discussed yet", "haven't discussed" in out, out)
    out = bot._conversation_recap_result("what did we talk about so far", None)
    check("no user_id: still replies, never crashes", out is not None)


def test_recap_lists_prior_questions_verbatim():
    bot = make_bot()
    qs = ["what are the admission requirements",
          "how much is the tuition fee for BSIT",
          "is there a scholarship for freshmen"]
    seed(bot, "u1", qs)
    out = bot._conversation_recap_result("summarize our conversation", "u1")
    for q in qs:
        check(f"recap includes: {q!r}", q in out, out)
    check("recap is numbered", "1. " in out and "3. " in out, out)


def test_recap_masks_pii():
    bot = make_bot()
    seed(bot, "u1", ["my student number is 202112345 what is my enrollment status"])
    out = bot._conversation_recap_result("what did I ask you", "u1")
    check("recap masks id numbers", "202112345" not in out, out)


def test_prior_recaps_excluded_and_capped_at_10():
    bot = make_bot()
    seed(bot, "u1", [f"question number {i}" for i in range(1, 15)])
    bot.conversation_history["u1"].append(
        {"user_message": "summarize our conversation", "bot_response": "…",
         "intent": HybridChatbot.RECAP_INTENT, "confidence": 1.0,
         "model_used": "Conversation Recap", "session_id": None,
         "entities": {}, "is_follow_up": False})
    out = bot._conversation_recap_result("what did we talk about so far", "u1")
    check("prior recap turn excluded", "summarize our conversation" not in out, out)
    check("caps at 10 questions", "question number 5" in out
          and "question number 4" not in out, out)
    check("says earlier questions omitted", "omitted" in out, out)


def test_tagalog_ask_gets_tagalog_reply():
    bot = make_bot()
    seed(bot, "u1", ["magkano ang tuition"])
    out = bot._conversation_recap_result("ano ang napag-usapan natin", "u1")
    check("tagalog header", out.startswith("Narito"), out)


# ---- gate fixes ----------------------------------------------------------
def test_gate_fixes():
    ng, sg = NonsenseGate(), ScopeGate()
    for msg in ["po", "opo", "oo", "ty", "thx", "sup", "yo", "gm",
                "lol", "wow", "bye", "thanks"]:
        check(f"NonsenseGate allows {msg!r}", ng.allows(msg)[0], ng.allows(msg)[1])
    for msg in ["How much is the tuition?", "how much is the tuition fee for BSIT",
                "how much is the entrance exam fee"]:
        check(f"ScopeGate allows {msg!r}", sg.allows(msg)[0], sg.allows(msg)[1])
    # The strictness the gates exist for must survive the loosening.
    for msg, gate, why in [
        ("qq", ng, "two-letter junk still too_short"),
        ("fgbhnjk", ng, "keysmash still blocked"),
        ("asdfasdf", ng, "keyboard walk still blocked"),
        ("how much is 12 * 5", sg, "actual arithmetic still blocked"),
        ("what is 2+2", sg, "math question still blocked"),
        ("solve x + 3 = 7", sg, "equation still blocked"),
    ]:
        check(f"gate still blocks {msg!r}", not gate.allows(msg)[0], why)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print()
    if FAILURES:
        print(f"FAIL  {len(FAILURES)} failure(s)")
        sys.exit(1)
    print("ALL PASS")
