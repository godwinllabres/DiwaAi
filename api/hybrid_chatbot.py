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
import threading
import urllib.request
import urllib.error
import numpy as np
from typing import Tuple, Optional
import joblib

import nltk
from nltk.stem import WordNetLemmatizer

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

        self.model = tf.keras.models.load_model(os.path.join(model_dir, "nn_model.h5"))
        with open(os.path.join(model_dir, "nn_tokenizer.pkl"), "rb") as f:
            self.tokenizer = pickle.load(f)
        with open(os.path.join(model_dir, "nn_label_encoder.pkl"), "rb") as f:
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

        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 512},
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "DIWA/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("message", {}).get("content", "").strip() or None
        except urllib.error.URLError as e:
            print(f"[WARNING] Ollama request failed: {e}")
            return None
        except Exception as e:
            print(f"[WARNING] Ollama generate error: {type(e).__name__}: {e}")
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
    _ALLOW_SHORT = {
        "hi", "hello", "hey", "yes", "no", "ok", "okay",
        "cvsu", "ceit", "con", "cas", "cafenr", "cemds",
        "ojt", "tor", "cat", "map", "fee", "fees",
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
        alphas = sum(c.isalpha() for c in t)

        # Single-word / very short input — only allow well-known short tokens.
        if alphas < self.MIN_ALPHAS:
            return False, "too_short"
        if " " not in t and t_lower not in self._ALLOW_SHORT and alphas < 4:
            return False, "too_short"

        if self._PROFANITY.search(t):
            return False, "profanity"

        if self._KEYSMASH.search(t):
            return False, "keysmash"

        # Prompt-injection language is blocked unconditionally (CvSU
        # mention is not an exemption — these phrasings are abusive).
        if self._PROMPT_INJECTION.search(t):
            return False, "prompt_injection"

        # Vowel-starved token = keyboard noise (e.g. "fgbhnj", "tnsmnsl")
        if alphas >= 5:
            vowels = sum(c.lower() in "aeiou" for c in t if c.isalpha())
            if vowels / alphas < self.MIN_VOWEL_RATIO:
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
    _MATH_KEYWORDS = re.compile(
        r"\b(solve|calculate|compute|evaluate|simplify|integrate|"
        r"differentiate|derivative|integral|equation|factorial|"
        r"logarithm|sine|cosine|tangent|matrix|determinant|"
        r"probability of|how much is|what is \d|whats \d)\b",
        re.IGNORECASE,
    )
    _MATH_EXPRESSION = re.compile(r"\d+\s*[\+\-\*/\^x×÷]\s*\d+")
    _EQUATION_LIKE = re.compile(r"[a-z]\s*[\+\-\*/=]\s*\d+", re.IGNORECASE)

    # Off-topic keyword list (each must match as a whole phrase/word)
    _OFFTOPIC = re.compile(
        r"\b(capital of|weather in|recipe|cook|bake|"
        r"celebrity|movie|netflix|tiktok|"
        r"sports score|football|basketball game|nba|fifa|"
        r"write code|debug|python|javascript|java code|c\+\+|"
        r"write a poem|write a story|write a song|write me a|"
        r"translate to|translate this|translation of|"
        r"tell a joke|tell me a joke|funny joke|"
        r"president of|prime minister|election|"
        r"bitcoin|crypto|stock price|forex|"
        r"horoscope|zodiac|tarot)\b",
        re.IGNORECASE,
    )

    REFUSAL_MESSAGES = [
        "I can only help with questions about Cavite State University — programs, admissions, fees, scholarships, campus services, and policies. Is there something CvSU-related I can help with?",
        "That's outside my scope. I'm DIWA, the CvSU Digital Intelligent Web Assistant — I focus on Cavite State University topics like enrollment, courses, scholarships, and campus information. What would you like to know about CvSU?",
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
        if self._MATH_EXPRESSION.search(text):
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


class HybridChatbot:
    """
    Hierarchical Hybrid Chatbot
    Strategy: Use fast NB first, fallback to accurate NN if uncertain
    """

    NB_CONFIDENCE_THRESHOLD = 0.55  # If NB confidence > 55%, use it; otherwise defer to NN
    NN_CONFIDENCE_THRESHOLD = 0.50  # NN minimum confidence threshold
    FALLBACK_INTENT = "nlu_fallback"

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

        # Initialize conversation tracking
        self.conversation_history = {}
        self.model_usage_stats = {
            "naive_bayes_used": 0,
            "neural_network_used": 0,
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

        if provider == "claude":
            print("\n[4/5] Initialising Claude API fallback...")
            self.llm = ClaudeLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] Claude LLM ready  model={self.llm.model}")
            else:
                if not ANTHROPIC_AVAILABLE:
                    print("[WARNING] anthropic package not installed — pip install anthropic")
                else:
                    print("[WARNING] ANTHROPIC_API_KEY not set or invalid — Claude fallback disabled")
        elif provider == "ollama":
            print("\n[4/5] Initialising local LLM fallback (Ollama)...")
            self.llm = LocalLLM(system_prompt=scope_locked_prompt)
            if self.llm.available:
                print(f"[OK] Local LLM ready  model={self.llm.model}  url={self.llm.base_url}")
                # Warm-up in background so the first user query doesn't pay
                # the 60-120s cold-start cost on CPU-only machines.
                self._warm_up_llm_async()
            else:
                print("[WARNING] Local LLM not reachable — deep-fallback disabled")
                print("          Start Ollama and run: ollama pull llama3.1")
        else:
            print(f"\n[4/5] LLM fallback disabled (LLM_PROVIDER={provider})")

        self.model_usage_stats["llm_fallback_used"] = 0
        self.model_usage_stats["scope_gate_blocked"] = 0

        print("\n[5/5] Initialization complete")
        print("=" * 60)
        print(f"Strategy: NB threshold = {self.NB_CONFIDENCE_THRESHOLD:.0%}")
        print("         NN threshold = adaptive per-intent")
        llm_status = "enabled" if (self.llm and self.llm.available) else "disabled"
        print(f"         LLM fallback = {llm_status} (provider={self.llm_provider})")
        print("=" * 60 + "\n")

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

    @staticmethod
    def _system_prompt_text() -> str:
        """Compact system prompt passed to the local LLM for deep-fallback answers."""
        return (
            "You are DIWA, the Digital Intelligent Web Assistant for Cavite State University. "
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

    def predict(self, user_input: str, user_id: str = None) -> Tuple[str, str, float, str, dict]:
        """
        Hierarchical prediction: NB → NN → Local LLM → static fallback.

        Returns:
            (intent, response, confidence, model_used, nlu_data)
        """
        # Step 1: Naive Bayes (+ optional NLU enhancement)
        nb_intent, nb_confidence, nlu_data = self._nb_result(user_input, user_id)
        if nb_intent and nb_confidence >= self.NB_CONFIDENCE_THRESHOLD:
            self.model_usage_stats["naive_bayes_used"] += 1
            return nb_intent, random.choice(self.responses_map[nb_intent]), nb_confidence, "Naive Bayes (NLU Enhanced)", nlu_data

        # Step 2: Neural Network with adaptive per-intent threshold
        nn_intent, nn_confidence = self._nn_result(user_input)
        if nn_intent and nn_confidence >= self.nn_model.get_threshold(nn_intent):
            response = random.choice(self.responses_map.get(nn_intent, self.responses_map[self.FALLBACK_INTENT]))
            self.model_usage_stats["neural_network_used"] += 1
            return nn_intent, response, nn_confidence, "Neural Network", nlu_data

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

            llm_reply = self.llm.generate(user_input, conversation_context=self._llm_context(user_id))
            # LLM emitted the refusal token → out of scope per the model's own judgment
            if llm_reply and LLM_REFUSAL_TOKEN in llm_reply:
                self.model_usage_stats["scope_gate_blocked"] += 1
                provider_label = "Claude" if isinstance(self.llm, ClaudeLLM) else "Ollama"
                return self.FALLBACK_INTENT, self.scope_gate.refusal(), 0.0, f"{provider_label} (out-of-scope)", nlu_data

            if llm_reply:
                self.model_usage_stats["llm_fallback_used"] += 1
                provider_label = "Claude LLM" if isinstance(self.llm, ClaudeLLM) else "Local LLM"
                return self.FALLBACK_INTENT, llm_reply, 0.0, provider_label, nlu_data

        # Step 4: Static fallback
        self.model_usage_stats["fallback_used"] += 1
        return self.FALLBACK_INTENT, random.choice(self.responses_map[self.FALLBACK_INTENT]), 0.0, "Fallback", nlu_data

    def chat(
        self,
        user_input: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Tuple[str, str, float, str, dict]:
        """
        Chat with conversation tracking and NLU enhancements

        Returns:
            (intent, response, confidence, model_used, nlu_data)
        """
        intent, response, confidence, model_used, nlu_data = self.predict(user_input, user_id)

        # Track conversation
        if user_id:
            if user_id not in self.conversation_history:
                self.conversation_history[user_id] = []

            self.conversation_history[user_id].append({
                "user_message": user_input,
                "bot_response": response,
                "intent": intent,
                "confidence": confidence,
                "model_used": model_used,
                "session_id": session_id,
                "entities": nlu_data.get("entities", {}),
                "is_follow_up": nlu_data.get("is_follow_up", False),
            })

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
        return """You are DIWA, the Digital Intelligent Web Assistant for Cavite State University - a helpful, friendly guide.

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

