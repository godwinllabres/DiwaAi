"""Inference wrapper for the joint intent + BIO slot model (models/joint_*).

Loads models/joint_nlu.keras lazily and tags an utterance with BIO slot spans
mapped back to the ORIGINAL, un-normalised text. Shadow mode only: the intent it
returns is recorded for offline comparison and is never allowed to route. The API
only reaches this module when SEVI_JOINT_NLU=1.

Importing this module must NOT import TensorFlow -- api/hybrid_chatbot.py:83-87
guards the NLU import against ImportError only, and TF routinely raises
RuntimeError/OSError at import time on Windows, which would crash API startup.
Every TF import therefore lives inside a function, and every public entry point
returns None/False instead of raising.

Reads : models/joint_nlu.keras, models/joint_token_vocab.json,
        models/joint_labels.json, models/joint_meta.json
"""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional, Sequence

from .slot_metrics import bio_spans
from .slot_schema import MAX_LEN, O_TAG, SCHEMA_VERSION, encode_tokens, joint_tokenize

_MODEL_FILE = "joint_nlu.keras"
_VOCAB_FILE = "joint_token_vocab.json"
_LABELS_FILE = "joint_labels.json"
_META_FILE = "joint_meta.json"

_LOADER_LOCK = threading.Lock()
_SINGLETON: Optional["JointNLU"] = None
_SINGLETON_TRIED = False


class JointNLU:
    """Lazy, never-raising inference wrapper around the joint intent+slot model."""

    def __init__(self, model_dir: str = "models") -> None:
        self.model_dir = model_dir
        self.model_path = os.path.join(model_dir, _MODEL_FILE)
        self.vocab_path = os.path.join(model_dir, _VOCAB_FILE)
        self.labels_path = os.path.join(model_dir, _LABELS_FILE)
        self.meta_path = os.path.join(model_dir, _META_FILE)
        self._model = None
        self._call = None
        self._token_vocab: Dict[str, int] = {}
        self._tags: List[str] = []
        self._intents: List[str] = []
        self.meta: Dict = {}
        self.error: Optional[str] = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def load(self) -> bool:
        """Load the artifacts and warm up. Returns False on ANY failure. Idempotent."""
        if self._ready:
            return True
        try:
            tags, intents, token_vocab, meta = self._read_artifacts()

            from tensorflow import keras  # noqa: PLC0415 -- lazy: TF stays out of import time

            model = keras.models.load_model(self.model_path)
            call = _trace_call(model)
            self._warmup(call, tags, intents)

            self._model = model
            self._call = call
            self._token_vocab = token_vocab
            self._tags = tags
            self._intents = intents
            self.meta = meta
            self.error = None
            self._ready = True
            return True
        except Exception as exc:  # noqa: BLE001 -- never raise into the chat hot path
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    def _read_artifacts(self) -> tuple[List[str], List[str], Dict[str, int], Dict]:
        """Read and validate the three JSON artifacts. Raises on any defect."""
        for path in (self.model_path, self.vocab_path, self.labels_path):
            if not os.path.exists(path):
                raise FileNotFoundError(path)

        with open(self.labels_path, encoding="utf-8") as f:
            labels = json.load(f)
        version = labels.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"schema_version {version!r} != {SCHEMA_VERSION!r}")
        tags = list(labels.get("tags") or [])
        intents = list(labels.get("intents") or [])
        if not tags or not intents:
            raise ValueError(f"{self.labels_path} has no tags or no intents")

        with open(self.vocab_path, encoding="utf-8") as f:
            token_vocab = {str(k): int(v) for k, v in json.load(f).items()}
        if not token_vocab:
            raise ValueError(f"{self.vocab_path} is empty")

        meta: Dict = {}
        if os.path.exists(self.meta_path):
            with open(self.meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        return tags, intents, token_vocab, meta

    def _warmup(self, call, tags: Sequence[str], intents: Sequence[str]) -> None:
        """One forward pass; traces the graph and asserts the heads match the label
        files. Raises on mismatch."""
        import numpy as np  # noqa: PLC0415 -- kept off the module import path with TF

        p_slots, p_intent = self._unpack(call(np.zeros((1, MAX_LEN), dtype="int32")))
        if p_slots is None or p_intent is None:
            raise ValueError("model outputs are not the expected dict")
        if p_slots.shape[1] != MAX_LEN or p_slots.shape[2] != len(tags):
            raise ValueError(f"slot head {tuple(p_slots.shape)} does not match max_len "
                             f"{MAX_LEN} / {len(tags)} tags")
        if p_intent.shape[1] != len(intents):
            raise ValueError(f"intent head {tuple(p_intent.shape)} does not match "
                             f"{len(intents)} intents")

    @staticmethod
    def _unpack(probs):
        """Dict outputs only. List outputs do not come back in declaration order and
        must never be indexed positionally."""
        import numpy as np  # noqa: PLC0415 -- kept off the module import path with TF

        if not isinstance(probs, dict) or "slots" not in probs or "intent" not in probs:
            return None, None
        return np.asarray(probs["slots"]), np.asarray(probs["intent"])

    def predict_tokens(self, tokens: Sequence[str]) -> Optional[Dict]:
        """Tag an already-tokenized utterance. Char offsets in the result are -1."""
        try:
            toks = list(tokens)
        except Exception:  # noqa: BLE001 -- a non-sequence argument is not a crash
            return None
        return self._predict(toks, None, None)

    def predict(self, text: str) -> Optional[Dict]:
        """Tokenize raw text and tag it. Char offsets index into `text` verbatim."""
        try:
            if not isinstance(text, str) or not text.strip():
                return None
            toks = joint_tokenize(text)
            # Emoji-only / punctuation-only input carries no slot or intent signal;
            # the model is never called on it.
            if not toks or not any(c.isalnum() for t, _s, _e in toks for c in t):
                return None
            return self._predict([t for t, _s, _e in toks], toks, text)
        except Exception:  # noqa: BLE001 -- never raise into the chat hot path
            return None

    def _predict(
        self, tokens: List[str], offsets: Optional[List], text: Optional[str]
    ) -> Optional[Dict]:
        try:
            if not self._ready or not tokens:
                return None

            import numpy as np  # noqa: PLC0415 -- kept off the module import path with TF

            ids = encode_tokens(tokens, self._token_vocab)
            x = np.asarray([ids], dtype="int32")
            p_slots, p_intent = self._unpack(self._call(x))
            if p_slots is None or p_intent is None:
                return None
            p_slots = p_slots[0]
            p_intent = p_intent[0]

            n = len(tokens)
            k = min(n, MAX_LEN)
            tag_ids = p_slots[:k].argmax(-1)
            conf = p_slots[:k].max(-1)
            bio = [self._tags[int(i)] for i in tag_ids]
            bio.extend([O_TAG] * (n - k))
            if len(bio) != n:
                return None

            intent_id = int(p_intent.argmax(-1))
            if intent_id >= len(self._intents):
                return None

            spans, slots = _decode_spans(bio, tokens, offsets, text, conf, k)

            return {
                "schema_version": SCHEMA_VERSION,
                "model": "joint_nlu",
                "tokens": list(tokens),
                "bio": bio,
                "spans": spans,
                "slots": slots,
                "intent": self._intents[intent_id],
                "confidence": round(float(p_intent.max()), 4),
            }
        except Exception:  # noqa: BLE001 -- never raise into the chat hot path
            return None


def _trace_call(model):
    """Return a single-example forward pass, traced once at load time.

    The body is exactly `model(x, training=False)` -- model.predict() is never used.
    Tracing is not cosmetic: measured on this stack (TF 2.21 / CPU), the untraced
    eager call costs ~220 ms per turn against ~1.4 ms traced, and _nb_result runs on
    every turn. Falls back to the eager call if tf.function is unavailable."""
    try:
        import tensorflow as tf  # noqa: PLC0415 -- lazy: TF stays out of import time

        @tf.function(input_signature=[tf.TensorSpec([1, MAX_LEN], tf.int32)],
                     reduce_retracing=True)
        def _call(x):
            return model(x, training=False)

        return _call
    except Exception:  # noqa: BLE001 -- correctness does not depend on the graph
        return lambda x: model(x, training=False)


def _decode_spans(
    bio: Sequence[str],
    tokens: Sequence[str],
    offsets: Optional[Sequence],
    text: Optional[str],
    conf,
    k: int,
) -> tuple[List[Dict], Dict[str, List[str]]]:
    """BIO -> span dicts + type->surfaces map. Surfaces are sliced verbatim from the
    original text when it is available; predict_tokens has no text, so offsets are -1."""
    spans: List[Dict] = []
    slots: Dict[str, List[str]] = {}
    for slot_type, i, j in bio_spans(bio):
        if offsets is None or text is None:
            start = end = -1
            surface = " ".join(tokens[i:j])
        else:
            start = int(offsets[i][1])
            end = int(offsets[j - 1][2])
            surface = text[start:end]
        score = float(conf[i:min(j, k)].min()) if i < k else 0.0
        spans.append({
            "type": slot_type,
            "text": surface,
            "token_start": i,
            "token_end": j,
            "start": start,
            "end": end,
            "confidence": round(score, 4),
        })
        slots.setdefault(slot_type, []).append(surface)
    return spans, slots


def load_joint_filler(model_dir: str = "models") -> Optional[JointNLU]:
    """Process-wide singleton. The load is attempted exactly once per process; a
    failure is cached as None so a missing model costs nothing per request."""
    global _SINGLETON, _SINGLETON_TRIED
    try:
        if _SINGLETON_TRIED:
            return _SINGLETON
        with _LOADER_LOCK:
            if _SINGLETON_TRIED:
                return _SINGLETON
            filler = JointNLU(model_dir)
            _SINGLETON = filler if filler.load() else None
            _SINGLETON_TRIED = True
            return _SINGLETON
    except Exception:  # noqa: BLE001 -- never raise into the chat hot path
        _SINGLETON_TRIED = True
        _SINGLETON = None
        return None
