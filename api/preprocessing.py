"""
Text preprocessing helpers for SeviAi training and inference.

`preprocess_text` below is NOT the function the shipped models use. Five
byte-identical copies of a *different* pipeline (lowercase -> strip non-alnum
-> nltk.word_tokenize -> lemmatize, no stopword removal) are what actually run:

    api/hybrid_chatbot.py   NaiveBayesModel._preprocess      NB inference
    api/hybrid_chatbot.py   NeuralNetworkModel._preprocess   NN inference
    api/hybrid_chatbot.py   NeuralNetworkTrainer._preprocess NN training
    training/train_naive_bayes.py  preprocess_text           NB training
    train_naive_bayes.py           preprocess_text           NB training (legacy root copy)

They agree with each other, so train and serve are aligned today — but the
alignment is a coincidence maintained by hand across five files, which is
exactly the failure this module's docstring used to claim it prevented.
`preprocess_text` here has different semantics (a stoplist, unicode-preserving
punctuation handling) and no importer; treat it as unused until something
consolidates onto it.

`expand_abbreviations` IS shared: all five sites call it, so the shorthand map
below has one home even though the tokenizers do not.
"""

import re
from functools import lru_cache

import nltk
from nltk.stem import WordNetLemmatizer

# Ensure NLTK resources are available at import time
for _resource in ("punkt_tab", "wordnet"):
    try:
        nltk.data.find(f"tokenizers/{_resource}" if _resource.startswith("punkt") else f"corpora/{_resource}")
    except LookupError:
        nltk.download(_resource, quiet=True)

_lemmatizer = WordNetLemmatizer()

# Deliberately small stoplist. We do NOT use sklearn's `stop_words='english'`
# because it strips wh-words (what/where/when/how) which are the highest-signal
# tokens for short user questions like "Where is CvSU?".
_STOP = frozenset({"the", "a", "an", "of", "to", "in", "is", "it"})

# Keep unicode word characters (so Filipino/Spanish diacritics survive),
# drop punctuation. Replace with space so we don't merge tokens.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Chat shorthand -> the word the intent patterns are actually written in.
#
# "Admission Reqs" fell to the fallback while "Admission Requirements" answered
# correctly at 0.70 — the single most-cited complaint of the 2026 tester round
# ("a lot of students like myself use abbreviations or casual wording when
# chatting with an AI"). Nothing upstream normalised the short form, so it
# reached the vectorizer as an out-of-vocabulary token carrying no signal.
#
# Expanded per token BEFORE lemmatisation, so "reqs" -> "requirements" ->
# "requirement" lands on the same lemma the spelled-out patterns produce.
#
# Deliberately conservative. Every entry has exactly one sensible reading in a
# university-enquiry context; anything that is also an ordinary word or has a
# second campus meaning is left alone, because a wrong expansion is worse than
# no expansion — it silently rewrites a question the classifier would otherwise
# have got right. Excluded for that reason: "admin" (a word, and the name of a
# building), "app" (application vs. mobile app), "reg" (registration vs.
# registrar), "gen" , "ed".
_ABBREVIATIONS = {
    "reqs": "requirements",
    "req": "requirement",
    "reqts": "requirements",
    "reqmts": "requirements",
    "requirements": "requirements",  # identity: documents the target form
    "info": "information",
    "sched": "schedule",
    "scheds": "schedules",
    "schol": "scholarship",
    "schols": "scholarships",
    "dept": "department",
    "depts": "departments",
    "uni": "university",
    "sem": "semester",
    "sems": "semesters",
    "prof": "professor",
    "profs": "professors",
    "cert": "certificate",
    "certs": "certificates",
    "docs": "documents",
    "yr": "year",
    "yrs": "years",
    "enrolment": "enrollment",   # PH usage runs both spellings
    "enrolments": "enrollments",
}


@lru_cache(maxsize=8192)
def _lemmatize_cached(token: str) -> str:
    return _lemmatizer.lemmatize(token)


def expand_abbreviations(text: str) -> str:
    """Rewrite whole-token chat shorthand to the form the patterns use.

    Call AFTER lowercasing and punctuation stripping and BEFORE tokenizing or
    lemmatizing, so "reqs" -> "requirements" -> "requirement" lands on the same
    lemma "admission requirements" produces.

    Whole tokens only — a substring pass would turn "prerequisite" into
    "prerequirementuisite". Returns the text unchanged when nothing matches,
    which is the overwhelmingly common case.
    """
    if not text:
        return text
    parts = text.split()
    if not any(p in _ABBREVIATIONS for p in parts):
        return text
    return " ".join(_ABBREVIATIONS.get(p, p) for p in parts)


def preprocess_text(text: str) -> str:
    """Lowercase, strip punctuation, expand shorthand, lemmatize, drop stopwords.

    Expansion runs on both sides of the model — the trainer imports this same
    function — so the training patterns and the user's shorthand land on one
    token. See _ABBREVIATIONS for why the list is short.
    """
    if not text:
        return ""
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return ""
    text = expand_abbreviations(text)
    out = []
    for tok in text.split():
        if tok in _STOP:
            continue
        if tok.isascii() and tok.isalpha():
            tok = _lemmatize_cached(tok)
        out.append(tok)
    return " ".join(out)
