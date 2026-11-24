"""Single source of truth for the local-LLM endpoint defaults.

Every deployment sets OLLAMA_BASE_URL (or OPENAI_BASE_URL) in sevi.env /
config/*.env (the in-network `ollama` service, or host.docker.internal for a
host install). The fallbacks here only apply to a bare local dev run — kept in
ONE place so the same literal does not drift across the modules that talk to
the local LLM: the LLM fallback tier (hybrid_chatbot), the AIS / connectors LLM
routers, the agentic loop and the safety second-opinion.

Model names are deliberately NOT defaulted here (or anywhere in code): each LLM
wrapper reads its model from the environment (OLLAMA_MODEL / OPENAI_MODEL /
CLAUDE_MODEL) and disables itself loudly when unset, rather than baking a
specific deployment's model version into the source.
"""
import os
import re
from typing import NamedTuple

# Used only when OLLAMA_BASE_URL is unset (bare local dev). Every deployment
# overrides it via env; this is the one place the localhost default lives.
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
# Same for OPENAI_BASE_URL — the API base INCLUDING the /v1 prefix.
_DEFAULT_OPENAI_BASE_URL = "http://localhost:8080/v1"

# LLM_PROVIDER values served by an OpenAI-compatible server (llama.cpp's
# llama-server, LM Studio, LocalAI, vLLM ...) addressed through OPENAI_*.
OPENAI_COMPAT_PROVIDERS = frozenset({"openai", "localai"})

# Hybrid-reasoning families that think by default. Chat answers and tool
# routing don't need it, and the reasoning eats the whole max_tokens budget
# (empty content) or leaks into the reply — every caller turns it off.
THINKING_MODEL_RE = re.compile(r"^(qwen3|deepseek-r1|magistral|gpt-oss)", re.IGNORECASE)


def ollama_base_url() -> str:
    """Resolve the Ollama endpoint from OLLAMA_BASE_URL (trailing slash trimmed)."""
    return os.environ.get("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def openai_base_url() -> str:
    """Resolve the OpenAI-compatible API base (incl. /v1) from OPENAI_BASE_URL."""
    return os.environ.get("OPENAI_BASE_URL", _DEFAULT_OPENAI_BASE_URL).rstrip("/")


def openai_headers() -> dict:
    """Request headers for the OpenAI-compatible server. The key is optional —
    llama-server / LM Studio / LocalAI usually run without one."""
    headers = {"Content-Type": "application/json", "User-Agent": "DIWA/1.0"}
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def no_think_fields(model: str) -> dict:
    """Extra /chat/completions body fields that switch reasoning off for a
    hybrid-thinking model on an OpenAI-compatible server (llama-server, LM
    Studio and vLLM all honour chat_template_kwargs). Empty for other models."""
    if THINKING_MODEL_RE.match(model or ""):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


class LocalChatTarget(NamedTuple):
    provider: str  # "ollama" | "openai" | "localai" — for log lines
    url: str       # full POST URL of the chat-completions endpoint
    model: str
    headers: dict
    extra_body: dict  # merge into the request body (thinking switch)


def local_chat_target(ollama_model: str) -> LocalChatTarget:
    """Where a module's local, OpenAI-style chat call goes, read PER CALL so the
    admin LLM toggle (which rewrites LLM_PROVIDER / *_MODEL) steers it.

    LLM_PROVIDER=openai|localai -> {OPENAI_BASE_URL}/chat/completions with
    OPENAI_MODEL. Anything else -> Ollama's OpenAI-compatible
    {OLLAMA_BASE_URL}/v1/chat/completions with the caller's `ollama_model`
    (its own AIS_MCP_* / CONNECTORS_MCP_* override chain).
    """
    provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if provider in OPENAI_COMPAT_PROVIDERS:
        model = os.environ.get("OPENAI_MODEL", "").strip()
        return LocalChatTarget(
            provider, f"{openai_base_url()}/chat/completions", model,
            openai_headers(), no_think_fields(model),
        )
    return LocalChatTarget(
        "ollama", f"{ollama_base_url()}/v1/chat/completions", ollama_model, {}, {},
    )
