"""Offline regression for the Tier 1/2 template tone rewrite — run:
    python test_template_tone.py

Covers, without loading models or starting a server:
  • the three response copies (cavsu_intents.json, responses_map.json, the DB)
    are identical — any drift makes load_intents() delete and rebuild the DB
  • the rewritten templates answer first: no "Per the ..." lead-in, no ALL-CAPS
    header lines, no "Salamat sa inyong tanong!", no ** (Messenger shows it raw)
  • the served English/Filipino variants keep their language
  • the 2026 roster facts survive the rewrite
Templates are rewritten by scripts/rewrite_template_tone.py.
"""
import json
import re
import sqlite3
import sys

sys.path.insert(0, "scripts")
from api.hybrid_chatbot import _filipino_ratio  # noqa: E402
from rewrite_template_tone import CHARTER_LEADS, REWRITES, served_indices  # noqa: E402

failures = 0


def check_true(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


doc = json.load(open("data/cavsu_intents.json", encoding="utf-8"))
intents = {i["tag"]: i["responses"] for i in doc["intents"]}
rmap = json.load(open("models/responses_map.json", encoding="utf-8"))
con = sqlite3.connect("data/cavsu_intents.db")
db = {}
for tag, text in con.execute(
        "SELECT i.tag, r.response_text FROM responses r JOIN intents i "
        "ON i.id = r.intent_id ORDER BY r.id"):
    db.setdefault(tag, []).append(text)
con.close()

# ── three-copy consistency ───────────────────────────────────────────────────
print("── three-copy consistency ──")
drift = [t for t in intents if not (intents[t] == rmap.get(t) == db.get(t))]
check_true(f"JSON == responses_map == DB for every intent (drift: {drift[:5]})", not drift)
check_true("same intent set in all three copies",
           set(intents) == set(db) == set(rmap) - {"llm_unavailable"})
check_true("responses_map keeps the code-owned llm_unavailable key", "llm_unavailable" in rmap)

# ── tone fence over the rewritten intents ────────────────────────────────────
print("\n── tone fence ──")
TAGS = sorted({t for t, *_ in REWRITES} | {t for t, *_ in CHARTER_LEADS})
HEADING_RE = re.compile(r"^[A-Z0-9][A-Z0-9 ,()/&'’.:–—-]{2,}:?$")
for tag in TAGS:
    for i, r in enumerate(intents[tag]):
        label = f"{tag}[{i}]"
        caps = [ln.strip() for ln in r.splitlines()
                if len(ln.strip()) > 12 and HEADING_RE.match(ln.strip())]
        check_true(f"{label}: no attribution lead-in",
                   not re.match(r"^(Per the|As per|Ayon sa opisyal)", r))
        check_true(f"{label}: no ALL-CAPS header lines {caps[:1]}", not caps)
        check_true(f"{label}: no ** markup", "**" not in r)
        check_true(f"{label}: no 'Salamat sa inyong tanong' closer",
                   "Salamat sa inyong tanong" not in r)
        check_true(f"{label}: no campus-assumption prefix",
                   not r.startswith(("Assuming the Main Campus", "Ipagpalagay nating")))

# ── served-variant language ──────────────────────────────────────────────────
print("\n── served-variant language ──")
for tag in TAGS:
    rs = intents[tag]
    if len(rs) < 2:
        continue
    en, fil = served_indices(rs)
    en_r, fil_r = _filipino_ratio(rs[en]), _filipino_ratio(rs[fil])
    check_true(f"{tag}: served EN reads English ({en_r:.3f} < 0.05)", en_r < 0.05)
    # Officials lists are mostly proper nouns, so the Filipino ratio is low in
    # absolute terms; it only has to clearly out-rank the English variant.
    check_true(f"{tag}: served FIL out-ranks EN ({fil_r:.3f} >= 0.05)",
               fil_r >= 0.05 and fil_r > en_r)


def first_line_is_sentence(text):
    """Mirror of app._is_header_line: the lead must not be skipped as a header."""
    s = text.split("\n", 1)[0].strip()
    letters = [c for c in s if c.isalpha()]
    return (len(s) >= 25 and not (s.endswith(":") and len(s) < 60)
            and not all(c.isupper() for c in letters))


# ── lead-first ───────────────────────────────────────────────────────────────
print("\n── lead-first ──")
uo = intents["university_officials"]
en_uo = uo[served_indices(uo)[0]]
check_true("university_officials EN opens with the President",
           en_uo.startswith("CvSU's University President is Dr. Ma. Agnes P. Nuestro."))
for tag in TAGS:
    for i, r in enumerate(intents[tag]):
        check_true(f"{tag}[{i}]: first line is a sentence, not a header",
                   first_line_is_sentence(r))

# ── roster facts kept ────────────────────────────────────────────────────────
print("\n── roster facts ──")
for tag in ("university_officials", "college_deans", "campus_officials"):
    rs = intents[tag]
    en, fil = served_indices(rs)
    check_true(f"{tag} EN keeps the roster dates", "1 January - 31 December 2026" in rs[en])
    check_true(f"{tag} FIL keeps the roster dates", "1 Enero" in rs[fil] and "2026" in rs[fil])
    check_true(f"{tag} cites contact-us-2 in both languages",
               "contact-us-2" in rs[en] and "contact-us-2" in rs[fil])
uo_all = " ".join(uo)
for vp in ("Cristina M. Signo", "Melbourne R. Talactac", "John Xavier B. Nepomuceno",
           "Mary Jane D. Tepora", "Almira G. Magcawas", "Myrna Q. Mallari",
           "Catherine J. Quiñones"):
    check_true(f"university_officials keeps {vp}", vp in uo_all)
check_true("no Board Secretary", "Board Secretary" not in uo_all)
deans = " ".join(intents["college_deans"])
check_true("CEIT dean is Buclatin",
           "CEIT (College of Engineering and Information Technology): Dr. Willie C. Buclatin"
           in deans)
check_true("12 deans listed in each variant",
           all(r.count("): Dr. ") == 12 for r in intents["college_deans"]))
check_true("12 campus heads listed in each variant",
           all(r.count(" Campus: ") == 12 for r in intents["campus_officials"]))
check_true("charter sources kept verbatim",
           all("Source: CvSU Citizen's Charter 2026 — " in intents[t][0]
               for t, *_ in CHARTER_LEADS))
check_true("RA 10931 still cited in tuition_fees EN",
           "RA 10931" in intents["tuition_fees"][served_indices(intents["tuition_fees"])[0]])

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
