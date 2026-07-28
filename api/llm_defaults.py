"""Single source of truth for the local-LLM endpoint default.

Every deployment sets OLLAMA_BASE_URL in sevi.env / config/*.env (the
in-network `ollama` service, or host.docker.internal for a host install). The
fallback here only applies to a bare local dev run — kept in ONE place so the
same literal does not drift across the modules that talk to Ollama: the LLM
fallback tier (hybrid_chatbot), the AIS / connectors LLM routers, the safety
second-opinion, and the /health warm-up probe.

Model names are deliberately NOT defaulted here (or anywhere in code): each LLM
wrapper reads its model from the environment (OLLAMA_MODEL / OPENAI_MODEL /
CLAUDE_MODEL) and disables itself loudly when unset, rather than baking a
specific deployment's model version into the source.
"""
import os

# Used only when OLLAMA_BASE_URL is unset (bare local dev). Every deployment
# overrides it via env; this is the one place the localhost default lives.
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


def ollama_base_url() -> str:
    """Resolve the Ollama endpoint from OLLAMA_BASE_URL (trailing slash trimmed)."""
    return os.environ.get("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL).rstrip("/")
