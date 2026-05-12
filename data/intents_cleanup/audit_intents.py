"""
Audit script for cavsu_intents.json.

Produces:
  - audit_report.md     : human-readable findings
  - intents_summary.csv : per-tag stats and issue flags
"""
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "intents_raw.json"
REPORT = ROOT / "audit_report.md"
CSV_OUT = ROOT / "intents_summary.csv"

with SRC.open(encoding="utf-8") as f:
    data = json.load(f)

intents = data["intents"]

# --- Noisy templated prefixes/suffixes that pollute classification ---
NOISE_PREFIXES = [
    "please help me with ",
    "sana matulungan mo ako sa ",
    "hi, i just want to ask - ",
    "quick question - ",
    "can i ask about ",
    "i want to know ",
    "gusto ko pong malaman ang ",
    "can you tell me ",
    "i have a question about ",
]
NOISE_SUFFIXES = [
    " for cvsu indang?",
    " for cvsu bacoor?",
    " for cvsu imus?",
    " for 2026?",
    " for freshmen?",
    " please answer asap.",
    " it's urgent.",
    " thanks!",
    " asap po",
    " po?",
    " please?",
]

def is_noisy_template(p: str) -> bool:
    pl = p.lower().strip()
    for pre in NOISE_PREFIXES:
        if pl.startswith(pre):
            return True
    for suf in NOISE_SUFFIXES:
        if pl.endswith(suf):
            return True
    return False

# --- Unverified factual claims patterns ---
# These match content that should be sanitized out of responses.
UNVERIFIED_CLAIMS = [
    (r"\b13\s+campuses?\b", "specific campus count (13)"),
    (r"\b11\s+colleges?\b", "specific college count (11)"),
    (r"\b84\+?\s+(?:recognized\s+)?(?:student\s+)?organizations?\b", "org count (84+)"),
    (r"\b92,?000\s+books?\b", "library book count"),
    (r"\b72\s+hectares?\b", "campus size (72 hectares)"),
    (r"\b7,?490\b", "fabricated admission stat (7,490)"),
    (r"\b21,?739\b", "fabricated applicant stat (21,739)"),
    (r"\b34\.45\s*%", "fabricated acceptance-rate stat"),
    (r"046-?436-?6584", "specific phone number"),
    (r"\b\d{3}-?\d{3}-?\d{4}\b", "specific phone number"),
    (r"\bestablished:\s*1\d{3}\b", "fabricated establishment year per campus"),
    (r"originally:\s+cavite\s+college", "specific historical attribution per campus"),
    (r"\bdasmari[nñ]as\s+learning\s+center\s*-\s*2023", "made-up campus + year"),
    (r"\bmaragondon\s*-\s*2015", "made-up campus + year"),
    (r"\bbacoor\s*-\s*2008", "made-up campus + year"),
    (r"\btanza\s*-\s*2007", "made-up campus + year"),
    (r"\bsilang\s*-\s*2006", "made-up campus + year"),
    (r"\btrece\s+martires\s*-\s*2005", "made-up campus + year"),
    (r"\bimus\s*-\s*2003", "made-up campus + year"),
    (r"\bcarmona\s*-\s*2002", "made-up campus + year"),
    (r"\bcavite\s+city\s*-\s*2001", "made-up campus + year"),
    (r"\brosario/?ccat\s*-\s*1969", "made-up campus + year"),
    (r"\bnaic\s*-\s*1961", "made-up campus + year"),
    (r"\bgeneral\s+trias\s*-\s*2012", "made-up campus + year"),
    (r"acceptance\s+rate", "acceptance-rate claim"),
    (r"center\s+of\s+excellence", "Center-of-Excellence claim (verify)"),
    (r"aaccup", "AACCUP claim (verify)"),
    (r"iso\s+certified", "ISO certification claim (verify)"),
    (r"\bgreen\s+hornets\b", "athletic team / mascot claim (verify)"),
    (r"ladislao\s+diwa\s+memorial\s+library", "specific named facility (verify)"),
    (r"laya\s+at\s+diwa\s+monument", "specific named landmark (verify)"),
    (r"inaugurated\s+2006", "specific inauguration year"),
    (r"\b(?:ceit|graduate|academic|admissions?|registrarmain|ictmain|osas)@cvsu\.edu\.ph", "specific email (verify)"),
    (r"admission\.cvsu\.edu\.ph", "subdomain (verify still active)"),
]

# --- Patterns that DON'T match the intent's tag (cross-contamination) ---
# Heuristic: detect obviously off-topic patterns by tag.
TAG_KEYWORDS = {
    "thanks":   ["thank", "salamat", "appreciate", "helpful", "got it", "cool thanks", "great, thanks"],
    "greeting": ["hello", "hi ", "hi,", "hey", "good morning", "good afternoon", "good evening", "good day",
                 "kumusta", "magandang", "howdy", "greetings", "start", "begin", "musta", "diwa", "yo "],
    "goodbye":  ["bye", "goodbye", "see you", "see ya", "take care", "paalam", "exit", "quit", "done",
                 "ingat", "farewell", "stop", "later", "ciao", "tapos", "alis", "salamat at paalam",
                 "nothing more", "no further", "got what i needed"],
}

per_tag_stats = []
overall = {
    "total_tags": len(intents),
    "total_patterns": 0,
    "total_responses": 0,
    "total_noisy_patterns": 0,
    "total_unverified_responses": 0,
    "duplicate_patterns_across_tags": 0,
}

# Map every pattern → set of tags it appears in (for cross-tag dupes)
pattern_to_tags = defaultdict(set)
for it in intents:
    for p in it.get("patterns", []):
        pattern_to_tags[p.strip().lower()].add(it["tag"])

# Issues collected for the audit
critical_issues = []
warnings = []

for it in intents:
    tag = it["tag"]
    patterns = it.get("patterns", [])
    responses = it.get("responses", [])
    n_patterns = len(patterns)
    n_responses = len(responses)

    # Lowercased deduped
    seen = set()
    unique = []
    dupes = []
    for p in patterns:
        key = p.strip().lower()
        if key in seen:
            dupes.append(p)
        else:
            seen.add(key)
            unique.append(p)

    noisy = [p for p in unique if is_noisy_template(p)]

    # Cross-contamination: patterns that match another tag's keywords
    misplaced = []
    if tag in ("thanks", "greeting", "goodbye"):
        # OK
        pass
    else:
        for p in unique:
            pl = p.lower().strip()
            # Pure pleasantries shouldn't live in non-chit-chat tags
            if pl in ("hello", "hi", "hi there", "good morning", "good afternoon",
                      "good evening", "thanks", "thank you", "bye", "goodbye"):
                misplaced.append(p)

    # Unverified factual claims in responses
    unverified_hits = []
    for r in responses:
        rl = r.lower()
        for pat, label in UNVERIFIED_CLAIMS:
            if re.search(pat, rl):
                unverified_hits.append((label, r[:80] + "..." if len(r) > 80 else r))
                break  # 1 flag per response is enough

    has_tagalog_response = any(
        any(w in r.lower() for w in ["salamat", "po ", "ang cvsu", "ang ", "ng ", "ay ", "para sa", "mga "])
        for r in responses
    )

    overall["total_patterns"] += n_patterns
    overall["total_responses"] += n_responses
    overall["total_noisy_patterns"] += len(noisy)
    overall["total_unverified_responses"] += len(unverified_hits)

    # Critical issues
    if n_patterns == 0:
        critical_issues.append(f"`{tag}`: 0 patterns")
    if n_responses == 0:
        critical_issues.append(f"`{tag}`: 0 responses")
    if misplaced:
        critical_issues.append(f"`{tag}`: {len(misplaced)} pleasantry patterns leaked in (e.g., {misplaced[:3]})")
    if unverified_hits:
        critical_issues.append(
            f"`{tag}`: {len(unverified_hits)} response(s) contain unverified facts — "
            f"examples: " + "; ".join(f"[{l}]" for l, _ in unverified_hits[:3])
        )

    # Warnings
    if len(dupes) > 0:
        warnings.append(f"`{tag}`: {len(dupes)} duplicate patterns within the same tag")
    if len(noisy) > 20:
        warnings.append(f"`{tag}`: {len(noisy)} noisy templated patterns (e.g., 'Please help me with...')")

    per_tag_stats.append({
        "tag": tag,
        "n_patterns": n_patterns,
        "n_unique_patterns": len(unique),
        "n_duplicates": len(dupes),
        "n_noisy_template": len(noisy),
        "n_responses": n_responses,
        "has_tagalog_response": has_tagalog_response,
        "n_misplaced": len(misplaced),
        "n_unverified_response_claims": len(unverified_hits),
        "notes": "; ".join({u[0] for u in unverified_hits}) if unverified_hits else "",
    })

# Cross-tag duplicates
cross_dupes = {p: tags for p, tags in pattern_to_tags.items() if len(tags) > 1}
overall["duplicate_patterns_across_tags"] = len(cross_dupes)

# --- Write CSV ---
with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(per_tag_stats[0].keys()))
    writer.writeheader()
    writer.writerows(per_tag_stats)

# --- Write Markdown report ---
def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(out)

lines = []
lines.append("# DIWA / CvSU Intents — Quality Audit\n")
lines.append(f"Source file: `data/cavsu_intents.json`\n")
lines.append("## Top-level numbers\n")
lines.append(f"- **Tags:** {overall['total_tags']}")
lines.append(f"- **Total patterns:** {overall['total_patterns']:,}")
lines.append(f"- **Total responses:** {overall['total_responses']:,}")
lines.append(f"- **Noisy / templated patterns** (e.g., starting with 'Please help me with...', 'Sana matulungan mo ako sa...'): **{overall['total_noisy_patterns']:,}**")
lines.append(f"- **Responses containing unverified factual claims:** **{overall['total_unverified_responses']}**")
lines.append(f"- **Patterns that appear in more than one tag:** {overall['duplicate_patterns_across_tags']}\n")

lines.append("## Critical issues — must fix before training\n")
if critical_issues:
    for c in critical_issues:
        lines.append(f"- {c}")
else:
    lines.append("- _None detected at the critical level._")
lines.append("")

lines.append("## Warnings — should fix\n")
if warnings:
    for w in warnings:
        lines.append(f"- {w}")
else:
    lines.append("- _None._")
lines.append("")

lines.append("## Patterns that appear in MORE THAN ONE tag (top 30)\n")
lines.append("These are direct sources of model confusion — the same exact pattern is labeled with different intents.\n")
sorted_dupes = sorted(cross_dupes.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:30]
if sorted_dupes:
    lines.append(md_table(
        [[f"`{p[:60]}`", ", ".join(sorted(tags))] for p, tags in sorted_dupes],
        ["Pattern", "Appears in tags"]
    ))
else:
    lines.append("_No cross-tag duplicate patterns._")
lines.append("")

lines.append("## Per-tag stats\n")
lines.append(md_table(
    [[s["tag"], s["n_patterns"], s["n_unique_patterns"],
      s["n_duplicates"], s["n_noisy_template"],
      s["n_responses"], "yes" if s["has_tagalog_response"] else "no",
      s["n_misplaced"], s["n_unverified_response_claims"]] for s in per_tag_stats],
    ["tag", "patterns", "unique", "dupes", "noisy", "responses", "tagalog?", "misplaced", "unverified resp."]
))
lines.append("")

lines.append("## What 'unverified' means here\n")
lines.append("The audit flags responses containing claims I cannot verify from training data and that should not be presented to students as fact:\n")
lines.append("- Specific campus *counts* (e.g., '13 campuses', '11 colleges') — the number changes over time.")
lines.append("- A specific *list* of campuses with establishment years (these dates in the source file are not corroborated).")
lines.append("- Specific student-population, acceptance-rate, or library-book statistics (e.g., '92,000 books', '7,490 admitted', '34.45% acceptance rate', '84+ recognized organizations', '72 hectares').")
lines.append("- Specific phone numbers and named offices' email addresses — these change and should be looked up on the official directory.")
lines.append("- Awards, accreditations, and 'Center of Excellence' designations — these are time-sensitive and program-specific.\n")
lines.append("**What stays in the dataset:** CvSU's broad history (1906 origins as Indang Intermediate School during the Thomasite era; university status granted in 1998 via RA 8468), main-campus location (Don Severino delas Alas Campus, Indang, Cavite), motto ('Truth, Excellence, Service'), 'Iskolar para sa Bayan', RA 10931 (Universal Access to Quality Tertiary Education Act of 2017), NSTP under RA 9163, and the general 1.0–5.0 grading scale used by Philippine SUCs.\n")

REPORT.write_text("\n".join(lines), encoding="utf-8")
print("Wrote", REPORT)
print("Wrote", CSV_OUT)
print()
print("Top-level:")
for k, v in overall.items():
    print(f"  {k}: {v}")
print()
print("Critical issues:", len(critical_issues))
print("Warnings:", len(warnings))
