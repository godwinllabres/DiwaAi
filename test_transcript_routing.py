"""Routing gaps from the 2026-11-25 production transcript review.

Run: python test_transcript_routing.py

  - "can you summarize" was classified as the greeting; a bare summarize ask
    is now the conversation recap (Step 0.5);
  - "Reaklly? you;re so useless" burned an LLM call; non-profane frustration
    at Sevi now gets a curated reply in the small-talk tier (Step 0.6).

Both triggers are swept against every training pattern in
data/cavsu_intents.db: neither may claim a real question.
"""
import os
import sqlite3
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")

from api.hybrid_chatbot import HybridChatbot, NonsenseGate, ScopeGate
from api.smalltalk import is_frustration, smalltalk_reply

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f": {detail}"))
    if not cond:
        FAILURES.append(name)


RECAP = HybridChatbot._RECAP_RE

# ── recap trigger ────────────────────────────────────────────────────────
for text in ["can you summarize", "Can you summarize?", "summarize", "recap please",
             "could you summarize everything so far", "can you sum it up", "pakibuod",
             "pakibuod naman po", "i-summarize mo", "summarize our conversation"]:
    check(f"recap: {text!r}", RECAP.search(text), text)
for text in ["summarize the admission requirements", "can you summarize the enrollment steps",
             "recap of the scholarship rules", "summarize the tuition fees for BSIT"]:
    check(f"not recap (content ask): {text!r}", not RECAP.search(text), text)

# ── frustration trigger ──────────────────────────────────────────────────
for text in ["Reaklly? you;re so useless", "you're useless", "youre so dumb",
             "ur not helpful", "u r useless", "this bot is useless", "what a useless bot",
             "you suck", "walang kwenta", "wala kang silbi", "hindi ka nakakatulong",
             "you are of no use", "so useless"]:
    check(f"frustration: {text!r}", is_frustration(text), text)
for text in ["you're so helpful, thanks!", "you're useless, where is the registrar?",
             "is the library useless during finals", "how do I enroll",
             "this is useless information about tuition, what are the actual fees?",
             "useless", "really?"]:
    check(f"not frustration: {text!r}", not is_frustration(text), text)
reply = smalltalk_reply("Reaklly? you;re so useless")
check("frustration reply owns it and asks what they need",
      reply and reply.startswith("Sorry") and "cvsu.edu.ph" in reply, reply)
reply = smalltalk_reply("walang kwenta", filipino=True)
check("Filipino frustration reply", reply and reply.startswith("Pasensya"), reply)

# ── no training pattern is claimed by either trigger ────────────────────
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cavsu_intents.db")
with sqlite3.connect(db) as conn:
    rows = conn.execute(
        "SELECT i.tag, p.pattern_text FROM patterns p JOIN intents i ON i.id = p.intent_id"
    ).fetchall()
recap_hits = [(t, p) for t, p in rows if RECAP.search(p) and t != "conversation_recap"]
frustration_hits = [(t, p) for t, p in rows if is_frustration(p)]
check(f"recap claims none of {len(rows)} intent patterns", not recap_hits, recap_hits[:5])
check(f"frustration claims none of {len(rows)} intent patterns", not frustration_hits,
      frustration_hits[:5])

# ── end to end on a stubbed bot ──────────────────────────────────────────
bot = HybridChatbot.__new__(HybridChatbot)
bot.conversation_history = OrderedDict()
bot.model_usage_stats = {}
bot._MAX_HISTORY_SESSIONS = 50
bot._MAX_HISTORY_TURNS = 50
bot.nonsense_gate = NonsenseGate()
bot.scope_gate = ScopeGate()
bot.nb_model = bot.nn_model = bot.llm = None
bot.llm_provider = "none"
bot.responses_map = {"nlu_fallback": ["fb"], "llm_unavailable": ["down"],
                     "greeting": ["Hi! I'm Sevi, your CvSU virtual assistant."]}
bot._nb_result = lambda u, uid: ("greeting", 0.99, 0.9, {})
bot._retrieval_topk = lambda u: None
bot._intent_retrieval_result = lambda *a: None
bot._place_resolver_result = lambda *a: None
bot.record_turn("s", user_input="how do I enroll?", response="Steps...", intent="enrollment_procedure",
                confidence=1.0, model_used="Naive Bayes")
intent, reply, _, model, _ = bot.chat("can you summarize", session_id="s")
check("'can you summarize' → recap, not the greeting",
      model == "Conversation Recap" and "how do I enroll?" in reply, (model, reply))
intent, reply, _, model, _ = bot.chat("Reaklly? you;re so useless", session_id="s")
check("frustration → curated small talk, no LLM",
      model == "Small Talk" and reply.startswith("Sorry"), (model, reply))

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
    raise SystemExit(1)
print("\nAll transcript-routing checks passed.")
