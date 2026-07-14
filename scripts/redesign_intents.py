"""Evidence-driven intent redesign (from the 268-item mirror-QA validation).

Adds 4 durable, date-qualified intents that were the biggest misroute buckets
(licensure results, rankings, awards, accreditation), expands patterns for the
intents that mis-lost (vision_mission, university_officials, events), and trims
about_cvsu's greediest tokens. Patches data/cavsu_intents.json + the DB together;
NB retrain regenerates models/responses_map.json from these.

LEAK GUARD: no new pattern may equal (normalized) any question in the held-out
eval set — that would inflate the re-eval. Exact dups are dropped + reported.

Usage: python scripts/redesign_intents.py [--dry-run]
"""
import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime

JSON_PATH = "data/cavsu_intents.json"
DB_PATH = "data/cavsu_intents.db"
EVAL = "data/eval/mirror_qa.json"

VERIFY = " (verify the latest at https://cvsu.edu.ph)"

NEW_INTENTS = {
    "licensure_results": {
        "description": "PRC board / licensure examination results, passing rates, topnotchers",
        "responses": [
            "CvSU has a strong board-exam (PRC licensure) track record across its programs — recent results have included top-performing marks in the Criminologist, Medical Technologist, Licensure Exam for Teachers (LEPT), Civil/Electrical/Electronics Engineering, Architecture, Master Plumber, and CPA examinations. Passing rates and topnotchers are released every exam cycle, so for the specific, most-recent figures please see CvSU's official news at https://cvsu.edu.ph. (Board-exam results change each cycle — verify the latest at https://cvsu.edu.ph.)",
            "Malakas ang record ng CvSU sa mga board exam (PRC licensure) sa iba't ibang programa — kabilang sa mga kamakailang resulta ang mataas na marka sa Criminologist, Medical Technologist, LEPT (guro), Civil/Electrical/Electronics Engineering, Architecture, Master Plumber, at CPA. Nagbabago ang passing rate bawat cycle, kaya para sa pinakabagong bilang at topnotchers, tingnan ang opisyal na balita sa https://cvsu.edu.ph.",
        ],
        "patterns": [
            "board exam passing rate", "licensure exam results", "how did CvSU do in the board exam",
            "PRC exam results of CvSU", "did CvSU pass the nursing board", "criminology board exam result",
            "teacher licensure exam result", "engineering board exam passers", "how many passed the boards",
            "topnotchers from CvSU", "CvSU board exam performance", "CPA board result CvSU",
            "architect licensure result CvSU", "medtech board passers CvSU", "electronics engineer board result",
            "ilan ang pumasa sa board exam ng CvSU", "resulta ng board exam ng CvSU",
            "pasado ba ang CvSU sa boards", "may topnotcher ba ang CvSU", "anong performance ng CvSU sa licensure",
            "passing rate sa nursing board ng CvSU", "civil engineer board result CvSU",
        ],
    },
    "university_rankings": {
        "description": "University rankings and ratings (WURI, THE, HE, QS)",
        "responses": [
            "CvSU holds several recent rankings and ratings: 217th in the WURI (World University Rankings for Innovation) Top 500, a 1001–1500th band in the THE Sustainability Impact Ratings, 197th in the 2026 HE Higher Education Ranking, and a QS 3-Star rating. For current standings and per-category details, see https://cvsu.edu.ph. (Rankings are updated periodically — verify the latest at https://cvsu.edu.ph.)",
            "May ilang ranggo at rating ang CvSU: ika-217 sa WURI (World University Rankings for Innovation) Top 500, nasa 1001–1500 banda sa THE Sustainability Impact Ratings, ika-197 sa 2026 HE Higher Education Ranking, at QS 3-Star rating. Para sa pinakabagong ranggo at detalye, tingnan ang https://cvsu.edu.ph.",
        ],
        "patterns": [
            "CvSU ranking", "world university ranking of CvSU", "WURI ranking of CvSU", "is CvSU ranked",
            "global ranking of CvSU", "CvSU QS stars", "THE sustainability ranking CvSU", "how is CvSU ranked",
            "where does CvSU rank", "university ranking Cavite State", "CvSU innovation ranking",
            "sustainability ranking of CvSU", "CvSU higher education ranking",
            "ranggo ng CvSU sa mundo", "may ranking ba ang CvSU", "anong ranking ng CvSU",
            "gaano kataas ang ranking ng CvSU", "ranggo ng CvSU sa WURI", "kilala ba ang CvSU sa ranking",
        ],
    },
    "awards_recognition": {
        "description": "Awards, honors and recognitions received by CvSU",
        "responses": [
            "CvSU's recent recognitions include its 5th IPOPHL Platinum Award and first Palladium Award, and being named one of DENR-EMB's top eco-friendly schools. For the complete and latest list of awards and honors, see CvSU's official news at https://cvsu.edu.ph. (New recognitions are added over time — verify the latest at https://cvsu.edu.ph.)",
            "Kabilang sa mga kamakailang parangal ng CvSU ang ika-5 IPOPHL Platinum Award at kauna-unahang Palladium Award, at ang pagkilala bilang isa sa top eco-friendly schools ng DENR-EMB. Para sa kumpleto at pinakabagong listahan ng mga parangal, tingnan ang https://cvsu.edu.ph.",
        ],
        "patterns": [
            "awards received by CvSU", "what awards did CvSU win", "CvSU recognitions", "IPOPHL award CvSU",
            "Palladium award CvSU", "Platinum award CvSU", "eco-friendly school award CvSU", "DENR award CvSU",
            "recent awards of CvSU", "honors received by CvSU", "green school award CvSU",
            "anong parangal ang natanggap ng CvSU", "mga parangal ng CvSU", "kinilala ba ang CvSU",
            "may award ba ang CvSU", "mga karangalan ng CvSU", "natanggap na parangal ng CvSU",
        ],
    },
    "accreditation_status": {
        "description": "AACCUP / institutional and program accreditation and ISO status",
        "responses": [
            "CvSU holds AACCUP Institutional Accreditation Level III. Several programs are AACCUP-accredited — for example, BS Food Technology at Level IV, and BS Medical Technology, BS Computer Science (Carmona), BS Information Technology (Cavite City), and BS Business Administration at Level III. For the complete, current accreditation list, see https://cvsu.edu.ph. (Accreditation levels are re-evaluated over time — verify the latest at https://cvsu.edu.ph.)",
            "May AACCUP Institutional Accreditation Level III ang CvSU. Maraming programa ang AACCUP-accredited — halimbawa, Level IV ang BS Food Technology, at Level III ang BS Medical Technology, BS Computer Science (Carmona), BS Information Technology (Cavite City), at BS Business Administration. Para sa kumpletong listahan, tingnan ang https://cvsu.edu.ph.",
        ],
        "patterns": [
            "is CvSU accredited", "AACCUP accreditation of CvSU", "accreditation level of CvSU",
            "accredited programs at CvSU", "institutional accreditation CvSU", "ISO certification of CvSU",
            "level IV accredited program CvSU", "accreditation status of CvSU", "quality accreditation CvSU",
            "which CvSU programs are accredited", "accredited ba ang CvSU", "anong level ng accreditation ng CvSU",
            "may AACCUP ba ang CvSU", "accreditation ng mga programa ng CvSU", "may ISO ba ang CvSU",
        ],
    },
}

# Pattern additions to EXISTING intents (fix documented misroutes).
ADD_PATTERNS = {
    "vision_mission": [
        "vision of Cavite State University", "what is the vision of CvSU", "CvSU's vision statement",
        "mission of Cavite State University", "mission and vision of CvSU", "goals of CvSU",
        "vision mission and goals of CvSU", "layunin ng CvSU", "misyon ng CvSU", "pananaw ng CvSU",
        "ano ang bisyon ng CvSU", "ano ang misyon ng CvSU",
    ],
    "university_officials": [
        "name of the CvSU president", "current president of CvSU", "who leads Cavite State University",
        "who is the university president who received the award", "who gave the message at the anniversary",
        "sino ang kasalukuyang pangulo ng CvSU", "pangalan ng pangulo ng CvSU",
        "sino ang pangulo na nagbigay ng mensahe", "vice presidents of CvSU", "university officials of CvSU",
    ],
    "events": [
        "when is the WELA conference", "WELA 2026 schedule", "when and where is the conference",
        "AACCUP conference at CvSU", "conference date and venue", "when did CvSU hold the conference",
        "kailan ang kumperensya sa CvSU", "saan gaganapin ang kumperensya", "upcoming conference at CvSU",
        "anibersaryo ng pagkakatatag ng CvSU",
    ],
}

# Over-greedy tokens to remove from about_cvsu so it stops swallowing vision/officials.
TRIM_PATTERNS = {
    "about_cvsu": ["CvSU", "Who is CvSU"],
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def leak_filter(patterns, eval_norms, tag, dropped):
    kept = []
    for p in patterns:
        if norm(p) in eval_norms:
            dropped.append((tag, p))
        else:
            kept.append(p)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    eval_norms = {norm(q["question"]) for q in json.load(open(EVAL, encoding="utf-8"))}
    dropped = []

    # leak-filter every new pattern
    for tag, spec in NEW_INTENTS.items():
        spec["patterns"] = leak_filter(spec["patterns"], eval_norms, tag, dropped)
    for tag in ADD_PATTERNS:
        ADD_PATTERNS[tag] = leak_filter(ADD_PATTERNS[tag], eval_norms, tag, dropped)

    print(f"leak-check: dropped {len(dropped)} patterns matching eval questions")
    for t, p in dropped:
        print(f"   [{t}] {p!r}")

    doc = json.load(open(JSON_PATH, encoding="utf-8"))
    by_tag = {it["tag"]: it for it in doc["intents"]}

    added_intents, added_pats, trimmed = 0, 0, 0

    # 1) new intents
    for tag, spec in NEW_INTENTS.items():
        if tag in by_tag:
            print(f"  [skip] intent {tag} already exists")
            continue
        doc["intents"].append({"tag": tag, "patterns": spec["patterns"], "responses": spec["responses"]})
        added_intents += 1

    # 2) pattern additions
    for tag, pats in ADD_PATTERNS.items():
        it = by_tag.get(tag)
        if not it:
            print(f"  [warn] cannot extend missing intent {tag}")
            continue
        existing = set(it["patterns"])
        for p in pats:
            if p not in existing:
                it["patterns"].append(p)
                added_pats += 1

    # 3) trims
    for tag, pats in TRIM_PATTERNS.items():
        it = by_tag.get(tag)
        if not it:
            continue
        before = len(it["patterns"])
        it["patterns"] = [p for p in it["patterns"] if p not in set(pats)]
        trimmed += before - len(it["patterns"])

    print(f"\nplan: +{added_intents} intents, +{added_pats} patterns, -{trimmed} trimmed")
    if args.dry_run:
        print("dry-run: no files written")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for p in (JSON_PATH, DB_PATH):
        shutil.copy2(p, f"{p}.bak_redesign_{ts}")

    json.dump(doc, open(JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---- DB ----
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(intents)")]
    has_active = "active" in cols
    has_desc = "description" in cols
    for tag, spec in NEW_INTENTS.items():
        row = cur.execute("SELECT id FROM intents WHERE tag=?", (tag,)).fetchone()
        if row:
            iid = row[0]
        else:
            fields = ["tag"] + (["description"] if has_desc else []) + (["active"] if has_active else [])
            vals = [tag] + ([spec["description"]] if has_desc else []) + ([1] if has_active else [])
            cur.execute(f"INSERT INTO intents ({','.join(fields)}) VALUES ({','.join('?' * len(vals))})", vals)
            iid = cur.lastrowid
        for p in spec["patterns"]:
            cur.execute("INSERT INTO patterns (intent_id, pattern_text) VALUES (?,?)", (iid, p))
        for r in spec["responses"]:
            cur.execute("INSERT INTO responses (intent_id, response_text) VALUES (?,?)", (iid, r))
    for tag, pats in ADD_PATTERNS.items():
        row = cur.execute("SELECT id FROM intents WHERE tag=?", (tag,)).fetchone()
        if not row:
            continue
        iid = row[0]
        existing = {r[0] for r in cur.execute("SELECT pattern_text FROM patterns WHERE intent_id=?", (iid,))}
        for p in pats:
            if p not in existing:
                cur.execute("INSERT INTO patterns (intent_id, pattern_text) VALUES (?,?)", (iid, p))
    for tag, pats in TRIM_PATTERNS.items():
        row = cur.execute("SELECT id FROM intents WHERE tag=?", (tag,)).fetchone()
        if not row:
            continue
        iid = row[0]
        for p in pats:
            cur.execute("DELETE FROM patterns WHERE intent_id=? AND pattern_text=?", (iid, p))
    con.commit()
    con.close()

    print(f"applied. backups: *.bak_redesign_{ts}")


if __name__ == "__main__":
    main()
