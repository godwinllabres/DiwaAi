"""In-process routing A/B: OLD vs NEW (NB+NN) on the 268 held-out questions.

Replicates hybrid_chatbot's NB->NN gate exactly (NB@0.65, then NN@adaptive
threshold with temperature scaling), using the real api/ model classes. Isolates
the redesign's routing effect WITHOUT the LLM tier (held per user's choice).

Reports, OLD vs NEW: confident-misroutes, correct-topic routing, and how many
new-topic questions the 4 new intents now catch.

Usage: python scripts/eval_routing_ab.py
"""
import os
import sys

os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import json  # noqa: E402
from collections import Counter  # noqa: E402
from api.hybrid_chatbot import NaiveBayesModel, NeuralNetworkModel  # noqa: E402

NB_GATE = 0.65
NEW_TAGS = {"licensure_results", "university_rankings", "awards_recognition", "accreditation_status"}


def load(model_dir):
    nb = NaiveBayesModel(os.path.join(model_dir, "CvSU_classifier.pkl"))
    nn = NeuralNetworkModel(model_dir)
    return nb, nn


def route(nb, nn, q):
    """Return (served_intent_or_DEFER, tier)."""
    nb_i, nb_c = nb.predict(q)
    if nb_c >= NB_GATE:
        return nb_i, "NB"
    nn_i, nn_c = nn.predict(q)
    if nn_c >= nn.get_threshold(nn_i):
        return nn_i, "NN"
    return "DEFER", "defer"


def target(exp):
    e = exp.lower()
    if "licensure" in e or "exam_result" in e:
        return "licensure_results"
    if "ranking" in e:
        return "university_rankings"
    if "award" in e or "ipophl" in e or "recognition" in e:
        return "awards_recognition"
    if "accreditation" in e or "iso_cert" in e:
        return "accreditation_status"
    return None


def acceptable(exp):
    """Set of acceptable served values; 'DEFER' means deferring is correct."""
    t = target(exp)
    if t:
        return {t}
    if exp == "REFUSE":
        return {"DEFER", "out_of_scope", "nlu_fallback"}
    if exp.startswith("MISSING"):
        return {"DEFER"}  # in-scope but uncovered -> defer to grounding is correct
    return {exp}  # a concrete existing intent


def score(nb, nn, data, label):
    good = bad = defer_miss = 0
    new_catch = 0
    newtopic = 0
    misroute_examples = []
    for it in data:
        exp = it["expected_intent"]
        acc = acceptable(exp)
        served, tier = route(nb, nn, it["question"])
        tgt = target(exp)
        if tgt:
            newtopic += 1
            if served == tgt:
                new_catch += 1
        if served in acc:
            good += 1
        elif served == "DEFER":
            defer_miss += 1  # should have routed to a concrete intent, deferred instead
        else:
            bad += 1  # confident route to a wrong intent
            if len(misroute_examples) < 12:
                misroute_examples.append((it["question"][:52], str(exp), served))
    total = len(data)
    print(f"\n[{label}]  n={total}")
    print(f"  GOOD (served acceptable / correct defer): {good}  ({good/total*100:.1f}%)")
    print(f"  BAD  (confident misroute to wrong intent): {bad}  ({bad/total*100:.1f}%)")
    print(f"  DEFER-miss (deferred but a concrete intent expected): {defer_miss}")
    print(f"  new-topic questions caught by a NEW intent: {new_catch}/{newtopic}")
    return {"good": good, "bad": bad, "defer_miss": defer_miss, "new_catch": new_catch,
            "newtopic": newtopic, "misroutes": misroute_examples}


def main():
    data = json.load(open("data/eval/mirror_qa.json", encoding="utf-8"))
    print("Loading OLD models (models_old/) ...")
    old = load("models_old")
    print("Loading NEW models (models/) ...")
    new = load("models")

    o = score(*old, data, "OLD")
    n = score(*new, data, "NEW")

    print("\n===== DELTA (NEW - OLD) =====")
    print(f"  GOOD routing : {o['good']:>3} -> {n['good']:>3}   ({n['good']-o['good']:+d})")
    print(f"  BAD misroutes: {o['bad']:>3} -> {n['bad']:>3}   ({n['bad']-o['bad']:+d})")
    print(f"  new-intent catch: {o['new_catch']:>3} -> {n['new_catch']:>3}   ({n['new_catch']-o['new_catch']:+d}) of {n['newtopic']} new-topic Qs")

    print("\nsample residual confident-misroutes (NEW):")
    for q, exp, got in n["misroutes"]:
        print(f"  {q!r:56s} exp={exp} got={got}")

    json.dump({"old": {k: v for k, v in o.items() if k != 'misroutes'},
               "new": {k: v for k, v in n.items() if k != 'misroutes'}},
              open("data/eval/routing_ab.json", "w", encoding="utf-8"), indent=2)
    print("\nwrote data/eval/routing_ab.json")


if __name__ == "__main__":
    main()
