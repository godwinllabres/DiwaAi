""""is this the latest?" about the previous answer, and the CAPS catalog
(2026-11-25).

Run: LLM_PROVIDER=none python test_source_freshness.py

The two-turn exchange "graduate programs" -> "is this the latest?" got the
welcome message: follow-up routing recognised the turn, then the
conversational guard read the topic-free message (and "is this" as a
question about the bot) as small talk. And the answer itself cited the 2018
GS-OLC page while CvSU publishes a newer CAPS catalog (14 programs).

Drives the real FastAPI app through TestClient; only the LLM is stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ["CONNECTORS_MCP_ENABLED"] = "0"
os.environ["AIS_MCP_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

import api.app as app_module  # noqa: E402
from api import grounding_gate  # noqa: E402
from api.college_programs import find_college  # noqa: E402
from api.source_freshness import freshness_ask  # noqa: E402

FAILURES = []
CAPS_URL = "https://cvsu.edu.ph/programs-offered-caps/"
OLD_URL = "https://cvsu.edu.ph/2018/01/13/ogs-olc-program-offerings/"
CAPS_PHONE = "(046) 890-5926"


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f": {detail}"))
    if not cond:
        FAILURES.append(name)


# ── detection ────────────────────────────────────────────────────────────
for text in ["is this the latest?", "Is this the latest?", "updated pa ba ito?",
             "updated pa po ba ito?", "is this current for AY 2026-2027?",
             "is this current for AY 2026–2027?", "is this still up to date?",
             "is that list updated?", "latest na ba yan?", "when was this last updated?",
             "is there a newer version?", "is this updated pa ba?", "luma na ba ito?",
             "ito na ba ang pinakabago?", "but is this the latest one?",
             "is the CAPS list the latest?", "is the GS-OLC list still current?"]:
    check(f"freshness: {text!r}", freshness_ask(text) is not None)
for text in ["is this Sevi?", "is this a bot?", "what is the latest tuition fee?",
             "is the library open?", "is the website updated?",
             "is this the latest tuition fee for BSIT in Indang and Imus campus this year?",
             "what are the latest CAPS programs?", "updated ba ang schedule ng enrollment?",
             "hello", "is this campus open on saturday?"]:
    check(f"not freshness: {text!r}", freshness_ask(text) is None, freshness_ask(text))
check("AY span is parsed", (freshness_ask("is this current for AY 2026-27?") or {}).get("ay")
      == "AY 2026–2027")
check("named topic resolves to CAPS",
      ((freshness_ask("is the GS-OLC list still current?") or {}).get("college") or {})
      .get("abbr") == "CAPS")

# "is this <freshness word>" is about the answer, not the bot.
bot_re = grounding_gate._BOT_ADDRESS_RE
check("bot address: is this sevi", bool(bot_re.search("is this sevi?")))
check("bot address: is this a bot", bool(bot_re.search("is this a bot")))
check("bot address: is this real", bool(bot_re.search("is this real?")))
check("not bot address: is this the latest", not bot_re.search("is this the latest?"))
check("not bot address: is this still current", not bot_re.search("is this still current?"))

# ── registry: CAPS catalog with the GS-OLC aliases kept ──────────────────
for alias in ["GS-OLC programs", "OGS/OLC programs", "graduate school programs",
              "CAPS programs", "College of Advanced and Professional Studies programs"]:
    college = find_college(alias) or {}
    check(f"alias {alias!r} -> CAPS", college.get("abbr") == "CAPS", college.get("abbr"))
caps = find_college("CAPS")
names = [p["name"] for p in caps["programs"]]
check("CAPS lists 14 programs", len(names) == 14, len(names))
check("CAPS lists MA English Language Studies",
      "Master of Arts in English Language Studies" in names)
check("MPA is a standalone degree", "Master in Public Administration" in names
      and not any("Professional Studies" in n for n in names))
check("CAPS majors are not carried over from 2018",
      all(not p["majors"] for p in caps["programs"]))

# ── the full exchange through /chat ──────────────────────────────────────
client = TestClient(app_module.app)
bot = app_module.chatbot


def ask(message, session_id):
    res = client.post("/chat", json={"message": message, "session_id": session_id,
                                     "user_id": "fresh-user"})
    assert res.status_code == 200, (res.status_code, res.text[:300])
    return res.json()


# 1. The exact two turns.
first = ask("what graduate programs does CvSU offer?", "fr-1")
check("graduate answer cites the CAPS catalog", CAPS_URL in first["text"]
      and OLD_URL not in first["text"], first["text"][:300])
body = ask("is this the latest?", "fr-1")
check("'is this the latest?' -> source_freshness", body["source"] == "source_freshness",
      (body["source"], body["text"][:200]))
check("…not the welcome message", "What would you like to know" not in body["text"])
check("…names the CAPS catalog and its count", "CAPS" in body["text"] and "14" in body["text"],
      body["text"])
check("…cites the catalog and the directory", CAPS_URL in body["text"]
      and "https://cvsu.edu.ph/contact-us-2/" in body["text"], body["text"])
check("…keeps the verified CAPS number", CAPS_PHONE in body["text"], body["text"])

# 2. Taglish.
ask("GS-OLC programs", "fr-2")
body = ask("updated pa ba ito?", "fr-2")
check("Taglish -> source_freshness", body["source"] == "source_freshness", body["source"])
check("Taglish reply is Filipino", "po" in body["text"].split() and "CAPS" in body["text"],
      body["text"])

# 3. Academic year — after another freshness reply, still about the list.
ask("graduate school programs", "fr-3")
ask("is this the latest?", "fr-3")
body = ask("is this current for AY 2026–2027?", "fr-3")
check("AY question -> source_freshness", body["source"] == "source_freshness", body["source"])
check("AY question is answered in English", "academic year" in body["text"], body["text"])
check("AY question says the catalog is undated for that AY",
      "AY 2026–2027" in body["text"] and "doesn't say which academic year" in body["text"],
      body["text"])

# 4. A reply that cited the superseded 2018 page (e.g. a session from before
#    this fix, or a retrieved passage) -> "No, there's a newer catalog".
bot.record_turn("fr-4", user_input="graduate programs?", intent="graduate_programs",
                confidence=0.9, model_used="Naive Bayes (NLU Enhanced)", owner_id="fresh-user",
                response=f"CvSU's Graduate School and Open Learning College (OGS/OLC) offer "
                         f"master's and doctoral programs. See {OLD_URL}.")
body = ask("is this the latest?", "fr-4")
text = body["text"]
check("stale citation -> 'newer catalog'", text.startswith("No — CvSU has a newer"), text)
check("…compares 13 with 14", "13 programs" in text and "lists 14" in text
      and "5 November 2026" in text, text)
check("…names MA English Language Studies and the MPA",
      "English Language Studies" in text and "Public Administration" in text, text)
check("…gives the CAPS number", CAPS_PHONE in text, text)

# 5. No previous answer, no topic -> ask which list.
body = ask("is this the latest?", "fr-5")
check("no previous answer -> which list?", body["source"] == "source_freshness"
      and "Which answer or list" in body["text"], body["text"])
body = ask("is the GS-OLC list still current?", "fr-5b")
check("named topic without history -> CAPS answer", "CAPS catalog" in body["text"],
      body["text"])

# 6. A college with a dated page.
ask("CEIT programs", "fr-6")
body = ask("is this the latest?", "fr-6")
check("CEIT list -> states its publication date", "last published" in body["text"],
      body["text"])

# 7. Identity still answered as identity.
body = ask("is this Sevi?", "fr-7")
check("'is this Sevi?' -> identity", body["source"] == "smalltalk" and "Sevi" in body["text"],
      (body["source"], body["text"][:200]))


# 8. With an LLM up: freshness never calls it, and a follow-up the router
#    claimed is not swallowed by the small-talk guard.
class FakeLLM:
    def __init__(self):
        self.calls = []

    def ensure_available(self):
        return True

    def generate(self, msg, conversation_context=None):
        self.calls.append(msg)
        return "Here is more on that."


saved = (bot.llm, bot.llm_provider)
try:
    bot.llm, bot.llm_provider = FakeLLM(), "ollama"
    ask("what graduate programs does CvSU offer?", "fr-8")
    body = ask("is this the latest?", "fr-8")
    check("LLM up: still source_freshness", body["source"] == "source_freshness", body["source"])
    check("LLM up: freshness made no LLM call", not bot.llm.calls, bot.llm.calls[-1:])
    body = ask("is that right?", "fr-8")
    check("LLM up: a routed follow-up is not small talk", body["source"] != "smalltalk",
          (body["source"], body["text"][:200]))
finally:
    bot.llm, bot.llm_provider = saved

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("ALL PASS")
