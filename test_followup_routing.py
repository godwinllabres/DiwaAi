"""Offline regression for contextual follow-up routing + session slots.

Run: python test_followup_routing.py

Transcript failure (2026-11): after Sevi linked trece.cvsu.edu.ph the user
asked "why only trece?" and the cascade served the static list-of-campuses
intent. Follow-ups now go to the LLM with history, retrieving on the anchor
question (not on the campus the user objected to). Pins the detector's
negatives too, so standalone questions keep their curated answers.

No model artifacts or LLM needed: the classifiers, retrieval and the LLM are
stubbed on a __new__-built bot, as in test_conversation_recap.py.
"""
import os
import sys
import time
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")

from api import campus_context
from api.hybrid_chatbot import HybridChatbot, NonsenseGate, ScopeGate, _level_slot

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: {detail}")
        FAILURES.append(name)


class FakeLLM:
    def __init__(self, available=True):
        self.available = available
        self.calls = []

    def ensure_available(self):
        return self.available

    def generate(self, msg, conversation_context=None):
        self.calls.append((msg, conversation_context))
        # Distinct per call so the cross-turn repeat check never fires.
        return f"Enrollment follows the Main Campus procedure (note {len(self.calls)})."


def make_bot(available=True):
    bot = HybridChatbot.__new__(HybridChatbot)
    bot.conversation_history = OrderedDict()
    bot.model_usage_stats = {}
    bot._MAX_HISTORY_SESSIONS = 50
    bot._MAX_HISTORY_TURNS = 50
    bot.nonsense_gate = NonsenseGate()
    bot.scope_gate = ScopeGate()
    bot.nb_model = None
    bot.nn_model = None
    bot.llm_provider = "ollama"
    bot.responses_map = {"nlu_fallback": ["fb"], "llm_unavailable": ["down"]}
    bot.llm = FakeLLM(available)
    bot._nb_result = lambda u, uid: ("list_of_campuses", 0.99, 0.9, {})
    bot._retrieval_topk = lambda u: None
    bot._select_response = lambda i, u, l=None: f"CANNED:{i}"
    bot._intent_retrieval_result = lambda *a: None
    bot._place_resolver_result = lambda *a: None
    bot.gathered = []

    def gather(query, campus=None, scope_text=None):
        bot.gathered.append((query, campus, scope_text))
        return [], "", None
    bot._gather_grounding = gather
    return bot


def seed(bot, uid, turns):
    for user, reply, model in turns:
        bot.record_turn(uid, user_input=user, response=reply, intent="x",
                        confidence=1.0, model_used=model)


ENROLL = ("how can I enroll next year",
          "You can enroll online; see https://trece.cvsu.edu.ph for the steps.",
          "Local LLM (site-grounded)")
CONTENT = ("where is the registrar", "The Registrar is in the Admin Building.", "Naive Bayes (NLU Enhanced)")


def is_llm(model_used):
    return model_used.startswith("Local LLM")


# 1. Transcript regression ────────────────────────────────────────────────
bot = make_bot()
seed(bot, "u", [ENROLL])
intent, reply, _, model, nlu = bot.chat("why only trece?", user_id="u")
check("1 why only trece -> LLM", is_llm(model), model)
check("1 not the canned list", not reply.startswith("CANNED"), reply)
q, campus, scope_text = bot.gathered[-1]
check("1 gather query anchors on enroll", "enroll" in q.lower(), q)
check("1 gather query drops trece", "trece" not in q.lower(), q)
check("1 gather campus None", campus is None, campus)
msg, ctx = bot.llm.calls[-1]
check("1 literal follow-up is the question", msg.rstrip().endswith("Question: why only trece?"), msg[-80:])
check("1 context <= 8 messages and has prior turn",
      ctx and len(ctx) <= 8 and any("enroll" in m["content"] for m in ctx), ctx)
check("1 nlu marks follow-up", nlu.get("contextual_followup") and nlu.get("followup_kind") == "contrast", nlu)
check("1 counter bumped", bot.model_usage_stats.get("followup_routed_to_llm") == 1, bot.model_usage_stats)

# 2. Chained follow-up anchors on the real question ─────────────────────────
_, _, _, model, _ = bot.chat("what about Imus?", user_id="u")
q = bot.gathered[-1][0]
check("2 chained -> LLM", is_llm(model), model)
check("2 anchors on enroll + Imus", "enroll" in q.lower() and "imus" in q.lower(), q)
check("2 not anchored on the previous follow-up", "why only" not in q.lower(), q)

# 3. Detector negatives keep the curated path ─────────────────────────────
for i, text in enumerate(("why is tuition free at CvSU?", "what if I fail the entrance exam",
                          "so how do I enroll", "bakit mahal ang tuition",
                          "also where is the library", "no thanks", "thank you",
                          "how about you?", "where is the registrar")):
    bot = make_bot()
    seed(bot, f"n{i}", [CONTENT])
    _, reply, _, model, _ = bot.chat(text, user_id=f"n{i}")
    check(f"3 negative {text!r} not routed", not is_llm(model)
          and not bot.model_usage_stats.get("followup_routed_to_llm"), model)

# 3b. Openers followed by a topic of their own keep the curated path, and
# retrieval is not polluted with the previous question (review regressions).
for i, text in enumerate(("is it possible to shift courses", "is it true that tuition is free",
                          "are there night classes", "how about the dorm fees",
                          "what about scholarships for athletes", "and how much is the tuition",
                          "ano naman ang requirements sa scholarship",
                          "bakit hindi pa lumalabas ang grades ko",
                          "just give me the list of deans", "and where is the clinic",
                          "what about the CEIT building", "no, I mean the online programs")):
    bot = make_bot()
    seed(bot, f"t{i}", [CONTENT])
    _, reply, _, model, _ = bot.chat(text, user_id=f"t{i}")
    check(f"3b topic {text!r} not routed", not is_llm(model)
          and not bot.model_usage_stats.get("followup_routed_to_llm")
          and not any("registrar" in q.lower() for q, _, _ in bot.gathered), model)

# 4. Detector positives route to the LLM ───────────────────────────────────
for i, text in enumerate(("bakit trece lang?", "paano naman sa Imus?", "eh sa graduate school?",
                          "how about graduate school", "is that free?", "and the requirements?",
                          "just create a list available online")):
    bot = make_bot()
    seed(bot, f"p{i}", [ENROLL])
    _, reply, _, model, _ = bot.chat(text, user_id=f"p{i}")
    check(f"4 positive {text!r} -> LLM", is_llm(model)
          and bot.model_usage_stats.get("followup_routed_to_llm") == 1, model)

# 5. No history ───────────────────────────────────────────────────────────
bot = make_bot()
_, reply, _, model, _ = bot.chat("why only trece?", user_id="fresh")
check("5 no history -> canned", reply == "CANNED:list_of_campuses", model)

# 6. Stale history ────────────────────────────────────────────────────────
bot = make_bot()
seed(bot, "s", [ENROLL])
bot.conversation_history["s"][-1]["at"] = time.time() - 7200
_, reply, _, model, _ = bot.chat("what about Imus?", user_id="s")
check("6 stale history -> canned", reply == "CANNED:list_of_campuses", model)

# 7. Previous turn was a refusal / small talk / recap ───────────────────────
for label in ("ScopeGate (offtopic_keyword)", "Small Talk", "Conversation Recap"):
    bot = make_bot()
    seed(bot, "g", [ENROLL, ("blah", "Sorry, I can only help with CvSU.", label)])
    _, reply, _, model, _ = bot.chat("why?", user_id="g")
    check(f"7 after {label} -> not routed", not bot.model_usage_stats.get("followup_routed_to_llm"), model)

# 8. LLM unavailable ──────────────────────────────────────────────────────
bot = make_bot(available=False)
seed(bot, "d", [ENROLL])
_, reply, _, model, _ = bot.chat("why only trece?", user_id="d")
check("8 LLM down -> canned", reply == "CANNED:list_of_campuses", model)
check("8 counter not bumped", not bot.model_usage_stats.get("followup_routed_to_llm"), bot.model_usage_stats)

# 9. List references win ──────────────────────────────────────────────────
bot = make_bot()
seed(bot, "l", [("programs of CEIT", "1. BS Computer Science\n2. BS Information Technology",
                 "College Programs")])
_, reply, _, model, nlu = bot.chat("2", user_id="l")
check("9 list ref resolved", nlu.get("resolved_from") == "2"
      and bot.conversation_history["l"][-1]["user_message"] == "BS Information Technology", nlu)
check("9 list ref not LLM-routed", not bot.model_usage_stats.get("followup_routed_to_llm"), model)
bot = make_bot()
seed(bot, "l2", [CONTENT])
_, reply, _, model, _ = bot.chat("I mean 7", user_id="l2")
check("9 unresolved 'I mean 7' not LLM-routed",
      not bot.model_usage_stats.get("followup_routed_to_llm"), model)

# 10. Recap and small talk still win ───────────────────────────────────────
bot = make_bot()
seed(bot, "r", [ENROLL])
_, _, _, model, _ = bot.chat("what did we talk about", user_id="r")
check("10 recap wins", model == "Conversation Recap", model)
_, _, _, model, _ = bot.chat("tell me a joke", user_id="r")
check("10 small talk wins", model == "Small Talk", model)

# 11. Gates still run on the literal follow-up ─────────────────────────────
# These carry a topic of their own, so they are not follow-ups; on the LLM
# path (skip_intents, as Step 0.9 sets) the gates still refuse them.
for uid, text, want in (("m", "what about 2+2?", "ScopeGate"),
                        ("o", "what about UP Diliman?", "ScopeGate (other_school)")):
    bot = make_bot()
    seed(bot, uid, [CONTENT])
    check(f"11 {text!r} not a follow-up", bot._followup_route(text, uid) is None)
    _, _, _, model, _ = bot.predict(text, user_id=uid, skip_intents=True)
    check(f"11 {text!r} refused", model.startswith(want), model)

# 12. Contrastive campus mentions are not sticky ────────────────────────────
s = f"fu-{time.time()}"
check("12 'why only trece?' sets no campus", campus_context.resolve(s, "why only trece?").campus is None)
campus_context.resolve(s + "b", "taga Imus ako")
check("12 contrast keeps the earlier campus",
      campus_context.resolve(s + "b", "why only trece?").campus == "Imus City Campus")
check("12 'what about Imus?' still sticks",
      campus_context.resolve(s + "c", "what about Imus?").campus == "Imus City Campus")
check("12 'not trece' scopes nothing", campus_context.query_campus_key("not trece") is None)

# 13. Session slots + the stated assumption ─────────────────────────────────
bot = make_bot()
seed(bot, "gs", [("I want to apply to graduate school", "Sure.", "Naive Bayes (NLU Enhanced)")])
out = bot._maybe_state_assumption("X", "tuition_fees", "tuition fees", None, None, "gs")
check("13 graduate level -> Heads up note", out.startswith("Heads up:")
      and not out.startswith("Assuming the Main Campus"), out)
bot.record_turn("gs", user_input="tuition fees", response=out, intent="tuition_fees",
                confidence=1.0, model_used="Naive Bayes (NLU Enhanced)")
check("13 level note once per session",
      bot._maybe_state_assumption("X", "tuition_fees", "tuition fees", None, None, "gs") == "X")
bot = make_bot()
seed(bot, "fr", [("I'm an incoming freshman", "Welcome!", "Naive Bayes (NLU Enhanced)")])
check("13 undergraduate level -> no note",
      bot._maybe_state_assumption("X", "tuition_fees", "tuition fees", None, None, "fr") == "X")
bot = make_bot()
notes = 0
for _ in range(2):
    out = bot._maybe_state_assumption("X", "tuition_fees", "tuition fees", None, None, "fs")
    notes += out.startswith("Assuming the Main Campus")
    bot.record_turn("fs", user_input="tuition fees", response=out, intent="tuition_fees",
                    confidence=1.0, model_used="Naive Bayes (NLU Enhanced)")
check("13 main-campus assumption stated exactly once", notes == 1, notes)
bot = make_bot()
seed(bot, "cs", [("taga Imus ako", "Hello Imus!", "Naive Bayes (NLU Enhanced)")])
check("13 remembered campus -> no note",
      bot._maybe_state_assumption("X", "tuition_fees", "tuition fees", None, None, "cs") == "X")
check("13 'graduate with honors' sets no level", _level_slot("how to graduate with honors") is None)
check("13 'masteral' sets graduate", _level_slot("may masteral ba kayo") == "graduate")

# 14. _llm_context truncation + gate skipping ───────────────────────────────
bot = make_bot()
seed(bot, "c", [(f"question {i}", "word " * 600, "Naive Bayes (NLU Enhanced)") for i in range(5)])
ctx = bot._llm_context("c", turns=4)
check("14 four turns -> eight messages", len(ctx) == 8, len(ctx))
check("14 assistant content truncated",
      all(len(m["content"]) <= 602 for m in ctx if m["role"] == "assistant"),
      [len(m["content"]) for m in ctx])
seed(bot, "c", [("you idiot", "Let's keep it respectful.", "SafetyGate (abuse)")])
ctx = bot._llm_context("c", turns=4)
check("14 SafetyGate turn skipped", not any("idiot" in m["content"] for m in ctx), ctx)

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED")
    sys.exit(1)
print("\nALL PASSED")
