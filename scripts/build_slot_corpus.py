"""Build the ATIS-format BIO slot corpus from data/cavsu_intents.json.

Weak-labels the 3135 intent patterns with five gazetteer/rule slot types, splits
them group-aware so template families cannot leak across splits, and writes the
ATIS-style corpus. It does NOT train anything, does NOT touch data/cavsu_intents.json,
and does NOT produce human-verified labels -- every span here is machine-generated.

Reads : data/cavsu_intents.json, api/campus_context.py, api/campus_places.py,
        api/campus_directory.py, data/waypoints_override.json, data/custom_markers.json
Writes: data/slots/{train,valid,test}, data/slots/vocab.slot,
        data/slots/vocab.intent, data/slots/build_report.json

Usage: python scripts/build_slot_corpus.py [--seed 42] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
from datetime import datetime, timezone
from typing import Mapping, Sequence

os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")
os.environ.pop("DATABASE_URL", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# api.hybrid_chatbot is deliberately never imported here: it pulls TensorFlow in
# at module level and this script must stay a cheap offline data job.
from api import campus_context, campus_directory, campus_places  # noqa: E402
from api.slot_schema import (  # noqa: E402
    MAX_LEN,
    SCHEMA_VERSION,
    SLOT_TYPES,
    TAGS,
    fold,
    joint_tokenize,
    tokens_of,
    vocab_key,
    write_corpus,
    write_vocab,
)

PRIORITY: tuple[str, ...] = (
    "grade_value",
    "document_type",
    "program_name",
    "campus_name",
    "campus_place",
)

# Surfaces that are removed from EVERY gazetteer as standalone entries. They
# remain matchable inside longer entries ("College of Engineering").
AMBIGUOUS_BLOCKLIST: frozenset[str] = frozenset(
    {"coe", "cav", "con", "cas", "ced", "bsa", "let", "als", "may", "ba", "po"}
)

# Generic heads that are only ever part of a longer name, never a span.
BARE_HEAD_BLOCKLIST: frozenset[str] = frozenset(
    {
        "certificate", "clearance", "id", "subject", "faculty", "staff", "dean",
        "engineering", "education", "office", "program", "course", "degree",
    }
)

ROLE_HEADS: frozenset[str] = frozenset(
    {
        "dean", "deans", "registrar", "administrator", "president", "chancellor",
        "counselor", "director", "coordinator", "head",
    }
)

_WHO_CUES: frozenset[str] = frozenset({"who", "whos", "sino"})
_WHO_CUE_WINDOW = 3
_DEANS_LIST_FOLLOWERS: frozenset[str] = frozenset({"list", "lister"})

# A.1 strips a trailing "campus" from every campus alias. For "main campus" that
# leaves the bare token "main", which occurs far more often as "main gate" /
# "main building" than as a campus reference, so the stripped form is dropped
# rather than allowed to fire. The canonical "Indang" alias still covers it.
_GENERIC_AFTER_CAMPUS_STRIP: frozenset[str] = frozenset({"main"})

_GRADE_NUM_RE = re.compile(r"^[1-5]\.(?:0|00|25|5|50|75)$")
_GRADE_LITERALS: tuple[str, ...] = (
    "INC", "DRP", "GWA", "incomplete grade", "passing grade", "failing grade",
)

# NEW hand-written closed list -- deliberately excludes the cue words
# (document|paper|requirement|file) that EntityExtractor.ENTITY_PATTERNS matches,
# because those are cues, not spans.
_DOCUMENT_TYPES: tuple[str, ...] = (
    "TOR", "transcript of records", "transcript", "Form 137", "Form 138", "F137",
    "F138", "diploma", "good moral certificate",
    "certificate of good moral character", "certificate of registration",
    "certificate of enrollment", "certificate of grades",
    "certificate of employment", "honorable dismissal", "NBI clearance",
    "barangay clearance", "police clearance", "student clearance", "red ribbon",
    "apostille", "authentication", "student ID", "school ID", "alumni ID",
    "library card", "affidavit of loss", "birth certificate",
    "PSA birth certificate", "medical certificate", "report card",
    "character reference", "reference letter", "NSTP certificate",
)

# Harvested once with ^BS\s+[A-Z] over data/cavsu_intents.json and pruned by hand
# (trailing "CvSU", "available", "details", "ba sa CvSU" removed). CLOSED LIST:
# no productive "BS <anything>" matching, which over-captured in Survey 1.
_PROGRAM_NAMES: tuple[str, ...] = (
    "BS Accountancy", "BS Agribusiness", "BS Agricultural Engineering",
    "BS Agriculture", "BS Agroforestry", "BS Animal Science",
    "BS Business Administration", "BS Business Management",
    "BS Chemical Engineering", "BS Civil Engineering", "BS COMSCI",
    "BS Computer Science", "BS Criminology", "BS Crop Science", "BS Education",
    "BS Electrical Engineering", "BS Electronics Engineering",
    "BS Elementary Education", "BS Entrepreneurship", "BS Food Technology",
    "BS Forestry", "BS Geodetic Engineering", "BS Horticulture",
    "BS Hospitality Management", "BS Industrial Engineering", "BS Management",
    "BS Marketing", "BS Mechanical Engineering", "BS Medical Technology",
    "BS Midwifery", "BS Nursing", "BS Office Administration", "BS Psychology",
    "BS Public Safety", "BS Secondary Education", "BS Tourism",
    "BS Veterinary Medicine",
    "BSIT", "BSCS", "BSBA", "BSN", "BSED", "BEED", "BSEED", "BSHM", "BSTM",
    "DVM", "COMSCI",
)

# Gap fix required by the contract; api/campus_context.py itself is NOT edited.
_EXTRA_CAMPUS_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Dasmarinas", ("dasmarinas", "dasmariñas")),
)

_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_DASH_SPLIT_RE = re.compile(r"\s[–—-]\s")

_blocklist_hits = 0


def _entry(text: str) -> tuple[str, ...]:
    return tuple(fold(t) for t in tokens_of(text))


def _add(bucket: set[tuple[str, ...]], text: str) -> None:
    """Add one gazetteer surface, applying both standalone blocklists."""
    global _blocklist_hits
    ent = _entry(text)
    if not ent:
        return
    if len(ent) == 1 and (
        ent[0] in AMBIGUOUS_BLOCKLIST or ent[0] in BARE_HEAD_BLOCKLIST
    ):
        _blocklist_hits += 1
        return
    bucket.add(ent)


def _specific_place_terms() -> list[str]:
    """Alternation members of campus_context._SPECIFIC_PLACE_RE as LITERAL
    strings. The compiled pattern is never used as a regex here."""
    pat = campus_context._SPECIFIC_PLACE_RE.pattern.replace("\\b", "")
    pat = pat.strip()
    if pat.startswith("(") and pat.endswith(")"):
        pat = pat[1:-1]
    return [p.strip() for p in pat.split("|") if p.strip()]


def _json_labels(path: str) -> list[str]:
    """Label/name strings out of an override JSON, tolerant of an empty file."""
    if not os.path.exists(path):
        print(f"[WARN] missing optional gazetteer source: {path}")
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    out: list[str] = []
    values = data.values() if isinstance(data, dict) else data
    for item in values:
        if isinstance(item, dict):
            for key in ("label", "name", "title"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    out.append(val.strip())
                    break
    if not out:
        print(f"[WARN] no label fields in {path} -- contributes 0 campus_place entries")
    return sorted(set(out))


def _place_surfaces(text: str) -> list[str]:
    """One metadata string -> the surfaces worth matching.

    Fulls join two names with an en dash ("... (OSAS) - University Registrar")
    and carry a trailing acronym in parentheses; both the raw and the
    parenthetical-stripped form of each dash-separated part are kept.
    """
    out: list[str] = []
    for part in _DASH_SPLIT_RE.split(text):
        part = part.strip()
        if not part:
            continue
        out.append(part)
        stripped = _PAREN_RE.sub("", part).strip()
        if stripped and stripped != part:
            out.append(stripped)
    return out


def build_gazetteers() -> dict[str, list[tuple[str, ...]]]:
    """slot_type -> folded token tuples, deduped, sorted by (-len, tuple) so a
    longest-match-wins lookup is a plain linear scan."""
    global _blocklist_hits
    # Reset first: _blocklist_hits is module state, and without this a second
    # call would double-count into build_report.json's suppressed.blocklist_hits.
    _blocklist_hits = 0
    buckets: dict[str, set[tuple[str, ...]]] = {t: set() for t in SLOT_TYPES}

    campuses: list[tuple[str, Sequence[str]]] = [
        (canonical, aliases) for canonical, aliases in campus_context.CAMPUSES.items()
    ]
    campuses.extend(_EXTRA_CAMPUS_ALIASES)
    for canonical, aliases in campuses:
        for surface in (_PAREN_RE.sub("", canonical).strip(), *aliases):
            ent = _entry(surface)
            if ent and ent[-1] == "campus":
                ent = ent[:-1]
            if not ent:
                continue
            if len(ent) == 1 and (
                ent[0] in AMBIGUOUS_BLOCKLIST
                or ent[0] in BARE_HEAD_BLOCKLIST
                or ent[0] in _GENERIC_AFTER_CAMPUS_STRIP
            ):
                continue
            buckets["campus_name"].add(ent)

    for meta in campus_places._PLACE_METADATA.values():
        if meta.get("num") == 0:
            # The virtual full-campus view; its short label is the bare word
            # "Campus", which is not a named physical place (A.2) and would
            # contradict the A.1 boundary rule.
            continue
        for field in ("short", "full"):
            for surface in _place_surfaces(str(meta.get(field, ""))):
                _add(buckets["campus_place"], surface)

    for _place_id, _blurb, terms in campus_places._CATEGORY_PLACES:
        for term in terms:
            _add(buckets["campus_place"], term)

    for surface in (
        campus_places._OSAS_BUILDING,
        campus_places._ADMIN_BUILDING,
        campus_places._OUR,
    ):
        for part in _place_surfaces(surface):
            _add(buckets["campus_place"], part)

    for term in _specific_place_terms():
        _add(buckets["campus_place"], term)

    for key in campus_directory.DIRECTORY:
        for part in _place_surfaces(key):
            _add(buckets["campus_place"], part)

    for label in _json_labels(os.path.join("data", "waypoints_override.json")):
        _add(buckets["campus_place"], label)
    for label in _json_labels(os.path.join("data", "custom_markers.json")):
        _add(buckets["campus_place"], label)

    for surface in _DOCUMENT_TYPES:
        _add(buckets["document_type"], surface)
    for surface in _PROGRAM_NAMES:
        _add(buckets["program_name"], surface)
    for surface in _GRADE_LITERALS:
        _add(buckets["grade_value"], surface)

    return {
        slot: sorted(entries, key=lambda e: (-len(e), e))
        for slot, entries in buckets.items()
    }


# Built once per gazetteer mapping; label_tokens' signature is frozen by the
# contract so the index cannot be threaded through as an argument.
# The cache VALUE keeps a strong reference to the mapping it was keyed on. Without
# that reference CPython may reuse the id() of a collected mapping for a different
# one, and label_tokens would silently label against a stale gazetteer.
_INDEX_CACHE: dict[
    int, tuple[Mapping[str, list[tuple[str, ...]]], dict[str, dict[str, list[tuple[str, ...]]]]]
] = {}


def _index(gazetteers: Mapping[str, list[tuple[str, ...]]]):
    cached = _INDEX_CACHE.get(id(gazetteers))
    if cached is not None and cached[0] is gazetteers:
        return cached[1]
    index: dict[str, dict[str, list[tuple[str, ...]]]] = {}
    for slot, entries in gazetteers.items():
        by_first: dict[str, list[tuple[str, ...]]] = {}
        for ent in entries:
            by_first.setdefault(ent[0], []).append(ent)
        for bucket in by_first.values():
            bucket.sort(key=lambda e: (-len(e), e))
        index[slot] = by_first
    _INDEX_CACHE[id(gazetteers)] = (gazetteers, index)
    return index


_suppressed = {"who_is": 0, "deans_list": 0}


def label_tokens(
    tokens: Sequence[str], gazetteers: Mapping[str, list[tuple[str, ...]]]
) -> list[str]:
    """Left-to-right, non-overlapping, longest-match-wins across PRIORITY."""
    index = _index(gazetteers)
    folded = [fold(t) for t in tokens]
    n = len(folded)
    tags = ["O"] * n
    who_context = any(f in _WHO_CUES for f in folded[:_WHO_CUE_WINDOW])

    i = 0
    while i < n:
        head = folded[i]
        match_type: str | None = None
        match_len = 0
        for slot in PRIORITY:
            best = 0
            for ent in index[slot].get(head, ()):
                if len(ent) <= n - i and tuple(folded[i : i + len(ent)]) == ent:
                    best = len(ent)
                    break
            if slot == "grade_value" and best == 0 and _GRADE_NUM_RE.match(tokens[i]):
                best = 1
            if best:
                match_type = slot
                match_len = best
                break

        if match_type is None:
            i += 1
            continue

        if who_context and head in ROLE_HEADS:
            _suppressed["who_is"] += 1
            i += 1
            continue
        end = i + match_len
        if head.startswith("dean") and end < n and folded[end] in _DEANS_LIST_FOLLOWERS:
            _suppressed["deans_list"] += 1
            i += 1
            continue

        tags[i] = f"B-{match_type}"
        for j in range(i + 1, end):
            tags[j] = f"I-{match_type}"
        i = end

    return tags


def label_pattern(
    pattern: str, intent: str, gazetteers: Mapping[str, list[tuple[str, ...]]]
) -> tuple[list[str], list[str], str] | None:
    spans = joint_tokenize(pattern)
    if not spans:
        return None
    tokens = [t for t, _s, _e in spans]
    # Round-trip invariant: the tokenizer must consume every non-space character
    # verbatim, or a predicted span could not be sliced back out of the text.
    assert "".join(pattern[s:e] for _t, s, e in spans) == "".join(pattern.split())
    assert all(tok == pattern[s:e] for tok, s, e in spans)
    return tokens, label_tokens(tokens, gazetteers), intent


_DIGIT_RE = re.compile(r"[0-9]")


def group_key(tokens: Sequence[str], tags: Sequence[str], intent: str) -> str:
    parts: list[str] = []
    for tok, tag in zip(tokens, tags):
        if tag == "O":
            parts.append(_DIGIT_RE.sub("#", vocab_key(tok)))
        elif tag.startswith("B-"):
            parts.append("<" + tag[2:] + ">")
    return intent + "|" + " ".join(p for p in parts if p)


def split_rows(rows, seed: int):
    """(train, valid, test), group-aware per intent. Deterministic.

    Random line splitting is forbidden: near-duplicate template families
    ("what does 1.0 mean" x11) would otherwise straddle the splits and inflate F1.
    """
    by_intent: dict[str, dict[str, list]] = {}
    for row in rows:
        # Rows carry an optional 4th element (the original pattern index, used by
        # _order). Unpack positionally so a bare label_pattern() 3-tuple also works.
        tokens, tags, intent = row[0], row[1], row[2]
        by_intent.setdefault(intent, {}).setdefault(
            group_key(tokens, tags, intent), []
        ).append(row)

    train: list = []
    valid: list = []
    test: list = []
    for intent in sorted(by_intent):
        groups = by_intent[intent]
        keys = sorted(groups)
        random.Random(f"{seed}:{intent}").shuffle(keys)
        n = len(keys)
        if n < 3:
            for key in keys:
                train.extend(groups[key])
            continue
        n_test = max(1, round(0.10 * n))
        n_valid = max(1, round(0.10 * n))
        for pos, key in enumerate(keys):
            if pos < n_test:
                test.extend(groups[key])
            elif pos < n_test + n_valid:
                valid.extend(groups[key])
            else:
                train.extend(groups[key])
    return train, valid, test


def _n_spans(tags: Sequence[str]) -> int:
    return sum(1 for t in tags if t.startswith("B-"))


def _span_types(tags: Sequence[str]) -> set[str]:
    return {t[2:] for t in tags if t.startswith("B-")}


def _order(rows: list) -> list:
    return sorted(rows, key=lambda r: (r[2], r[3]))


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--intents", default="data/cavsu_intents.json")
    ap.add_argument("--out-dir", default="data/slots")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report", default="data/slots/build_report.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.intents):
        print(f"[ERROR] missing {args.intents}")
        return 1

    with open(args.intents, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    intents = payload.get("intents") or []
    if not intents:
        print(f"[ERROR] no intents in {args.intents}")
        return 1

    gazetteers = build_gazetteers()
    print("[OK] gazetteers: " + ", ".join(
        f"{slot}={len(gazetteers[slot])}" for slot in SLOT_TYPES
    ))

    # Reset before labeling: these are module counters, and a caller that ran the
    # labeler before main() would otherwise inflate suppressed.{who_is,deans_list}.
    _suppressed["who_is"] = 0
    _suppressed["deans_list"] = 0

    n_in = 0
    n_empty = 0
    n_dupe = 0
    seen: set[tuple[tuple[str, ...], str]] = set()
    rows: list = []
    for entry in intents:
        intent = str(entry.get("tag") or "").strip()
        if not intent:
            continue
        for idx, pattern in enumerate(entry.get("patterns") or []):
            n_in += 1
            labeled = label_pattern(str(pattern), intent, gazetteers)
            if labeled is None:
                n_empty += 1
                continue
            tokens, tags, _ = labeled
            key = (tuple(tokens), intent)
            if key in seen:
                n_dupe += 1
                continue
            seen.add(key)
            rows.append((tokens, tags, intent, idx))

    if not rows:
        print("[ERROR] no rows produced")
        return 1

    lengths = [len(r[0]) for r in rows]
    spans_per_type = {slot: 0 for slot in SLOT_TYPES}
    n_spans = 0
    n_with_span = 0
    n_multislot = 0
    for tokens, tags, _intent, _idx in rows:
        count = _n_spans(tags)
        n_spans += count
        if count:
            n_with_span += 1
        types = _span_types(tags)
        if len(types) >= 2:
            n_multislot += 1
        for tag in tags:
            if tag.startswith("B-"):
                spans_per_type[tag[2:]] += 1

    train, valid, test = split_rows(rows, args.seed)
    train, valid, test = _order(train), _order(valid), _order(test)
    split_spans = {
        name: sum(_n_spans(r[1]) for r in part)
        for name, part in (("train", train), ("valid", valid), ("test", test))
    }
    n_groups = len({group_key(r[0], r[1], r[2]) for r in rows})
    all_o_fraction = round(1.0 - (n_with_span / len(rows)), 4)
    intents_out = sorted({r[2] for r in rows})

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed,
        "n_intents": len(intents_out),
        "n_patterns_in": n_in,
        "n_patterns_skipped_empty": n_empty,
        "n_patterns_deduped": n_dupe,
        "n_rows_out": len(rows),
        "n_rows_with_any_span": n_with_span,
        "all_O_fraction": all_o_fraction,
        "n_spans_total": n_spans,
        "spans_per_type": spans_per_type,
        "n_rows_multislot": n_multislot,
        "suppressed": {
            "who_is": _suppressed["who_is"],
            "deans_list": _suppressed["deans_list"],
            "blocklist_hits": _blocklist_hits,
        },
        "split": {"train": len(train), "valid": len(valid), "test": len(test)},
        "split_spans": split_spans,
        "n_groups": n_groups,
        "gazetteer_sizes": {slot: len(gazetteers[slot]) for slot in SLOT_TYPES},
        "token_stats": {
            "median_len": int(statistics.median(lengths)),
            "mean_len": round(sum(lengths) / len(lengths), 3),
            "max_len": max(lengths),
            "n_over_max_len": sum(1 for n in lengths if n > MAX_LEN),
        },
    }

    print("\n===== CORPUS =====")
    print(f"patterns in            : {n_in}")
    print(f"skipped (no tokens)    : {n_empty}")
    print(f"deduped                : {n_dupe}")
    print(f"rows out               : {len(rows)}")
    print(f"rows with >=1 span     : {n_with_span} "
          f"({100.0 * n_with_span / len(rows):.1f}%)")
    print(f"all_O_fraction         : {all_o_fraction:.4f} "
          f"({100.0 * all_o_fraction:.1f}% of rows carry NO span)")
    print(f"rows with >=2 types    : {n_multislot} "
          f"({100.0 * n_multislot / len(rows):.1f}%)")
    print(f"spans total            : {n_spans}")
    for slot in SLOT_TYPES:
        print(f"  {slot:<14}     : {spans_per_type[slot]}")
    print(f"suppressed who_is      : {_suppressed['who_is']}")
    print(f"suppressed deans_list  : {_suppressed['deans_list']}")
    print(f"blocklisted entries    : {_blocklist_hits}")
    print(f"groups                 : {n_groups}")
    print(f"token len median/mean/max: {report['token_stats']['median_len']}/"
          f"{report['token_stats']['mean_len']}/{report['token_stats']['max_len']}"
          f"  over MAX_LEN={MAX_LEN}: {report['token_stats']['n_over_max_len']}")

    print("\n===== SPLIT =====")
    print(f"split       : {report['split']}")
    print(f"split_spans : {split_spans}")

    print("\n===== SAMPLE (10 labeled lines, eyeball QA) =====")
    sample = [r for r in train if _n_spans(r[1])][:10]
    if len(sample) < 10:
        sample += [r for r in train if not _n_spans(r[1])][: 10 - len(sample)]
    for tokens, tags, intent, _idx in sample:
        line = " ".join(f"{t}:{g}" for t, g in zip(tokens, tags)) + " <=> " + intent
        print("  " + _ascii(line))

    if args.dry_run:
        print("\n[skip] --dry-run: nothing written")
        return 0

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    for name, part in (("train", train), ("valid", valid), ("test", test)):
        write_corpus(
            os.path.join(out_dir, name), [(r[0], r[1], r[2]) for r in part]
        )
    write_vocab(os.path.join(out_dir, "vocab.slot"), sorted(TAGS))
    write_vocab(os.path.join(out_dir, "vocab.intent"), intents_out)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\n[OK] wrote {out_dir}/{{train,valid,test}}, vocab.slot ({len(TAGS)}), "
          f"vocab.intent ({len(intents_out)})")
    print(f"[OK] report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
