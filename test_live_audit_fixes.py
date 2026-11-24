"""Offline regression for the fixes from the 2026-11-24 live conversation audit
(docs/handoff/LIVE_AUDIT_2026-11-24.md).

Run: python test_live_audit_fixes.py

  - "who is the dean of CEIT?" → that one dean, not the 12-dean roster;
  - a curated answer identical to the previous reply is not served twice;
  - off-topic small talk ("help me with my python homework") is declined;
  - "what programs are offered there?" after a campus turn is a follow-up,
    grounded on that campus;
  - Filipino greeting/thanks get Filipino replies.

Stubbed on a __new__-built bot (as in test_followup_routing.py), so no model
artifacts or LLM are needed.
"""
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")

from api.hybrid_chatbot import HybridChatbot, NonsenseGate, ScopeGate

FAILURES = []

DEANS = ("Each CvSU college has its own dean. Here's the current list — just find your college:\n"
         "- CEIT (College of Engineering and Information Technology): Dr. Willie C. Buclatin\n"
         "- CON (College of Nursing): Dr. Evelyn M. Del Mundo\n"
         "- CVMBS (College of Veterinary Medicine and Biomedical Sciences): Dr. Rezin C. Bahia\n"
         "- COM (College of Medicine): Dr. Eleanor J. Galvez\n"
         "- CEMDS (College of Economics, Management and Development Studies): Dr. A. Econ\n"
         "- CTHM (College of Tourism and Hospitality Management): Dr. B. Tour\n\n"
         "Source: https://cvsu.edu.ph/contact-us-2/ — designations change, so please verify there.")


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f": {detail}"))
    if not cond:
        FAILURES.append(name)


class FakeLLM:
    def __init__(self):
        self.calls = []

    def ensure_available(self):
        return True

    def generate(self, msg, conversation_context=None):
        self.calls.append(msg)
        return f"Imus Campus offers several programs (note {len(self.calls)})."


def make_bot(intent="university_officials", llm=None):
    bot = HybridChatbot.__new__(HybridChatbot)
    bot.conversation_history = OrderedDict()
    bot.model_usage_stats = {}
    bot._MAX_HISTORY_SESSIONS = 50
    bot._MAX_HISTORY_TURNS = 50
    bot.nonsense_gate = NonsenseGate()
    bot.scope_gate = ScopeGate()
    bot.nb_model = None
    bot.nn_model = None
    bot.llm_provider = "ollama" if llm else "none"
    bot.llm = llm
    bot.responses_map = {
        "nlu_fallback": ["fb"], "llm_unavailable": ["down"],
        "college_deans": [DEANS],
        "university_officials": ["ROSTER: President and the five Vice Presidents."],
        "chitchat": ["Hi! I'm Sevi. Ask me about CvSU.", "Hello! Ask me about admissions."],
    }
    bot.intent = intent
    bot._nb_result = lambda u, uid: (bot.intent, 0.99, 0.9, {})
    bot._retrieval_topk = lambda u: None
    bot._intent_retrieval_result = lambda *a: None
    bot._place_resolver_result = lambda *a: None
    bot.gathered = []

    def gather(query, campus=None, scope_text=None):
        bot.gathered.append((query, campus, scope_text))
        return [], "", None
    bot._gather_grounding = gather
    return bot


# ── dean lookup ──────────────────────────────────────────────────────────
bot = make_bot()
_, reply, _, model, _ = bot.chat("who is the dean of CEIT?", user_id="d")
check("dean by abbreviation", model == "Dean Lookup" and "Dr. Willie C. Buclatin" in reply
      and "Del Mundo" not in reply, reply)
check("dean reply carries the roster source", "https://cvsu.edu.ph/contact-us-2/" in reply, reply)
_, reply, _, model, _ = bot.chat("sino ang dean ng nursing?", user_id="d2")
check("dean by subject word, Filipino", model == "Dean Lookup" and "Del Mundo" in reply
      and reply.startswith("Ang dekano"), reply)
_, reply, _, model, _ = bot.chat("who is the dean of veterinary medicine", user_id="d3")
check("specific subject word beats generic", "Bahia" in reply, reply)
_, _, _, model, _ = bot.chat("who is the dean", user_id="d4")
check("no college named → not the lookup", model != "Dean Lookup", model)
_, _, _, model, _ = bot.chat("what is the dean's list of CEIT", user_id="d5")
check("dean's list is not a dean question", model != "Dean Lookup", model)
for text in ("where is the CEIT dean's office?", "what is the email of the dean of nursing",
             "saan ang opisina ng dean ng CEIT"):
    _, _, _, model, _ = bot.chat(text, user_id="d6")
    check(f"not an identity question → not the lookup: {text!r}", model != "Dean Lookup", model)
_, _, _, model, _ = bot.chat("who are the deans of CEIT and nursing?", user_id="d7")
check("two colleges named → not the lookup", model != "Dean Lookup", model)
_, reply, _, model, _ = bot.chat("who is the dean of hospitality management", user_id="d8")
check("'management' inside 'hospitality management' is not a second college",
      model == "Dean Lookup" and "B. Tour" in reply, (model, reply))
_, reply, _, model, _ = bot.chat(
    "who is the dean of the College of Tourism and Hospitality Management", user_id="d9")
check("full college name resolves to one college", model == "Dean Lookup" and "B. Tour" in reply,
      (model, reply))

# ── curated repeat ───────────────────────────────────────────────────────
bot = make_bot("university_officials")
bot.chat("who is the president", user_id="o")
_, reply, _, _, _ = bot.chat("how about the vice presidents?", user_id="o")
check("identical curated answer not repeated", not reply.startswith("ROSTER"), reply)
bot.chat("who is the president", user_id="o2")
_, reply, _, _, _ = bot.chat("can you repeat that?", user_id="o2")
check("explicit repeat request still repeats", reply.startswith("ROSTER"), reply)
bot = make_bot("chitchat")
_, first, _, _, _ = bot.chat("what can you help with?", user_id="c")
_, second, _, _, _ = bot.chat("what can you do?", user_id="c")
check("small talk rotates to another variant", first != second and second in bot.responses_map["chitchat"],
      (first, second))

# ── off-topic small talk ─────────────────────────────────────────────────
bot = make_bot("chitchat")
_, reply, _, model, _ = bot.chat("can you help me with my python homework", user_id="h")
check("homework declined", model.startswith("ScopeGate"), (model, reply))
_, _, _, model, _ = bot.chat("what can you help with?", user_id="h2")
check("in-scope small talk still answered", model.startswith("Naive Bayes"), model)

# ── "there" = the session's campus ───────────────────────────────────────
bot = make_bot("courses_offered", llm=FakeLLM())
bot.responses_map["courses_offered"] = ["ALL COLLEGES"]
bot.record_turn("p", user_input="what about imus?", response="Imus Campus is in Imus City.",
                intent="x", confidence=1.0, model_used="Local LLM")
_, reply, _, model, nlu = bot.chat("what programs are offered there?", user_id="p")
check("'there' routes to the LLM as a follow-up", nlu.get("followup_kind") == "place"
      and "ALL COLLEGES" not in reply, (model, reply))
check("'there' grounds on the session campus", bot.gathered and bot.gathered[-1][1]
      and "Imus" in bot.gathered[-1][1], bot.gathered)
check("'there' is resolved in the LLM prompt", bot.llm.calls
      and '"there" = Imus' in bot.llm.calls[-1], bot.llm.calls[-1:])
bot = make_bot("courses_offered", llm=FakeLLM())
bot.responses_map["courses_offered"] = ["ALL COLLEGES"]
bot.record_turn("p2", user_input="how much is tuition", response="Free under RA 10931.",
                intent="tuition_fees", confidence=1.0, model_used="Naive Bayes")
_, _, _, _, nlu = bot.chat("what programs are offered there?", user_id="p2")
check("'there' without a campus in play is not a follow-up", nlu.get("followup_kind") != "place", nlu)

# ── two tabs of one user keep their own history ──────────────────────────
bot = make_bot("courses_offered", llm=FakeLLM())
bot.responses_map["courses_offered"] = ["ALL COLLEGES"]
bot.record_turn("tabA", user_input="what about trece?", response="Trece is in Trece Martires.",
                intent="x", confidence=1.0, model_used="Local LLM", session_id="tabA")
bot.record_turn("tabB", user_input="what about imus?", response="Imus Campus is in Imus City.",
                intent="x", confidence=1.0, model_used="Local LLM", session_id="tabB")
bot.chat("what programs are offered there?", user_id="same-user", session_id="tabA")
check("history keys on the session, not the shared user_id",
      bot.gathered and "Trece" in (bot.gathered[-1][1] or "") and "Imus" not in bot.gathered[-1][0],
      bot.gathered[-1:])
bot.chat("what programs are offered there?", user_id="same-user", session_id="tabA",
         campus="Trece Martires City Campus")
check("routed campus wins over history for the place query",
      "Trece Martires City Campus" in bot.gathered[-1][0], bot.gathered[-1:])

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
    raise SystemExit(1)
print("\nAll live-audit regression checks passed.")
