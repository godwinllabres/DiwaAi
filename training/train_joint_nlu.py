"""Train the joint intent + BIO slot model on data/slots/.

Liu & Lane (2016) style: one shared BiLSTM encoder, a per-token softmax slot head
and a pooled softmax intent head, trained jointly. It does NOT retrain, replace or
read models/nn_model.h5, models/CvSU_classifier.pkl or any existing artifact --
it only writes new models/joint_* files. Nothing it produces is loaded by the API
unless SEVI_JOINT_NLU=1.

Reads : data/slots/{train,valid}, data/slots/{vocab.slot,vocab.intent}
Writes: models/joint_nlu.keras, models/joint_token_vocab.json,
        models/joint_labels.json, models/joint_meta.json

Usage: python training/train_joint_nlu.py [--epochs 40] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Mapping, Sequence

import numpy as np

os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")
os.environ.pop("DATABASE_URL", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from api.slot_metrics import evaluate  # noqa: E402
from api.slot_schema import (  # noqa: E402
    EMBEDDING_DIM,
    LSTM_UNITS,
    MAX_LEN,
    OOV_ID,
    OOV_TOKEN,
    O_TAG,
    PAD_ID,
    PAD_TOKEN,
    SCHEMA_VERSION,
    TAGS,
    VOCAB_SIZE,
    encode_tokens,
    read_corpus,
    read_vocab,
    vocab_key,
)

ARTIFACTS = ("joint_nlu.keras", "joint_token_vocab.json", "joint_labels.json", "joint_meta.json")

# Keras 3 renders model.summary() with unicode box-drawing characters; the repo
# convention (contract G.16) is ASCII-only console output.
_ASCII_FOLD = str.maketrans({
    "─": "-", "━": "-", "│": "|", "┃": "|",
    "┌": "+", "┍": "+", "┎": "+", "┏": "+",
    "┐": "+", "┑": "+", "┒": "+", "┓": "+",
    "└": "+", "┕": "+", "┖": "+", "┗": "+",
    "┘": "+", "┙": "+", "┚": "+", "┛": "+",
    "├": "+", "┣": "+", "┤": "+", "┫": "+",
    "┬": "+", "┳": "+", "┴": "+", "┻": "+",
    "┼": "+", "╋": "+", "═": "=", "║": "|",
    "╡": "+", "╢": "+", "╤": "+", "╧": "+",
    "╪": "+", "╠": "+", "╣": "+", "╬": "+",
    " ": " ",
})


def ascii_print(line: str) -> None:
    """ASCII-fold a line before printing (Keras summary emits box-drawing glyphs)."""
    folded = str(line).translate(_ASCII_FOLD)
    print(folded.encode("ascii", "replace").decode("ascii"))


def load_split(path: str) -> tuple[list[list[str]], list[list[str]], list[str]]:
    """Read an ATIS-format split into parallel token / tag / intent lists."""
    rows = read_corpus(path)
    tokens = [r[0] for r in rows]
    tags = [r[1] for r in rows]
    intents = [r[2] for r in rows]
    return tokens, tags, intents


def build_token_vocab(train_tokens: Sequence[Sequence[str]]) -> dict[str, int]:
    """Token vocab from the TRAIN split only -- valid/test tokens must stay OOV."""
    counts: Counter[str] = Counter()
    for toks in train_tokens:
        counts.update(vocab_key(t) for t in toks)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    vocab = {PAD_TOKEN: PAD_ID, OOV_TOKEN: OOV_ID}
    for token, _count in ranked:
        if len(vocab) >= VOCAB_SIZE:
            break
        if token not in vocab:
            vocab[token] = len(vocab)
    return vocab


def vectorize(
    tokens: Sequence[Sequence[str]],
    tags: Sequence[Sequence[str]],
    intents: Sequence[str],
    token_vocab: Mapping[str, int],
    tag2id: Mapping[str, int],
    intent2id: Mapping[str, int],
    o_weight: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """-> (X, y_slots, y_intent, w_slots, w_intent).

    w_slots is 0.0 at every padded timestep, so padding contributes exactly zero
    to the slot loss and to the weighted slot metric. Without it a model that
    predicts O everywhere scores ~95% "slot accuracy" purely on padding.
    """
    o_id = tag2id[O_TAG]
    n = len(tokens)
    x = np.zeros((n, MAX_LEN), dtype="int32")
    y_slots = np.full((n, MAX_LEN), o_id, dtype="int32")
    y_intent = np.zeros((n,), dtype="int32")
    w_slots = np.zeros((n, MAX_LEN), dtype="float32")
    w_intent = np.ones((n,), dtype="float32")
    for i, (toks, tg, intent) in enumerate(zip(tokens, tags, intents)):
        x[i] = encode_tokens(toks, token_vocab)
        k = min(len(toks), MAX_LEN)
        for j in range(k):
            tid = tag2id[tg[j]]
            y_slots[i, j] = tid
            w_slots[i, j] = o_weight if tid == o_id else 1.0
        y_intent[i] = intent2id[intent]
    return x, y_slots, y_intent, w_slots, w_intent


def build_model(n_tokens: int, n_tags: int, n_intents: int):
    """Shared embedding + BiLSTM encoder, per-token slot head, pooled intent head."""
    from tensorflow import keras  # noqa: PLC0415 -- lazy: TF must never load at import time

    layers = keras.layers
    inp = layers.Input(shape=(MAX_LEN,), dtype="int32", name="tokens")
    emb = layers.Embedding(n_tokens, EMBEDDING_DIM, mask_zero=True, name="emb")(inp)
    d1 = layers.Dropout(0.3, name="emb_drop")(emb)
    enc = layers.Bidirectional(
        layers.LSTM(LSTM_UNITS, return_sequences=True, dropout=0.0, recurrent_dropout=0.0),
        name="bilstm",
    )(d1)
    d2 = layers.Dropout(0.3, name="enc_drop")(enc)
    slots = layers.Dense(n_tags, activation="softmax", name="slots")(d2)
    pooled = layers.GlobalAveragePooling1D(name="pool")(d2)
    hidden = layers.Dense(64, activation="relu", name="intent_hidden")(pooled)
    hidden = layers.Dropout(0.3, name="intent_drop")(hidden)
    intent = layers.Dense(n_intents, activation="softmax", name="intent")(hidden)
    return keras.Model(inp, {"slots": slots, "intent": intent}, name="joint_nlu")


def _as_outputs(probs) -> tuple[np.ndarray, np.ndarray] | None:
    """Unpack the model's dict outputs. Positional unpacking is refused on purpose:
    list-output ordering is not the declaration order and cannot be trusted."""
    if not isinstance(probs, dict) or "slots" not in probs or "intent" not in probs:
        return None
    return np.asarray(probs["slots"]), np.asarray(probs["intent"])


def decode_predictions(
    p_slots: np.ndarray,
    p_intent: np.ndarray,
    tokens: Sequence[Sequence[str]],
    id2tag: Sequence[str],
    id2intent: Sequence[str],
) -> tuple[list[list[str]], list[str]]:
    """Argmax decode, then re-expand to the ORIGINAL token count.

    Tokens past MAX_LEN were truncated away and are emitted as O so that
    len(pred_tags[i]) == len(tokens[i]) always holds for span scoring."""
    pred_tags: list[list[str]] = []
    pred_intents: list[str] = []
    for i, toks in enumerate(tokens):
        k = min(len(toks), MAX_LEN)
        row = [id2tag[int(t)] for t in p_slots[i, :k].argmax(-1)]
        row.extend([O_TAG] * (len(toks) - k))
        pred_tags.append(row)
        pred_intents.append(id2intent[int(p_intent[i].argmax(-1))])
    return pred_tags, pred_intents


def masked_slot_accuracy(
    gold_tags: Sequence[Sequence[str]], pred_tags: Sequence[Sequence[str]]
) -> tuple[float, float]:
    """-> (accuracy over REAL tokens, accuracy over real non-O gold tokens).

    Padding is excluded from both. The second number is the one that cannot be
    won by predicting O everywhere."""
    total = hit = 0
    nz_total = nz_hit = 0
    for gold, pred in zip(gold_tags, pred_tags):
        for g, p in zip(gold, pred):
            total += 1
            ok = g == p
            hit += ok
            if g != O_TAG:
                nz_total += 1
                nz_hit += ok
    return (hit / total if total else 0.0, nz_hit / nz_total if nz_total else 0.0)


def print_report(title: str, result: dict, tags_acc: tuple[float, float]) -> None:
    """ASCII report. Span F1 is the headline; token accuracy is a labelled diagnostic."""
    micro = result["span_micro"]
    print(f"\n===== {title} =====")
    print(f"utterances            {result['n_utterances']}")
    print(f"gold spans            {result['n_gold_spans']}")
    print(f"pred spans            {result['n_pred_spans']}")
    print(f"all-O gold fraction   {result['all_O_gold_fraction']:.4f}")
    print(f"SPAN micro P/R/F1     {micro['precision']:.4f} / {micro['recall']:.4f} "
          f"/ {micro['f1']:.4f}   <- headline")
    print(f"SPAN macro F1         {result['span_macro_f1']:.4f}")
    print(f"intent accuracy       {result['intent_accuracy']:.4f}")
    print(f"frame accuracy        {result['frame_accuracy']:.4f}")
    print(f"{'label':<16}{'P':>8}{'R':>8}{'F1':>8}{'support':>9}")
    for label, d in result["per_label"].items():
        print(f"{label:<16}{d['precision']:>8.4f}{d['recall']:>8.4f}"
              f"{d['f1']:>8.4f}{d['support']:>9}")
    print(f"tag accuracy (real tokens only, pad excluded)   {tags_acc[0]:.4f}")
    print(f"tag accuracy (real non-O gold tokens only)      {tags_acc[1]:.4f}")
    print("NOTE: tag_accuracy is inflated by the O majority class; span f1 is the headline.")
    print("NOTE: gold labels here are machine-generated by scripts/build_slot_corpus.py --")
    print("      this measures agreement with a gazetteer, not with human judgement.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Train the joint intent + BIO slot model.")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--o-weight", type=float, default=0.20)
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default="models")
    # Additive to the frozen CLI: lets the trainer be exercised against a throwaway
    # fixture without writing anything into the repo.
    ap.add_argument("--data-dir", default="data/slots")
    ap.add_argument("--force", action="store_true",
                    help="allow overwriting existing models/joint_* artifacts")
    args = ap.parse_args()

    train_path = os.path.join(args.data_dir, "train")
    valid_path = os.path.join(args.data_dir, "valid")
    slot_vocab_path = os.path.join(args.data_dir, "vocab.slot")
    intent_vocab_path = os.path.join(args.data_dir, "vocab.intent")

    for path in (train_path, valid_path, slot_vocab_path, intent_vocab_path):
        if not os.path.exists(path):
            print(f"[ERROR] missing {path} -- run: python scripts/build_slot_corpus.py")
            return 1

    existing = [f for f in ARTIFACTS if os.path.exists(os.path.join(args.out_dir, f))]
    if existing and not args.dry_run and not args.force:
        print(f"[ERROR] refusing to overwrite existing artifacts in {args.out_dir}: "
              f"{', '.join(existing)}")
        print("[ERROR] move them aside or pass --force")
        return 1

    try:
        train_tokens, train_tags, train_intents = load_split(train_path)
        valid_tokens, valid_tags, valid_intents = load_split(valid_path)
        tag_list = read_vocab(slot_vocab_path)
        intent_list = read_vocab(intent_vocab_path)
    except Exception as exc:  # noqa: BLE001 -- a corpus defect is a user error, not a traceback
        print(f"[ERROR] cannot read the corpus: {type(exc).__name__}: {exc}")
        return 1

    if tuple(tag_list) != tuple(TAGS):
        print(f"[ERROR] {slot_vocab_path} does not match api.slot_schema.TAGS "
              f"(schema {SCHEMA_VERSION})")
        return 1
    if not train_tokens or not valid_tokens:
        print("[ERROR] train or valid split is empty")
        return 1

    tag2id = {t: i for i, t in enumerate(tag_list)}
    intent2id = {t: i for i, t in enumerate(intent_list)}
    unknown = sorted({i for i in train_intents + valid_intents if i not in intent2id})
    if unknown:
        print(f"[ERROR] intents missing from {intent_vocab_path}: {', '.join(unknown[:5])}")
        return 1

    random.seed(args.seed)
    np.random.seed(args.seed)

    token_vocab = build_token_vocab(train_tokens)
    x_tr, ys_tr, yi_tr, ws_tr, wi_tr = vectorize(
        train_tokens, train_tags, train_intents, token_vocab, tag2id, intent2id, args.o_weight)
    x_va, ys_va, yi_va, ws_va, wi_va = vectorize(
        valid_tokens, valid_tags, valid_intents, token_vocab, tag2id, intent2id, args.o_weight)

    n_over = sum(1 for t in train_tokens + valid_tokens if len(t) > MAX_LEN)
    oov_tr = sum(1 for toks in train_tokens for t in toks if vocab_key(t) not in token_vocab)
    n_tok_tr = sum(len(t) for t in train_tokens)
    print(f"[OK] train {len(train_tokens)} rows, valid {len(valid_tokens)} rows")
    print(f"[OK] tokens {len(token_vocab)} (cap {VOCAB_SIZE}), tags {len(tag_list)}, "
          f"intents {len(intent_list)}")
    print(f"[OK] max_len {MAX_LEN}; rows over max_len {n_over} "
          f"({n_over / max(1, len(train_tokens) + len(valid_tokens)):.2%})")
    print(f"[OK] train OOV token rate {oov_tr / max(1, n_tok_tr):.2%}")
    real = float(ws_tr.astype(bool).sum())
    nonzero = float((ws_tr >= 1.0).sum())
    print(f"[OK] slot targets: {int(real)} real timesteps of {ws_tr.size} "
          f"({1 - real / ws_tr.size:.2%} padding, sample-weighted to 0.0); "
          f"{int(nonzero)} non-O ({nonzero / max(1.0, real):.2%} of real)")

    from tensorflow import keras  # noqa: PLC0415 -- lazy: TF must never load at import time

    keras.utils.set_random_seed(args.seed)
    model = build_model(len(token_vocab), len(tag_list), len(intent_list))
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=args.lr),
        loss={"slots": "sparse_categorical_crossentropy",
              "intent": "sparse_categorical_crossentropy"},
        loss_weights={"slots": 1.0, "intent": 1.0},
        metrics={"slots": "accuracy", "intent": "accuracy"},
    )

    if args.dry_run:
        model.summary(print_fn=ascii_print)
        print(f"[OK] X {x_tr.shape} {x_tr.dtype}; y_slots {ys_tr.shape} {ys_tr.dtype}; "
              f"y_intent {yi_tr.shape} {yi_tr.dtype}")
        print(f"[OK] w_slots {ws_tr.shape} {ws_tr.dtype}; w_intent {wi_tr.shape} {wi_tr.dtype}")
        print("[OK] dry run -- nothing trained, nothing written")
        return 0

    history = model.fit(
        x_tr,
        {"slots": ys_tr, "intent": yi_tr},
        sample_weight={"slots": ws_tr, "intent": wi_tr},
        validation_data=(x_va, {"slots": ys_va, "intent": yi_va},
                         {"slots": ws_va, "intent": wi_va}),
        epochs=args.epochs,
        batch_size=args.batch_size,
        shuffle=True,
        verbose=2,
        callbacks=[keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=args.patience, restore_best_weights=True)],
    )
    epochs_run = len(history.history.get("loss", []))

    outputs = _as_outputs(model.predict(x_va, batch_size=128, verbose=0))
    if outputs is None:
        print("[ERROR] model returned non-dict outputs; refusing to score blindly")
        return 1
    p_slots, p_intent = outputs
    pred_tags, pred_intents = decode_predictions(
        p_slots, p_intent, valid_tokens, tag_list, intent_list)
    result = evaluate(valid_tags, pred_tags, valid_intents, pred_intents)
    acc = masked_slot_accuracy(valid_tags, pred_tags)
    print_report("VALID", result, acc)

    os.makedirs(args.out_dir, exist_ok=True)
    model_path = os.path.join(args.out_dir, "joint_nlu.keras")
    vocab_path = os.path.join(args.out_dir, "joint_token_vocab.json")
    labels_path = os.path.join(args.out_dir, "joint_labels.json")
    meta_path = os.path.join(args.out_dir, "joint_meta.json")

    model.save(model_path)
    with open(vocab_path, "w", encoding="utf-8") as f:
        json.dump(token_vocab, f, ensure_ascii=False, indent=2)
    with open(labels_path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": SCHEMA_VERSION, "tags": list(tag_list),
                   "intents": list(intent_list)}, f, ensure_ascii=False, indent=2)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "max_len": MAX_LEN,
        "vocab_size": VOCAB_SIZE,
        "embedding_dim": EMBEDDING_DIM,
        "lstm_units": LSTM_UNITS,
        "o_weight": args.o_weight,
        "seed": args.seed,
        "epochs_run": epochs_run,
        "n_train": len(train_tokens),
        "n_valid": len(valid_tokens),
        "n_tokens": len(token_vocab),
        "n_tags": len(tag_list),
        "n_intents": len(intent_list),
        "valid_metrics": result,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] wrote {model_path}")
    print(f"[OK] wrote {vocab_path}")
    print(f"[OK] wrote {labels_path}")
    print(f"[OK] wrote {meta_path}")
    print("[OK] the test split was never read by this script -- it is held out for "
          "scripts/eval_joint_nlu.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
