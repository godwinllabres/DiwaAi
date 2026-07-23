"""Offline regression for api/slot_metrics.py -- run:
    python test_slot_metrics.py   (or: pytest test_slot_metrics.py)

Needs no models, no server, no TensorFlow, no corpus and no database: the module
under test is stdlib-only arithmetic over tag strings. Every expected value below
was worked out by hand from the definitions in the frozen contract (section F),
not by running the implementation and pasting its output; the derivations are in
the comments so a wrong implementation cannot quietly redefine "correct".

Output is ASCII only.
"""
from typing import List

from api import slot_metrics as sm

EPS = 1e-9


def _close(got: float, want: float) -> bool:
    return abs(got - want) < EPS


# --------------------------------------------------------------------------
# bio_spans: chunk decoding, including every malformed shape the contract lists
# --------------------------------------------------------------------------

def test_bio_spans_empty():
    assert sm.bio_spans([]) == []                                   # case 1


def test_bio_spans_all_o():
    assert sm.bio_spans(["O", "O", "O"]) == []                      # case 2


def test_bio_spans_single_b():
    assert sm.bio_spans(["B-campus_name"]) == [("campus_name", 0, 1)]   # case 3


def test_bio_spans_lenient_i_without_b():
    # case 4: 'I-' with nothing open opens the chunk instead of dropping the
    # token. Dropping it would silently delete a real prediction.
    assert sm.bio_spans(["I-campus_name"]) == [("campus_name", 0, 1)]


def test_bio_spans_i_of_different_type_closes_and_opens():
    # case 5: 'I-document_type' cannot continue an open campus_name chunk, so
    # the campus_name closes at index 1 and a document_type opens there.
    assert sm.bio_spans(["B-campus_name", "I-document_type"]) == [
        ("campus_name", 0, 1),
        ("document_type", 1, 2),
    ]


def test_bio_spans_o_splits_same_type():
    # case 6: an intervening O breaks one type into two separate chunks.
    assert sm.bio_spans(["B-campus_place", "O", "I-campus_place"]) == [
        ("campus_place", 0, 1),
        ("campus_place", 2, 3),
    ]


def test_bio_spans_b_after_b_same_type():
    # case 7: B- always starts a NEW chunk, even directly after B- of the same
    # type -- that is the whole point of the B/I distinction.
    assert sm.bio_spans(["B-x", "B-x"]) == [("x", 0, 1), ("x", 1, 2)]


def test_bio_spans_chunk_closed_at_end_of_sequence():
    assert sm.bio_spans(["O", "B-x"]) == [("x", 1, 2)]              # case 8


def test_bio_spans_unrecognized_tags_are_outside():
    # case 9: 'X-foo' (bad prefix), 'B' (no dash), '' (empty) are all treated as
    # O. Never raises -- this function is fed raw model argmax output.
    assert sm.bio_spans(["X-foo", "B", "", "O"]) == []


def test_bio_spans_empty_type_is_outside():
    # 'B-' has an empty type; the regex requires at least one type character.
    assert sm.bio_spans(["B-", "I-"]) == []


def test_bio_spans_malformed_worked_example():
    # Contract section F.1, walked by hand:
    #   i=0 I-a, nothing open  -> open (a,0)
    #   i=1 I-a, open a        -> extend
    #   i=2 I-b, open a != b   -> close (a,0,2); open (b,2)
    #   i=3 O                  -> close (b,2,3)
    #   i=4 B-b                -> open (b,4)
    #   i=5 I-b, open b        -> extend
    #   i=6 B-b                -> close (b,4,6); open (b,6)
    #   i=7 O                  -> close (b,6,7)
    tags = ["I-a", "I-a", "I-b", "O", "B-b", "I-b", "B-b", "O"]
    assert sm.bio_spans(tags) == [("a", 0, 2), ("b", 2, 3), ("b", 4, 6), ("b", 6, 7)]


def test_bio_spans_open_chunk_closes_at_len():
    assert sm.bio_spans(["O", "B-a", "I-a"]) == [("a", 1, 3)]


# --------------------------------------------------------------------------
# span_counts / prf
# --------------------------------------------------------------------------

def test_span_counts_perfect_match():
    gold = ["B-a", "I-a", "O", "B-b"]
    assert sm.span_counts(gold, list(gold)) == (2, 0, 0)
    assert sm.prf(2, 0, 0) == (1.0, 1.0, 1.0)


def test_span_counts_total_mismatch():
    # Nothing in common: 2 gold spans, 2 predicted, zero overlap.
    gold = ["B-a", "I-a", "O", "B-b"]
    pred = ["O", "B-c", "I-c", "O"]
    assert sm.bio_spans(gold) == [("a", 0, 2), ("b", 3, 4)]
    assert sm.bio_spans(pred) == [("c", 1, 3)]
    assert sm.span_counts(gold, pred) == (0, 1, 2)
    p, r, f1 = sm.prf(0, 1, 2)
    assert (p, r, f1) == (0.0, 0.0, 0.0)


def test_span_counts_boundary_one_token_short():
    # case 10: predicted span is one token short. Exact match only -> no partial
    # credit; it is simultaneously a false positive and a false negative.
    assert sm.span_counts(["B-a", "I-a"], ["B-a", "O"]) == (0, 1, 1)
    assert sm.prf(0, 1, 1) == (0.0, 0.0, 0.0)


def test_span_counts_boundary_one_token_long():
    # gold (a,0,1); pred (a,0,2)
    assert sm.span_counts(["B-a", "O"], ["B-a", "I-a"]) == (0, 1, 1)


def test_span_counts_type_confusion_correct_boundaries():
    # case 11: boundaries identical, type wrong -> no type-only credit.
    assert sm.span_counts(["B-program_name"], ["B-document_type"]) == (0, 1, 1)


def test_span_counts_no_partial_credit_on_three_token_span():
    # case 12: 2 of 3 tokens right is still a miss.
    assert sm.span_counts(["B-a", "I-a", "I-a"], ["B-a", "I-a", "O"]) == (0, 1, 1)


def test_span_counts_one_right_one_wrong():
    # case 13: (cn,0,1) matches; gold (dt,2,4) vs pred (dt,2,3) differ.
    gold = ["B-cn", "O", "B-dt", "I-dt"]
    pred = ["B-cn", "O", "B-dt", "O"]
    assert sm.span_counts(gold, pred) == (1, 1, 1)
    p, r, f1 = sm.prf(1, 1, 1)
    assert (p, r, f1) == (0.5, 0.5, 0.5)


def test_span_counts_all_o_both_sides():
    # case 14: nothing gold, nothing predicted. Every denominator is 0, so
    # P = R = F1 = 0.0 by definition (NOT 1.0, and never a ZeroDivisionError).
    assert sm.span_counts(["O"], ["O"]) == (0, 0, 0)
    assert sm.prf(0, 0, 0) == (0.0, 0.0, 0.0)


def test_span_counts_empty_sequences():
    assert sm.span_counts([], []) == (0, 0, 0)


def test_span_counts_length_mismatch_raises():
    # case 15
    try:
        sm.span_counts(["B-a"], ["B-a", "O"])
    except ValueError as exc:
        assert "length mismatch" in str(exc)
    else:
        raise AssertionError("span_counts must raise ValueError on length mismatch")


def test_prf_precision_only():
    # tp=1 fp=1 fn=0 -> P=1/2, R=1/1=1.0, F1 = 2*(0.5*1)/(1.5) = 2/3
    p, r, f1 = sm.prf(1, 1, 0)
    assert _close(p, 0.5) and _close(r, 1.0) and _close(f1, 2.0 / 3.0)


# --------------------------------------------------------------------------
# evaluate: micro, macro, intents, frames
# --------------------------------------------------------------------------

def test_evaluate_micro_two_sentences():
    # case 16. s1 contributes (tp,fp,fn) = (1,0,0); s2 contributes (0,1,2).
    # Totals tp=1 fp=1 fn=2 -> P = 1/2, R = 1/3,
    # F1 = 2*(1/2)(1/3) / (1/2 + 1/3) = (1/3)/(5/6) = 2/5 = 0.4
    gold = [["B-a"], ["B-a", "O", "B-b"]]
    pred = [["B-a"], ["O", "B-a", "O"]]
    res = sm.evaluate(gold, pred)
    assert res["n_gold_spans"] == 3 and res["n_pred_spans"] == 2
    assert _close(res["span_micro"]["precision"], 0.5)
    assert _close(res["span_micro"]["recall"], 1.0 / 3.0)
    assert _close(res["span_micro"]["f1"], 0.4)
    assert res["span_micro"]["support"] == 3


def test_evaluate_macro_is_unweighted():
    # case 17. Type A: tp=1 fp=0 fn=0 -> F1 1.0. Type B: tp=0 fp=0 fn=1 -> F1 0.0.
    # Macro = (1.0 + 0.0) / 2 = 0.5, regardless of support.
    res = sm.evaluate([["B-A", "B-B"]], [["B-A", "O"]])
    assert _close(res["per_label"]["A"]["f1"], 1.0)
    assert _close(res["per_label"]["B"]["f1"], 0.0)
    assert _close(res["span_macro_f1"], 0.5)
    assert res["per_label"]["A"]["support"] == 1
    assert res["per_label"]["B"]["support"] == 1


def test_evaluate_macro_skips_types_with_no_gold_and_no_pred():
    # 'unused' is requested explicitly but never occurs -> support 0, predicted 0,
    # excluded from the macro mean (otherwise a phantom 0.0 would drag it down).
    res = sm.evaluate([["B-A"]], [["B-A"]], labels=["A", "unused"])
    assert list(res["per_label"].keys()) == ["A", "unused"]
    assert res["per_label"]["unused"] == {
        "precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0,
    }
    assert _close(res["span_macro_f1"], 1.0)


def test_evaluate_duplicate_labels_do_not_double_count_macro():
    # A caller-supplied `labels` with a repeat must not weight that type twice.
    # per_label is a dict and dedupes on its own, so a double-counted macro would
    # silently disagree with the table printed next to it.
    # Type A: tp=1 fp=0 fn=0 -> F1 1.0.  Type B: tp=0 fp=0 fn=1 -> F1 0.0.
    # macro = (1.0 + 0.0) / 2 = 0.5, whether or not "A" was passed twice.
    res = sm.evaluate([["B-A", "B-B"]], [["B-A", "O"]], labels=["A", "A", "B"])
    assert list(res["per_label"].keys()) == ["A", "B"]
    assert _close(res["span_macro_f1"], 0.5)


def test_evaluate_empty_corpus():
    res = sm.evaluate([], [])
    assert res["n_utterances"] == 0
    assert res["n_gold_spans"] == 0 and res["n_pred_spans"] == 0
    assert _close(res["all_O_gold_fraction"], 0.0)
    assert _close(res["span_micro"]["f1"], 0.0)
    assert _close(res["span_macro_f1"], 0.0)
    assert _close(res["tag_accuracy"], 0.0)


def test_evaluate_zero_length_utterance():
    # An utterance that tokenized to nothing: no spans, no tokens, no crash.
    res = sm.evaluate([[]], [[]])
    assert res["n_utterances"] == 1
    assert _close(res["all_O_gold_fraction"], 1.0)
    assert _close(res["tag_accuracy"], 0.0)


def test_evaluate_empty_prediction_against_real_gold():
    # Predictor emits nothing: recall 0, precision 0 (nothing predicted).
    res = sm.evaluate([["B-a", "I-a"]], [["O", "O"]])
    assert res["n_pred_spans"] == 0 and res["n_gold_spans"] == 1
    assert _close(res["span_micro"]["precision"], 0.0)
    assert _close(res["span_micro"]["recall"], 0.0)
    assert _close(res["span_micro"]["f1"], 0.0)


def test_evaluate_all_o_gold_fraction():
    # case 25: 4 utterances, only the second carries a gold span -> 3/4 = 0.75
    gold = [["O"], ["B-a"], ["O", "O"], ["O"]]
    pred = [["O"], ["B-a"], ["O", "O"], ["O"]]
    res = sm.evaluate(gold, pred)
    assert _close(res["all_O_gold_fraction"], 0.75)


def test_evaluate_length_mismatch_outer_raises():
    # case 24
    try:
        sm.evaluate([["O"], ["O"]], [["O"]])
    except ValueError:
        pass
    else:
        raise AssertionError("evaluate must raise ValueError on outer length mismatch")


def test_evaluate_length_mismatch_inner_raises():
    try:
        sm.evaluate([["O", "O"]], [["O"]])
    except ValueError as exc:
        assert "utterance 0" in str(exc)
    else:
        raise AssertionError("evaluate must raise ValueError on inner length mismatch")


def test_evaluate_half_supplied_intents_raises():
    try:
        sm.evaluate([["O"]], [["O"]], gold_intents=["a"])
    except ValueError:
        pass
    else:
        raise AssertionError("evaluate must raise when only one intent sequence is given")


def test_evaluate_intent_length_mismatch_raises():
    try:
        sm.evaluate([["O"]], [["O"]], gold_intents=["a", "b"], pred_intents=["a", "b"])
    except ValueError:
        pass
    else:
        raise AssertionError("evaluate must raise on intent/utterance count mismatch")


def test_evaluate_without_intents():
    # case 23
    res = sm.evaluate([["B-a"]], [["B-a"]])
    assert res["intent_supplied"] is False
    assert _close(res["intent_accuracy"], 0.0)
    assert _close(res["frame_accuracy"], 0.0)


def test_intent_correct_multi_intent():
    # cases 18-20: the ATIS '#' convention.
    assert sm.intent_correct("atis_flight#atis_airfare", "atis_airfare") is True
    assert sm.intent_correct("atis_flight#atis_airfare", "atis_meal") is False
    assert sm.intent_correct("atis_flight#atis_airfare", "atis_flight#atis_airfare") is True
    assert sm.intent_correct("greeting", "greeting") is True
    assert sm.intent_correct("greeting", "library") is False


def test_frame_accuracy_intent_right_spans_wrong():
    # case 21: utt1 exact on both -> hit; utt2 intent right, one span boundary
    # wrong -> miss. 1/2 = 0.5. Intent accuracy is 2/2 = 1.0.
    gold = [["B-a"], ["B-a", "I-a"]]
    pred = [["B-a"], ["B-a", "O"]]
    res = sm.evaluate(gold, pred, gold_intents=["x", "y"], pred_intents=["x", "y"])
    assert _close(res["intent_accuracy"], 1.0)
    assert _close(res["frame_accuracy"], 0.5)


def test_frame_accuracy_intent_wrong_spans_exact():
    # case 22: perfect slots do not rescue a wrong intent.
    res = sm.evaluate([["B-a"]], [["B-a"]], gold_intents=["x"], pred_intents=["y"])
    assert _close(res["span_micro"]["f1"], 1.0)
    assert _close(res["intent_accuracy"], 0.0)
    assert _close(res["frame_accuracy"], 0.0)


def test_frame_accuracy_all_o_with_right_intent_is_a_hit():
    # Both span sets are empty, so the frames are equal. Documented consequence:
    # on a ~78% all-O corpus frame accuracy largely restates intent accuracy.
    res = sm.evaluate([["O", "O"]], [["O", "O"]], gold_intents=["x"], pred_intents=["x"])
    assert _close(res["frame_accuracy"], 1.0)
    assert _close(res["span_micro"]["f1"], 0.0)


def test_all_o_predictor_is_exposed_by_span_f1():
    # case 26 -- the degenerate solution. Gold: 3 spans across 15 tokens.
    # Predictor says O everywhere: span F1 = 0.0, but 12 of 15 tags are "right",
    # so tag_accuracy = 12/15 = 0.8. This is why span F1 is the headline.
    gold = [
        ["O", "O", "O", "B-a", "O", "O", "O", "O", "O", "O"],
        ["O", "O", "B-b", "I-b", "O"],
    ]
    pred = [["O"] * 10, ["O"] * 5]
    res = sm.evaluate(gold, pred)
    assert res["n_gold_spans"] == 2 and res["n_pred_spans"] == 0
    assert _close(res["span_micro"]["f1"], 0.0)
    assert _close(res["tag_accuracy"], 0.8)
    assert res["tag_accuracy"] > 0.7


# --------------------------------------------------------------------------
# Full worked micro-example, all values as exact fractions
# --------------------------------------------------------------------------

def test_worked_example_end_to_end():
    """Three utterances, every number derived by hand.

    u1 gold B-cp I-cp O   -> {(cp,0,2)}      pred identical      -> tp 1
    u2 gold O B-dt I-dt   -> {(dt,1,3)}      pred O B-dt O -> {(dt,1,2)}
                                              -> fp 1, fn 1
    u3 gold B-gv O B-cp   -> {(gv,0,1),(cp,2,3)}
       pred B-gv O O      -> {(gv,0,1)}      -> tp 1, fn 1

    micro tp=2 fp=1 fn=2 ; gold spans 4 ; pred spans 3
      P  = 2/3
      R  = 2/4 = 1/2
      F1 = 2*(2/3)*(1/2) / (2/3 + 1/2) = (2/3)/(7/6) = 4/7
    per label
      cp: tp 1 fp 0 fn 1 -> P 1/1 = 1, R 1/2, F1 = 2*(1)(1/2)/(3/2) = 2/3, support 2
      dt: tp 0 fp 1 fn 1 -> P 0, R 0, F1 0, support 1
      gv: tp 1 fp 0 fn 0 -> P 1, R 1, F1 1, support 1
      macro = (2/3 + 0 + 1)/3 = 5/9
    tag accuracy = (3 + 2 + 2) / 9 = 7/9
    all_O_gold_fraction = 0/3 = 0
    intents: gold ["library", "transcript_request#registrar", "retention_policy_grades"]
             pred ["library", "registrar", "library"]
      u1 equal -> ok ; u2 'registrar' is a member of the compound gold -> ok ;
      u3 wrong. intent accuracy = 2/3
    frames: u1 intent ok AND spans equal -> hit ; u2 intent ok but spans differ ;
            u3 intent wrong. frame accuracy = 1/3
    """
    gold = [
        ["B-cp", "I-cp", "O"],
        ["O", "B-dt", "I-dt"],
        ["B-gv", "O", "B-cp"],
    ]
    pred = [
        ["B-cp", "I-cp", "O"],
        ["O", "B-dt", "O"],
        ["B-gv", "O", "O"],
    ]
    gold_intents = ["library", "transcript_request#registrar", "retention_policy_grades"]
    pred_intents = ["library", "registrar", "library"]
    res = sm.evaluate(gold, pred, gold_intents=gold_intents, pred_intents=pred_intents)

    assert res["n_utterances"] == 3
    assert res["n_gold_spans"] == 4
    assert res["n_pred_spans"] == 3
    assert _close(res["all_O_gold_fraction"], 0.0)
    assert _close(res["span_micro"]["precision"], 2.0 / 3.0)
    assert _close(res["span_micro"]["recall"], 1.0 / 2.0)
    assert _close(res["span_micro"]["f1"], 4.0 / 7.0)
    assert res["span_micro"]["support"] == 4
    assert _close(res["per_label"]["cp"]["precision"], 1.0)
    assert _close(res["per_label"]["cp"]["recall"], 0.5)
    assert _close(res["per_label"]["cp"]["f1"], 2.0 / 3.0)
    assert res["per_label"]["cp"]["support"] == 2
    assert _close(res["per_label"]["dt"]["f1"], 0.0)
    assert res["per_label"]["dt"]["support"] == 1
    assert _close(res["per_label"]["gv"]["f1"], 1.0)
    assert res["per_label"]["gv"]["support"] == 1
    assert _close(res["span_macro_f1"], 5.0 / 9.0)
    assert _close(res["tag_accuracy"], 7.0 / 9.0)
    assert res["intent_supplied"] is True
    assert _close(res["intent_accuracy"], 2.0 / 3.0)
    assert _close(res["frame_accuracy"], 1.0 / 3.0)
    assert list(res["per_label"].keys()) == ["cp", "dt", "gv"]


def test_report_is_ascii_and_carries_the_caveat():
    res = sm.evaluate([["B-a", "O"]], [["B-a", "O"]], gold_intents=["x"], pred_intents=["x"])
    text = sm.conlleval_report(res)
    assert text == text.encode("ascii", "ignore").decode("ascii")
    assert not text.endswith("\n")
    assert "NOTE: tag_accuracy is inflated by the O majority class" in text
    assert "micro" in text and "macro f1" in text


def test_report_without_intents_still_renders():
    text = sm.conlleval_report(sm.evaluate([["B-a"]], [["O"]]))
    assert "n/a" in text


# --------------------------------------------------------------------------

def _check(cond, msg, fails):
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)


if __name__ == "__main__":
    fails: List[str] = []
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    for name, fn in tests:
        try:
            fn()
            _check(True, name, fails)
        except AssertionError as exc:
            _check(False, f"{name}: {exc}", fails)
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, not a stack trace
            _check(False, f"{name}: {type(exc).__name__}: {exc}", fails)

    print(f"\n{len(tests) - len(fails)}/{len(tests)} passed")
    print("ALL PASS" if not fails else f"{len(fails)} FAILED")
    raise SystemExit(1 if fails else 0)
