"""Regression: the LLM tier's evidence verdict (grounding-gate tranche, 2026-09-02).

The grounded prompt makes the model open with "EVIDENCE: yes|no" before it
answers. UAT 2026-09-02: "wow, you can answer questions like that?" retrieved a
GAD-workshop page at 0.126 and the LLM parroted it, citation and all. With the
verdict line, qwen3:8b and gemma3:4b both answered "EVIDENCE: no" and replied
conversationally (measured on the same excerpts; 20/20 replies carried a
verdict). This pins the contract around that line:

  1. The prompt asks for the verdict only when excerpts are attached.
  2. The verdict is parsed off the reply in every shape a small model emits it
     (plain, bold, trailing punctuation, CRLF) and never off a real answer.
  3. After "EVIDENCE: no" every citation-shaped span is stripped, markdown
     links survive, and the turn is labelled "(no-evidence)" — never
     "-grounded".
  4. After "EVIDENCE: yes" nothing changes from the pre-tranche behaviour.
  5. A reply that is ONLY the verdict line falls back to the honest fallback
     text instead of an empty bubble.

Steps 3-5 drive predict() with a stub LLM so the routing itself is exercised.
Run: python test_evidence_verdict.py   (loads the committed models — no LLM,
no server; the repo's tests are scripts, not pytest — see ci.yml)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Windows consoles default to cp1252; the checks print curated Unicode text.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")
os.environ["LLM_PROVIDER"] = "none"

from api import hybrid_chatbot as hc  # noqa: E402
from api.hybrid_chatbot import (  # noqa: E402
    HybridChatbot, _split_evidence_verdict, _strip_citation_spans,
)

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


print("── prompt shape ──")
site_cite = "“GADRC facilitates Workshop” — official CvSU site, 2023-01-12 (https://cvsu.edu.ph/x/)"
grounding = [(0.126, site_cite, "Most of the common problems encountered ...", "site")]
with_excerpts = HybridChatbot._grounded_prompt("wow, you can answer questions like that?", grounding)
without = HybridChatbot._grounded_prompt("wow, you can answer questions like that?", [])
check("grounded prompt demands the verdict line",
      "EVIDENCE: yes" in with_excerpts and "EVIDENCE: no" in with_excerpts)
check("grounded prompt warns the excerpts may be off-topic",
      "MAY NOT be about" in with_excerpts)
check("grounded prompt still carries the excerpt and its label",
      site_cite in with_excerpts and "Most of the common problems" in with_excerpts)
check("no-excerpt prompt does not ask for a verdict", "EVIDENCE:" not in without)
fil = HybridChatbot._grounded_prompt("magkano ang tuition", grounding, language="fil")
check("Filipino directive keeps the verdict line English",
      "EVIDENCE line itself is always in English" in fil and "Filipino" in fil)

print("\n── verdict parsing ──")
for raw, want_v, want_body in [
    ("EVIDENCE: no\nThat's great to hear! How can I help?", "no", "That's great to hear! How can I help?"),
    ("EVIDENCE: yes\nCvSU ranked 217th [x].", "yes", "CvSU ranked 217th [x]."),
    ("evidence: NO.\r\nOk.", "no", "Ok."),
    ("**EVIDENCE: no**\nSure.", "no", "Sure."),
    ("EVIDENCE: no  \nYou can ask me anything.", "no", "You can ask me anything."),
    ("EVIDENCE: no", "no", ""),
    ("The evidence: no such program exists.", None, "The evidence: no such program exists."),
    ("Yes, CvSU offers it. EVIDENCE: yes", None, "Yes, CvSU offers it. EVIDENCE: yes"),
    ("", None, ""),
    (None, None, ""),
]:
    got_v, got_body = _split_evidence_verdict(raw)
    check(f"split {raw!r:.45}", (got_v, got_body) == (want_v, want_body),
          f"got {(got_v, got_body)!r}")

print("\n── citation stripping ──")
text = ("Yes I can help [“GADRC facilitates Workshop” — official CvSU site, 2023-01-12] . "
        "See [cvsu.edu.ph](https://cvsu.edu.ph) or [CvSU Citizens' Charter, p. 12]. Item [1] stays.")
out, n = _strip_citation_spans(text)
check("strips site + charter citation spans", n == 2, f"n={n} out={out!r}")
check("keeps markdown link", "[cvsu.edu.ph](https://cvsu.edu.ph)" in out, out)
check("keeps non-citation brackets", "[1]" in out, out)
check("tidies the space before punctuation", "help ." not in out and "help." in out, out)
check("no-op on plain text", _strip_citation_spans("plain") == ("plain", 0))

print("\n── predict() routing with a stub LLM ──")


class _StubLLM:
    """Minimal LocalLLM stand-in: reachable, replies from a queue."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.available = True

    def ensure_available(self):
        return True

    def generate(self, prompt, conversation_context=None):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else None


bot = HybridChatbot("models", "models/responses_map.json")
bot.llm_provider = "ollama"
# A real question the curated tiers never own, whose retrieved excerpts are
# related but do not answer it (the corpus has the March 2026 civil-engineer
# article, not a September 2026 one), so predict() reaches the LLM with
# grounding attached and "EVIDENCE: no" is the correct verdict. The stub
# decides the reply.
QUERY = "How many CvSU examinees passed the September 2026 civil engineering board exam?"


def run(reply, query=QUERY):
    stub = _StubLLM([reply])
    bot.llm = stub
    intent, response, conf, model_used, _ = bot.predict(query, user_id="ev-test")
    return intent, response, model_used, stub


print("\n── conversational guard (Step 2.9) ──")
# The UAT utterance itself never reaches retrieval or the LLM any more.
# Some of these are already owned by a curated classifier tier ("nice, very
# helpful" -> thanks via the NN); the property under test is that NONE of them
# ever reaches the LLM, and the ones the classifiers drop land on the guard.
for text in ("wow, you can answer questions like that?", "can you answer questions?",
             "nice, very helpful", "are you a bot", "show me the map"):
    intent, response, model_used, stub = run("EVIDENCE: no\nshould not be called", query=text)
    check(f"{text!r} never reaches the LLM",
          not stub.prompts and not model_used.startswith(("Local LLM", "Site RAG", "Charter RAG", "Fallback")),
          f"model_used={model_used} prompts={len(stub.prompts)}")
    check(f"reply for {text!r} is curated text", "GADRC" not in response and len(response) > 20, response)
for text in ("wow, you can answer questions like that?", "can you answer questions?", "show me the map"):
    intent, response, model_used, stub = run("EVIDENCE: no\nshould not be called", query=text)
    check(f"guard owns {text!r}", model_used.startswith("Small Talk (conversational guard)"), model_used)
intent, response, model_used, stub = run("EVIDENCE: yes\nCvSU ranked 217th.", query="how did CvSU rank in the WURI 2026 list?")
check("a real question still reaches the LLM", bool(stub.prompts), model_used)


intent, response, model_used, stub = run(
    "EVIDENCE: no\nThat's great to hear! How can I assist you with CvSU topics? "
    "[“GADRC facilitates Workshop” — official CvSU site, 2023-01-12 (https://cvsu.edu.ph/x/)]")
check("stub actually received a grounded prompt", any("EVIDENCE: yes" in p for p in stub.prompts))
check("EVIDENCE:no reply is served", response.startswith("That's great to hear!"), response)
check("EVIDENCE:no strips the citation the model still emitted",
      "GADRC" not in response and "[" not in response, response)
check("EVIDENCE:no is labelled no-evidence, never -grounded",
      "(no-evidence)" in model_used and "grounded" not in model_used, model_used)
check("verdict line never reaches the user", "EVIDENCE" not in response, response)

# The citation must be a label the bot actually attached to this query's
# excerpts — the pre-existing guard strips any citation it cannot match.
grounding_live, _, _ = bot._gather_grounding(QUERY)
check("the CEIT question is grounded on at least one excerpt", bool(grounding_live))
live_cite = grounding_live[0][1] if grounding_live else "none"
intent, response, model_used, stub = run(
    f"EVIDENCE: yes\nCvSU ranked 217th in WURI 2026 [{live_cite}].")
check("EVIDENCE:yes keeps the answer", response.startswith("CvSU ranked 217th"), response)
check("EVIDENCE:yes keeps a citation that matches the excerpts",
      live_cite in response, response)
check("EVIDENCE:yes keeps the -grounded label", "grounded" in model_used, model_used)

intent, response, model_used, stub = run("Yes, I can answer questions about CvSU.")
check("missing verdict is the pre-tranche path", response.startswith("Yes, I can answer"), response)
check("missing verdict keeps the -grounded label", "grounded" in model_used, model_used)

intent, response, model_used, stub = run("EVIDENCE: no")
check("verdict-only reply falls back to the honest fallback text",
      response in bot.responses_map[bot.FALLBACK_INTENT], response)

intent, response, model_used, stub = run("[OUT_OF_SCOPE]")
check("refusal token still short-circuits before the verdict parse",
      "(out-of-scope)" in model_used, model_used)

stats = bot.model_usage_stats
check("verdict counters are bumped",
      all(k in stats for k in ("llm_evidence_no", "llm_evidence_yes", "llm_verdict_missing")),
      f"stats keys: {sorted(k for k in stats if k.startswith('llm_'))}")

print(f"\n{'ALL PASS' if not FAILED else f'{FAILED} FAILURE(S)'}  ({PASSED} passed)")
sys.exit(1 if FAILED else 0)
