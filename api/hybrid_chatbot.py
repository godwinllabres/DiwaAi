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
import time
from collections import OrderedDict
import urllib.request
import urllib.error
import numpy as np
from typing import List, Optional, Tuple
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
# Single source for the Ollama endpoint default (shared with the MCP routers,
# safety second-opinion, and /health warm-up).
from .llm_defaults import ollama_base_url

try:
    from .smalltalk import smalltalk_reply as _smalltalk_reply
except ImportError:  # pragma: no cover - smalltalk is optional, never fatal
    def _smalltalk_reply(text, filipino=False):  # type: ignore[misc]
        return None

try:
    from .college_programs import college_program_reply as _college_program_reply
except ImportError:  # pragma: no cover - registry is optional, never fatal
    def _college_program_reply(text, filipino=False):  # type: ignore[misc]
        return None

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

# P1-6 post-generation output guard for the LLM tier. The prompt already
# forbids invented specifics, but an 8B model answering in Taglish will still
# occasionally emit a contact detail that is in its weights rather than in the
# passages — and an invented email is worse than no answer. Replies failing
# the guard are withheld and replaced with an honest can't-verify message.
LLM_MAX_REPLY_CHARS = int(os.getenv("LLM_MAX_REPLY_CHARS", "2200"))
_LLM_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LLM_URL_RE = re.compile(r"https?://[^\s<>\)\]\"']+")
# The one URL the grounded prompt itself tells the model to hand out.
_LLM_SAFE_URL_PREFIXES = (
    "https://cvsu.edu.ph", "http://cvsu.edu.ph", "https://www.cvsu.edu.ph",
)

# An LLM reply that IS a scope refusal, just without the [OUT_OF_SCOPE] token.
# qwen3:8b frequently declines in prose ("I'm not able to answer that — I'm
# built to help with CvSU-related questions only…") — correct behavior that,
# unrecognized, gets logged/metered as a normal answer. Anchored to the reply's
# OPENING so an actual answer that later hedges ("I don't have the exact fee,
# but enrollment steps are…") is not swallowed.
_LLM_PROSE_REFUSAL_RE = re.compile(
    r"^(?:i['’]?m (?:not able|unable|sorry)|i can(?:no|')t (?:help|assist|answer)"
    r"|i can (?:only )?help with cvsu|i can only help|that(?:['’]s| is) (?:not something|outside)"
    r"|i don['’]?t have that (?:specific )?information"
    r"|paumanhin|hindi ko (?:po )?(?:ka?yang?|ma))",
    re.IGNORECASE,
)

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

    def predict_top2(self, text: str) -> Tuple[str, float, float]:
        """Predict intent plus the top1−top2 probability margin.

        NB's raw confidence is uncalibrated (temperature scaling exists only
        on the NN), so a high top-1 alone can be confidently wrong. The margin
        is the cheap second signal the arbitration gate (P1-5) requires: a
        near-tie between the top two classes means "don't trust the winner".
        """
        clean_text = self._preprocess(text)
        proba = self.pipeline.predict_proba([clean_text])[0]
        order = np.argsort(proba)
        top1 = float(proba[order[-1]])
        top2 = float(proba[order[-2]]) if len(proba) > 1 else 0.0
        intent = str(self.pipeline.classes_[order[-1]])
        return intent, top1, top1 - top2

    def predict_topk(self, text: str, k: int = 3) -> List[Tuple[str, float]]:
        """Top-k (intent, probability) pairs, best first — the candidate set a
        margin-triggered clarification (P2-8) offers the user to choose from."""
        clean_text = self._preprocess(text)
        proba = self.pipeline.predict_proba([clean_text])[0]
        order = np.argsort(proba)[::-1][:k]
        return [(str(self.pipeline.classes_[i]), float(proba[i])) for i in order]

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

    # Keras/TF inference is not guaranteed thread-safe, and one process holds a
    # single loaded model, so serialize the call itself. Class-level on purpose:
    # the constraint belongs to the framework, not to any one instance. The
    # critical section is a single predict on a batch of one — microseconds of
    # contention, versus a segfault-class failure if two threads enter together.
    _predict_lock = threading.Lock()

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

        with self._predict_lock:
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


# Re-probe cadence for a previously-unreachable LLM server. Env-configurable so
# an operator can tune how aggressively a down local server is retried.
_PROBE_COOLDOWN_SECONDS = float(os.getenv("LLM_PROBE_COOLDOWN_SECONDS", "30"))


def _reprobe(wrapper) -> bool:
    """Cooldown-gated availability check shared by the network-backed LLM
    wrappers (LocalLLM / OpenAICompatLLM).

    Returns True when the server is reachable, re-probing a previously-down
    server at most once per _PROBE_COOLDOWN_SECONDS. This lets a transient
    outage self-heal without a container restart, while never stalling every
    turn on the probe timeout for as long as the server stays down. (The old
    `self.llm.available` guard read a flag fixed at boot, so a server that was
    down at startup — or that briefly blipped — latched the whole tier off.)
    """
    if wrapper.available:
        return True
    now = time.monotonic()
    if now - getattr(wrapper, "_last_probe", 0.0) < _PROBE_COOLDOWN_SECONDS:
        return False
    wrapper._last_probe = now
    wrapper.available = wrapper._probe()
    return wrapper.available


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

    # Endpoint + model both come from the environment (OLLAMA_BASE_URL,
    # OLLAMA_MODEL). The base-url default lives once in llm_defaults; the model
    # has NO baked-in default — an unset OLLAMA_MODEL disables the tier loudly
    # rather than guessing a model that may not be pulled.
    # 8B models on CPU can take 60-120s on first call (cold start loads weights into RAM);
    # subsequent calls are 2-15s. Set generously so cold start doesn't fail.
    TIMEOUT_SECONDS = 180

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        system_prompt: str = "",
    ):
        self.base_url = (base_url or ollama_base_url()).rstrip("/")
        self.model = (model or os.getenv("OLLAMA_MODEL", "")).strip()
        self.system_prompt = system_prompt
        # No model configured → tier stays down (the caller surfaces the reason);
        # otherwise availability is the live reachability probe.
        self.available = bool(self.model) and self._probe()

    def _probe(self) -> bool:
        """Return True if the Ollama server is reachable AND actually answered.

        Uses a generous timeout to accommodate Cloudflare Tunnel latency when
        Ollama is exposed via a remote URL.

        The response is parsed, not merely fetched. urlopen() follows redirects,
        so an auth gateway in front of Ollama turns a 302 into a perfectly happy
        HTTP 200 — and "the fetch worked" then means "I reached a login page".
        Observed 2026-07-28: the Render deployment reported llm_ready=True while
        this probe was landing on a Cloudflare Access sign-in page
        (text/html, final URL godwincreates.cloudflareaccess.com/cdn-cgi/access/
        login/...), and every real generate() call failed with llm_unavailable.
        /health lied about a tier that was down. Requiring Ollama's own JSON
        shape makes a captive portal fail the probe, which is the truth.
        """
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET",
                                         headers={"User-Agent": "DIWA/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read())
            if isinstance(payload, dict) and "models" in payload:
                return True
            print(f"[WARNING] Ollama probe reached {self.base_url}/api/tags but the "
                  f"reply was not Ollama's tag list — an auth gateway or proxy is "
                  f"likely intercepting it")
            return False
        except Exception as e:
            print(f"[WARNING] Ollama probe failed: {type(e).__name__}: {e}  url={self.base_url}")
            return False

    def ensure_available(self) -> bool:
        """Reachable now? Re-probes a previously-down Ollama server on a cooldown
        so a transient outage self-heals without a container restart."""
        return _reprobe(self)

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
            # temperature 0 (P1-6): the tier answers ONLY from retrieved
            # passages, so sampling variety is pure fabrication risk for an
            # 8B model answering in Taglish.
            "options": {"temperature": 0.0, "num_predict": 512},
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

    # Override with env vars: OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_API_KEY.
    # OPENAI_MODEL has NO baked-in default — unset disables the tier loudly.
    DEFAULT_BASE_URL = "http://localhost:8080/v1"
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
        self.model = (model or os.getenv("OPENAI_MODEL", "")).strip()
        # Optional — LocalAI usually needs no key; a hosted OpenAI-compatible
        # endpoint (or a LocalAI configured with API_KEY) does.
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.system_prompt = system_prompt
        # No model configured → tier stays down; else it's the reachability probe.
        self.available = bool(self.model) and self._probe()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": "DIWA/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _probe(self) -> bool:
        """Return True if the OpenAI-compatible server is reachable (GET /models).

        Parses the reply rather than trusting that the fetch succeeded — same
        auth-gateway trap documented on OllamaLLM._probe above: urlopen()
        follows redirects, so a sign-in page returns HTTP 200 and would
        otherwise read as a healthy server. Only the JSON shape is required,
        not a specific key, since OpenAI-compatible implementations vary.
        """
        try:
            req = urllib.request.Request(f"{self.base_url}/models", method="GET",
                                         headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read())
            if isinstance(payload, dict):
                return True
            print(f"[WARNING] OpenAI-compat probe reached {self.base_url}/models but the "
                  f"reply was not JSON — an auth gateway or proxy is likely intercepting it")
            return False
        except Exception as e:
            print(f"[WARNING] OpenAI-compat probe failed: {type(e).__name__}: {e}  url={self.base_url}")
            return False

    def ensure_available(self) -> bool:
        """Reachable now? Re-probes a previously-down server on a cooldown so a
        transient outage self-heals without a container restart."""
        return _reprobe(self)

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
            "temperature": 0.0,  # P1-6: evidence-gated tier — no sampling variety
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
        r"translate to|translate this|translation of|itranslate|i-translate mo|"
        # Gold-eval OOS leaks (2026-08-04): investing, ride-hailing bookings,
        # gadget repair. Phrase-level on purpose — "medicine" is NOT here
        # (College of Medicine / Veterinary Medicine are real intents); the
        # LLM's prose-refusal detector backstops what this list can't name.
        r"stock market|what stock|stocks? to (?:buy|invest)|"
        r"book(?:ing)? (?:a |ng )?(?:grab|angkas)|grab papunta|"
        r"fix my (?:phone|laptop)|ayusin ang (?:phone|cellphone)|"
        r"ayaw mag-?on ng (?:phone|cellphone)|"
        # Joke asks are handled by api/smalltalk.py (Step 0.6) rather than
        # refused: a flat "outside my scope" reads as cold from a campus
        # assistant. They never reach here, so the alternatives are gone.
        r"write a joke about|"
        # "president of CvSU / Cavite State" is a university_officials ask;
        # only the national-politics form is off-topic.
        r"president of (?!cvsu|cavite)|prime minister|election|"
        r"bitcoin|crypto|stock price|forex|"
        r"horoscope|zodiac|tarot)\b",
        re.IGNORECASE,
    )

    REFUSAL_MESSAGES = [
        "I can only help with questions about Cavite State University — programs, admissions, fees, scholarships, campus services, and policies. Is there something CvSU-related I can help with?",
        "That's not something I can help with. I'm Sevi, the CvSU virtual assistant — I stick to Cavite State University topics like enrollment, courses, scholarships, and campus information. What would you like to know about CvSU?",
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

    MAX_TOKENS = 400
    TIMEOUT_SECONDS = 12

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: str = "",
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.model = (model or os.getenv("CLAUDE_MODEL", "")).strip()
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
        if not self.model:
            # Claude requires an explicit model id — no baked-in default.
            return
        try:
            self.client = anthropic.Anthropic(
                api_key=self.api_key,
                # TIMEOUT_SECONDS is PER ATTEMPT, and the SDK's default is 2
                # retries — so one generate() can occupy its caller for ~3x the
                # timeout plus backoff, not the 12s the constant suggests. That
                # is the difference between shedding load and stalling behind a
                # provider brownout, so bound the attempts explicitly.
                max_retries=1,
                timeout=self.TIMEOUT_SECONDS,
            )
            self.available = True
        except Exception as e:
            print(f"[WARNING] Claude client init failed: {e}")
            self.available = False

    def ensure_available(self) -> bool:
        # Claude availability is static (SDK + key present at init); there is no
        # server to re-probe. Transient API errors are handled per-call in generate().
        return self.available

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
                temperature=0.0,  # P1-6: evidence-gated tier — deterministic
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
# The optional lead-in covers corrections and second attempts — "I mean 3",
# "sorry, 3", "actually 3", "no 3", "yung 3" — which are the commonest way a
# user re-points at the list after the first pick answered something else.
_ORDINAL_REF_RE = re.compile(
    r"^\s*(?:(?:i\s+)?mean(?:t)?|sorry|oops|actually|no|nope|wait|"
    r"make\s+it|let'?s\s+do|give\s+me|show\s+me|yung|ay|hindi)?[\s,:-]*"
    r"(?:the\s+)?(?:#|no\.?|nr\.?|number|item|option|choice|ika-)?\s*"
    r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:one|item|option|po|please|pls|nga|naman)?"
    r"\s*[.?!]*\s*$",
    re.IGNORECASE,
)

# How far back a printed list stays pointable. The answer to the user's first
# pick sits between the menu and their correction, so looking only at the
# previous turn misses "I mean 3" entirely.
_LIST_REF_LOOKBACK = 6


# Retention bounds. Parsing costs ~22 microseconds on the largest curated
# reply, so the cost is storage, not CPU: without caps, 2000 sessions x 50
# turns each holding a long enumeration is ~191 MB, against a 2 GB container.
# A pointer only needs enough text to re-run as a query, and _ORDINAL_REF_RE
# reads at most two digits, so anything past 20 items is unreachable anyway.
_MAX_LIST_ITEMS = 20
_MAX_LIST_ITEM_CHARS = 120


def _numbered_items(text: str) -> list:
    """Ordered list items in a bot reply, so a later turn can dereference them."""
    if not text or len(text) > 20000:
        return []
    items = _NUMBERED_ITEM_RE.findall(text)
    # Require a real enumeration starting at 1 — a lone "1. step" or a stray
    # "2024." in prose is not a menu the user can point at.
    if len(items) < 2 or items[0][0] != "1":
        return []
    return [
        body.strip()[:_MAX_LIST_ITEM_CHARS]
        for _, body in items[:_MAX_LIST_ITEMS]
    ]


class HybridChatbot:
    """
    Hierarchical Hybrid Chatbot
    Strategy: Use fast NB first, fallback to accurate NN if uncertain
    """

    # Class-level fallbacks for the two state locks. __init__ replaces these
    # with per-instance locks; they exist because __init__ loads pickled models,
    # so callers that only want the pure helpers build instances via
    # HybridChatbot.__new__ and never run it (see test_conversation_recap.py,
    # test_place_resolver.py). Sharing one lock across such instances is
    # strictly more conservative than per-instance, never less.
    _history_lock = threading.Lock()
    _stats_lock = threading.Lock()
    # Class-level fallback for the same __new__-built instances: the P1-5
    # disagreement sink is optional everywhere, so None must be readable even
    # when __init__ never ran.
    disagreement_logger = None

    NB_CONFIDENCE_THRESHOLD = 0.65  # If NB confidence >= 65%, use it; otherwise defer to NN.
    # Raised from 0.55: with the NLU boost no longer inflating confidence, borderline
    # NB force-fits (e.g. an off-topic query landing in courses_offered at ~0.63) now
    # defer to the NN + scope/nonsense gates + LLM-grounded tiers instead of being served.

    # P1-5 cross-tier arbitration (HANDOFF-QUALITY 2026-08-03). A confident
    # NB answer is served only when BOTH hold:
    #   margin     top1 − top2 ≥ NB_MARGIN_THRESHOLD — a near-tie means the
    #              uncalibrated top-1 is not to be trusted;
    #   agreement  NB's intent appears in the TF-IDF pattern index's top-k
    #              distinct intents for the same query — a cheap independent
    #              vote from a different representation (char n-grams).
    # Failing either escalates to the NN (which carries its own NB-agreement
    # guard) instead of answering, and logs the turn to the tier_disagreements
    # review queue. Tune δ against training/run_gold_eval.py, not by feel.
    NB_MARGIN_THRESHOLD = float(os.getenv("NB_MARGIN_THRESHOLD", "0.25"))
    NB_AGREEMENT_TOPK = int(os.getenv("NB_AGREEMENT_TOPK", "5"))
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
    # Benign small talk answered from curated content (api/smalltalk.py).
    # Not in the trained taxonomy for the same reason as RECAP_INTENT.
    SMALLTALK_INTENT = "smalltalk"
    # Complete per-college program list from data/college_programs.json.
    PROGRAMS_INTENT = "college_programs"
    # P2-8: margin-triggered disambiguation. When NB clears the confidence bar
    # but its top-2 are a near-tie ACROSS topic families, guessing answers one
    # question and ignores the other — ask instead (2 chip options via
    # `suggestions`). Same-family near-ties (enrollment_procedure vs
    # enrollment_schedule) still fall through to the NN: either answer is
    # on-topic. Code-owned tag, not in the trained taxonomy.
    CLARIFY_INTENT = "intent_disambiguation"
    # Minimum probability for the runner-up before it is worth asking about.
    CLARIFY_MIN_RUNNERUP = float(os.getenv("CLARIFY_MIN_RUNNERUP", "0.15"))

    # Emitted when an LLM IS configured but its server was unreachable or
    # errored on this turn. Distinct from FALLBACK_INTENT (a genuine no-match)
    # so the reply reads as "try again in a moment" instead of "I didn't
    # understand", and so an outage is not logged/mined as an unanswered ask.
    LLM_UNAVAILABLE_INTENT = "llm_unavailable"

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

        # P1-5: optional persistent sink for cross-tier disagreements. app.py
        # wires this to ChatLogger.log_tier_disagreement; left None (e.g. in
        # tests and training scripts) disagreements still print to the log tail.
        self.disagreement_logger = None

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
            "smalltalk_used": 0,
            "college_programs_used": 0,
            "llm_unavailable": 0,
            "fallback_used": 0,
            "nlu_enhanced": 0
        }

        # Two narrow locks rather than one coarse one. Chat turns now run
        # concurrently in worker threads (see the turn gate in api/app.py), and
        # both structures above are written on every turn.
        #
        # _history_lock is the load-bearing one: conversation_history is an
        # OrderedDict, and concurrent popitem/move_to_end/insert from DIFFERENT
        # sessions corrupt its internal linked list. That is real corruption,
        # not a stale read — which is also why a per-session lock cannot cover
        # it; the structure is shared even when the sessions are not.
        #
        # _stats_lock is separate because the counters are touched far more
        # often and held far more briefly. Sharing one lock would serialize
        # every turn behind bookkeeping for no benefit.
        self._history_lock = threading.Lock()
        self._stats_lock = threading.Lock()

        # Initialize NLU engine for advanced understanding
        if NLU_AVAILABLE:
            self.nlu_engine = AdvancedNLUEngine()
            print("[OK] Advanced NLU Engine loaded")
        else:
            self.nlu_engine = None
            print("[WARNING] Advanced NLU Engine not available")

        # Initialize LLM fallback. Default to the local LLM (Ollama) — that is
        # what every deployment actually serves (Claude is off on-prem: no key,
        # and RA 10173 keeps data local). A missing LLM_PROVIDER must degrade to
        # the deployed local LLM, never to an unavailable Claude that silently
        # disables the whole tier and drops every unmatched turn to the static
        # "I didn't quite understand" card.
        provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
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
            print(f"       OPENAI_MODEL={os.getenv('OPENAI_MODEL', '(unset)')}")
            print(f"       OPENAI_API_KEY={'set' if os.getenv('OPENAI_API_KEY') else 'unset'}")
        elif provider == "ollama":
            print(f"       OLLAMA_BASE_URL={ollama_base_url()}")
            print(f"       OLLAMA_MODEL={os.getenv('OLLAMA_MODEL', '(unset)')}")
        elif provider == "claude":
            print(f"       CLAUDE_MODEL={os.getenv('CLAUDE_MODEL', '(unset)')}")
            print(f"       ANTHROPIC_API_KEY={'set' if os.getenv('ANTHROPIC_API_KEY') else 'unset'}")

        if provider == "claude":
            print("       initialising Claude API fallback...")
            self.llm = ClaudeLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] Claude LLM ready  model={self.llm.model}")
            else:
                if not ANTHROPIC_AVAILABLE:
                    self.llm_last_error = "anthropic package not installed (pip install anthropic)"
                elif not self.llm.api_key:
                    self.llm_last_error = "ANTHROPIC_API_KEY not set or invalid"
                else:
                    self.llm_last_error = "CLAUDE_MODEL not set — set it to the Claude model id"
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
                if not self.llm.model:
                    self.llm_last_error = "OLLAMA_MODEL not set — set it to the pulled model name in sevi.env"
                    print(f"[WARNING] {self.llm_last_error} — deep-fallback disabled")
                else:
                    self.llm_last_error = (
                        f"Ollama not reachable at {self.llm.base_url} (model={self.llm.model})"
                    )
                    print(f"[WARNING] {self.llm_last_error} — deep-fallback disabled")
                    print("          Start Ollama and pull the model named in OLLAMA_MODEL")
        elif provider in ("openai", "localai"):
            print(f"       initialising OpenAI-compatible LLM fallback ({provider})...")
            self.llm = OpenAICompatLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] OpenAI-compat LLM ready  model={self.llm.model}  url={self.llm.base_url}")
                self._warm_up_llm_async()
            else:
                if not self.llm.model:
                    self.llm_last_error = "OPENAI_MODEL not set — set it to the served model name"
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
        print(f"Strategy: NB threshold = {self.NB_CONFIDENCE_THRESHOLD:.0%}"
              f" (margin >= {self.NB_MARGIN_THRESHOLD:.2f},"
              f" retrieval top-{self.NB_AGREEMENT_TOPK} agreement)")
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
            # ensure_available() re-probes (on a cooldown) so /health reflects a
            # server that has since recovered, not the flag frozen at boot.
            "available": bool(self.llm and self.llm.ensure_available()),
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

    def _nb_result(self, user_input: str, user_id: str) -> Tuple[Optional[str], float, float, dict]:
        """Run NB + optional NLU enhancement.

        Returns (intent, confidence, margin, nlu_data) or (None, 0, 0, {}).
        `margin` is NB's top1−top2 gap for the arbitration gate. When the NLU
        engine overrides NB's winner (deterministic context logic — campus
        follow-ups, entity carry-over), the margin is reported as 1.0: the
        override IS the disambiguation, and NB's distribution no longer
        describes the intent being served.
        """
        if not self.nb_model:
            return None, 0.0, 0.0, {}
        intent, confidence, margin = self.nb_model.predict_top2(user_input)
        nlu_data = {}
        if self.nlu_engine and user_id:
            result = self.nlu_engine.enhance_prediction(user_input, intent, confidence, user_id)
            if result["intent"] != intent:
                margin = 1.0
            intent = result["intent"]
            confidence = result["confidence"]
            nlu_data = result
            self._bump("nlu_enhanced")
        return intent, confidence, margin, nlu_data

    def _nn_result(self, user_input: str) -> Tuple[Optional[str], float]:
        """Run NN. Returns (intent, confidence) or (None, 0)."""
        if not self.nn_model:
            return None, 0.0
        return self.nn_model.predict(user_input)

    def _llm_context(self, user_id: Optional[str]) -> list:
        """Build the last-3-turns conversation context for the LLM."""
        if not user_id:
            return []
        # Copy the slice under the lock, then build the message list outside it.
        # Slicing a list another thread is appending to (or deleting from, when
        # a session ages past _MAX_HISTORY_TURNS) can otherwise read a torn view.
        with self._history_lock:
            turns = list(self.conversation_history.get(user_id, [])[-3:])
        context = []
        for turn in turns:
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
                "https://cvsu.edu.ph — do not guess. You have no source excerpts, so do "
                f"not use bracketed citations.{hint}\n\nQuestion: {user_input}"
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
            "5. Keep the answer concise and only cite a source you actually used, "
            "copying its bracketed label exactly as shown above — never alter the "
            "source name, edition, or page number."
            f"{hint}\n\n{excerpts}\n\nQuestion: {user_input}"
        )

    # ── P2-8: disambiguation + stated-assumption helpers ─────────────────

    # Human-readable topic labels (english, filipino) for clarification chips.
    # Fallback is the tag with underscores replaced — always renderable.
    _INTENT_TOPIC_LABELS = {
        "tuition_fees":             ("tuition and fees", "matrikula at bayarin"),
        "free_tuition_law_details": ("the RA 10931 free-tuition coverage", "ang libreng matrikula sa RA 10931"),
        "scholarship":              ("scholarships", "scholarship"),
        "admissions_requirements":  ("admission requirements", "requirements sa admission"),
        "admissions_exam":          ("the entrance exam", "ang entrance exam"),
        "enrollment_procedure":     ("how to enroll", "paano mag-enroll"),
        "enrollment_schedule":      ("the enrollment schedule", "ang schedule ng enrollment"),
        "graduation_requirements":  ("graduation requirements", "requirements sa pag-graduate"),
        "transcript_request_details": ("getting your TOR", "pagkuha ng TOR"),
        "shifting_program":         ("shifting to another program", "paglipat ng kurso"),
        "transferee_admission":     ("transferring to CvSU", "paglipat sa CvSU"),
        "registrar":                ("the Registrar's services", "mga serbisyo ng Registrar"),
        "academic_calendar":        ("the academic calendar", "ang academic calendar"),
        "courses_offered":          ("the programs offered", "mga programang inaalok"),
    }

    # Tags that share a family answer the same underlying question — a near-tie
    # within a family is not worth a clarification round-trip.
    _INTENT_FAMILY_OVERRIDES = {
        "transferee_admission": "admissions", "als_admission": "admissions",
        "homeschool_admission": "admissions", "foreign_student_admission": "admissions",
        "senior_high_to_college": "admissions", "second_courser": "admissions",
        "late_enrollment": "enrollment", "returning_student": "enrollment",
        "tuition_fees": "money", "free_tuition_law_details": "money",
        "student_refund": "money",
    }

    # Intents whose correct answer varies by {campus, program level}: when the
    # question and the session pin down neither, the answer opens by stating
    # the assumption instead of silently answering for Indang undergrad.
    _ASSUMPTION_INTENTS = frozenset({
        "tuition_fees", "admissions_requirements",
        "enrollment_schedule", "academic_calendar",
    })
    _CAMPUS_TOKEN_RE = re.compile(
        r"\b(indang|main campus|imus|bacoor|rosario|silang|naic|trece"
        r"|tanza|carmona|cavite city|gen(?:eral)?\s*trias|ccat)\b", re.IGNORECASE)
    _LEVEL_TOKEN_RE = re.compile(
        r"\b(undergrad(?:uate)?|graduate|masteral|master'?s|doctoral|phd"
        r"|senior high|shs|freshman|transferee)\b", re.IGNORECASE)

    @classmethod
    def _intent_family(cls, tag: str) -> str:
        return cls._INTENT_FAMILY_OVERRIDES.get(tag, tag.split("_", 1)[0])

    @classmethod
    def _intent_topic_label(cls, tag: str, filipino: bool) -> str:
        labels = cls._INTENT_TOPIC_LABELS.get(tag)
        if labels is None:
            return tag.replace("_", " ")
        return labels[1] if filipino else labels[0]

    def _clarification_reply(self, user_input: str, candidates: list) -> Tuple[str, list]:
        """(text, chip_labels) asking the user to pick between candidate topics."""
        filipino = _is_filipino(user_input)
        labels = [self._intent_topic_label(tag, filipino) for tag, _ in candidates]
        if filipino:
            text = (f"Para masagot ko nang tama: ang tanong po ba ninyo ay tungkol sa "
                    f"{labels[0]}, o sa {labels[1]}? Pindutin o i-type ang paksa.")
        else:
            text = (f"Happy to help — just so I answer the right thing: are you asking "
                    f"about {labels[0]}, or about {labels[1]}? Tap or type the topic.")
        return text, labels

    def _maybe_state_assumption(self, response: str, intent: str,
                                user_input: str, campus: Optional[str]) -> str:
        """Prefix an explicit default-context line on answers that vary by
        campus/level when the turn pinned down neither (P2-8 slot-filling,
        stated-assumption branch)."""
        if intent not in self._ASSUMPTION_INTENTS or campus:
            return response
        if self._CAMPUS_TOKEN_RE.search(user_input) or self._LEVEL_TOKEN_RE.search(user_input):
            return response
        if _is_filipino(user_input):
            note = ("Ipagpalagay nating para sa Main Campus (Indang), undergraduate, "
                    "ang tanong — sabihin lang (hal. \"Imus campus\" o \"graduate\") "
                    "kung iba ang ibig ninyong sabihin.")
        else:
            note = ("Assuming the Main Campus (Indang) at undergraduate level — "
                    "tell me if you mean another campus or level "
                    "(e.g. \"Imus campus\", \"graduate school\").")
        return f"{note}\n\n{response}"

    # Served when the output guard withholds an LLM reply: honest, states
    # scope (the handoff's refusal contract), and points at verification.
    LLM_GUARD_MESSAGE = (
        "I came across details I couldn't verify against the official CvSU "
        "sources I have, so I'd rather not pass them along. Please check "
        "https://cvsu.edu.ph or ask the relevant campus office directly. "
        "I can help with admissions, enrollment, programs, tuition, "
        "scholarships, campus directions, and other CvSU topics."
    )

    def _llm_output_guard(self, reply: str, grounding: list) -> Tuple[bool, str, str]:
        """(ok, reason, reply_out) — enforce the evidence contract on LLM output.

        Rejects (P1-6):
          invented_email     an email address not present in the passages;
          invented_url       a URL that is neither a passage URL nor the
                             official portal the prompt itself points at;
          invented_citation  only when stripping (below) leaves no answer.
        A bracketed citation naming a source that was not among the retrieved
        passages is STRIPPED, not rejected: the guard can't verify facts, only
        attributions, so an uncited answer is no worse than one the guard
        already passes — while a fabricated citation lends false authority.
        Invented emails/URLs stay hard rejections (actionable contact data).
        Over-length replies are trimmed at a sentence boundary, not rejected.
        """
        evidence = " ".join(f"{cite} {text}" for _, cite, text, _ in grounding).lower()
        for email in _LLM_EMAIL_RE.findall(reply):
            if email.lower() not in evidence:
                return False, f"invented_email:{email}", reply
        for url in _LLM_URL_RE.findall(reply):
            trimmed = url.rstrip(".,;:!?").lower()
            if any(trimmed.startswith(p) for p in _LLM_SAFE_URL_PREFIXES):
                continue
            if trimmed not in evidence:
                return False, "invented_url", reply
        cites = [cite.lower() for _, cite, _, _ in grounding]
        stripped = 0
        for span in re.findall(r"\[([^\[\]]{4,120})\]", reply):
            span_l = " ".join(span.strip().lower().split())
            # Only citation-shaped brackets — "[1]", "[emphasis mine]" pass.
            if not ("p." in span_l or "charter" in span_l or "cvsu" in span_l):
                continue
            if not any(span_l in c or c in span_l for c in cites):
                reply = reply.replace(f"[{span}]", "")
                stripped += 1
        if stripped:
            reply = re.sub(r"[ \t]+([.,;:!?])", r"\1", reply)
            reply = re.sub(r"[ \t]{2,}", " ", reply)
            reply = re.sub(r"[ \t]+\n", "\n", reply).strip()
            self._bump("llm_guard_citation_stripped")
            print(f"[LLM GUARD] stripped {stripped} invented citation(s) from reply")
            if not reply:
                return False, "invented_citation", reply
        if len(reply) > LLM_MAX_REPLY_CHARS:
            cut = reply[:LLM_MAX_REPLY_CHARS]
            for boundary in (". ", "! ", "? ", "\n"):
                idx = cut.rfind(boundary)
                if idx > LLM_MAX_REPLY_CHARS // 2:
                    cut = cut[: idx + 1]
                    break
            reply = cut.rstrip() + " …"
        return True, "", reply

    def _retrieval_topk(self, user_input: str) -> Optional[list]:
        """Top-k distinct intents from the TF-IDF pattern index, best first.

        None means the index has no vote (disabled, DB unreadable, or an
        internal error) — the arbitration gate treats that as an abstention,
        never a veto.
        """
        try:
            index = intent_retrieval.get_index()
            if index is None:
                return None
            return index.retrieve_topk(user_input, self.NB_AGREEMENT_TOPK) or None
        except Exception:  # noqa: BLE001 — an arbitration signal must never fail a turn
            return None

    def _log_disagreement(
        self, *, query: str, nb_intent: Optional[str], nb_confidence: float,
        nb_margin: float, reason: str, nn_intent: Optional[str] = None,
        nn_confidence: Optional[float] = None, topk: Optional[list] = None,
    ) -> None:
        """Record a cross-tier disagreement (P1-5 weekly review queue).

        Always prints (the /admin/logs tail captures stdout); additionally
        persists via `disagreement_logger` when app.py has wired it to
        ChatLogger.log_tier_disagreement. Best-effort on both paths.
        """
        top_str = ", ".join(f"{m.intent}:{m.score:.2f}" for m in (topk or [])[:5])
        print(f"[ARBITRATION] {reason}: nb={nb_intent}@{nb_confidence:.2f} "
              f"margin={nb_margin:.2f}"
              + (f" nn={nn_intent}@{nn_confidence:.2f}" if nn_intent else "")
              + (f" topk=[{top_str}]" if top_str else "")
              + f" q={query[:80]!r}")
        callback = self.disagreement_logger
        if callback is None:
            return
        try:
            callback(
                query=query, nb_intent=nb_intent, nb_confidence=nb_confidence,
                nb_margin=nb_margin, reason=reason, nn_intent=nn_intent,
                nn_confidence=nn_confidence,
                retrieval_topk=[
                    {"intent": m.intent, "score": round(m.score, 3)}
                    for m in (topk or [])
                ],
            )
        except Exception:  # noqa: BLE001 — the review queue must never fail a turn
            pass

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
        self._bump("intent_retrieval_used")
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
        self._bump(stat)
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
        # Walk back to the most recent turn that actually printed a list: the
        # reply to the user's first pick sits in between, so history[-1] alone
        # cannot answer "I mean 3". Done entirely under the lock — a concurrent
        # turn both appends to this list and blanks the list_items of the entry
        # aging out of the pointable window.
        items = []
        with self._history_lock:
            history = self.conversation_history.get(user_id) or []
            for turn in reversed(history[-_LIST_REF_LOOKBACK:]):
                if turn.get("list_items"):
                    items = list(turn["list_items"])
                    break
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

        # Copy under the lock: this list-comprehends over the session's turns
        # while a concurrent turn may be appending to or truncating them.
        history = self.snapshot_history(user_id) if user_id else []
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
        if skip_intents and not (self.llm and self.llm.ensure_available()):
            skip_intents = False

        nlu_data = {}

        # Step 0.5: Conversation Recap — must precede the classifiers; see
        # _conversation_recap_result for why neither tier below can own this.
        recap = self._conversation_recap_result(user_input, user_id)
        if recap is not None:
            self._bump("conversation_recap_used")
            return self.RECAP_INTENT, recap, 1.0, "Conversation Recap", nlu_data

        # Step 0.6: Benign small talk — states the scope boundary and still
        # answers. Curated content only; see api/smalltalk.py for the GAD screen.
        small = _smalltalk_reply(user_input, filipino=_is_filipino(user_input))
        if small is not None:
            self._bump("smalltalk_used")
            return self.SMALLTALK_INTENT, small, 1.0, "Small Talk", nlu_data

        # Step 0.7: College Programs — naming any college and asking about
        # programs returns that college's COMPLETE list. Must precede the
        # classifiers: `courses_offered` owns the CEIT patterns and answers
        # with the generic all-colleges blurb, so it would win every time.
        programs = _college_program_reply(user_input, filipino=_is_filipino(user_input))
        if programs is not None:
            self._bump("college_programs_used")
            return self.PROGRAMS_INTENT, programs, 1.0, "College Programs", nlu_data

        if not skip_intents:
            # Step 1: Naive Bayes (+ optional NLU enhancement), arbitrated.
            # A confident-looking NB answer must also pass the margin check
            # and the retrieval agreement vote (P1-5) — NB's raw confidence is
            # uncalibrated, and before this gate a confidently-wrong NB
            # suppressed every better tier below it.
            nb_intent, nb_confidence, nb_margin, nlu_data = self._nb_result(user_input, user_id)
            if nb_intent and nb_confidence >= self.NB_CONFIDENCE_THRESHOLD:
                margin_ok = nb_margin >= self.NB_MARGIN_THRESHOLD
                topk = self._retrieval_topk(user_input)
                # None = index unavailable/empty → abstain, don't veto: losing
                # the intents DB must degrade to the old behavior, not take
                # the whole NB tier down with it.
                agrees = topk is None or any(m.intent == nb_intent for m in topk)
                if margin_ok and agrees:
                    self._bump("naive_bayes_used")
                    response = self._maybe_state_assumption(
                        self._select_response(nb_intent, user_input),
                        nb_intent, user_input, campus)
                    return nb_intent, response, nb_confidence, "Naive Bayes (NLU Enhanced)", nlu_data
                reasons = []
                if not margin_ok:
                    reasons.append("margin_below")
                if not agrees:
                    reasons.append("retrieval_disagree")
                self._bump("nb_arbitration_escalated")
                self._log_disagreement(
                    query=user_input, nb_intent=nb_intent,
                    nb_confidence=nb_confidence, nb_margin=nb_margin,
                    reason="+".join(reasons), topk=topk,
                )
                # P2-8: a thin margin ACROSS topic families means the student
                # could be asking either question — ask which, instead of
                # guessing. Same-family ties fall through to the NN as before.
                if not margin_ok and self.nb_model:
                    pair = self.nb_model.predict_topk(user_input, k=2)
                    if (len(pair) == 2
                            and pair[1][1] >= self.CLARIFY_MIN_RUNNERUP
                            and self._intent_family(pair[0][0]) != self._intent_family(pair[1][0])):
                        text, chips = self._clarification_reply(user_input, pair)
                        self._bump("intent_clarify_asked")
                        nlu_data = {**nlu_data, "suggestions": chips}
                        return self.CLARIFY_INTENT, text, nb_confidence, "Disambiguation (margin)", nlu_data
                # fall through to the NN and the deeper tiers

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
                response = self._maybe_state_assumption(
                    self._select_response(nn_intent, user_input),
                    nn_intent, user_input, campus)
                self._bump("neural_network_used")
                return nn_intent, response, nn_confidence, "Neural Network", nlu_data
            if (nn_intent and nn_intent != nb_intent
                    and nn_confidence >= self.nn_model.get_threshold(nn_intent)):
                # NN would have answered differently and confidently — the
                # classic NB-vs-NN disagreement the weekly review queue exists
                # to surface. Not served (the agreement guard stands); logged.
                self._log_disagreement(
                    query=user_input, nb_intent=nb_intent,
                    nb_confidence=nb_confidence, nb_margin=nb_margin,
                    reason="nn_vs_nb", nn_intent=nn_intent,
                    nn_confidence=nn_confidence,
                )

            # Step 2.5: Intent retrieval — soft lexical match over the intent
            # patterns corpus. Catches phrasings the classifiers under-score
            # ("complete list of courses": NB 0.28 / NN 0.37) and serves the
            # curated response with no LLM latency.
            served = self._intent_retrieval_result(user_input, nb_intent)
            if served is not None:
                intent, response, score = served
                response = self._maybe_state_assumption(response, intent, user_input, campus)
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
                self._bump("place_resolver_used")
                nlu_data = {**nlu_data, "place_id": place_id}
                return self.FIND_PLACE_INTENT, response, 1.0, "Place Resolver", nlu_data

        # Other-school guard (P1-6): every deep tier below grounds on CvSU-only
        # corpora, so a question about ANOTHER university can only be "answered"
        # by dredging up an irrelevant CvSU passage that happens to name that
        # school — both gold-eval OOS false-accepts were exactly this (site-RAG
        # answering UP Diliman / De La Salle admission questions). The curated
        # tiers above keep first claim on transfer/comparison intents that
        # legitimately mention other schools.
        if intent_retrieval.mentions_other_school(user_input):
            self._bump("scope_gate_blocked")
            return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, "ScopeGate (other_school)", nlu_data

        # Step 3: LLM fallback — fires only when NB+NN are both below threshold.
        # ensure_available() re-probes a previously-unreachable local server on
        # a cooldown, so a transient Ollama outage self-heals without a restart
        # instead of latching the whole tier off until the container is recreated.
        llm_configured = self.llm is not None and self.llm_provider != "none"
        llm_unavailable = False
        if llm_configured and self.llm.ensure_available():
            # NonsenseGate first: catches gibberish, profanity, and
            # fact-injection attempts ("the correct answer is...",
            # "Ang turon ay X") before ScopeGate's off-topic check.
            ns_allowed, ns_reason = self.nonsense_gate.allows(user_input)
            if not ns_allowed:
                self._bump("scope_gate_blocked")
                return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, f"NonsenseGate ({ns_reason})", nlu_data

            allowed, reason = self.scope_gate.allows(user_input)
            if not allowed:
                # Pre-filter blocked the query — don't even call the API
                self._bump("scope_gate_blocked")
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
                self._bump("scope_gate_blocked")
                provider_label = "Claude" if isinstance(self.llm, ClaudeLLM) else "Ollama"
                return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, f"{provider_label} (out-of-scope)", nlu_data

            if llm_reply:
                # P1-6 output guard: withhold a reply carrying contact details
                # or citations that are not in the retrieved passages.
                guard_ok, guard_reason, llm_reply = self._llm_output_guard(llm_reply, grounding)
                if not guard_ok:
                    self._bump("llm_guard_rejected")
                    print(f"[LLM GUARD] reply withheld ({guard_reason}) q={user_input[:80]!r}")
                    return (self.FALLBACK_INTENT, self.LLM_GUARD_MESSAGE, 0.0,
                            f"LLM Guard ({guard_reason.split(':', 1)[0]})", nlu_data)
                if _LLM_PROSE_REFUSAL_RE.match(llm_reply.strip()):
                    # The model declined in prose without the refusal token.
                    # Keep its wording (often more helpful than the canned
                    # refusal — e.g. redirecting a medical ask to the clinic)
                    # but label the turn out-of-scope so provenance, the OOS
                    # metrics, and the anti-pattern miner see a refusal.
                    self._bump("scope_gate_blocked")
                    provider_label = "Claude" if isinstance(self.llm, ClaudeLLM) else "Ollama"
                    return self.FALLBACK_INTENT, llm_reply, 0.0, f"{provider_label} (out-of-scope)", nlu_data
                self._bump("llm_fallback_used")
                provider_label = "Claude LLM" if isinstance(self.llm, ClaudeLLM) else "Local LLM"
                return self.FALLBACK_INTENT, llm_reply, 0.0, f"{provider_label}{charter_suffix}", nlu_data

            # Reached the model but it returned nothing (timeout / API error /
            # empty) — the assistant is degraded, not the user's phrasing.
            llm_unavailable = True
        elif llm_configured:
            # A provider is configured but its server is unreachable right now
            # (e.g. Ollama down). Same degraded state — not a genuine no-match.
            llm_unavailable = True

        # Step 3.5: Verbatim document tier — no LLM (or it returned nothing),
        # but the Citizens' Charter or the official website has a strongly-
        # matching passage. Quote the best one with a citation instead of
        # shrugging. Beats both fallbacks, so it runs before them.
        served = self._verbatim_document_reply(user_input)
        if served is not None:
            tag, reply, score, label = served
            return tag, reply, score, label, nlu_data

        # Step 4a: LLM-unavailable degrade. An LLM was configured but could not
        # answer this turn (down or errored). Return a distinct "try again"
        # reply — NOT the "I didn't understand" card (which blames the user's
        # wording) — under its own intent so the anti-pattern miner and the
        # fallback log don't count an outage as an unanswered question.
        if llm_unavailable:
            self._bump("llm_unavailable")
            return (self.LLM_UNAVAILABLE_INTENT,
                    self._select_response(self.LLM_UNAVAILABLE_INTENT, user_input),
                    0.0, "LLM Unavailable", nlu_data)

        # Step 4b: Static fallback — a genuine no-match (or the LLM was
        # intentionally disabled via LLM_PROVIDER=none).
        self._bump("fallback_used")
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
            self.record_turn(
                user_id,
                user_input=user_input,
                response=response,
                intent=intent,
                confidence=confidence,
                model_used=model_used,
                session_id=session_id,
                nlu_data=nlu_data,
            )

        return intent, response, confidence, model_used, nlu_data

    def record_turn(
        self,
        user_id: str,
        *,
        user_input: str,
        response: str,
        intent: str,
        confidence: float,
        model_used: str,
        session_id: Optional[str] = None,
        nlu_data: Optional[dict] = None,
    ) -> None:
        """Append one turn to a session's history — the only writer.

        Extracted from chat() so every mutation of conversation_history goes
        through one lock-holding place. popitem/move_to_end/insert each rewrite
        the OrderedDict's internal linked list, so two turns from DIFFERENT
        sessions interleaving here corrupt it outright — not a stale read.
        """
        nlu_data = nlu_data or {}
        with self._history_lock:
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
            # Drop the enumeration from the turn that just aged out of the
            # pointable window — _resolve_list_reference will never read it
            # again, and holding it is what makes retention grow with history.
            if len(turns) > _LIST_REF_LOOKBACK:
                turns[-(_LIST_REF_LOOKBACK + 1)]["list_items"] = []

            if len(turns) > self._MAX_HISTORY_TURNS:
                del turns[: -self._MAX_HISTORY_TURNS]

    def _bump(self, key: str, n: int = 1) -> None:
        """Increment a usage counter — the only writer to model_usage_stats.

        `d[k] += 1` is a read-modify-write spanning several bytecodes, so with
        concurrent turns it silently drops increments. Routing every write
        through here keeps the counters accurate rather than approximate.
        """
        with self._stats_lock:
            self.model_usage_stats[key] = self.model_usage_stats.get(key, 0) + n

    def get_usage_stats(self) -> dict:
        """Get model usage statistics"""
        # Snapshot once under the lock. Computing percentages directly against
        # a dict that is still being written yields parts that don't sum to the
        # total the caller was shown.
        with self._stats_lock:
            stats = self.model_usage_stats.copy()

        total = sum(stats.values())
        if total == 0:
            return stats

        def pct(key: str) -> float:
            return stats.get(key, 0) / total * 100

        return {
            "total_predictions": total,
            "naive_bayes_used": stats.get("naive_bayes_used", 0),
            "naive_bayes_percentage": pct("naive_bayes_used"),
            "neural_network_used": stats.get("neural_network_used", 0),
            "neural_network_percentage": pct("neural_network_used"),
            "place_resolver_used": stats.get("place_resolver_used", 0),
            "place_resolver_percentage": pct("place_resolver_used"),
            "llm_fallback_used": stats.get("llm_fallback_used", 0),
            "llm_fallback_percentage": pct("llm_fallback_used"),
            "llm_unavailable": stats.get("llm_unavailable", 0),
            "llm_unavailable_percentage": pct("llm_unavailable"),
            "fallback_used": stats.get("fallback_used", 0),
            "fallback_percentage": pct("fallback_used"),
            "nlu_enhanced": stats.get("nlu_enhanced", 0),
        }

    def get_history(self) -> dict:
        """Get conversation history, with each session's turns copied.

        The old shallow `.copy()` handed out the live per-session lists, which
        a caller would then serialize while a worker thread appended to and
        truncated them.
        """
        with self._history_lock:
            return {uid: list(turns) for uid, turns in self.conversation_history.items()}

    def snapshot_history(self, user_id: str) -> list:
        """One session's turns, copied under the lock.

        Callers that used to reach into conversation_history[user_id] directly
        should use this: it is the difference between serializing a stable list
        and serializing one a worker is mutating underneath them.
        """
        with self._history_lock:
            return list(self.conversation_history.get(user_id) or [])

    def drop_history(self, user_id: str) -> bool:
        """Forget one session. Returns whether anything was actually removed."""
        with self._history_lock:
            return self.conversation_history.pop(user_id, None) is not None

    def clear_history(self, user_id: Optional[str] = None):
        """Clear conversation history"""
        with self._history_lock:
            if user_id:
                self.conversation_history.pop(user_id, None)
            else:
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
- For any specific number, date, name, or requirement, qualify clearly. Use a bracketed citation ONLY when a bracketed source excerpt was provided in the prompt, copying its label exactly; NEVER compose a citation (source name, edition, or page number) from memory. If no excerpts were provided, answer without bracketed citations.
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

