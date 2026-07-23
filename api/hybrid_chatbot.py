"""
Hierarchical Hybrid Chatbot
Combines Naive Bayes (fast) + Neural Network (accurate)
Strategy: Use NB first, fallback to NN if confidence is low
"""

import json
import os
import random
import re
import pickle
import hashlib
import threading
from collections import OrderedDict
import urllib.request
import urllib.error
import numpy as np
from typing import Tuple, Optional
import joblib

import nltk
from nltk.stem import WordNetLemmatizer

# --- Model artifact integrity gate ---------------------------------------
# pickle/joblib/keras execute code on load, so an attacker who can replace a
# models/*.pkl file gets code execution in the API process. We pin known-good
# SHA-256 hashes in models/trusted_hashes.json and refuse to load anything
# that doesn't match. Override for local experiments with
# SEVI_ALLOW_UNVERIFIED_MODELS=1. Regenerate after a retrain:
#   python scripts/update_trusted_hashes.py
_TRUSTED_HASHES_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "trusted_hashes.json")


def _load_trusted_hashes() -> dict:
    try:
        with open(_TRUSTED_HASHES_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("artifacts", {})
    except (FileNotFoundError, ValueError):
        return {}


def verify_artifact(path: str) -> None:
    """Raise if `path`'s SHA-256 isn't the pinned trusted value.

    Unknown artifacts (not in the manifest) are allowed but warned about, so a
    new file type doesn't hard-break startup; a *mismatch* on a known artifact
    is fatal unless SEVI_ALLOW_UNVERIFIED_MODELS=1.
    """
    if os.getenv("SEVI_ALLOW_UNVERIFIED_MODELS") == "1":
        return
    trusted = _load_trusted_hashes()
    name = os.path.basename(path)
    expected = trusted.get(name)
    if expected is None:
        print(f"[WARN] {name} has no pinned hash in trusted_hashes.json — loading unverified.")
        return
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != expected:
        raise ValueError(
            f"Refusing to load {name}: SHA-256 {actual[:12]}… does not match the "
            f"trusted value {expected[:12]}…. If this was a legitimate retrain, run "
            f"scripts/update_trusted_hashes.py; otherwise the artifact may be tampered with."
        )

# Load .env (optional — graceful fallback if python-dotenv missing)
try:
    from dotenv import load_dotenv
    _DOTENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_DOTENV_PATH):
        load_dotenv(_DOTENV_PATH)
except ImportError:
    pass

# Anthropic SDK for Claude fallback (optional)
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# Import advanced NLU engine
try:
    from .nlu_engine import AdvancedNLUEngine
    NLU_AVAILABLE = True
except ImportError:
    NLU_AVAILABLE = False

# Citizens' Charter retrieval tier (document tier of the hybrid brain)
from . import charter_rag, intent_retrieval, site_rag

# TensorFlow imports (optional - graceful fallback if not available)
try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import Dense, Dropout, Embedding, GlobalAveragePooling1D, Bidirectional, LSTM
    from tensorflow.keras.preprocessing.text import Tokenizer
    from tensorflow.keras.preprocessing.sequence import pad_sequences
    from sklearn.preprocessing import LabelEncoder
    from sklearn.model_selection import train_test_split
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False
    print("[WARNING] TensorFlow not available - Neural Network features disabled")
    print("          Run with Python 3.11 or 3.12 for TensorFlow support")

# Download NLTK resources (idempotent — no-op if already present)
for resource, kind in [('punkt_tab', 'tokenizers'), ('wordnet', 'corpora')]:
    try:
        nltk.data.find(f'{kind}/{resource}')
    except (LookupError, OSError):
        nltk.download(resource, quiet=True)

lemmatizer = WordNetLemmatizer()
_NON_ALPHA_RE = r"[^a-z0-9\s]"

# Shared refusal token — any LLM (Claude or Ollama) emits this when it
# judges a query out of scope. The orchestrator intercepts and returns
# a canned refusal in its place.
LLM_REFUSAL_TOKEN = "[OUT_OF_SCOPE]"

# Every LLM_PROVIDER value the fallback tier understands. Adding a new backend
# means adding its client class AND its name here (and to the admin toggle's
# Literal in app.py). Anything outside this set is treated as a hard config
# error at startup rather than silently disabling the LLM tier — that silent
# path is what made LLM_PROVIDER=localai look "broken" before the provider
# existed. "none" is valid and means: intentionally no LLM fallback.
KNOWN_LLM_PROVIDERS = frozenset({"claude", "ollama", "openai", "localai", "none"})

# Function words that are UNAMBIGUOUSLY Filipino — used only to answer "was
# this written in Filipino/Taglish?" when choosing which curated response
# variant to serve. Deliberately excludes tokens that collide with English
# ("at", "may", "o", "an", "a", "i"), so an English sentence cannot drift over
# the threshold on a coincidence.
_FILIPINO_MARKERS = frozenset({
    "ang", "ng", "mga", "sa", "ay", "na", "ba", "po", "opo",
    "ako", "ikaw", "ka", "ko", "mo", "siya", "niya", "kami", "kayo", "sila",
    "nila", "namin", "natin", "niyo", "nyo",
    "yung", "ung", "ito", "iyan", "iyon", "dito", "diyan", "doon",
    "kung", "pero", "kasi", "lang", "naman", "nga", "din", "rin", "pa",
    "meron", "wala", "hindi", "oo", "para", "dahil", "tapos", "kaya",
    "ano", "anong", "saan", "kailan", "kelan", "sino", "paano", "bakit",
    "alin", "ilan", "magkano", "salamat", "raw", "daw", "sana", "muna",
    # Standalone words that ARE the whole message often enough to matter — a
    # one-token input has no other signal, so "sige" must be recognisable on
    # its own or the acknowledgement comes back in English.
    "sige", "ayos", "talaga", "grabe", "tama", "mali", "ganun", "ganon",
    "gets", "oo", "opo", "aywan", "ewan", "bakit",
})
_WORD_RE = re.compile(r"[a-zñ']+")


def _filipino_ratio(text: str) -> float:
    """Share of tokens that are unambiguously Filipino function words."""
    tokens = _WORD_RE.findall((text or "").lower())
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if t in _FILIPINO_MARKERS) / len(tokens)


def _is_filipino(text: str, threshold: float = 0.10) -> bool:
    """True when `text` reads as Filipino/Taglish rather than English.

    A ratio, not a keyword hit: one stray marker in a long English passage
    should not flip it, while a short Taglish question ("kelan ang enrollment")
    is mostly markers and clears the bar easily.

    Only use this on USER INPUT. For choosing between response variants use the
    ratio directly — a Filipino answer that is mostly proper nouns ("- CEIT
    (College of Engineering and Information Technology): Dr. Willie C.
    Buclatin") dilutes below any fixed threshold and would be misread as
    English.
    """
    return _filipino_ratio(text) >= threshold


def build_scope_locked_prompt(
    base_persona: str,
    intent_list: list,
    campus_glossary: Optional[list] = None,
) -> str:
    """
    Combine the DIWA persona with the strict-scope protocol and the list of
    allowed intent topics. Used by both ClaudeLLM and LocalLLM so the model
    can't be tricked into off-topic answers.

    Args:
        campus_glossary: Optional list of (acronym, full_name) tuples. When provided,
            injected as a glossary so the LLM doesn't have to guess at CvSU-specific
            acronyms like CAFENR, CEMDS, CEIT.
    """
    glossary_section = ""
    if campus_glossary:
        glossary_section = (
            "CAMPUS GLOSSARY — these are the authoritative names of CvSU "
            "Indang campus locations and colleges. NEVER guess at these "
            "acronyms; use ONLY the meanings below. If asked about an acronym "
            "not in this list, say you're not sure and refer them to the "
            "registrar or relevant office.\n\n"
            + "\n".join(f"  - {acr}: {full}" for acr, full in campus_glossary)
            + "\n\n"
        )

    scope_section = (
        "STRICT SCOPE — you can ONLY answer questions about Cavite State "
        "University (CvSU). Your knowledge surface is limited to these "
        "topic categories:\n\n"
        + "\n".join(f"  - {tag}" for tag in intent_list)
        + "\n\n"
        "REFUSAL PROTOCOL:\n"
        f"- If the user asks ANYTHING outside CvSU scope (math, general "
        f"knowledge, programming, jokes, other universities, current events, "
        f"weather, recipes, translations, etc.), respond with EXACTLY this "
        f"token and nothing else: {LLM_REFUSAL_TOKEN}\n"
        "- Do not attempt to answer off-topic questions partially.\n"
        "- Do not apologize before the token. Just output the token.\n\n"
        "RESPONSE RULES (when in scope):\n"
        "- Keep answers under 4 sentences unless the user asks for detail.\n"
        "- Never fabricate tuition fees, deadlines, professor names, course codes, building names, or specific numbers — if uncertain, say so and direct the user to the relevant CvSU office.\n"
        "- NEVER guess at acronyms. If an acronym isn't in the Campus Glossary above, say you're not sure and recommend asking the registrar.\n"
        "- For time-sensitive info (deadlines, fees, schedules), always recommend verification with the proper office.\n"
        "- Disambiguate campus when relevant (Indang vs. Imus vs. other satellite campuses).\n"
        "- Respond in the same language as the user (English, Filipino, or Taglish).\n"
    )
    return (base_persona + "\n\n" + glossary_section + scope_section).strip()

class NaiveBayesModel:
    """Fast Naive Bayes model"""

    def __init__(self, model_path: str):
        verify_artifact(model_path)
        self.pipeline = joblib.load(model_path)
        self.name = "Naive Bayes"

    def predict(self, text: str) -> Tuple[str, float]:
        """
        Predict intent and confidence

        Returns:
            (intent, confidence)
        """
        clean_text = self._preprocess(text)
        intent = self.pipeline.predict([clean_text])[0]
        proba = self.pipeline.predict_proba([clean_text])[0]
        confidence = float(np.max(proba))
        return intent, confidence

    @staticmethod
    def _preprocess(text: str) -> str:
        """Preprocess text"""
        text = text.lower()
        text = re.sub(_NON_ALPHA_RE, "", text)
        tokens = nltk.word_tokenize(text)
        return " ".join([lemmatizer.lemmatize(t) for t in tokens])


class NeuralNetworkModel:
    """Accurate Neural Network model (requires TensorFlow)"""

    DEFAULT_CONFIDENCE_THRESHOLD = 0.50
    VOCAB_SIZE = 1000
    MAX_LEN = 20
    EMBEDDING_DIM = 64

    def __init__(self, model_dir: str):
        if not TF_AVAILABLE:
            raise ImportError("TensorFlow required for Neural Network model")

        nn_path = os.path.join(model_dir, "nn_model.h5")
        tok_path = os.path.join(model_dir, "nn_tokenizer.pkl")
        enc_path = os.path.join(model_dir, "nn_label_encoder.pkl")
        verify_artifact(nn_path)
        verify_artifact(tok_path)
        verify_artifact(enc_path)
        self.model = tf.keras.models.load_model(nn_path)
        with open(tok_path, "rb") as f:
            self.tokenizer = pickle.load(f)
        with open(enc_path, "rb") as f:
            self.label_encoder = pickle.load(f)
        self.name = "Neural Network"

        thresholds_path = os.path.join(model_dir, "nn_thresholds.json")
        if os.path.exists(thresholds_path):
            with open(thresholds_path, "r", encoding="utf-8") as f:
                self.adaptive_thresholds: dict = json.load(f)
            print(f"[OK] Loaded adaptive thresholds for {len(self.adaptive_thresholds)} intents")
        else:
            self.adaptive_thresholds = {}

        # Temperature scalar for confidence calibration (T=1 = uncalibrated)
        temp_path = os.path.join(model_dir, "nn_temperature.json")
        if os.path.exists(temp_path):
            with open(temp_path, "r", encoding="utf-8") as f:
                self.temperature: float = json.load(f).get("temperature", 1.0)
            print(f"[OK] Temperature scaling T={self.temperature:.4f}")
        else:
            self.temperature = 1.0

    def get_threshold(self, intent: str) -> float:
        """Return the calibrated confidence threshold for a given intent."""
        return self.adaptive_thresholds.get(intent, self.DEFAULT_CONFIDENCE_THRESHOLD)

    def predict(self, text: str) -> Tuple[str, float]:
        """
        Predict intent and confidence with temperature scaling.

        Returns:
            (intent, confidence)
        """
        clean_text = self._preprocess(text)
        seq = self.tokenizer.texts_to_sequences([clean_text])
        padded = pad_sequences(seq, maxlen=self.MAX_LEN, padding="post")

        proba = self.model.predict(padded, verbose=0)[0]
        if abs(self.temperature - 1.0) > 1e-6:
            scaled = np.power(np.clip(proba, 1e-7, 1.0), 1.0 / self.temperature)
            proba = scaled / scaled.sum()

        intent_idx = int(np.argmax(proba))
        confidence = float(proba[intent_idx])
        intent = self.label_encoder.classes_[intent_idx]

        return intent, confidence

    @staticmethod
    def _preprocess(text: str) -> str:
        """Preprocess text"""
        text = text.lower()
        text = re.sub(_NON_ALPHA_RE, "", text)
        tokens = nltk.word_tokenize(text)
        return " ".join([lemmatizer.lemmatize(t) for t in tokens])


class LocalLLM:
    """
    Thin wrapper around a locally-hosted LLM served via Ollama
    (http://localhost:11434).  Used as the final fallback when both
    NB and NN are below their confidence thresholds.

    To use a different local backend (llama.cpp server, LM Studio, etc.)
    just point OLLAMA_BASE_URL / OLLAMA_MODEL to the compatible endpoint.

    Falls back gracefully to None if the server is unreachable so the
    rest of the chatbot pipeline is unaffected.
    """

    # Override with env vars: OLLAMA_BASE_URL, OLLAMA_MODEL
    DEFAULT_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "llama3.1"
    # 8B models on CPU can take 60-120s on first call (cold start loads weights into RAM);
    # subsequent calls are 2-15s. Set generously so cold start doesn't fail.
    TIMEOUT_SECONDS = 180

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        system_prompt: str = "",
    ):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", self.DEFAULT_MODEL)
        self.system_prompt = system_prompt
        self.available = self._probe()

    def _probe(self) -> bool:
        """Return True if the Ollama server is reachable.

        Uses a generous timeout to accommodate Cloudflare Tunnel latency
        when Ollama is exposed via a remote URL.
        """
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET",
                                         headers={"User-Agent": "DIWA/1.0"})
            with urllib.request.urlopen(req, timeout=15):
                return True
        except Exception as e:
            print(f"[WARNING] Ollama probe failed: {type(e).__name__}: {e}  url={self.base_url}")
            return False

    def generate(self, user_message: str, conversation_context: list = None) -> Optional[str]:
        """
        Send a message to the local LLM and return its reply, or None on error.
        Re-probes if previously unavailable so a transient outage doesn't
        permanently disable the fallback.

        Args:
            user_message: The user's raw input.
            conversation_context: Optional list of prior {"role", "content"} dicts
                                  for multi-turn context (last N turns).
        """
        if not self.available:
            self.available = self._probe()
            if not self.available:
                return None

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if conversation_context:
            messages.extend(conversation_context[-6:])  # last 3 turns
        messages.append({"role": "user", "content": user_message})

        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 512},
        }
        # Thinking models (qwen3, deepseek-r1, ...) reason before answering by
        # default — on CPU that multiplies latency and can spend the whole
        # num_predict budget on reasoning, returning empty content. Chat
        # answers don't need it; turn it off.
        if re.match(r"^(qwen3|deepseek-r1|magistral|gpt-oss)", self.model, re.IGNORECASE):
            body["think"] = False
        payload = json.dumps(body).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "DIWA/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data.get("message", {}).get("content", "")
                # Defensive: strip inlined reasoning if a thinking model
                # ignored the think=false request.
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
                return content.strip() or None
        except urllib.error.URLError as e:
            print(f"[WARNING] Ollama request failed: {e}")
            return None
        except Exception as e:
            print(f"[WARNING] Ollama generate error: {type(e).__name__}: {e}")
            return None


class OpenAICompatLLM:
    """
    Fallback backed by any OpenAI-compatible chat-completions server —
    LocalAI, vLLM, llama.cpp's server, LM Studio, text-generation-webui,
    or Ollama's own /v1 endpoint.

    Talks the OpenAI wire format (POST /chat/completions, GET /models) rather
    than Ollama's native /api/chat, so OPENAI_BASE_URL must point at the API
    base *including* the version prefix, e.g. http://localai:8080/v1.

    Falls back gracefully to None if the server is unreachable so the rest of
    the pipeline is unaffected.
    """

    # Override with env vars: OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_API_KEY
    DEFAULT_BASE_URL = "http://localhost:8080/v1"
    DEFAULT_MODEL = "gpt-3.5-turbo"
    # Local CPU inference has the same cold-start cost as Ollama — be generous.
    TIMEOUT_SECONDS = 180

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        api_key: str = None,
        system_prompt: str = "",
    ):
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("OPENAI_MODEL", self.DEFAULT_MODEL)
        # Optional — LocalAI usually needs no key; a hosted OpenAI-compatible
        # endpoint (or a LocalAI configured with API_KEY) does.
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.system_prompt = system_prompt
        self.available = self._probe()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": "DIWA/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _probe(self) -> bool:
        """Return True if the OpenAI-compatible server is reachable (GET /models)."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models", method="GET",
                                         headers=self._headers())
            with urllib.request.urlopen(req, timeout=15):
                return True
        except Exception as e:
            print(f"[WARNING] OpenAI-compat probe failed: {type(e).__name__}: {e}  url={self.base_url}")
            return False

    def generate(self, user_message: str, conversation_context: list = None) -> Optional[str]:
        """Send a message and return the reply, or None on error. Re-probes if
        previously unavailable so a transient outage doesn't permanently disable
        the fallback."""
        if not self.available:
            self.available = self._probe()
            if not self.available:
                return None

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if conversation_context:
            messages.extend(conversation_context[-6:])  # last 3 turns
        messages.append({"role": "user", "content": user_message})

        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.3,
            "max_tokens": 512,
        }
        payload = json.dumps(body).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=payload,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices") or []
                content = choices[0].get("message", {}).get("content", "") if choices else ""
                # Defensive: strip inlined reasoning if a thinking model emits it.
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
                return content.strip() or None
        except urllib.error.URLError as e:
            print(f"[WARNING] OpenAI-compat request failed: {e}")
            return None
        except Exception as e:
            print(f"[WARNING] OpenAI-compat generate error: {type(e).__name__}: {e}")
            return None


class NonsenseGate:
    """
    Blocks gibberish, prompt-injection, and off-topic statements before
    they reach the LLM. Rule set is tuned from observed bad inputs in
    chat_*.log — see notes in each pattern. Intentionally conservative:
    a clear question word or "?" lets borderline messages through, so
    legitimate Filipino + English queries are not blocked.
    """

    MIN_LEN = 3
    MIN_ALPHAS = 2
    MIN_VOWEL_RATIO = 0.18  # below this on length-5+ tokens = keysmash

    # Short words we accept on their own (whole-message equality).
    # The second row is conversational Filipino/English particles observed
    # refused in production testing (2026-07): "po"/"opo" answered with a
    # CvSU-scope refusal reads as a non-sequitur to a Filipino user.
    _ALLOW_SHORT = {
        "hi", "hello", "hey", "yes", "no", "ok", "okay",
        "po", "opo", "oo", "ty", "thx", "tnx", "sup", "yo",
        "gm", "gn", "bye", "lol", "wow", "yep", "yup", "nah", "thanks", "k",
        "cvsu", "ceit", "con", "cas", "cafenr", "cemds",
        "ojt", "tor", "cor", "cav", "cat", "map", "fee", "fees",
    }

    # Vowel-free-but-real tokens the keysmash heuristic must not score:
    # campus acronyms ("CWTS CvSU" is 1 vowel in 8 letters) and connectors.
    _KNOWN_TOKENS = _ALLOW_SHORT | {
        "cwts", "lts", "rotc", "nstp", "gwa", "dtr", "coe", "cog",
        "mdl", "lms", "gmc", "ssg", "lgbtq", "vs",
        "bsit", "bscs", "bsba", "bsn", "dvm", "bshm", "bstm", "bsbm",
        "bsed", "beed", "bsa", "bsp",
    }

    # Profanity / pure venting — no information to act on.
    # NOTE: tang(ina|ena)\w* catches "tangina", "tanginamo", "tanginang", etc.
    _PROFANITY = re.compile(
        r"\b(wtf|f[*u]ck|sh[*i]t|bullsh|tang(?:ina|ena)\w*|gago\w*|"
        r"putang\w*|tarantado|bobo|hayop|ulol)\b",
        re.IGNORECASE,
    )

    # Explicit prompt-injection cues — always block, even with CvSU words.
    _PROMPT_INJECTION = re.compile(
        r"\b(the\s+correct\s+answer\s+is|correct\s+answer\s+is\s+that|"
        r"ignore\s+(?:previous|prior|the)\s+instructions|"
        r"you\s+are\s+now|forget\s+(?:everything|your\s+instructions)|"
        r"as\s+an\s+ai\b|system\s+prompt)\b",
        re.IGNORECASE,
    )

    # Keyboard-mashing patterns ("asdfgh", "qwerqwer", "zxcvb")
    _KEYSMASH = re.compile(
        r"(?:asdf|qwer|zxcv|hjkl|fdsa|rewq|poiu|jkl;)",
        re.IGNORECASE,
    )

    # Fact-injection / prompt-injection assertions. Caught examples:
    #   "Ang Turon ay isang sikat na meryenda..."
    #   "The correct answer is that ..."
    #   "Ang swimming pool ay matatagpuan malapit sa saluysoy"
    #   "Saging ang laman ng lumpiang saging..."
    #   "Lumpiang saging is just a playful term for ..."
    _FACT_INJECTION = re.compile(
        r"\b(ang\s+\w+(?:\s+\w+){0,3}\s+ay\s+\S+|"
        r"\w+\s+ang\s+laman\s+ng\s+\w+|"
        r"magkaiba\s+ang\s+\w+|"
        r"\w+\s+is\s+just\s+a\b|"
        r"the\s+correct\s+answer\s+is|"
        r"correct\s+answer\s+is\s+that|"
        r"\w+\s+ay\s+matatagpuan|"
        r"\w+\s+is\s+near\s+\w+|"
        r"\w+\s+is\s+the\s+same\s+as|"
        r"hindi\s+\w+,?\s+\w+\s+ang)\b",
        re.IGNORECASE,
    )

    # Off-topic concrete nouns (food etc.) that have no CvSU meaning.
    _OFFTOPIC_NOUNS = re.compile(
        r"\b(turon|lumpia(?:ng)?|adobo|sinigang|kakanin|halo[\-\s]?halo|"
        r"hotdog|lechon|kainan|sikat\s+na\s+meryenda|merienda|meryenda)\b",
        re.IGNORECASE,
    )

    # Strong question signals — having any of these lets a borderline
    # message through (we don't want to block real Filipino questions).
    _QUESTION = re.compile(
        r"[?]|^\s*(what|when|where|why|how|who|which|"
        r"is\s|are\s|can\s|does\s|do\s|will\s|may\s|"
        r"ano|saan|kailan|sino|paano|bakit|alin|kamusta|"
        r"may|meron|mayroon|pwede|puwede)\b",
        re.IGNORECASE,
    )

    # CvSU context — exempts assertions that mention real CvSU terms
    # (so "BSCS ay 4-year program" still gets through to the model).
    _CVSU_CONTEXT = re.compile(
        r"\b(cvsu|cavite\s+state|admission|enrollment|tuition|"
        r"ceit|cafenr|cemds|cas|college|registrar|campus|"
        r"course|program|class|student|scholarship|"
        r"freshmen|transferee|graduate|bs[a-z]{1,4})\b",
        re.IGNORECASE,
    )

    def allows(self, text: str) -> Tuple[bool, str]:
        if not text or not text.strip():
            return False, "empty"
        t = text.strip()
        t_lower = t.lower()
        # Allowlist comparisons ignore trailing punctuation: "TOR?" and "po!"
        # are the allowlisted word, asked — curated patterns "TOR?"/"COR?"/
        # "Hey?" were refused as too_short before this strip (2026-07).
        t_bare = t_lower.strip("?!.,")
        # An exact allowlisted token is conversational, not junk — accept it
        # before the length rules ("k" is a curated acknowledgement pattern
        # that MIN_ALPHAS would refuse).
        if t_bare in self._ALLOW_SHORT:
            return True, "ok"
        alphas = sum(c.isalpha() for c in t)

        # Single-word / very short input — only allow well-known short tokens.
        if alphas < self.MIN_ALPHAS:
            return False, "too_short"
        if " " not in t and alphas < 4:
            return False, "too_short"

        if self._PROFANITY.search(t):
            return False, "profanity"

        if self._KEYSMASH.search(t):
            return False, "keysmash"

        # Prompt-injection language is blocked unconditionally (CvSU
        # mention is not an exemption — these phrasings are abusive).
        if self._PROMPT_INJECTION.search(t):
            return False, "prompt_injection"

        # Vowel-starved text = keyboard noise (e.g. "fgbhnj", "tnsmnsl") —
        # but score only the tokens we don't recognize: acronym asks like
        # "CWTS CvSU" (1 vowel / 8 letters) and "thanks" (1/6, < 0.18) are
        # real messages the raw ratio refused (2026-07). Recognizing tokens
        # is the whole exemption — do NOT also exempt on _CVSU_CONTEXT, or
        # "tnsmnsl bcdfg cvsu" walks straight through the keysmash guard.
        if alphas >= 5:
            unknown = "".join(
                w for w in re.split(r"[^a-z]+", t_lower)
                if w and w not in self._KNOWN_TOKENS
            )
            u_alphas = len(unknown)
            vowels = sum(c in "aeiou" for c in unknown)
            # A single vowel-light English word ("sports", "sprint", "stars")
            # sits at 1/6 = 0.167, under the ratio — so the ratio alone only
            # judges longer spans, and short spans must be vowel-FREE to count
            # as keysmash ("jkjkjk", "fgbhnjk").
            if (u_alphas >= 5 and vowels == 0) or (
                    u_alphas >= 8 and vowels / u_alphas < self.MIN_VOWEL_RATIO):
                return False, "low_vowel_ratio"

        # Off-topic food / non-CvSU noun without any CvSU context.
        if self._OFFTOPIC_NOUNS.search(t) and not self._CVSU_CONTEXT.search(t):
            return False, "offtopic_subject"

        # Fact-injection statement without question + without CvSU context.
        if (
            self._FACT_INJECTION.search(t)
            and not self._QUESTION.search(t)
            and not self._CVSU_CONTEXT.search(t)
        ):
            return False, "fact_injection"

        return True, "ok"


class ScopeGate:
    """
    Pre-filter that blocks off-topic queries before they reach the LLM.

    Cheaper and more reliable than letting the LLM decide — catches math
    problems, programming questions, general-knowledge queries, etc. with
    deterministic rules so the model never gets a chance to embarrass us
    by answering them.
    """

    MAX_LENGTH = 800  # chars — anything longer is suspicious

    # Math / computation patterns (lowercased input)
    # Tuned 2026-07 against all 3135 intent patterns + the 268-Q mirror eval;
    # the previous form refused real CvSU questions as math:
    #   "how much is the tuition fee for BSIT"   (bare "how much is")
    #   "how to compute GWA" / "calculate my GWA" (bare "compute|calculate")
    #   "what is 1.0 in CvSU"                     ("what is \d" — a grades ask)
    # calculate/compute/evaluate/simplify fire only on a MATH OBJECT, not on
    # the bare verb: an allowlist beats a blocklist here because those verbs
    # are ordinary CvSU vocabulary ("how to compute GWA", "paano mag-compute
    # ng GWA", "criteria used to evaluate PSR candidates") while the objects
    # ("two plus two", "the square root of", "the area of") are not. Homework
    # asks that slip ("compute this") have their own curated intent,
    # off_topic_homework, which refuses them properly.
    # (?<!-)integrate: Tagalog "na-integrate sa CvSU" is school history, not
    # calculus. "integrated" never matched (the trailing \b sees the 'd').
    _MATH_KEYWORDS = re.compile(
        r"\b(solve|(?<!-)integrate|"
        r"differentiate|derivative|integral|equation|factorial|"
        r"logarithm|sine|cosine|tangent|matrix|determinant|"
        r"probability of|(?:calculate|compute|evaluate|simplify)\s+"
        r"(?:the\s+|this\s+|that\s+|a\s+)?"
        r"(?:\d|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"hundred|thousand|square\s+root|fraction|area|volume|perimeter|"
        r"circumference|sum|product|quotient|expression))\b",
        re.IGNORECASE,
    )
    _MATH_EXPRESSION = re.compile(r"\d+\s*[\+\*/\^x×÷]\s*\d")
    # Subtraction needs its own rule: "500-125" is arithmetic but "2025-2028",
    # "AY 2025-2026", "10:00-12:00", "K-12" and "F-137" are ranges/compounds.
    # The lookbehind rejects a digit/colon/dot/hyphen on the left, so only the
    # first number of a run can start a match; the lookahead spares year pairs.
    _SUBTRACTION = re.compile(
        r"(?<![\d:.\-])(?!(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2})\d{1,4}\s*-\s*\d{1,4}(?![\d:])"
    )
    # No bare '-' here either (it would eat "K-12"); "x-3 = 7" is caught by the
    # '=' arm, which accepts a digit or a letter on its left.
    _EQUATION_LIKE = re.compile(r"[a-z]\s*[\+\*/]\s*\d+|[a-z0-9]\s*=\s*\d", re.IGNORECASE)

    # Off-topic keyword list (each must match as a whole phrase/word)
    _OFFTOPIC = re.compile(
        r"\b(capital of|weather in|recipe|cook|bake|"
        r"celebrity|movie|netflix|tiktok|"
        # Bare "football|basketball game" blocked sports_athletics asks
        # ("football team CvSU" is intramurals, not the NFL).
        r"sports score|nba|fifa|nfl|premier league|world cup|"
        r"write code|debug|python|javascript|java code|c\+\+|"
        r"write a poem|write a story|write a song|write me a|"
        r"translate to|translate this|translation of|"
        r"tell a joke|tell me a joke|funny joke|"
        # "president of CvSU / Cavite State" is a university_officials ask;
        # only the national-politics form is off-topic.
        r"president of (?!cvsu|cavite)|prime minister|election|"
        r"bitcoin|crypto|stock price|forex|"
        r"horoscope|zodiac|tarot)\b",
        re.IGNORECASE,
    )

    REFUSAL_MESSAGES = [
        "I can only help with questions about Cavite State University — programs, admissions, fees, scholarships, campus services, and policies. Is there something CvSU-related I can help with?",
        "That's outside my scope. I'm Sevi, the CvSU virtual assistant — I focus on Cavite State University topics like enrollment, courses, scholarships, and campus information. What would you like to know about CvSU?",
        "I'm not able to answer that — I'm built to help with CvSU-related questions only (admissions, programs, fees, campus services). Please ask me something about Cavite State University.",
    ]

    def allows(self, text: str) -> Tuple[bool, str]:
        """
        Returns (allowed, reason). If allowed=False, reason names which rule fired.
        """
        if not text or not text.strip():
            return False, "empty"
        if len(text) > self.MAX_LENGTH:
            return False, "too_long"
        if self._MATH_KEYWORDS.search(text):
            return False, "math_keyword"
        if self._MATH_EXPRESSION.search(text) or self._SUBTRACTION.search(text):
            return False, "math_expression"
        if self._EQUATION_LIKE.search(text):
            return False, "equation"
        if self._OFFTOPIC.search(text):
            return False, "offtopic_keyword"
        return True, "ok"

    def refusal(self) -> str:
        """Return a randomly selected refusal message."""
        return random.choice(self.REFUSAL_MESSAGES)


class ClaudeLLM:
    """
    Claude API fallback — used when NB+NN are both below threshold and
    the ScopeGate allowed the query through.

    Hard-locks Claude to CvSU topics via system prompt + intent list.
    Uses prompt caching so the large system prompt is ~0.1x cost on
    repeated calls.

    Returns None on any error so the caller can degrade to the static
    fallback gracefully.
    """

    DEFAULT_MODEL = "claude-haiku-4-5"
    MAX_TOKENS = 400
    TIMEOUT_SECONDS = 12

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: str = "",
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.model = model or os.getenv("CLAUDE_MODEL", self.DEFAULT_MODEL)
        # Single cached block — system prompt is stable, served at ~0.1x cost after first call
        self.system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self.client = None
        self.available = False

        if not ANTHROPIC_AVAILABLE:
            return
        if not self.api_key:
            return
        try:
            self.client = anthropic.Anthropic(
                api_key=self.api_key,
                timeout=self.TIMEOUT_SECONDS,
            )
            self.available = True
        except Exception as e:
            print(f"[WARNING] Claude client init failed: {e}")
            self.available = False

    def generate(
        self,
        user_message: str,
        conversation_context: Optional[list] = None,
    ) -> Optional[str]:
        """
        Returns Claude's reply, the REFUSAL_TOKEN if out of scope, or None on error.
        """
        if not self.available or not self.client:
            return None

        messages = []
        if conversation_context:
            for turn in conversation_context[-6:]:
                role = turn.get("role")
                content = turn.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.MAX_TOKENS,
                system=self.system_blocks,
                messages=messages,
            )
            text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
            reply = "".join(text_parts).strip()
            return reply or None
        except anthropic.APIStatusError as e:
            print(f"[WARNING] Claude API status error: {e.status_code} {getattr(e, 'message', '')}")
            return None
        except anthropic.APIConnectionError:
            print("[WARNING] Claude API connection error")
            return None
        except Exception as e:
            print(f"[WARNING] Claude generate failed: {e}")
            return None


# "1. College deans" / "2) Tuition" — an enumerated item in a bot reply.
_NUMBERED_ITEM_RE = re.compile(r"^[ \t]*(\d{1,2})[.)]\s+(\S.*?)[ \t]*$", re.MULTILINE)

# A whole message that is nothing but a pointer at a list position: "10",
# "#10", "no. 10", "number 10", "the 10th one", "ika-10". Anchored end to end
# so it can never fire on a real question, and \d{1,2} with no decimal part
# keeps CvSU grade values ("1.0", "2.75") out of it.
_ORDINAL_REF_RE = re.compile(
    r"^\s*(?:the\s+)?(?:#|no\.?|nr\.?|number|item|option|choice|ika-)?\s*"
    r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:one|item|option|po|please|pls)?\s*[.?!]*\s*$",
    re.IGNORECASE,
)


def _numbered_items(text: str) -> list:
    """Ordered list items in a bot reply, so the next turn can dereference them."""
    if not text or len(text) > 20000:
        return []
    items = _NUMBERED_ITEM_RE.findall(text)
    # Require a real enumeration starting at 1 — a lone "1. step" or a stray
    # "2024." in prose is not a menu the user can point at.
    if len(items) < 2 or items[0][0] != "1":
        return []
    return [text.strip() for _, text in items]


class HybridChatbot:
    """
    Hierarchical Hybrid Chatbot
    Strategy: Use fast NB first, fallback to accurate NN if uncertain
    """

    NB_CONFIDENCE_THRESHOLD = 0.65  # If NB confidence >= 65%, use it; otherwise defer to NN.
    # Raised from 0.55: with the NLU boost no longer inflating confidence, borderline
    # NB force-fits (e.g. an off-topic query landing in courses_offered at ~0.63) now
    # defer to the NN + scope/nonsense gates + LLM-grounded tiers instead of being served.
    NN_CONFIDENCE_THRESHOLD = 0.50  # NN minimum confidence threshold
    FALLBACK_INTENT = "nlu_fallback"
    # Wayfinding replies from the Place Resolver tier. Matches the map-first
    # regexes on both ends (api/app.py _MAP_FIRST_INTENT_RE and the frontend),
    # so the map card renders open above the text.
    FIND_PLACE_INTENT = "find_place"
    # Session-recap replies from the Conversation Recap tier. Not in the
    # trained taxonomy: the tier answers deterministically from this session's
    # history, so the classifiers must never own it ("chitchat" captures
    # "summarize our conversation" at 0.65 and answers with a greeting, and
    # the grounded LLM invents a recap from corpus passages instead).
    RECAP_INTENT = "conversation_recap"

    # Meta-questions about the conversation itself. Every alternative requires
    # a conversation word or a we/I-asked construction so content asks like
    # "summarize the admission requirements" never match. Swept against all
    # 3135 intent patterns and the 268-question mirror eval: 0 hits.
    _RECAP_RE = re.compile(
        r"(?:\b(?:summarize|summarise|recap)\b.{0,24}?"
        r"\b(?:our|this|the)\s+(?:conversation|convo|chat|discussion|usapan)\b)"
        r"|(?:\b(?:summarize|summarise|recap)\s+what\s+(?:i|we)\b)"
        r"|(?:\bwhat\s+(?:did|have|had)\s+(?:we|i)\s+"
        r"(?:talk(?:ed)?|discuss(?:ed)?|ask(?:ed)?|say|said|cover(?:ed)?)\b)"
        r"|(?:\bwhat\s+(?:did|do)\s+(?:we|i)\s+(?:talk|speak)\s+about\b)"
        r"|(?:\b(?:ano|anong)\b.{0,16}?\b(?:napag|pinag)-?usapan\b)"
        r"|(?:\bbuod\s+ng\s+(?:usapan|pinag-?usapan)\b)",
        re.IGNORECASE,
    )

    def __init__(self, model_dir: str, responses_path: str):
        """
        Initialize hybrid chatbot with both models

        Args:
            model_dir: Directory containing trained models
            responses_path: Path to responses JSON
        """
        print("\n" + "=" * 60)
        print("  HIERARCHICAL HYBRID CHATBOT INITIALIZATION")
        print("=" * 60)

        # Load both models
        print("\n[1/4] Loading Naive Bayes (Fast)...")
        try:
            self.nb_model = NaiveBayesModel(
                os.path.join(model_dir, "CvSU_classifier.pkl")
            )
            print("[OK] Naive Bayes loaded")
        except Exception as e:
            print(f"[FAILED] Failed to load NB: {e}")
            self.nb_model = None

        print("\n[2/4] Loading Neural Network (Accurate)...")
        if not TF_AVAILABLE:
            print("[WARNING] TensorFlow not available - NN disabled")
            print("          Install Python 3.11/3.12 + TensorFlow to enable NN")
            self.nn_model = None
        else:
            try:
                self.nn_model = NeuralNetworkModel(model_dir)
                print("[OK] Neural Network loaded")
            except Exception as e:
                print(f"[WARNING] Could not load NN: {e}")
                print("          Run 'python train_hybrid.py' to train the NN model")
                self.nn_model = None

        # Load responses
        print("\n[3/4] Loading responses...")
        with open(responses_path, "r", encoding="utf-8") as f:
            self.responses_map = json.load(f)
        print(f"[OK] Loaded {len(self.responses_map)} intent responses")

        # Conversation tracking. Bounded LRU: the public cvsu.edu.ph widget
        # mints a fresh session per visitor, so an unbounded dict would grow one
        # never-freed entry per visitor. Cap the number of tracked sessions and
        # the turns kept per session (only the last few are ever read for LLM
        # context anyway).
        self.conversation_history = OrderedDict()
        self._MAX_HISTORY_SESSIONS = int(os.getenv("MAX_HISTORY_SESSIONS", "2000"))
        self._MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "50"))
        self.model_usage_stats = {
            "naive_bayes_used": 0,
            "neural_network_used": 0,
            "place_resolver_used": 0,
            "conversation_recap_used": 0,
            "fallback_used": 0,
            "nlu_enhanced": 0
        }

        # Initialize NLU engine for advanced understanding
        if NLU_AVAILABLE:
            self.nlu_engine = AdvancedNLUEngine()
            print("[OK] Advanced NLU Engine loaded")
        else:
            self.nlu_engine = None
            print("[WARNING] Advanced NLU Engine not available")

        # Initialize LLM fallback (Claude API by default, Ollama optional)
        provider = os.getenv("LLM_PROVIDER", "claude").strip().lower()
        self.scope_gate = ScopeGate()
        self.nonsense_gate = NonsenseGate()
        self.llm = None
        self.llm_provider = provider
        # Populated below so /health and the log can explain *why* a provider is
        # not ready without anyone shelling into the container.
        self.llm_last_error: Optional[str] = None

        # Build campus glossary so the LLM doesn't hallucinate on CvSU acronyms
        # (e.g. asking about CAFENR shouldn't return "Cafeteria"). Pulls the
        # canonical names from the campus_places module — single source of truth.
        campus_glossary = self._build_campus_glossary()

        # Build the scope-locked system prompt once — used by whichever LLM provider runs.
        scope_locked_prompt = build_scope_locked_prompt(
            base_persona=self._system_prompt_text(),
            intent_list=list(self.responses_map.keys()),
            campus_glossary=campus_glossary,
        )

        # Echo the resolved config up front so the log shows exactly what the
        # process will try — the #1 thing you want when llm_ready comes back
        # false. Env is the source of truth here; values not secret are printed.
        print(f"\n[4/5] LLM fallback — resolving provider (LLM_PROVIDER={provider!r})")
        print(f"       known providers: {', '.join(sorted(KNOWN_LLM_PROVIDERS))}")
        if provider in ("openai", "localai"):
            print(f"       OPENAI_BASE_URL={os.getenv('OPENAI_BASE_URL', OpenAICompatLLM.DEFAULT_BASE_URL)}")
            print(f"       OPENAI_MODEL={os.getenv('OPENAI_MODEL', OpenAICompatLLM.DEFAULT_MODEL)}")
            print(f"       OPENAI_API_KEY={'set' if os.getenv('OPENAI_API_KEY') else 'unset'}")
        elif provider == "ollama":
            print(f"       OLLAMA_BASE_URL={os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}")
            print(f"       OLLAMA_MODEL={os.getenv('OLLAMA_MODEL', 'llama3.2:3b')}")
        elif provider == "claude":
            print(f"       CLAUDE_MODEL={os.getenv('CLAUDE_MODEL', '(default)')}")
            print(f"       ANTHROPIC_API_KEY={'set' if os.getenv('ANTHROPIC_API_KEY') else 'unset'}")

        if provider == "claude":
            print("       initialising Claude API fallback...")
            self.llm = ClaudeLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] Claude LLM ready  model={self.llm.model}")
            else:
                if not ANTHROPIC_AVAILABLE:
                    self.llm_last_error = "anthropic package not installed (pip install anthropic)"
                else:
                    self.llm_last_error = "ANTHROPIC_API_KEY not set or invalid"
                print(f"[WARNING] Claude fallback disabled — {self.llm_last_error}")
        elif provider == "ollama":
            print("       initialising local LLM fallback (Ollama)...")
            self.llm = LocalLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] Local LLM ready  model={self.llm.model}  url={self.llm.base_url}")
                # Warm-up in background so the first user query doesn't pay
                # the 60-120s cold-start cost on CPU-only machines.
                self._warm_up_llm_async()
            else:
                self.llm_last_error = (
                    f"Ollama not reachable at {getattr(self.llm, 'base_url', '?')} "
                    f"(model={getattr(self.llm, 'model', '?')})"
                )
                print(f"[WARNING] {self.llm_last_error} — deep-fallback disabled")
                print("          Start Ollama and run: ollama pull llama3.1")
        elif provider in ("openai", "localai"):
            print(f"       initialising OpenAI-compatible LLM fallback ({provider})...")
            self.llm = OpenAICompatLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] OpenAI-compat LLM ready  model={self.llm.model}  url={self.llm.base_url}")
                self._warm_up_llm_async()
            else:
                self.llm_last_error = (
                    f"OpenAI-compat server not reachable at {self.llm.base_url} "
                    f"(model={self.llm.model})"
                )
                print(f"[WARNING] {self.llm_last_error} — deep-fallback disabled")
                print("          Check OPENAI_BASE_URL / OPENAI_MODEL and that the model is loaded")
        elif provider == "none":
            print("       LLM fallback intentionally disabled (LLM_PROVIDER=none)")
        else:
            # Unknown provider: the exact trap that made localai silently disable
            # before ff665e7. Do NOT swallow it — make it impossible to miss.
            self.llm_provider = provider  # keep the bad value visible in /health
            self.llm_last_error = (
                f"unknown LLM_PROVIDER={provider!r} -- valid values are "
                f"{', '.join(sorted(KNOWN_LLM_PROVIDERS))}"
            )
            print("[ERROR] " + "!" * 60)
            print(f"[ERROR] {self.llm_last_error}")
            print("[ERROR] LLM fallback is DISABLED because the provider name was not recognised.")
            print("[ERROR] Fix LLM_PROVIDER in sevi.env and recreate the api container.")
            print("[ERROR] " + "!" * 60)

        self.model_usage_stats["llm_fallback_used"] = 0
        self.model_usage_stats["scope_gate_blocked"] = 0

        print("\n[5/5] Initialization complete")
        print("=" * 60)
        print(f"Strategy: NB threshold = {self.NB_CONFIDENCE_THRESHOLD:.0%}")
        print("         NN threshold = adaptive per-intent")
        llm_status = "enabled" if (self.llm and self.llm.available) else "disabled"
        print(f"         LLM fallback = {llm_status} (provider={self.llm_provider})")
        print("=" * 60 + "\n")

    def llm_status(self) -> dict:
        """Current LLM tier state, for /health-style reporting and the admin toggle.

        Includes base_url and the last init error so an operator can see *why*
        the tier is down (unreachable server, unknown provider, missing key)
        without reading container logs."""
        return {
            "provider": self.llm_provider,
            "model": getattr(self.llm, "model", None),
            "base_url": getattr(self.llm, "base_url", None),
            "available": bool(self.llm and self.llm.available),
            "known_provider": self.llm_provider in KNOWN_LLM_PROVIDERS,
            "error": self.llm_last_error,
        }

    def set_llm(self, provider: str, model: Optional[str] = None) -> dict:
        """Hot-swap the LLM fallback at runtime (admin toggle — no restart).

        Also updates the process env (LLM_PROVIDER, OLLAMA_MODEL/CLAUDE_MODEL)
        so the AIS and connectors LLM routers — which read the env per call —
        follow the same switch.
        """
        provider = (provider or "none").strip().lower()
        self.llm_last_error = None
        scope_locked_prompt = build_scope_locked_prompt(
            base_persona=self._system_prompt_text(),
            intent_list=list(self.responses_map.keys()),
            campus_glossary=self._build_campus_glossary(),
        )
        if provider == "claude":
            self.llm = ClaudeLLM(model=model, system_prompt=scope_locked_prompt)
            if model:
                os.environ["CLAUDE_MODEL"] = model
        elif provider == "ollama":
            self.llm = LocalLLM(model=model, system_prompt=scope_locked_prompt)
            if model:
                os.environ["OLLAMA_MODEL"] = model
        elif provider in ("openai", "localai"):
            self.llm = OpenAICompatLLM(model=model, system_prompt=scope_locked_prompt)
            if model:
                os.environ["OPENAI_MODEL"] = model
        else:
            # The Literal in app.py should stop this, but a direct caller could
            # still pass junk — record it rather than silently masking as "none".
            if provider != "none":
                self.llm_last_error = (
                    f"unknown provider {provider!r} -- valid values are "
                    f"{', '.join(sorted(KNOWN_LLM_PROVIDERS))}"
                )
            provider = "none"
            self.llm = None
        if self.llm is not None and not self.llm.available and self.llm_last_error is None:
            self.llm_last_error = (
                f"{provider} provider initialised but not reachable "
                f"(url={getattr(self.llm, 'base_url', '?')}, "
                f"model={getattr(self.llm, 'model', '?')})"
            )
        self.llm_provider = provider
        os.environ["LLM_PROVIDER"] = provider
        if provider in ("ollama", "openai", "localai") and self.llm and self.llm.available:
            # Pay the model cold-load now, not on the next user's question.
            self._warm_up_llm_async()
        return self.llm_status()

    def _build_campus_glossary(self) -> list:
        """
        Build a list of (acronym, full_name) tuples from the campus_places module.
        Returns an empty list if campus_places can't be imported (graceful fallback).
        """
        try:
            try:
                from .campus_places import _PLACE_METADATA  # package import
            except ImportError:
                from campus_places import _PLACE_METADATA  # direct script run
        except ImportError:
            print("[WARNING] campus_places not importable — LLM has no campus glossary")
            return []

        glossary = []
        for place_id, meta in _PLACE_METADATA.items():
            short = meta.get("short", "")
            full = meta.get("full", "")
            # Skip generic entries and ones where short==full (no acronym to clarify)
            if not short or not full or short == full or place_id == "main":
                continue
            glossary.append((short, full))
        print(f"[OK] Campus glossary built — {len(glossary)} entries injected into LLM prompt")
        return glossary

    def _warm_up_llm_async(self):
        """Fire a dummy LLM call in a background thread to load the model into memory."""
        def _warm():
            try:
                print("[INFO] Warming up local LLM in background (first load can take 60-120s)...")
                reply = self.llm.generate("warmup ping")
                if reply:
                    print("[OK] Local LLM warm-up complete — ready for user queries")
                else:
                    print("[WARNING] Local LLM warm-up returned no reply")
            except Exception as e:
                print(f"[WARNING] Local LLM warm-up failed: {e}")
        threading.Thread(target=_warm, daemon=True).start()

    def _select_response(self, intent: str, user_input: str) -> str:
        """Pick a curated response variant deterministically, in the user's language.

        This was random.choice, which is why an English question could come
        back in Taglish: 122 of 124 intents carry more than one variant and the
        pick ignored the question entirely. Observed in production — one user
        asked "What facilities are available on campus?" and got "Ang mga
        pasilidad ng CvSU ay kinabibilangan ng library...", then asked a
        related question two turns later and got English. Same intent, coin
        flip.

        Order of preference within the matching-language pool:
          1. a variant that cites a source (contains a link)
          2. the longer variant — curated long-forms carry the office name,
             hours and caveats the short ones drop
        Ties break on the variant's own text so the choice is stable across
        processes (no dict/set ordering dependence).
        """
        variants = self.responses_map.get(intent) or self.responses_map[self.FALLBACK_INTENT]
        if len(variants) == 1:
            return variants[0]
        # RELATIVE, not absolute. Classifying each variant against a fixed
        # threshold fails on answers that are mostly proper nouns — a Filipino
        # dean list is 90% college names and person names, so its marker ratio
        # falls under any threshold and it reads as "English". Ranking the
        # variants against each other has no such failure mode: whichever is
        # the most Filipino IS the Filipino one, whatever its absolute ratio.
        want_filipino = _is_filipino(user_input)
        ratios = {id(v): _filipino_ratio(v) for v in variants}
        best = max(ratios.values()) if want_filipino else min(ratios.values())
        pool = [v for v in variants if ratios[id(v)] == best]
        # Stable tie-break: prefer a variant that cites a source, then the
        # longer (curated long-forms keep the office name, hours and caveats),
        # then the text itself so the pick never depends on dict ordering.
        return max(pool, key=lambda v: ("http" in v, len(v), v))

    @staticmethod
    def _system_prompt_text() -> str:
        """Compact system prompt passed to the local LLM for deep-fallback answers."""
        return (
            "You are Sevi, the virtual assistant for Cavite State University. "
            "Answer questions about academic programs, admissions, fees, scholarships, "
            "campus services, and university policies concisely and accurately. "
            "If you are unsure, say so and direct the user to the relevant CvSU office. "
            "Never fabricate names, figures, deadlines, or official policies. "
            "Respond in the same language the user uses (English or Filipino/Taglish)."
        )

    def _nb_result(self, user_input: str, user_id: str) -> Tuple[Optional[str], float, dict]:
        """Run NB + optional NLU enhancement. Returns (intent, confidence, nlu_data) or (None, 0, {})."""
        if not self.nb_model:
            return None, 0.0, {}
        intent, confidence = self.nb_model.predict(user_input)
        nlu_data = {}
        if self.nlu_engine and user_id:
            result = self.nlu_engine.enhance_prediction(user_input, intent, confidence, user_id)
            intent = result["intent"]
            confidence = result["confidence"]
            nlu_data = result
            self.model_usage_stats["nlu_enhanced"] += 1
        return intent, confidence, nlu_data

    def _nn_result(self, user_input: str) -> Tuple[Optional[str], float]:
        """Run NN. Returns (intent, confidence) or (None, 0)."""
        if not self.nn_model:
            return None, 0.0
        return self.nn_model.predict(user_input)

    def _llm_context(self, user_id: Optional[str]) -> list:
        """Build the last-3-turns conversation context for the LLM."""
        if not user_id or user_id not in self.conversation_history:
            return []
        context = []
        for turn in self.conversation_history[user_id][-3:]:
            context.append({"role": "user", "content": turn["user_message"]})
            context.append({"role": "assistant", "content": turn["bot_response"]})
        return context

    @staticmethod
    def _grounded_prompt(user_input: str, grounding: list, suggestion: Optional[str] = None) -> str:
        """Evidence-gated prompt for the LLM tier.

        grounding: [(score, citation, text, corpus_label), ...] best-first.
        With evidence, the LLM must answer from the excerpts and cite them;
        without, it must say it doesn't have the information instead of
        improvising — optionally pointing at the nearest intent topic.
        """
        hint = f' If helpful, invite the user to ask about "{suggestion}".' if suggestion else ""
        if not grounding:
            return (
                "No official CvSU excerpt matched this question. Answer only from the "
                "conversation context and your CvSU scope; if you do not know the answer, "
                "say you don't have that information and point the user to "
                f"https://cvsu.edu.ph — do not guess.{hint}\n\nQuestion: {user_input}"
            )
        excerpts = "\n\n".join(f"[{cite}]\n{text[:700]}" for _, cite, text, _ in grounding)
        return (
            "Excerpts from official CvSU sources are provided below. Answer the question "
            "using ONLY these excerpts and the conversation context, and mention the "
            "bracketed source you used.\n"
            "STRICT RULES — follow exactly:\n"
            "1. State a specific figure, date, count, rank, name, or venue ONLY if it "
            "appears verbatim in an excerpt above. Never substitute a related, national, "
            "or approximate number for the one asked.\n"
            "2. If the excerpts are about a DIFFERENT exam, event, year, or program than "
            "the question asks about, do NOT answer from them — instead say you don't have "
            "that specific information yet and point the user to https://cvsu.edu.ph.\n"
            "3. If the exact detail asked for is not in the excerpts, say you don't have "
            "that specific information yet and point to https://cvsu.edu.ph — do not guess "
            "or fill the gap.\n"
            "4. Do NOT cite the Citizens' Charter as the source for news, licensure/board "
            "results, rankings, or awards; those come only from news excerpts.\n"
            "5. Keep the answer concise and only cite a source you actually used."
            f"{hint}\n\n{excerpts}\n\nQuestion: {user_input}"
        )

    def _intent_retrieval_result(
        self, user_input: str, nb_intent: Optional[str]
    ) -> Optional[Tuple[str, str, float]]:
        """Step 2.5 body — (intent, response, score) when a pattern match is
        strong enough to serve, else None.

        Guards: char-gram cosine rewards lexical look-alikes, so a sub-0.80
        match must agree with NB's top (sub-threshold) guess, and questions
        about other schools never short-circuit here. Also gated by the same
        Nonsense/Scope checks Step 3/3.5 use: char n-grams are robust to
        injected profanity/instruction text wrapped around a real question
        ("gago ka ba, candidate for graduation" still lexically resembles the
        graduation_requirements patterns even though NB/NN both correctly
        decline it below their thresholds), so without this gate a query the
        NonsenseGate is designed to always block could still get a normal
        curated answer through the pattern-similarity path.
        """
        ir_index = intent_retrieval.get_index()
        if ir_index is None or intent_retrieval.mentions_other_school(user_input):
            return None
        if not (self.nonsense_gate.allows(user_input)[0] and self.scope_gate.allows(user_input)[0]):
            return None
        match = ir_index.retrieve(user_input)
        if match is None or match.intent not in self.responses_map:
            return None
        agrees = match.score >= intent_retrieval.MATCH_MIN_SCORE and match.intent == nb_intent
        if match.score < intent_retrieval.HIGH_MATCH_SCORE and not agrees:
            return None
        self.model_usage_stats["intent_retrieval_used"] = (
            self.model_usage_stats.get("intent_retrieval_used", 0) + 1
        )
        return match.intent, self._select_response(match.intent, user_input), match.score

    @staticmethod
    def _cross_corpus_rank_key(bigram_hits: int, score: float, floor: float) -> Tuple[int, float]:
        """Ranking key comparable across charter/site's independently-fit
        TF-IDF spaces.

        Raw cosine scores from two separately-fit TfidfVectorizers are NOT on
        the same scale — a topically irrelevant passage from one corpus can
        numerically outscore a genuinely relevant passage from the other
        (observed: a Citizens' Charter passage on the Main Campus at 0.208
        lost to an unrelated site news article at 0.217). Rank on bigram
        phrase-match count first (both corpora compute it identically — the
        more direct relevance signal), then on score normalized to "multiples
        of that corpus's own calibration floor" as a tiebreak, instead of raw
        magnitude.
        """
        return bigram_hits, (score / floor if floor else score)

    def _gather_grounding(self, user_input: str) -> Tuple[list, str, Optional[str]]:
        """Collect LLM grounding passages from the charter and site corpora.

        Returns (grounding, model_label_suffix, nearest_intent_suggestion)
        where grounding is [(score, citation, text, corpus), ...] ranked
        best-first via _cross_corpus_rank_key, capped at 3.
        """
        ranked = []  # (rank_key, score, citation, text, corpus)
        charter_index = charter_rag.get_index()
        if charter_index is not None:
            for p in charter_index.retrieve(user_input, k=3)[:2]:
                if p.score >= charter_rag.AUGMENT_MIN_SCORE:
                    key = self._cross_corpus_rank_key(p.bigram_hits, p.score, charter_rag.AUGMENT_MIN_SCORE)
                    ranked.append((key, p.score, p.citation(), p.text, "charter"))
        site_index = site_rag.get_index()
        if site_index is not None:
            for p in site_index.retrieve(user_input, k=3)[:2]:
                if p.score >= site_rag.AUGMENT_MIN_SCORE:
                    key = self._cross_corpus_rank_key(p.bigram_hits, p.score, site_rag.AUGMENT_MIN_SCORE)
                    ranked.append((key, p.score, p.citation(), p.text, "site"))
        ranked.sort(key=lambda r: r[0], reverse=True)
        grounding = [(score, cite, text, corpus) for _, score, cite, text, corpus in ranked[:3]]
        corpora = {g[3] for g in grounding}
        if corpora == {"charter", "site"}:
            suffix = " (charter+site-grounded)"
        elif corpora == {"charter"}:
            suffix = " (charter-grounded)"
        elif corpora == {"site"}:
            suffix = " (site-grounded)"
        else:
            suffix = ""
        suggestion = None
        ir_index = intent_retrieval.get_index()
        if ir_index is not None:
            near = ir_index.retrieve(user_input)
            if near:
                suggestion = near.intent.replace("_", " ")
        return grounding, suffix, suggestion

    def _verbatim_document_reply(
        self, user_input: str
    ) -> Optional[Tuple[str, str, float, str]]:
        """Step 3.5 body — best verbatim charter/site passage, or None.

        Gated by BOTH gates (nonsense + scope) so gibberish or off-topic
        queries can't dredge up an arbitrary quote, by stricter score
        thresholds than the augmentation path, and by >= 1 bigram hit.
        """
        if not (self.nonsense_gate.allows(user_input)[0] and self.scope_gate.allows(user_input)[0]):
            return None
        best = None  # (rank_key, score, intent_tag, reply, model_label, stat_key)
        charter_index = charter_rag.get_index()
        if charter_index is not None:
            passages = charter_index.retrieve(user_input, k=1)
            if (
                passages
                and passages[0].score >= charter_rag.QUOTE_MIN_SCORE
                and passages[0].bigram_hits >= 1
            ):
                key = self._cross_corpus_rank_key(
                    passages[0].bigram_hits, passages[0].score, charter_rag.QUOTE_MIN_SCORE
                )
                best = (
                    key, passages[0].score, "charter_info",
                    charter_rag.verbatim_reply(passages[0]),
                    "Charter RAG", "charter_rag_used",
                )
        site_index = site_rag.get_index()
        if site_index is not None:
            passages = site_index.retrieve(user_input, k=1)
            if (
                passages
                and passages[0].score >= site_rag.QUOTE_MIN_SCORE
                and passages[0].bigram_hits >= 1
            ):
                key = self._cross_corpus_rank_key(
                    passages[0].bigram_hits, passages[0].score, site_rag.QUOTE_MIN_SCORE
                )
                if best is None or key > best[0]:
                    best = (
                        key, passages[0].score, "site_info",
                        site_rag.verbatim_reply(passages[0]),
                        "Site RAG", "site_rag_used",
                    )
        if best is None:
            return None
        _, score, tag, reply, label, stat = best
        self.model_usage_stats[stat] = self.model_usage_stats.get(stat, 0) + 1
        return tag, reply, score, label

    def _place_resolver_result(self, user_input: str, campus: Optional[str] = None):
        """Step 2.7: deterministic campus wayfinding from the map lexicon.

        A location ask whose place the classifiers don't know ("saan yung
        saluysoy", "saan pwede kumain?") resolves here from the same keyword
        lexicon the map card uses, so the reply text and the map pin always
        agree. Skipped when the session is grounded on a satellite campus —
        every place in the lexicon is on the Indang main campus.

        Returns (place_id, response) or None.
        """
        try:
            try:
                from .campus_places import resolve_place_query, place_answer
                from .campus_directory import is_satellite
            except ImportError:
                from campus_places import resolve_place_query, place_answer
                from campus_directory import is_satellite
        except ImportError:
            return None
        if is_satellite(campus):
            return None
        pq = resolve_place_query(user_input)
        if pq is None:
            return None
        return pq.place_id, place_answer(pq)

    def _resolve_list_reference(self, user_input: str, user_id: Optional[str]) -> Optional[str]:
        """Turn a bare "10" into the text of item 10 of the list just shown.

        When the previous reply was an enumeration, a lone number is a pointer
        into it, not a question. The classifiers cannot know that: "10" scores
        0.72 on retention_policy_grades (its patterns are full of "1.0"/"5.0"),
        so the bot confidently answers about GWA and Latin honors instead of
        the item the user picked. Observed in UAT, 2026-07.

        This is coreference resolution, but it needs no model: the bot wrote
        the list itself one turn ago, so the mapping is a lookup. Returns the
        rewritten query, or None to leave the message alone.
        """
        if not user_id:
            return None
        match = _ORDINAL_REF_RE.match(user_input or "")
        if not match:
            return None
        history = self.conversation_history.get(user_id) or []
        if not history:
            return None
        items = history[-1].get("list_items") or []
        index = int(match.group(1))
        if not 1 <= index <= len(items):
            return None
        return items[index - 1]

    def _conversation_recap_result(self, user_input: str, user_id: Optional[str]) -> Optional[str]:
        """Step 0.5: deterministic session recap for meta-questions about the
        conversation itself ("what did we talk about", "summarize our chat").

        Runs before the classifiers because chitchat captures these phrasings
        at ~0.65-0.70 and answers with a greeting, and the grounded LLM tier
        summarizes retrieved corpus passages instead of the conversation —
        a confident fabrication (observed 2026-07: it "recapped" campus
        locations in a session that discussed admissions and scholarships).

        Answers only from this session's history: it lists the user's own
        prior questions verbatim (PII-masked) and never involves the LLM,
        so it cannot invent topics. Returns the reply text, or None when the
        message is not a recap ask.
        """
        if not self._RECAP_RE.search(user_input):
            return None
        try:
            from .pii import mask_pii
        except ImportError:
            from pii import mask_pii

        history = self.conversation_history.get(user_id, []) if user_id else []
        # Skip prior recaps (they would recurse into "1. can you summarize…"
        # noise) and anything a gate refused. Echoing a refused turn would put
        # attacker-controlled text into a bot_response that _llm_context later
        # replays in the ASSISTANT role — the one role prompt-injection
        # defenses treat as the model's own prior words.
        asked = [
            t["user_message"] for t in history
            if t.get("intent") != self.RECAP_INTENT
            and not str(t.get("model_used", "")).startswith(("NonsenseGate", "ScopeGate", "SafetyGate"))
        ]

        filipino = _is_filipino(user_input)
        if not asked:
            return ("Wala pa tayong napag-uusapan sa session na ito. Magtanong ka lang "
                    "tungkol sa CvSU — admissions, enrollment, tuition, scholarships, o campus services."
                    if filipino else
                    "We haven't discussed anything yet this session. Ask me anything about "
                    "CvSU — admissions, enrollment, tuition, scholarships, or campus services.")

        recent = asked[-10:]
        header = ("Narito ang mga natanong mo sa session na ito:" if filipino
                  else "Here's what you've asked so far this session:")
        # Collapse whitespace and cap length: the echoed text is replayed to
        # the LLM as assistant content, so it must stay a short quoted line
        # and cannot carry multi-line structure of its own.
        lines = [f"{i}. {mask_pii(' '.join(q.split()))[:160]}"
                 for i, q in enumerate(recent, 1)]
        if len(asked) > len(recent):
            lines.append("… (earlier questions omitted)" if not filipino
                         else "… (may mga naunang tanong na hindi na isinama)")
        footer = ("Gusto mo bang balikan ang alinman sa mga ito?" if filipino
                  else "Want me to go over any of these again?")
        return "\n".join([header, *lines, footer])

    def predict(self, user_input: str, user_id: str = None, skip_intents: bool = False,
                campus: Optional[str] = None) -> Tuple[str, str, float, str, dict]:
        """
        Hierarchical prediction: NB → NN → intent retrieval → Place Resolver
        → LLM (charter+site grounded) → verbatim documents → static fallback.

        skip_intents: bypass the NB/NN tiers and go straight to the deep
        tiers (charter RAG + LLM). Used for context-rewritten queries (e.g.
        campus-grounded follow-ups) where a canned intent answer would drop
        the context the rewrite added. Only honored when the LLM is
        available — otherwise a canned answer beats a static fallback.

        Returns:
            (intent, response, confidence, model_used, nlu_data)
        """
        if skip_intents and not (self.llm and self.llm.available):
            skip_intents = False

        nlu_data = {}

        # Step 0.5: Conversation Recap — must precede the classifiers; see
        # _conversation_recap_result for why neither tier below can own this.
        recap = self._conversation_recap_result(user_input, user_id)
        if recap is not None:
            self.model_usage_stats["conversation_recap_used"] += 1
            return self.RECAP_INTENT, recap, 1.0, "Conversation Recap", nlu_data

        if not skip_intents:
            # Step 1: Naive Bayes (+ optional NLU enhancement)
            nb_intent, nb_confidence, nlu_data = self._nb_result(user_input, user_id)
            if nb_intent and nb_confidence >= self.NB_CONFIDENCE_THRESHOLD:
                self.model_usage_stats["naive_bayes_used"] += 1
                return nb_intent, self._select_response(nb_intent, user_input), nb_confidence, "Naive Bayes (NLU Enhanced)", nlu_data

            # Step 2: Neural Network with adaptive per-intent threshold, gated
            # on agreement with NB's top (sub-threshold) guess — the same guard
            # the Intent Retrieval tier uses. Measured on the 268-Q mirror eval
            # (2026-07): unguarded NN served 98 with 38 correct (39%); with the
            # agreement guard the NN tier is 29/39 correct and overall NB+NN
            # precision returns to the pre-cleanup baseline (~79%) at 4.6x its
            # recall. Disagreements fall through to retrieval / the LLM tiers.
            nn_intent, nn_confidence = self._nn_result(user_input)
            if (nn_intent and nn_confidence >= self.nn_model.get_threshold(nn_intent)
                    and nn_intent == nb_intent):
                response = self._select_response(nn_intent, user_input)
                self.model_usage_stats["neural_network_used"] += 1
                return nn_intent, response, nn_confidence, "Neural Network", nlu_data

            # Step 2.5: Intent retrieval — soft lexical match over the intent
            # patterns corpus. Catches phrasings the classifiers under-score
            # ("complete list of courses": NB 0.28 / NN 0.37) and serves the
            # curated response with no LLM latency.
            served = self._intent_retrieval_result(user_input, nb_intent)
            if served is not None:
                intent, response, score = served
                return intent, response, score, "Intent Retrieval", nlu_data

            # Step 2.7: Place Resolver — deterministic campus wayfinding.
            # Rescues location asks the classifiers dropped ("saan yung
            # saluysoy") with an answer built from the same place metadata the
            # map card uses. Runs after the curated intent tiers so richer
            # canned answers (registrar, library, ...) still win when the
            # classifiers are confident.
            placed = self._place_resolver_result(user_input, campus)
            if placed is not None:
                place_id, response = placed
                self.model_usage_stats["place_resolver_used"] += 1
                nlu_data = {**nlu_data, "place_id": place_id}
                return self.FIND_PLACE_INTENT, response, 1.0, "Place Resolver", nlu_data

        # Step 3: LLM fallback — fires only when NB+NN are both below threshold
        if self.llm and self.llm.available:
            # NonsenseGate first: catches gibberish, profanity, and
            # fact-injection attempts ("the correct answer is...",
            # "Ang turon ay X") before ScopeGate's off-topic check.
            ns_allowed, ns_reason = self.nonsense_gate.allows(user_input)
            if not ns_allowed:
                self.model_usage_stats["scope_gate_blocked"] += 1
                return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, f"NonsenseGate ({ns_reason})", nlu_data

            allowed, reason = self.scope_gate.allows(user_input)
            if not allowed:
                # Pre-filter blocked the query — don't even call the API
                self.model_usage_stats["scope_gate_blocked"] += 1
                return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, f"ScopeGate ({reason})", nlu_data

            # Official-source grounding — gather the best passages from BOTH
            # corpora (Citizens' Charter + official website) and hand them to
            # the LLM with an evidence-gated instruction: answer only from
            # the excerpts, cite the bracketed source, and say so when they
            # don't contain the answer instead of improvising.
            grounding, charter_suffix, suggestion = self._gather_grounding(user_input)
            llm_input = self._grounded_prompt(user_input, grounding, suggestion)

            llm_reply = self.llm.generate(llm_input, conversation_context=self._llm_context(user_id))
            # LLM emitted the refusal token → out of scope per the model's own judgment
            if llm_reply and LLM_REFUSAL_TOKEN in llm_reply:
                self.model_usage_stats["scope_gate_blocked"] += 1
                provider_label = "Claude" if isinstance(self.llm, ClaudeLLM) else "Ollama"
                return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, f"{provider_label} (out-of-scope)", nlu_data

            if llm_reply:
                self.model_usage_stats["llm_fallback_used"] += 1
                provider_label = "Claude LLM" if isinstance(self.llm, ClaudeLLM) else "Local LLM"
                return self.FALLBACK_INTENT, llm_reply, 0.0, f"{provider_label}{charter_suffix}", nlu_data

        # Step 3.5: Verbatim document tier — no LLM (or it returned nothing),
        # but the Citizens' Charter or the official website has a strongly-
        # matching passage. Quote the best one with a citation instead of
        # shrugging.
        served = self._verbatim_document_reply(user_input)
        if served is not None:
            tag, reply, score, label = served
            return tag, reply, score, label, nlu_data

        # Step 4: Static fallback
        self.model_usage_stats["fallback_used"] += 1
        return (self.FALLBACK_INTENT, self._select_response(self.FALLBACK_INTENT, user_input),
                0.0, "Fallback", nlu_data)

    def chat(
        self,
        user_input: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        skip_intents: bool = False,
        campus: Optional[str] = None,
    ) -> Tuple[str, str, float, str, dict]:
        """
        Chat with conversation tracking and NLU enhancements

        Returns:
            (intent, response, confidence, model_used, nlu_data)
        """
        # Conversation history keys on user_id, but anonymous web sessions may
        # only carry a session_id — fall back so multi-turn LLM context works
        # for them too.
        user_id = user_id or session_id

        # Resolve "10" against a list the bot itself just printed, BEFORE the
        # classifiers see it (see _resolve_list_reference).
        resolved = self._resolve_list_reference(user_input, user_id)
        if resolved:
            user_input, nlu_extra = resolved, {"resolved_from": user_input}
        else:
            nlu_extra = {}

        intent, response, confidence, model_used, nlu_data = self.predict(
            user_input, user_id, skip_intents=skip_intents, campus=campus)
        nlu_data = {**nlu_data, **nlu_extra}

        # Track conversation (bounded LRU — see __init__).
        if user_id:
            if user_id not in self.conversation_history:
                # Evict the least-recently-used session when at capacity.
                while len(self.conversation_history) >= self._MAX_HISTORY_SESSIONS:
                    self.conversation_history.popitem(last=False)
                self.conversation_history[user_id] = []
            else:
                self.conversation_history.move_to_end(user_id)

            turns = self.conversation_history[user_id]
            turns.append({
                "user_message": user_input,
                "bot_response": response,
                "intent": intent,
                "confidence": confidence,
                "model_used": model_used,
                "session_id": session_id,
                "entities": nlu_data.get("entities", {}),
                "is_follow_up": nlu_data.get("is_follow_up", False),
                # Lets the NEXT turn resolve a bare "10" against this reply.
                "list_items": _numbered_items(response),
            })
            if len(turns) > self._MAX_HISTORY_TURNS:
                del turns[: -self._MAX_HISTORY_TURNS]

        return intent, response, confidence, model_used, nlu_data

    def get_usage_stats(self) -> dict:
        """Get model usage statistics"""
        total = sum(self.model_usage_stats.values())
        if total == 0:
            return self.model_usage_stats.copy()

        def pct(key: str) -> float:
            return self.model_usage_stats[key] / total * 100

        return {
            "total_predictions": total,
            "naive_bayes_used": self.model_usage_stats["naive_bayes_used"],
            "naive_bayes_percentage": pct("naive_bayes_used"),
            "neural_network_used": self.model_usage_stats["neural_network_used"],
            "neural_network_percentage": pct("neural_network_used"),
            "place_resolver_used": self.model_usage_stats["place_resolver_used"],
            "place_resolver_percentage": pct("place_resolver_used"),
            "llm_fallback_used": self.model_usage_stats["llm_fallback_used"],
            "llm_fallback_percentage": pct("llm_fallback_used"),
            "fallback_used": self.model_usage_stats["fallback_used"],
            "fallback_percentage": pct("fallback_used"),
            "nlu_enhanced": self.model_usage_stats["nlu_enhanced"],
        }

    def get_history(self) -> dict:
        """Get conversation history"""
        return self.conversation_history.copy()

    def clear_history(self, user_id: Optional[str] = None):
        """Clear conversation history"""
        if user_id and user_id in self.conversation_history:
            del self.conversation_history[user_id]
        elif not user_id:
            self.conversation_history.clear()

    def get_all_intents(self) -> list:
        """Get list of all available intents"""
        return list(self.responses_map.keys())

    def get_intent_details(self, intent_tag: str) -> Optional[dict]:
        """Get details about a specific intent"""
        if intent_tag not in self.responses_map:
            return None

        return {
            "tag": intent_tag,
            "response_count": len(self.responses_map[intent_tag]),
            "sample_responses": self.responses_map[intent_tag][:3]
        }

    @property
    def model_name(self) -> str:
        """Model name"""
        return "Hybrid Chatbot (NB + NN + NLU)"

    @property
    def accuracy(self) -> float:
        """Model accuracy (from training)"""
        return 0.9559

    @property
    def total_intents(self) -> int:
        """Total number of intents"""
        return len(self.responses_map)

    @property
    def total_patterns(self) -> int:
        """Approximate total patterns"""
        return sum(len(responses) for responses in self.responses_map.values())

    @property
    def model_size_kb(self) -> float:
        """Approximate model size in KB"""
        return 79.5

    @property
    def system_instructions(self) -> str:
        """System instructions for the chatbot"""
        return """You are Sevi, the virtual assistant for Cavite State University - a helpful, friendly guide.

1. IDENTITY AND SCOPE
- You serve prospective students, current students, parents, faculty, and the general public.
- You cover academic programs, admissions, campus services, scholarships, fees, schedules, policies, and general information about CvSU's main campus in Indang and its satellite campuses (Imus, Rosario, Silang, Naic, Trece Martires, Tanza, General Trias, Carmona, Cavite City, Bacoor, and others).
- You do NOT process enrollment, payments, or official document requests. Always redirect high-stakes actions (enrollment, grade disputes, document authentication) to the proper office.

2. CORE PERSONALITY
- Professional yet approachable; warm and respectful of Filipino culture ("Iskolar para sa Bayan").
- Patient and empathetic - many users are first-generation applicants or parents unfamiliar with university processes. Avoid jargon without explanation.
- Proactive in offering next steps and pointing to verification.

3. RETRIEVAL AND VERIFICATION PROTOCOL
Before answering a factual question:
- Classify the query: (a) general/stable, (b) time-sensitive, (c) campus-specific, (d) personal/transactional.
- Time-sensitive items (deadlines, fees, schedules, CvSUAT dates) must be flagged for verification with the relevant office. Qualify with "as of [date], please verify with [office]."
- For any specific number, date, name, or requirement, cite the source or qualify clearly.
- Disambiguate campus before giving program-specific or fee-specific answers - CvSU Indang and CvSU Imus may have very different offerings.

4. CONFIDENCE TIERS - never blur these
- High confidence: from official, recently verified CvSU sources. State plainly.
- Medium confidence: from official sources but possibly outdated. State with date qualifier and recommend verification.
- Low confidence: from secondary sources, inference, or older data. State as such and direct the user to the relevant office.
- No information: admit the gap honestly. Never fabricate. Provide the contact path of who would know.

5. DISAMBIGUATION
When a query is ambiguous, ask one targeted clarifying question, e.g.:
- "CvSU has multiple campuses. Which one are you asking about?"
- "Are you asking as a freshman applicant, transferee, or graduate student?"
- "Which academic year - 2025-2026 or 2026-2027?"
Limit to one clarifying question per turn unless absolutely necessary.

6. RESPONSE STRUCTURE
- Direct answer first, supporting details second, caveats and verification reminders last.
- Include contact info for the specific office when relevant.
- Short answers for simple lookups; longer structured answers for process questions.
- Offer next steps: "Is there anything else I can help you with?"

7. LANGUAGE
- Primary: English (professional). Respond in the language the user uses; if they mix Tagalog and English (Taglish), respond in kind.
- Use formal Filipino academic terminology when discussing official terms (e.g., "Pagsusulit sa Pagpasok," "Rehistrar").

8. PRIVACY AND DATA HANDLING (RA 10173)
- Never request or store personal information (full name, student number, contact details) unless the platform explicitly supports secure data handling.
- Never speculate about specific students' grades, status, or records.
- Redirect all individual student inquiries to the registrar or guidance office.

9. ESCALATION PATHWAYS - surface the right office
- Admissions questions -> Office of Admissions, specific campus
- Enrollment issues -> Registrar, specific campus
- Financial concerns -> Cashier and Scholarship Office (note RA 10931 free higher education subsidy where applicable)
- Academic concerns -> department chair or college dean
- Student welfare -> Office of Student Affairs and Services (OSAS)
- Online system issues -> Management Information Systems (MIS) office
- Complaints/appeals -> Campus Administrator or University President's Office

10. REFUSAL AND REDIRECTION
Decline to:
- Predict admission outcomes for specific applicants.
- Compare CvSU unfavorably to other institutions in misleading ways.
- Give legal interpretations of university policies (refer to the official policy documents).
- Provide unofficial workarounds to academic requirements.
- Share contact details of individual faculty without official verification.

11. PROHIBITED
- Do NOT fabricate tuition figures, professor names, deadlines, course codes, or passing rates.
- Do NOT promise services beyond CvSU's scope.
- Do NOT provide personal opinions on university policies.
- Do NOT give a generic "CvSU" answer without first asking which campus when the campus matters.

12. META
You are a helpful starting point and information aggregator, not the final authority. For anything consequential - enrollment, scholarships, document requirements - empower the user to verify with the proper CvSU office, and provide the path to that verification."""


class NeuralNetworkTrainer:
    """Train neural network model for intent classification."""

    VOCAB_SIZE = 1000
    MAX_LEN = 20
    EMBEDDING_DIM = 64
    MAX_EPOCHS = 10000
    BATCH_SIZE = 8
    EARLY_STOPPING_PATIENCE = 150
    LR_REDUCE_PATIENCE = 50
    LR_REDUCE_FACTOR = 0.5
    LR_MIN = 1e-6

    @staticmethod
    def train(intents_path: str, output_dir: str = "models"):
        """Train neural network on intents with early stopping up to 10,000 epochs."""
        print("\n" + "=" * 60)
        print("  NEURAL NETWORK TRAINING  (max 10 000 epochs)")
        print("=" * 60)

        gpus = tf.config.list_physical_devices("GPU")
        print(f"\n[GPU] {'Using: ' + gpus[0].name if gpus else 'No GPU detected — training on CPU'}")

        print("\n[1/5] Loading intents...")
        with open(intents_path, "r", encoding="utf-8") as f:
            intents_data = json.load(f)

        patterns = []
        labels = []
        for intent in intents_data["intents"]:
            tag = intent["tag"]
            for pattern in intent["patterns"]:
                patterns.append(NeuralNetworkTrainer._preprocess(pattern))
                labels.append(tag)

        print(f"[OK] Loaded {len(patterns)} patterns from {len(intents_data['intents'])} intents")

        print("\n[2/5] Tokenizing patterns...")
        tokenizer = Tokenizer(num_words=NeuralNetworkTrainer.VOCAB_SIZE, oov_token="<OOV>")
        tokenizer.fit_on_texts(patterns)
        sequences = tokenizer.texts_to_sequences(patterns)
        padded = pad_sequences(sequences, maxlen=NeuralNetworkTrainer.MAX_LEN, padding="post")
        print(f"[OK] Tokenized {len(padded)} sequences")

        print("\n[3/5] Encoding labels...")
        label_encoder = LabelEncoder()
        label_encoder.fit(labels)
        encoded_labels = label_encoder.transform(labels)
        num_classes = len(label_encoder.classes_)
        y = tf.keras.utils.to_categorical(encoded_labels, num_classes=num_classes)
        print(f"[OK] Encoded {num_classes} intent classes")

        print("\n[4/5] Building neural network (Bidirectional LSTM)...")
        model = Sequential([
            Embedding(
                input_dim=NeuralNetworkTrainer.VOCAB_SIZE,
                output_dim=NeuralNetworkTrainer.EMBEDDING_DIM,
                input_length=NeuralNetworkTrainer.MAX_LEN,
                name="embedding"
            ),
            Bidirectional(LSTM(128, return_sequences=True), name="bilstm"),
            GlobalAveragePooling1D(name="pooling"),
            Dense(128, activation="relu", name="dense_1"),
            Dropout(0.3, name="dropout_1"),
            Dense(64, activation="relu", name="dense_2"),
            Dropout(0.2, name="dropout_2"),
            Dense(num_classes, activation="softmax", name="output")
        ], name="IntentClassifier_BiLSTM")

        model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
            loss="categorical_crossentropy",
            metrics=["accuracy"]
        )

        print(model.summary())

        # Monitor val_accuracy, not val_loss. With 120 imbalanced intents and
        # ~20 patterns each, val_loss climbs even after val_accuracy plateaus —
        # the misclassified samples dominate the cross-entropy as the model
        # gets confident. Restoring on val_loss picks an under-trained epoch.
        callbacks = [
            tf.keras.callbacks.EarlyStopping(
                monitor="val_accuracy",
                mode="max",
                patience=NeuralNetworkTrainer.EARLY_STOPPING_PATIENCE,
                restore_best_weights=True,
                verbose=1,
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_accuracy",
                mode="max",
                factor=NeuralNetworkTrainer.LR_REDUCE_FACTOR,
                patience=NeuralNetworkTrainer.LR_REDUCE_PATIENCE,
                min_lr=NeuralNetworkTrainer.LR_MIN,
                verbose=1,
            ),
        ]

        x_train, x_val, y_train, y_val, y_train_raw, _ = train_test_split(
            padded, y, encoded_labels, test_size=0.2, random_state=42, stratify=encoded_labels
        )

        # Class weight balancing — counters imbalanced intents (5 vs 426 patterns)
        from sklearn.utils.class_weight import compute_class_weight
        class_weights_arr = compute_class_weight(
            class_weight="balanced",
            classes=np.unique(y_train_raw),
            y=y_train_raw,
        )
        class_weight_dict = dict(enumerate(class_weights_arr))

        print(f"\n[5/5] Training model (max {NeuralNetworkTrainer.MAX_EPOCHS} epochs, "
              f"early stop patience={NeuralNetworkTrainer.EARLY_STOPPING_PATIENCE})...")
        history = model.fit(
            x_train, y_train,
            epochs=NeuralNetworkTrainer.MAX_EPOCHS,
            batch_size=NeuralNetworkTrainer.BATCH_SIZE,
            validation_data=(x_val, y_val),
            callbacks=callbacks,
            class_weight=class_weight_dict,
            verbose=1,
        )

        actual_epochs = len(history.history["accuracy"])
        print(f"\n[OK] Stopped at epoch {actual_epochs}/{NeuralNetworkTrainer.MAX_EPOCHS}")

        print("\n[+] Computing per-class confidence calibration...")
        all_proba = model.predict(padded, verbose=0)
        per_class_scores: dict = {}
        for i, label_idx in enumerate(encoded_labels):
            label = label_encoder.classes_[label_idx]
            conf = float(all_proba[i, label_idx])
            per_class_scores.setdefault(label, []).append(conf)

        adaptive_thresholds = {
            label: round(min(max(float(np.percentile(scores, 60)), 0.30), 0.65), 4)
            for label, scores in per_class_scores.items()
        }

        # Temperature scaling — find scalar T on val set so confidence ≈ accuracy.
        # Uses power scaling on softmax outputs: p_cal = p^(1/T) / sum(p^(1/T))
        # avoids needing a logit sub-model (compatible with restore_best_weights).
        print("[+] Calibrating temperature scalar on validation set...")
        from scipy.optimize import minimize_scalar

        proba_val = model.predict(x_val, verbose=0)

        def nll(temp):
            scaled = np.power(np.clip(proba_val, 1e-7, 1.0), 1.0 / max(temp, 0.01))
            calibrated = scaled / scaled.sum(axis=1, keepdims=True)
            true_idx = np.argmax(y_val, axis=1)
            return -np.mean(np.log(calibrated[np.arange(len(true_idx)), true_idx] + 1e-7))

        result = minimize_scalar(nll, bounds=(0.1, 10.0), method="bounded")
        temperature = float(round(result.x, 4))
        print(f"[OK] Temperature T = {temperature:.4f}  (1.0 = uncalibrated)")

        print("\n" + "=" * 60)
        os.makedirs(output_dir, exist_ok=True)

        model.save(os.path.join(output_dir, "nn_model.h5"))
        with open(os.path.join(output_dir, "nn_tokenizer.pkl"), "wb") as f:
            pickle.dump(tokenizer, f)
        with open(os.path.join(output_dir, "nn_label_encoder.pkl"), "wb") as f:
            pickle.dump(label_encoder, f)
        with open(os.path.join(output_dir, "nn_thresholds.json"), "w", encoding="utf-8") as f:
            json.dump(adaptive_thresholds, f, indent=2, ensure_ascii=False)
        with open(os.path.join(output_dir, "nn_temperature.json"), "w", encoding="utf-8") as f:
            json.dump({"temperature": temperature}, f)

        best_epoch = int(np.argmin(history.history["val_loss"]))
        best_val_acc = history.history["val_accuracy"][best_epoch]
        final_acc = history.history["accuracy"][best_epoch]

        print(f"[OK] Model saved to {output_dir}")
        print(f"  Training Accuracy:   {final_acc:.2%}  (epoch {best_epoch + 1})")
        print(f"  Validation Accuracy: {best_val_acc:.2%}  (best epoch)")
        print(f"  Epochs run:          {actual_epochs}")
        print(f"  Temperature:         {temperature:.4f}")
        print(f"  Adaptive thresholds: {len(adaptive_thresholds)} intents calibrated")
        print("=" * 60 + "\n")

        return model, tokenizer, label_encoder, adaptive_thresholds

    @staticmethod
    def _preprocess(text: str) -> str:
        """Preprocess text."""
        text = text.lower()
        text = re.sub(_NON_ALPHA_RE, "", text)
        tokens = nltk.word_tokenize(text)
        return " ".join([lemmatizer.lemmatize(t) for t in tokens])

