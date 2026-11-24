"""Offline regression for the LLM-tier reply polish (Tier 3 de-leak + pushback).

Run: python test_llm_reply_polish.py

Drives HybridChatbot.predict() Step 3 with a stub bot and a scripted fake LLM:
retrieval boilerplate ("Based on the excerpts provided, ...") is scrubbed, the
refusal token and the output guard still win, an orphaned "According to," left
by the guard's citation strip is cleaned, a reply repeating the previous turn
pivots instead, and a live-lookup pushback ("just create a list available
online") is answered without a generation. Also pins the scope-locked and
grounded prompts' safety rules. No model artifacts or LLM server needed.
"""
import os
import sys
import time
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")

import api.hybrid_chatbot as hc
from api.hybrid_chatbot import HybridChatbot, NonsenseGate, ScopeGate

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: {detail}")
        FAILURES.append(name)


SITE_CITE = "Admission — official CvSU site (https://cvsu.edu.ph/admission/)"


class FakeLLM:
    available = True

    def __init__(self, reply=""):
        self.reply = reply
        self.calls = []

    def ensure_available(self):
        return True

    def generate(self, msg, conversation_context=None):
        self.calls.append((msg, conversation_context))
        return self.reply


def make_bot(reply=""):
    bot = HybridChatbot.__new__(HybridChatbot)
    bot.conversation_history = OrderedDict()
    bot.model_usage_stats = {}
    bot.nonsense_gate = NonsenseGate()
    bot.scope_gate = ScopeGate()
    bot.llm_provider = "ollama"
    bot.llm = FakeLLM(reply)
    bot.responses_map = {
        HybridChatbot.FALLBACK_INTENT: ["fallback"],
        HybridChatbot.LLM_UNAVAILABLE_INTENT: ["unavailable"],
    }
    bot._gather_grounding = lambda q, campus=None, scope_text=None: (
        [(0.5, SITE_CITE, "Applicants submit Form 1 to the Admission Office.", "site")],
        " (site-grounded)", None)
    return bot


def seed(bot, user_id, question, reply):
    bot.conversation_history[user_id] = [{
        "user_message": question, "bot_response": reply, "intent": "x",
        "confidence": 0.0, "model_used": "Local LLM", "session_id": None,
        "entities": {}, "is_follow_up": False,
    }]


def ask(bot, text, user_id="u1"):
    return bot.predict(text, user_id=user_id, skip_intents=True)


# ---- boilerplate scrubbed, citation kept ------------------------------------
bot = make_bot(f"Based on the excerpts provided, applicants submit Form 1 [{SITE_CITE}].")
_, resp, _, used, _ = ask(bot, "how do I apply for admission")
check("lead-in stripped", resp.startswith("Applicants submit Form 1"), resp)
check("citation kept", f"[{SITE_CITE}]" in resp, resp)
check("label is Local LLM", used.startswith("Local LLM"), used)

# ---- refusal protocol intact ------------------------------------------------
bot = make_bot(hc.LLM_REFUSAL_TOKEN)
_, resp, _, used, _ = ask(bot, "how do I apply for admission")
check("refusal token -> out-of-scope", "(out-of-scope)" in used, used)
check("refusal text is ScopeGate's", resp in ScopeGate.REFUSAL_MESSAGES, resp)

# ---- invented citation stripped, no orphaned lead-in -----------------------
bot = make_bot("According to [CvSU Citizens' Charter 2099, p. 1], applicants submit Form 1.")
_, resp, _, used, _ = ask(bot, "how do I apply for admission")
check("invented citation stripped", "2099" not in resp, resp)
check("no orphaned According to", not resp.lower().startswith("according to"), resp)
check("orphan cleanup keeps answer", resp == "Applicants submit Form 1.", resp)

# ---- output guard intact ----------------------------------------------------
bot = make_bot("Email admissions@fake-cvsu.example.com to apply.")
_, resp, _, used, _ = ask(bot, "how do I apply for admission")
check("invented email withheld", resp == HybridChatbot.LLM_GUARD_MESSAGE, resp)

# ---- duplicated block inside one reply --------------------------------------
block = ("- Visit the Office of Admission\n- Check https://cvsu.edu.ph\n"
         "- Ask the Registrar for the schedule")
bot = make_bot(f"{block}\n\n{block}")
_, resp, _, used, _ = ask(bot, "how do I apply for admission")
check("in-reply duplicate dropped", resp == block, resp)

# ---- repeat of previous turn pivots ----------------------------------------
R = ("I only have enrollment details for the Trece Martires City Campus. Please check "
     "https://cvsu.edu.ph or contact the Office of the University Registrar.")
bot = make_bot(R)
seed(bot, "u1", "how can I enroll next year", R)
_, resp, _, used, _ = ask(bot, "why only trece?")
check("repeat -> pivot label", used.endswith("(repeat-pivot)"), used)
check("repeat pivot keeps LLM_LOCAL prefix", used.startswith("Local LLM"), used)
check("repeat pivot text", resp != R and "https://cvsu.edu.ph" in resp, resp)

bot = make_bot(R)
seed(bot, "u1", "how can I enroll next year", R)
_, resp, _, used, _ = ask(bot, "can you repeat that")
check("explicit repeat served", resp == R, f"{used}: {resp}")

bot = make_bot(R)
seed(bot, "u1", "how can I enroll next year", R)
_, resp, _, used, _ = ask(bot, "how can I enroll next year")
check("same question re-asked served", resp == R, f"{used}: {resp}")

# ---- live-lookup pushback: no generation ------------------------------------
bot = make_bot("should not be generated")
seed(bot, "u1", "list of deans", R)
_, resp, _, used, _ = ask(bot, "just create a list available online")
check("pushback pivot text", "offline assistant" in resp, resp)
check("pushback label", used.startswith("Local LLM") and used.endswith("(pushback)"), used)
check("pushback skipped LLM", bot.llm.calls == [], bot.llm.calls)

bot = make_bot("Admission requirements are listed on the Admission page.")
_, resp, _, used, _ = ask(bot, "just create a list available online")
check("pushback with no previous turn reaches LLM", len(bot.llm.calls) == 1, used)

bot = make_bot("The Admission page lists the requirements.")
seed(bot, "u1", "list of deans", R)
_, resp, _, used, _ = ask(bot, "what is on the website for admission")
check("website question still reaches LLM", len(bot.llm.calls) == 1, used)

# ---- topic clarifications that mention "online" are not live lookups --------
for text in ("no, I mean the online programs", "i mean online enrollment",
             "just the online application steps", "i meant the CvSU website address",
             "no, the live streaming of graduation"):
    bot = make_bot("Online programs are listed on the Admission page.")
    seed(bot, "u1", "list of deans", R)
    _, resp, _, used, _ = ask(bot, text)
    check(f"clarification {text!r} reaches LLM", len(bot.llm.calls) == 1
          and not used.endswith("(pushback)"), used)

# ---- stale previous turn: no pushback shortcut -------------------------------
bot = make_bot("Admission requirements are listed on the Admission page.")
seed(bot, "u1", "list of deans", R)
bot.conversation_history["u1"][-1]["at"] = time.time() - 7200
_, resp, _, used, _ = ask(bot, "just create a list available online")
check("stale pushback reaches LLM", len(bot.llm.calls) == 1, used)

# ---- repeated prose refusal keeps its redirect -------------------------------
CLINIC = ("I'm sorry, I can't give medical advice. Please visit the University Health "
          "Services clinic on campus for a check-up.")
bot = make_bot(CLINIC)
seed(bot, "u1", "my tooth hurts what should I do", CLINIC)
bot.conversation_history["u1"][-1]["model_used"] = "Ollama (out-of-scope)"
_, resp, _, used, _ = ask(bot, "I have a headache what pill should I take")
check("repeated refusal keeps redirect", resp == CLINIC, resp)
check("repeated refusal labelled out-of-scope", used == "Ollama (out-of-scope)", used)

# ---- same-shape answer for a different campus is served ----------------------
IMUS = "At Imus campus, the enrollment fee is paid at the Cashier's Office in the Administration Building."
BACOOR = IMUS.replace("Imus", "Bacoor")
bot = make_bot(BACOOR)
seed(bot, "u1", "where do I pay the enrollment fee at Imus", IMUS)
_, resp, _, used, _ = ask(bot, "what about Bacoor?")
check("different campus answer served", resp == BACOOR, f"{used}: {resp}")

# ---- prompts ----------------------------------------------------------------
# Length before the 2026-11 VOICE condensation; the prompt must not grow.
OLD_SCOPE_PROMPT_LEN = 2792
prompt = hc.build_scope_locked_prompt("P", None, None)
for needle in (hc.LLM_REFUSAL_TOKEN, "REFUSAL PROTOCOL", "Never fabricate tuition fees",
               "based on the excerpts", "offline assistant", "why only trece?",
               "do not restate an assumption"):
    check(f"scope prompt has {needle!r}", needle in prompt)
check("scope prompt not longer", len(prompt) <= OLD_SCOPE_PROMPT_LEN, len(prompt))

grounded = HybridChatbot._grounded_prompt("q", [(0.5, SITE_CITE, "text", "site")])
check("grounded has STRICT RULES", "STRICT RULES" in grounded)
# Six since the relevance-gate merge: the "answer using ONLY these excerpts"
# rule moved into the EVIDENCE header.
for n in range(1, 7):
    check(f"grounded has rule {n}", f"\n{n}. " in grounded)
check("grounded bans excerpt meta-talk", "Never mention the excerpts" in grounded)
check("grounded keeps satellite excerpts to their campus", "to that campus only" in grounded)


if FAILURES:
    print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("\nAll LLM reply polish checks passed.")
