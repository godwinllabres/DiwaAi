"""
Ingest fallback utterances from the chat log into the feedback pipeline.

Reads recent nlu_fallback messages from the DB, maps each to a suggested_intent
using keyword rules derived from the known intent taxonomy, posts feedback to the
API, then triggers /feedback/analyze with apply=True to patch the dataset and
rebuild the intent DB.

Usage:
  py -3.11 training/ingest_chat_log.py --base-url http://127.0.0.1:8001 --limit 10000
  py -3.11 training/ingest_chat_log.py --base-url http://127.0.0.1:8001 --limit 10000 --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Intent keyword rules — ordered from most specific to most general.
# Each entry: (intent_tag, [keyword/phrase list])
# A query matches the FIRST rule whose ANY keyword appears in it (case-insensitive).
# ---------------------------------------------------------------------------
INTENT_RULES: list[tuple[str, list[str]]] = [
    # Registrar — specific doc requests first
    ("registrar", [
        "transcript", "tor ", "t.o.r", "diploma", "certificate of enrollment",
        "certified true copy", "grades copy", "records", "registrar",
        "how to get my", "request a", "unpaid balance",
    ]),
    # Enrollment schedule — timing questions
    ("enrollment_schedule", [
        "when is enrollment", "when does enrollment", "enrollment schedule",
        "enrollment period", "enrollment date", "enrollment deadline",
        "kailan ang enrollment", "second semester enrollment",
        "late enrollment", "miss enrollment",
    ]),
    # Enrollment procedure — how-to questions
    ("enrollment_procedure", [
        "how do i enroll", "how to enroll", "enrollment process",
        "online enrollment", "student portal", "portal login", "portal password",
        "forgot.*password", "register for subject", "add or drop", "add drop",
        "overload subject", "paano mag-enroll", "paano i-access",
        "nakalimutan.*password", "pwede.*mag-enroll",
    ]),
    # Admissions exam
    ("admissions_exam", [
        "entrance exam", "cut-off score", "cut off score", "cutoff",
        "admission exam", "exam schedule", "exam date", "exam result",
        "eteeap",
    ]),
    # Admissions requirements — general admission
    ("admissions_requirements", [
        "requirement", "how do i apply", "how to apply", "admission",
        "application form", "application deadline", "documents",
        "form 137", "high school diploma", "grade average",
        "als passer", "als graduate", "ladderized", "transferee",
        "transfer to cvsu", "transfer from", "paano mag-apply",
        "ano ang requirements", "kailan ang deadline", "may entrance",
        "pwede ba mag-apply", "tanggap ba", "k-12",
        "homeschool", "second degree",
    ]),
    # Tuition fees
    ("tuition_fees", [
        "tuition", "fees", "miscellaneous", "ra 10931", "free tuition",
        "how much", "magkano", "total cost", "laboratory fee",
        "student id fee", "where do i pay", "pay.*online", "libre ba",
        "may bayad", "saan nagbabayad",
    ]),
    # Scholarship
    ("scholarship", [
        "scholarship", "ched", "dost", "financial aid", "grant",
        "stipend", "working student", "paano mag-apply ng scholarship",
        "requirements.*scholarship", "gpa.*scholarship", "shift.*scholarship",
    ]),
    # IT / CS courses
    ("it_cs_courses", [
        "computer science", "information technology", "bsit", "bscs",
        "bs it", "bs cs", "it program", "cs program",
    ]),
    # Graduate programs
    ("graduate_programs", [
        "graduate", "master", "phd", "ph.d", "doctorate", "mba",
        "maed", "master of arts", "master of science",
        "graduate school", "graduate program", "post-graduate",
    ]),
    # Academic calendar
    ("academic_calendar", [
        "academic calendar", "school year", "semester break", "sem break",
        "graduation", "holidays", "exam week", "summer class",
        "summer break", "first semester end", "second semester",
        "kailan nagsisimula", "kailan ang graduation", "kailan ang sem",
        "may summer class",
    ]),
    # Campus facilities
    ("campus_facilities", [
        "dormitory", "dorm", "canteen", "gymnasium", "computer lab",
        "wi-fi", "wifi", "clinic", "chapel", "sports facilit",
        "swimming pool", "parking", "library hour",
        "may dorm", "paano mag-apply.*dorm",
    ]),
    # Library (subset of facilities but distinct intent)
    ("library", [
        "library hour", "library borrow", "borrow.*library",
    ]),
    # Contact info
    ("contact_info", [
        "contact number", "email address", "phone number", "official website",
        "how do i reach", "how do i contact", "facebook page",
        "official email", "ano ang contact", "paano makipag-ugnayan",
        "ano ang email",
    ]),
    # Student organizations
    ("student_organizations", [
        "student organization", "student council", "journalism", "debate club",
        "band", "choir", "rotc", "intramural", "kasama",
        "paano sumali", "sports team", "may rotc", "join.*org",
        "cultural group",
    ]),
    # Events
    ("events", [
        "foundation day", "career fair", "job fair", "research symposium",
        "lantern parade", "cultural festival", "graduation ball",
        "kailan ang foundation", "may job fair",
    ]),
    # Vision / Mission
    ("vision_mission", [
        "vision", "mission", "motto", "truth.*excellence", "iskolar",
        "core values",
    ]),
    # About CvSU
    ("about_cvsu", [
        "history", "when was cvsu", "established", "president of cvsu",
        "awards", "accredited", "aaccup", "how many student",
        "center of excellence", "founded", "kailan naitayo", "sino ang presidente",
        "what is cvsu", "background", "pangulo ng cvsu", "ranking",
        "top state university", "how many campus", "student population",
        "university profile", "cvsu facts", "cvsu overview", "cvsu profile",
        "cavite state university", "ra 8468", "thomasites", "1906", "1998",
        "kasaysayan", "pinagmulan", "public university", "state university",
        "is cvsu accredited", "cvsu coe", "cvsu recognition",
    ]),
    # Campus-specific (named campuses other than main/general)
    ("campus_specific", [
        "imus campus", "bacoor campus", "naic campus", "carmona campus",
        "silang campus", "rosario campus", "trece campus", "tanza campus",
        "cvsu imus", "cvsu bacoor", "cvsu naic", "cvsu carmona",
        "cvsu silang", "cvsu rosario", "cvsu trece",
    ]),
    # Campus location — general
    ("campus_location", [
        "where is cvsu", "where is the cvsu", "how do i get to cvsu",
        "address of cvsu", "how far is cvsu", "public transport",
        "how many campus", "nearest.*campus", "campus near",
        "saan naroon", "paano pumunta", "ilang campus",
        "how to get to cvsu",
    ]),
    # Courses offered — general
    ("courses_offered", [
        "courses", "programs", "nursing", "criminology", "architecture",
        "accountancy", "tourism", "hospitality", "psychology", "law school",
        "education course", "mass communication", "social work",
        "agricultural engineering", "business administration",
        "stem course", "anong kurso", "may nursing", "may criminology",
        "available.*course",
    ]),
    # New intents — must come before generic greeting/goodbye/thanks
    ("nstp", [
        r"\bnstp\b", "national service training", "rotc.*cvsu", "cwts.*cvsu",
        "lts.*cvsu", "civic welfare training", "literacy training service",
        "reserve officers training", "nstp component", "nstp enrollment",
        "nstp requirements", "nstp certificate", "nstp mandatory",
        "nstp graduation", "nstp 1", "nstp 2",
    ]),
    ("academic_policies", [
        "grading system", "grading scale", "passing grade", "gwa computation",
        "how to compute gwa", r"\bgwa\b", "weighted average.*grade",
        "incomplete grade", r"\binc grade\b", "how to remove inc",
        "academic probation", "retention policy", "leave of absence",
        r"\bloa\b.*cvsu", "shifting course", "how to shift",
        "how many units to graduate", "units requirement",
        "retake.*subject", "failed subject", r"\b5\.0\b.*cvsu",
        "maximum residency", "dean.s list", "academic standing",
        "withdrawal.*subject", "dropped subject",
        "ano ang passing grade", "paano mag-compute ng gwa",
        "mag-loa", "mag-shift ng course",
    ]),
    ("dormitory", [
        "dormitory", r"\bdorm\b", "dorm application", "dorm requirement",
        "dorm fee", "dorm rate", "bed space", "may dorm ba",
        "paano mag-apply.*dorm", "student housing", "on.campus housing",
        "residential hall", "dorm curfew", "dorm rules", "dorm slot",
        "boarding house", "lodging.*campus", "accommodation.*cvsu",
    ]),
    ("student_portal", [
        "student portal", "portal login", "portal password",
        "how to access.*portal", "cvsu portal", r"\bdems\b",
        "my.cvsu.edu.ph", "portal not working", "cannot login.*portal",
        "forgot.*portal.*password", "reset.*portal.*password",
        "view grades online", "online enrollment portal",
        "ict office.*cvsu", "cvsu ict", "enrollment system",
        "student information system", r"\bsis\b.*cvsu",
        "nakalimutan.*password.*portal", "hindi.*makapag-login.*portal",
    ]),
    ("international_students", [
        "foreign student", "international student", "non-filipino student",
        "foreign applicant", "9f visa", "student visa.*cvsu",
        "can.*international.*apply", "does cvsu accept foreign",
        "foreign national.*cvsu", "international.*admission",
        "visa requirement.*cvsu", "apostille.*cvsu",
        "authenticated.*credential", "office of international affairs",
        r"\boia\b.*cvsu", "study.*cvsu.*foreigner",
    ]),
    ("online_programs", [
        "distance learning", "online degree", r"\beteeap\b",
        "part.time study", "modular learning", "blended learning",
        "flexible learning", "hybrid learning", "online course.*cvsu",
        "work while studying", "earn degree while working",
        "prior learning", "non-traditional student",
        "continuing education", "e-learning.*cvsu",
        "asynchronous learning", "synchronous learning",
        "may online program ba", "puwedeng mag-aral online",
        "alternative learning", "mdl.*cvsu",
    ]),
    # Greetings / conversational
    ("greeting", [
        "hello", "hi there", "good morning", "good afternoon", "good evening",
        "hey sevi", "kumusta",
    ]),
    ("goodbye", [
        "bye", "goodbye", "see you", "paalam",
    ]),
    ("thanks", [
        "thank you", "thanks", "salamat", "maraming salamat",
    ]),
    # Chitchat — meta, filler, off-topic (catch-all, must stay last)
    ("chitchat", [
        "are you a robot", "are you a bot", "are you an ai", "are you human",
        "who are you", "what are you", "tell me about yourself",
        "what can you do", "what can you help", "how does this work",
        "how do you work", "what is your purpose", "what do you do",
        "are you real", "can you think", "do you understand",
        r"^help$", r"^hi$", r"^hey$", r"^hello$", r"^ok$", r"^okay$",
        r"^yes$", r"^no$", r"^test$", r"^testing$", r"^\.$", r"^\.\.$",
        r"^huh\??$", r"^hmm\??$", r"^what\??$", r"^lol$", r"^haha$",
        "i need help", "can you help me", "i need assistance", "please help",
        "tulungan mo ako", "help naman",
        "how are you", "are you okay",
        "tell me a joke", "what time is it", "what is the weather",
        "what is the meaning of life",
        "ano ba yan", "wala lang", "ewan ko", "di ko alam", "hindi ko alam",
        "sige", "ayos na", "basta", "talaga",
        "what is sevi", "who is sevi", "is sevi a bot",
        r"^sevi$", "hey sevi", "hi sevi", "hello sevi",
        "never mind", "nevermind", r"^nvm$", "noted",
        r"^wow$", r"^nice$", r"^grabe$",
    ]),
]

_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (tag, [re.compile(kw, re.IGNORECASE) for kw in keywords])
    for tag, keywords in INTENT_RULES
]


_STRIP_PREFIXES = [
    "sana matulungan mo ako sa ",
    "gusto ko pong malaman ang ",
    "hi, i just want to ask - ",
    "quick question - ",
    "i have a question about ",
    "can i ask about ",
    "please help me with ",
    "i want to know ",
    "can you tell me ",
    "i need information about ",
    "tell me about ",
    "please explain ",
    "can you help me with ",
    "can you explain ",
    "give me details on ",
    "i'm asking about ",
    "share information on ",
    "i need to know ",
]
_STRIP_SUFFIXES = [
    " please answer asap.", " asap po", " it's urgent.",
    " for new students?", " for freshmen?", " for 2026?", " this year?",
    " for cvsu indang?", " for cvsu imus?", " for cvsu bacoor?",
    " for the main campus?", " by email?", " by phone?",
    " today?", " right now?", " as a transfer student?",
    " for international students?", " thanks!", " please.", " po?", " please?",
]


def _sanitize(text: str) -> Optional[str]:
    """Strip synthetic wrappers, fix duplicate prefixes, drop noise."""
    t = text.strip()
    # Fix duplicate prefix ("Tell me about Tell me about X" → "X")
    words = t.split()
    for n in range(2, 5):
        if len(words) >= 2 * n and words[:n] == words[n: 2 * n]:
            t = " ".join(words[n:]).strip()
            words = t.split()
            break
    # Iteratively strip wrappers
    changed = True
    while changed:
        changed = False
        low = t.lower()
        for p in _STRIP_PREFIXES:
            if low.startswith(p):
                t = t[len(p):].strip()
                low = t.lower()
                changed = True
        for s in _STRIP_SUFFIXES:
            if low.endswith(s):
                t = t[: -len(s)].strip()
                low = t.lower()
                changed = True
    return t if 4 <= len(t) <= 80 else None


def infer_intent(query: str) -> Optional[str]:
    for tag, patterns in _COMPILED:
        if any(p.search(query) for p in patterns):
            return tag
    return None


def _post_feedback(session: requests.Session, base_url: str, entry: dict, suggested: str, query: str) -> bool:
    payload = {
        "user_id":          entry.get("user_id"),
        "session_id":       entry.get("session_id"),
        "intent":           "nlu_fallback",
        "rating":           1,
        "helpful":          False,
        "comment":          "Auto-ingested: fallback for query mapped to '" + suggested + "'.",
        "suggested_intent": suggested,
        "user_message":     query,
    }
    r = session.post(f"{base_url}/feedback", json=payload, timeout=15)
    if not r.ok:
        print(f"  [WARN] feedback POST failed ({r.status_code}): {query[:60]}")
    return r.ok


def _trigger_analyze(session: requests.Session, base_url: str, limit: int) -> dict:
    payload = {"limit": limit + 500, "apply": True}
    ar = session.post(f"{base_url}/feedback/analyze", json=payload, timeout=60)
    ar.raise_for_status()
    return ar.json()


def _scan_fallbacks(
    session: requests.Session,
    base_url: str,
    fallbacks: list,
    dry_run: bool,
) -> tuple[int, int, dict[str, int]]:
    """Map each fallback to an intent and optionally post feedback. Returns (mapped, unmapped, intent_counts)."""
    mapped = unmapped = 0
    intent_counts: dict[str, int] = {}
    for entry in fallbacks:
        raw: str = (entry.get("user_message") or "").strip()
        if not raw:
            continue
        query = _sanitize(raw)
        if not query:
            unmapped += 1
            continue
        suggested = infer_intent(query)
        if not suggested:
            unmapped += 1
            continue
        intent_counts[suggested] = intent_counts.get(suggested, 0) + 1
        if dry_run or _post_feedback(session, base_url, entry, suggested, query):
            mapped += 1
    return mapped, unmapped, intent_counts


def _make_session(admin_pin: Optional[str] = None) -> requests.Session:
    """Session carrying the admin PIN so the gated reads this script relies on
    (GET /feedback/fallbacks, POST /feedback/analyze) authenticate. Falls back
    to the DASHBOARD_PIN env var. POST /feedback (submit) is public and needs no
    pin, but sending it is harmless."""
    s = requests.Session()
    pin = admin_pin or os.getenv("DASHBOARD_PIN", "")
    if pin:
        s.headers["X-Admin-Pin"] = pin
    return s


def run(base_url: str, limit: int, dry_run: bool, retrain: bool,
        admin_pin: Optional[str] = None) -> None:
    session = _make_session(admin_pin)

    resp = session.get(f"{base_url}/feedback/fallbacks", params={"limit": limit}, timeout=30)
    resp.raise_for_status()
    fallbacks = resp.json()["fallbacks"]
    print(f"Fetched {len(fallbacks)} fallback utterances from chat log.")

    mapped, unmapped, intent_counts = _scan_fallbacks(session, base_url, fallbacks, dry_run)

    print(f"\nMapped:   {mapped}")
    print(f"Unmapped: {unmapped}")
    print("\nBy intent:")
    for tag, count in sorted(intent_counts.items(), key=lambda x: -x[1]):
        print(f"  {tag}: {count}")

    if dry_run:
        print("\n[DRY RUN] No changes posted. Re-run without --dry-run to apply.")
        return

    if mapped == 0:
        print("\nNothing new to analyze — exiting.")
        return

    print("\nTriggering /feedback/analyze (apply=true) ...")
    result = _trigger_analyze(session, base_url, limit)
    print(f"  Status:           {result.get('status')}")
    print(f"  Entries analyzed: {result.get('entries_analyzed')}")
    print(f"  Patterns added:   {result.get('patterns_added')}")
    if result.get("by_intent"):
        for tag, pats in result["by_intent"].items():
            print(f"    {tag}: {len(pats)} patterns")

    if retrain and result.get("patterns_added", 0) > 0:
        import subprocess
        print("\nRetraining Naive Bayes model ...")
        subprocess.run(
            [sys.executable, str(ROOT / "train_naive_bayes.py")],
            cwd=str(ROOT),
            check=True,
        )
        print("Retrain complete. Restart the API server to load the new model.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest chat log fallbacks into the feedback pipeline.")
    p.add_argument("--base-url", default="http://127.0.0.1:8001")
    p.add_argument("--limit",    type=int, default=10000)
    p.add_argument("--dry-run",  action="store_true", help="Preview mappings without posting.")
    p.add_argument("--retrain",  action="store_true", help="Retrain model after applying patterns.")
    p.add_argument("--admin-pin", default=os.getenv("DASHBOARD_PIN", ""),
                   help="X-Admin-Pin for gated /feedback reads (default: DASHBOARD_PIN env var).")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    started = time.time()
    run(
        base_url=args.base_url.rstrip("/"),
        limit=args.limit,
        dry_run=args.dry_run,
        retrain=args.retrain,
        admin_pin=args.admin_pin,
    )
    print(f"\nDone in {time.time() - started:.1f}s")
