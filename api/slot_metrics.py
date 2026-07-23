"""conlleval-style span metrics for BIO slot filling + ATIS intent scoring.

Ports the EVALUATION CONVENTIONS of the SLU benchmark (exact-span P/R/F1 via
conlleval chunk decoding, the ATIS multi-intent '#' scoring trick, and Liu &
Lane semantic frame accuracy). No code was taken from that repo -- this is a
clean stdlib implementation of the same definitions.

Deliberately imports NOTHING: no TensorFlow, no numpy, no sklearn, and nothing
from this repo (not even api.slot_schema). Tag sequences arrive as plain
strings, so scripts and tests can use it with zero setup and in any order.

Reads : nothing
Writes: nothing
Usage : from api.slot_metrics import bio_spans, evaluate, conlleval_report
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0.0"

# A tag counts as a chunk marker only if it is exactly 'B-<type>' / 'I-<type>'
# with a non-empty type. Everything else -- 'O', '', 'B', 'B-', 'X-foo', None-ish
# junk -- is treated as outside. Malformed input must never raise here: this
# module is called on model argmax output, where anything is possible.
_TAG_RE = re.compile(r"^([BI])-(.+)$")

_NOTE_TAG_ACC = (
    "NOTE: tag_accuracy is inflated by the O majority class; span f1 is the headline."
)


def bio_spans(tags: Sequence[str]) -> List[Tuple[str, int, int]]:
    """Decode an IOB2 tag sequence into [(slot_type, start, end_exclusive)].

    conlleval semantics, lenient on malformed sequences (never raises):
      - 'O' or an unrecognized tag closes any open chunk.
      - 'B-T' closes any open chunk and opens a new one, even after 'B-T'.
      - 'I-T' extends an open chunk of the same type.
      - 'I-T' with no open chunk opens one (lenient start).
      - 'I-T' with an open chunk of a different type closes it and opens 'T'.
      - Any chunk still open at the end of the sequence is closed at len(tags).
    Spans are returned in ascending start order.
    """
    spans: List[Tuple[str, int, int]] = []
    open_type: Optional[str] = None
    open_start = 0

    for i, raw in enumerate(tags):
        m = _TAG_RE.match(raw) if isinstance(raw, str) else None
        if m is None:
            if open_type is not None:
                spans.append((open_type, open_start, i))
                open_type = None
            continue
        prefix, slot_type = m.group(1), m.group(2)
        if prefix == "I" and open_type == slot_type:
            continue
        if open_type is not None:
            spans.append((open_type, open_start, i))
        open_type, open_start = slot_type, i

    if open_type is not None:
        spans.append((open_type, open_start, len(tags)))
    return spans


def span_counts(gold_tags: Sequence[str], pred_tags: Sequence[str]) -> Tuple[int, int, int]:
    """-> (tp, fp, fn) over exact (type, start, end) triples for ONE utterance.

    Exact match only: no partial, boundary, or type-only credit. Spans within a
    single decoded sequence are unique by construction, so set arithmetic is
    exact (no multiset handling needed).
    """
    if len(gold_tags) != len(pred_tags):
        raise ValueError(f"length mismatch: gold={len(gold_tags)} pred={len(pred_tags)}")
    gold = set(bio_spans(gold_tags))
    pred = set(bio_spans(pred_tags))
    tp = len(gold & pred)
    return tp, len(pred) - tp, len(gold) - tp


def prf(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    """-> (precision, recall, f1). Every zero denominator yields 0.0, including
    the all-O-vs-all-O case where nothing was predicted and nothing was gold."""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def intent_correct(gold: str, pred: str) -> bool:
    """ATIS multi-intent convention: gold may be compound ('a#b'); a single-label
    prediction is correct if it names the whole gold string or any member."""
    return pred == gold or pred in gold.split("#")


def _validate_shapes(
    gold_tags: Sequence[Sequence[str]],
    pred_tags: Sequence[Sequence[str]],
    gold_intents: Optional[Sequence[str]],
    pred_intents: Optional[Sequence[str]],
) -> bool:
    """-> intent_supplied. Raises ValueError on any shape disagreement."""
    if len(gold_tags) != len(pred_tags):
        raise ValueError(f"length mismatch: gold={len(gold_tags)} pred={len(pred_tags)}")
    if (gold_intents is None) != (pred_intents is None):
        raise ValueError("gold_intents and pred_intents must both be given or both omitted")
    if gold_intents is None:
        return False
    if len(gold_intents) != len(gold_tags) or len(pred_intents) != len(gold_tags):
        raise ValueError(
            f"intent length mismatch: utterances={len(gold_tags)} "
            f"gold={len(gold_intents)} pred={len(pred_intents)}"
        )
    return True


def _per_label_table(
    per_type: Dict[str, List[int]], labels: Optional[Sequence[str]]
) -> Tuple[Dict[str, Dict[str, float]], float]:
    """-> (per_label report, macro f1). Types with no gold and no prediction are
    excluded from the macro mean, so an unused label cannot dilute it."""
    # dict.fromkeys de-duplicates while preserving the caller's order. Without
    # it a repeated label would appear once in per_label (dicts dedupe) but be
    # counted twice in the macro mean -- a silently wrong headline number.
    label_list = sorted(per_type) if labels is None else list(dict.fromkeys(labels))
    per_label: Dict[str, Dict[str, float]] = {}
    macro_f1s: List[float] = []
    for slot_type in label_list:
        l_tp, l_fp, l_fn = per_type.get(slot_type, (0, 0, 0))
        l_p, l_r, l_f1 = prf(l_tp, l_fp, l_fn)
        per_label[slot_type] = {
            "precision": l_p,
            "recall": l_r,
            "f1": l_f1,
            "support": l_tp + l_fn,
        }
        if (l_tp + l_fn) > 0 or (l_tp + l_fp) > 0:
            macro_f1s.append(l_f1)
    return per_label, (sum(macro_f1s) / len(macro_f1s)) if macro_f1s else 0.0


def _tally_types(
    per_type: Dict[str, List[int]],
    gold: set,
    pred: set,
    hits: set,
) -> None:
    """Accumulate per-slot-type [tp, fp, fn] in place. Gold spans start life as
    false negatives and predicted spans as false positives; an exact match then
    moves one count out of each column into tp."""
    for slot_type, _s, _e in gold:
        per_type.setdefault(slot_type, [0, 0, 0])[2] += 1
    for slot_type, _s, _e in pred:
        per_type.setdefault(slot_type, [0, 0, 0])[1] += 1
    for slot_type, _s, _e in hits:
        counts = per_type[slot_type]
        counts[0] += 1
        counts[1] -= 1
        counts[2] -= 1


def evaluate(
    gold_tags: Sequence[Sequence[str]],
    pred_tags: Sequence[Sequence[str]],
    gold_intents: Optional[Sequence[str]] = None,
    pred_intents: Optional[Sequence[str]] = None,
    labels: Optional[Sequence[str]] = None,
) -> Dict:
    """Full span + intent + frame report over a corpus. See docs/SLOT_FILLING.md.

    Raises ValueError if the outer sequences differ in length, if exactly one of
    gold_intents/pred_intents is given, if the intent sequences do not match the
    utterance count, or if any utterance's gold/pred tag lengths differ.
    """
    intent_supplied = _validate_shapes(gold_tags, pred_tags, gold_intents, pred_intents)

    tp = fp = fn = 0
    n_gold_spans = n_pred_spans = 0
    n_all_o_gold = 0
    n_tokens = n_tag_hits = 0
    n_frame_hits = 0
    n_intent_hits = 0
    per_type: Dict[str, List[int]] = {}

    for i, (g_tags, p_tags) in enumerate(zip(gold_tags, pred_tags)):
        if len(g_tags) != len(p_tags):
            raise ValueError(
                f"length mismatch at utterance {i}: gold={len(g_tags)} pred={len(p_tags)}"
            )
        gold = set(bio_spans(g_tags))
        pred = set(bio_spans(p_tags))
        hits = gold & pred

        n_gold_spans += len(gold)
        n_pred_spans += len(pred)
        if not gold:
            n_all_o_gold += 1
        tp += len(hits)
        fp += len(pred) - len(hits)
        fn += len(gold) - len(hits)

        _tally_types(per_type, gold, pred, hits)

        n_tokens += len(g_tags)
        n_tag_hits += sum(1 for a, b in zip(g_tags, p_tags) if a == b)

        if intent_supplied:
            ok_intent = intent_correct(gold_intents[i], pred_intents[i])
            n_intent_hits += 1 if ok_intent else 0
            if ok_intent and gold == pred:
                n_frame_hits += 1

    per_label, macro_f1 = _per_label_table(per_type, labels)
    micro_p, micro_r, micro_f1 = prf(tp, fp, fn)
    n = len(gold_tags)
    # No "schema_version" key here on purpose: the contract froze this dict's
    # key set and downstream reports embed it verbatim.
    return {
        "n_utterances": n,
        "n_gold_spans": n_gold_spans,
        "n_pred_spans": n_pred_spans,
        "all_O_gold_fraction": (n_all_o_gold / n) if n else 0.0,
        "span_micro": {
            "precision": micro_p,
            "recall": micro_r,
            "f1": micro_f1,
            "support": n_gold_spans,
        },
        "span_macro_f1": macro_f1,
        "per_label": per_label,
        "tag_accuracy": (n_tag_hits / n_tokens) if n_tokens else 0.0,
        "intent_supplied": intent_supplied,
        "intent_accuracy": (n_intent_hits / n) if (intent_supplied and n) else 0.0,
        "frame_accuracy": (n_frame_hits / n) if (intent_supplied and n) else 0.0,
    }


def conlleval_report(result: Dict) -> str:
    """ASCII-only fixed-width rendering of evaluate()'s dict. No trailing newline."""
    micro = result["span_micro"]
    lines = [
        "===== SPAN METRICS (conlleval exact match) =====",
        "utterances: {n}   gold spans: {g}   pred spans: {p}   all-O gold: {o:.1f}%".format(
            n=result["n_utterances"],
            g=result["n_gold_spans"],
            p=result["n_pred_spans"],
            o=100.0 * result["all_O_gold_fraction"],
        ),
        "",
        "{:<20}{:>10}{:>10}{:>10}{:>10}".format("label", "precision", "recall", "f1", "support"),
        "-" * 60,
    ]
    for slot_type, scores in result["per_label"].items():
        lines.append(
            "{:<20}{:>9.1f}%{:>9.1f}%{:>9.1f}%{:>10}".format(
                slot_type[:20],
                100.0 * scores["precision"],
                100.0 * scores["recall"],
                100.0 * scores["f1"],
                scores["support"],
            )
        )
    lines.append("-" * 60)
    lines.append(
        "{:<20}{:>9.1f}%{:>9.1f}%{:>9.1f}%{:>10}".format(
            "micro",
            100.0 * micro["precision"],
            100.0 * micro["recall"],
            100.0 * micro["f1"],
            micro["support"],
        )
    )
    lines.append("macro f1: {:.1f}%".format(100.0 * result["span_macro_f1"]))
    lines.append("")
    if result.get("intent_supplied"):
        lines.append(
            "intent accuracy: {:.1f}%   frame accuracy: {:.1f}%".format(
                100.0 * result["intent_accuracy"], 100.0 * result["frame_accuracy"]
            )
        )
    else:
        lines.append("intent accuracy: n/a (intents not supplied)   frame accuracy: n/a")
    lines.append("tag accuracy: {:.1f}%".format(100.0 * result["tag_accuracy"]))
    lines.append(_NOTE_TAG_ACC)
    return "\n".join(lines)
