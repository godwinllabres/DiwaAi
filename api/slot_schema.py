"""Single source of truth for the joint intent + BIO slot-filling contract.

Defines the slot inventory, the 11 BIO tags, the alignment-preserving tokenizer,
and the reader/writer for the ATIS-style corpus files under data/slots/.

Everything here is stdlib-only and import-cheap on purpose: the corpus builder,
the trainer, the inference wrapper and the eval script all import it, and one of
those runs inside the API process. No TensorFlow, no sklearn, no nltk, no NLTK
downloads, no repo imports.

SCHEMA_VERSION is embedded in every artifact produced by this feature; bump it
and every consumer must be re-run.

Reads : nothing at import time
Writes: nothing at import time
Usage: from api.slot_schema import joint_tokenize, TAGS, encode_tokens
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

SCHEMA_VERSION: str = "1.0.0"

SLOT_TYPES: tuple[str, ...] = (
    "campus_name",
    "campus_place",
    "document_type",
    "grade_value",
    "program_name",
)

# ASCII-lexicographic ascending; "O" sorts last naturally (B < I < O).
TAGS: tuple[str, ...] = (
    "B-campus_name",
    "B-campus_place",
    "B-document_type",
    "B-grade_value",
    "B-program_name",
    "I-campus_name",
    "I-campus_place",
    "I-document_type",
    "I-grade_value",
    "I-program_name",
    "O",
)

TAG2ID: dict[str, int] = {tag: i for i, tag in enumerate(TAGS)}
ID2TAG: tuple[str, ...] = TAGS
O_TAG: str = "O"
O_TAG_ID: int = TAG2ID[O_TAG]

# MAX_LEN is 24, not production's 20: this tokenizer emits punctuation as its own
# tokens, so a 14-word pattern can exceed 20 positions.
MAX_LEN: int = 24
# VOCAB_SIZE is 4000, not production's 1000: at 1000 most gazetteer surfaces
# ("Ladislao", "Maragondon", "apostille", "BSHM") would be OOV, which is fatal
# for slot filling and harmless for whole-sentence intent classification.
VOCAB_SIZE: int = 4000
EMBEDDING_DIM: int = 64
LSTM_UNITS: int = 64

PAD_TOKEN: str = "<pad>"
OOV_TOKEN: str = "<unk>"
PAD_ID: int = 0
OOV_ID: int = 1

SEP: str = " <=> "
# Format compatibility with corpora that carry no intent (the reference repo's
# MIT corpus). Our corpus always has an intent, so this never appears in it.
NULL_INTENT: str = "<NULL>"

CORPUS_DIR: str = "data/slots"
TRAIN_PATH: str = "data/slots/train"
VALID_PATH: str = "data/slots/valid"
TEST_PATH: str = "data/slots/test"
VOCAB_SLOT_PATH: str = "data/slots/vocab.slot"
VOCAB_INTENT_PATH: str = "data/slots/vocab.intent"

MODEL_PATH: str = "models/joint_nlu.keras"
TOKEN_VOCAB_PATH: str = "models/joint_token_vocab.json"
LABELS_PATH: str = "models/joint_labels.json"
META_PATH: str = "models/joint_meta.json"


class SlotFormatError(ValueError):
    """A corpus line, row or vocab entry violates the frozen format."""


JOINT_TOKEN_RE: re.Pattern = re.compile(
    r"""\d+(?:[.,]\d+)+                    # grouped/decimal numbers: 1,500.00  1.25
      | \d+                                # bare numbers: 8468, 92000
      | [^\W\d_]+(?:['’\-][^\W\d_]+)* # word runs, internal ' , U+2019, - kept: mag-OJT, dean's
      | \S                                 # any other single non-space char: ? , ! : peso emoji
    """,
    re.UNICODE | re.VERBOSE,
)


# ALIGNMENT-CRITICAL. This is deliberately NOT NeuralNetworkTrainer._preprocess
# (api/hybrid_chatbot.py:1902). That function lowercases -> deletes every char
# outside [a-z0-9\s] -> nltk.word_tokenize -> WordNet-lemmatizes -> rejoins.
# Measured effects: "Kumusta CvSU?" 3->2 tokens (? deleted); "paano mag-OJT" ->
# "paano magojt" (token rewritten); "students" -> "student"; every non-ASCII
# character (n-tilde, peso sign) deleted. That destroys the label[i] <-> token[i]
# invariant that slot filling requires. It is fine for whole-sentence intent
# classification, which is why production still uses it. The joint path receives
# the RAW text at api/nlu_engine.py:225 and must keep it raw.
def joint_tokenize(text: str) -> list[tuple[str, int, int]]:
    """Lossless, alignment-preserving tokenizer. -> [(surface, char_start, char_end)].

    Every non-whitespace character of `text` is consumed by exactly one token, so
    text[start:end] is the verbatim surface for any predicted span. No lowering,
    no lemmatizing, no deletion, no NLTK. Tokens never contain whitespace.
    """
    return [(m.group(0), m.start(), m.end()) for m in JOINT_TOKEN_RE.finditer(text)]


def tokens_of(text: str) -> list[str]:
    return [t for t, _s, _e in joint_tokenize(text)]


def vocab_key(token: str) -> str:
    """The only normalization applied before embedding-vocab lookup."""
    return token.lower()


def fold(token: str) -> str:
    """Diacritic-and-case fold, for GAZETTEER MATCHING ONLY. Never used to build
    the corpus surfaces, never used for vocab ids. Makes 'Dasmarinas' match
    'Dasmarinas' with n-tilde."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", token.lower())
        if not unicodedata.combining(c)
    )


def validate_row(tokens: Sequence[str], tags: Sequence[str], intent: str) -> None:
    """Raise SlotFormatError on: empty tokens; len mismatch; whitespace in a token;
    a tag not in TAGS; empty intent; whitespace in intent."""
    if not tokens:
        raise SlotFormatError("empty token sequence")
    if len(tokens) != len(tags):
        raise SlotFormatError(
            f"length mismatch: tokens={len(tokens)} tags={len(tags)}"
        )
    for tok in tokens:
        if not tok:
            raise SlotFormatError("empty token")
        if any(c.isspace() for c in tok):
            raise SlotFormatError(f"whitespace in token: {tok!r}")
    for tag in tags:
        if tag not in TAG2ID:
            raise SlotFormatError(f"unknown tag: {tag!r}")
    if not intent:
        raise SlotFormatError("empty intent")
    if any(c.isspace() for c in intent):
        raise SlotFormatError(f"whitespace in intent: {intent!r}")


def encode_line(tokens: Sequence[str], tags: Sequence[str], intent: str) -> str:
    """-> 'tok:TAG tok:TAG <=> intent' with NO trailing newline."""
    validate_row(tokens, tags, intent)
    return " ".join(f"{t}:{g}" for t, g in zip(tokens, tags)) + SEP + intent


def decode_line(line: str) -> tuple[list[str], list[str], str]:
    """Inverse of encode_line. Splits each field on its LAST colon, so a token
    that is itself ':' round-trips ('::O' -> (':', 'O'))."""
    text = line.rstrip("\n").rstrip("\r")
    if SEP not in text:
        raise SlotFormatError(f"missing {SEP!r} separator")
    body, intent = text.rsplit(SEP, 1)
    fields = body.split(" ")
    tokens: list[str] = []
    tags: list[str] = []
    for field in fields:
        if ":" not in field:
            raise SlotFormatError(f"field without ':': {field!r}")
        tok, tag = field.rsplit(":", 1)
        if not tok:
            raise SlotFormatError(f"empty token in field: {field!r}")
        if tag not in TAG2ID:
            raise SlotFormatError(f"unknown tag in field: {field!r}")
        tokens.append(tok)
        tags.append(tag)
    validate_row(tokens, tags, intent)
    return tokens, tags, intent


def read_corpus(path: str) -> list[tuple[list[str], list[str], str]]:
    rows: list[tuple[list[str], list[str], str]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            try:
                rows.append(decode_line(line))
            except SlotFormatError as exc:
                raise SlotFormatError(f"{path}:{lineno}: {exc}") from exc
    return rows


def write_corpus(
    path: str, rows: Iterable[tuple[Sequence[str], Sequence[str], str]]
) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for tokens, tags, intent in rows:
            fh.write(encode_line(tokens, tags, intent) + "\n")


def read_vocab(path: str) -> list[str]:
    """One item per line, order preserved (== id order)."""
    with open(path, "r", encoding="utf-8") as fh:
        return [line.rstrip("\n").rstrip("\r") for line in fh if line.strip()]


def write_vocab(path: str, items: Iterable[str]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(item + "\n")


def encode_tokens(tokens: Sequence[str], token_vocab: Mapping[str, int]) -> list[int]:
    """Token ids, post-truncated and post-padded to exactly MAX_LEN.

    truncating='post' deliberately diverges from production's default 'pre'
    (api/hybrid_chatbot.py:312): pre-truncation decapitates the utterance and
    would misalign every slot label against its token.
    """
    ids = [token_vocab.get(vocab_key(t), OOV_ID) for t in tokens[:MAX_LEN]]
    ids.extend([PAD_ID] * (MAX_LEN - len(ids)))
    return ids
