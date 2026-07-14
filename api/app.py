"""
CvSU Chatbot REST API
FastAPI-based endpoint for integration with web applications
"""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Optional, List, Dict, Any, Literal, Union
import asyncio
import json
import os
import random
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import joblib
import nltk
from nltk.stem import WordNetLemmatizer

# Import logger
from .logger import ChatLogger
# Import hybrid chatbot
from .hybrid_chatbot import HybridChatbot
from . import intent_retrieval as _intent_retrieval
from . import site_rag as _site_rag
# Seasonal topic recommender + intent-onboarding sanitation checks
from .topic_recommender import recommend as _recommend_topics
from .intent_curation import sanitize_candidate_intent as _sanitize_candidate_intent
# AIS MCP bridge — routes finance queries to the CvSU AIS MCP server
from .ais_mcp import try_handle as _try_ais
from .ais_mcp import metrics_snapshot as _ais_metrics_snapshot
from .ais_mcp import close_pool as _ais_close_pool
from .ais_mcp import call_tool as _ais_call_tool
from .ais_mcp import ToolCallError as _AisToolCallError
from .ais_mcp import _sanitize_tool_error as _ais_sanitize_tool_error
from .connectors_mcp import try_handle as _try_connectors
from .connectors_mcp import metrics_snapshot as _connectors_metrics_snapshot
from .ais_mcp import circuit_status as _ais_circuit_status
from . import safety as _safety
from . import campus_context as _campus
from . import charter_rag as _charter_rag
from . import intent_grounding as _intent_grounding
# Phase 2A Wave 2 — per-user AIS authentication for write actions.
from . import auth_ais as _ais_auth

import logging as _logging
_logger = _logging.getLogger("diwa.api")

# Download NLTK resources (idempotent — no-op if already present)
for resource, kind in [('punkt_tab', 'tokenizers'), ('wordnet', 'corpora')]:
    try:
        nltk.data.find(f'{kind}/{resource}')
    except (LookupError, OSError):
        nltk.download(resource, quiet=True)

lemmatizer = WordNetLemmatizer()

# ============================================================================
# SYSTEM INSTRUCTIONS / AGENT PERSONALITY
# ============================================================================
SYSTEM_INSTRUCTIONS = """
You are Sevi, the virtual assistant for Cavite State University - a helpful, friendly guide.

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
You are a helpful starting point and information aggregator, not the final authority. For anything consequential - enrollment, scholarships, document requirements - empower the user to verify with the proper CvSU office, and provide the path to that verification.
"""

# ============================================================================
# FastAPI Application
# ============================================================================
_is_production = os.getenv("RENDER", "") != "" or os.getenv("PRODUCTION", "") != ""


# Closes the cached AIS MCP session when uvicorn shuts down so we don't
# leak the SSE stream + transport. Open is lazy on first use; close is
# eager here.
from contextlib import asynccontextmanager  # noqa: E402

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await _ais_close_pool()


app = FastAPI(
    title="DIWA API",
    version="1.0.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
    lifespan=_lifespan,
)

# Enable CORS — explicit origins only (never wildcard in production).
# Default to localhost dev server; override via CORS_ORIGINS env var.
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
_allowed_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Pin"],
)

# ============================================================================
# Admin Authentication
# ============================================================================
DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")

# Simple in-memory rate limiter for PIN verification (brute-force protection)
_pin_attempts: Dict[str, list] = {}       # ip -> [timestamps]
_PIN_MAX_ATTEMPTS = 5
_PIN_WINDOW_SECONDS = 300                  # 5-minute window


def _check_rate_limit(client_ip: str) -> None:
    """Raise 429 if the client has exceeded PIN attempt limits."""
    now = time.time()
    attempts = _pin_attempts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < _PIN_WINDOW_SECONDS]
    _pin_attempts[client_ip] = attempts
    if len(attempts) >= _PIN_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many attempts. Try again later.")


def _record_attempt(client_ip: str) -> None:
    _pin_attempts.setdefault(client_ip, []).append(time.time())


# Per-session rate limiter for /chat. Cloudflare Access protects Desk but
# Diwa's /chat is intentionally anonymous, so defense-in-depth here matters.
# Keyed by session_id when present, else by client IP, so a single browser
# tab can't burst-query indefinitely. In-memory only — single-worker uvicorn
# is assumed; multi-worker deploys need Redis.
_CHAT_MAX_REQUESTS  = int(os.getenv("CHAT_RATE_LIMIT_MAX", "30"))
_CHAT_WINDOW_SECONDS = float(os.getenv("CHAT_RATE_LIMIT_WINDOW", "60"))
_chat_hits: Dict[str, list] = {}


def _check_chat_rate_limit(key: str) -> None:
    """Raise 429 if `key` has exceeded the chat-call budget for the window."""
    now = time.time()
    hits = [t for t in _chat_hits.get(key, []) if now - t < _CHAT_WINDOW_SECONDS]
    if len(hits) >= _CHAT_MAX_REQUESTS:
        raise HTTPException(429, "Too many chat requests. Slow down for a moment.")
    hits.append(now)
    _chat_hits[key] = hits


async def require_admin(request: Request) -> None:
    """Dependency: verify the X-Admin-Pin header matches DASHBOARD_PIN."""
    if not DASHBOARD_PIN:
        raise HTTPException(503, "Admin access not configured")
    pin = request.headers.get("X-Admin-Pin", "")
    if pin != DASHBOARD_PIN:
        raise HTTPException(401, "Unauthorized")


# ============================================================================
# Request/Response Models
# ============================================================================
class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    # Optional escape hatch for external clients (e.g. an admin tool or another
    # service) to call a specific AIS MCP tool without going through the
    # router. When set, `message` is still logged but `intent_hint` wins.
    intent_hint: Optional[str] = None
    intent_args: Optional[Dict[str, Any]] = None

# Campus map (48 official locations) — see api/campus_places.py
# All functions are now DB-primary with hardcoded fallback.
from api.campus_places import (
    MapData,
    PlaceMeta,
    Directory,
    resolve_map_data as _resolve_map_data,
    resolve_directory as _resolve_directory,
    build_place_meta as _build_place_meta,
    campus_map_payload as _campus_map_payload,
    has_place as _has_place,
    get_all_places as _get_all_places,
    save_coord_overrides as _save_coord_overrides,
    list_coord_overrides as _list_coord_overrides,
    reset_coord_overrides as _reset_coord_overrides,
    save_waypoint_overrides as _save_waypoint_overrides,
    list_waypoint_overrides as _list_waypoint_overrides,
    reset_waypoint_overrides as _reset_waypoint_overrides,
    delete_waypoint_override as _delete_waypoint_override,
    list_custom_markers as _list_custom_markers,
    upsert_custom_marker as _upsert_custom_marker,
    delete_custom_marker as _delete_custom_marker,
)

from api.db import ensure_schema as _ensure_db_schema

# Ensure all DB tables exist at startup (idempotent)
try:
    _ensure_db_schema()
except Exception:
    pass


class CoordEntry(BaseModel):
    x: int
    y: int


class WaypointEntry(BaseModel):
    """Waypoint coordinate plus optional adjacency list. The frontend uses
    `neighbors` for custom waypoints so they can be wired into the routing
    graph without code changes."""
    x: int
    y: int
    neighbors: Optional[List[str]] = None


class CoordsUpdate(BaseModel):
    """Body for PUT /map/coords. Keys are place_ids."""
    coords: Dict[str, CoordEntry]


class WaypointsUpdate(BaseModel):
    """Body for PUT /map/waypoints. Values may carry an adjacency list."""
    coords: Dict[str, WaypointEntry]


class CustomMarkerCreate(BaseModel):
    id: str
    name: str
    x: int
    y: int
    abbr: Optional[str] = None
    num: Optional[int] = None


def _is_header_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 25:
        return True
    if s.endswith(":") and len(s) < 60:
        return True
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _trim_to_sentence(head: str, max_chars: int) -> str:
    if len(head) <= max_chars:
        return head
    for terminator in (". ", "? ", "! "):
        idx = head.find(terminator)
        if 30 < idx < max_chars:
            return head[: idx + 1].strip()
    return head[:max_chars].rstrip() + "…"


# Office phone numbers are withheld from chat output (data-privacy: numbers
# rot and the official directory stays authoritative). Crisis-line responses
# (mental_health_immediate) are exempt — stripping NCMH/Hopeline numbers from
# them would be harmful. Short emergency codes (911, 1553) never match these
# patterns. SafetyGate refusals short-circuit before redaction ever runs.
_PHONE_REDACT_EXEMPT_INTENTS = {"mental_health_immediate"}
_PHONE_RE = re.compile(
    r"(?:\+63[\s.-]?\d{2,3}[\s.-]?\d{3}[\s.-]?\d{4}"  # +63 998 937 2020
    r"|\(0\d{1,2}\)\s?\d{3,4}[\s.-]?\d{4}"            # (046) 862-0850 / (02) 8804-4673
    r"|\b09\d{2}[\s.-]?\d{3}[\s.-]?\d{4}\b)"          # 0917 558-4673 / 09171234567
)
_PHONE_PLACEHOLDER = "[see the official directory at cvsu.edu.ph]"
# Collapses "[see …] / [see …]" chains left by multi-number listings.
_PHONE_COLLAPSE_RE = re.compile(
    re.escape(_PHONE_PLACEHOLDER)
    + r"(?:\s*[/,;]?\s*(?:or\s+)?"
    + re.escape(_PHONE_PLACEHOLDER)
    + r")+"
)


def _redact_office_phones(text: str, intent: Optional[str] = None) -> str:
    if not text or intent in _PHONE_REDACT_EXEMPT_INTENTS:
        return text
    redacted, hits = _PHONE_RE.subn(_PHONE_PLACEHOLDER, text)
    if hits < 2:  # 0 or 1 replacement — no placeholder chain to collapse
        return redacted
    return _PHONE_COLLAPSE_RE.sub(_PHONE_PLACEHOLDER, redacted)


# Display formatting — many corpus responses (esp. the Tagalog variants) are a
# single dense paragraph. The web renderer already handles headings, bullets
# and paragraphs, so give it structure: sentence-per-block, and colon
# enumerations ("events: A, B, at C") become bullet lists. Splitting is
# parenthesis-aware so "(Enero 28–29, 2026, kasama …)" stays intact.
_ABBREV_TAIL_RE = re.compile(
    r"\b(?:hal|e\.g|i\.e|etc|No|Blg|Mr|Mrs|Ms|Dr|Engr|Atty|Sta|Sto|s)\.$", re.IGNORECASE
)


def _split_outside_parens(text: str, boundary: str) -> List[str]:
    """Split on `boundary` ('. ' sentence ends or ', ' list commas) at paren
    depth 0. Sentence mode skips known abbreviations ("hal.", "No.")."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text) - 1:
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and text[i : i + 2] == boundary:
            head = text[start : i + (1 if boundary == ". " else 0)]
            if boundary != ". " or not _ABBREV_TAIL_RE.search(head.rstrip()):
                parts.append(head.strip())
                start = i + 2
        i += 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _bulletize(sentence: str) -> Optional[str]:
    """Turn 'intro: A, B, C, at D.' into an intro line + bullet list."""
    m = re.search(r": (?!\d)", sentence)
    if not m:
        return None
    intro, rest = sentence[: m.start()], sentence[m.end() :]
    items = _split_outside_parens(rest, ", ")
    # Only bulletize real enumerations: 3+ items, at least half of them
    # substantial — keeps "Indang, Cavite"-style addresses as prose.
    if len(items) < 3 or sum(len(i) >= 12 for i in items) * 2 < len(items):
        return None
    # "at Graduation." / "and Graduation" — drop the conjunction on the last item.
    items[-1] = re.sub(r"^(?:at|and)\s+", "", items[-1], flags=re.IGNORECASE)
    return intro + ":\n" + "\n".join(f"- {item.rstrip('.')}" for item in items)


def _format_display_text(text: str) -> str:
    if not text or "\n" in text or len(text) < 240:
        return text  # already structured, or short enough to read as-is
    sentences = _split_outside_parens(text, ". ")
    return "\n\n".join(_bulletize(s) or s for s in sentences)


def _extract_summary(text: str, max_chars: int = 240) -> str:
    """Pull the first meaningful paragraph or sentence from a response.

    Skips header-like lines ("CvSU REGISTRAR SERVICES:", all-caps, ends with
    a colon, or too short) so the UI summary never looks like just a heading.
    """
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for para in paragraphs:
        if _is_header_line(para.split("\n", 1)[0]):
            continue
        return _trim_to_sentence(para.replace("\n", " ").strip(), max_chars)
    if paragraphs:
        return _trim_to_sentence(paragraphs[0].replace("\n", " "), max_chars)
    return text[:max_chars].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Chat response envelope (v2)
#
# A `/chat` reply is the text plus a typed list of cards (map, directory, DV
# detail, table) plus provenance (which tier answered) plus a hint to the UI
# about how to lay it out. Replaces the v1 flat shape with `map_data /
# directory / dv_card / table` as separate optional fields.
# ─────────────────────────────────────────────────────────────────────────────


class ResponseSource(str, Enum):
    """Which tier of the cascade produced the reply."""
    NAIVE_BAYES     = "naive_bayes"
    NEURAL_NETWORK  = "neural_network"
    LLM_LOCAL       = "llm_local"
    LLM_CLAUDE      = "llm_claude"
    AIS_MCP         = "ais_mcp"
    CONNECTORS_MCP  = "connectors_mcp"
    CHARTER_RAG     = "charter_rag"
    SITE_RAG        = "site_rag"
    INTENT_RETRIEVAL = "intent_retrieval"
    FALLBACK        = "fallback"
    REFUSAL         = "refusal"


class RefusalReason(str, Enum):
    """Set when source == REFUSAL — why the reply is a refusal."""
    NONSENSE      = "nonsense"
    OUT_OF_SCOPE  = "out_of_scope"
    PROHIBITED    = "prohibited"
    ABUSIVE       = "abusive"   # profanity/insult directed at a person or the bot
    SAFETY        = "safety"    # self-harm referral or threat boundary


class DisplayHint(str, Enum):
    """How the frontend should lay out the response."""
    DEFAULT     = "default"      # text bubble first, cards under
    MAP_FIRST   = "map_first"    # show the map above the text
    CARD_FIRST  = "card_first"   # show the structured card (DV / table) first
    TEXT_ONLY   = "text_only"    # no cards expected; tighten spacing


class DirectoryCard(BaseModel):
    """Office / contact card."""
    kind: Literal["directory"] = "directory"
    office: str
    location: Optional[str] = None
    place_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[str] = None


class MapCard(BaseModel):
    """Campus map preview, expandable to fullscreen on the frontend."""
    kind: Literal["map"] = "map"
    place_id: str
    label: str
    default_open: bool = False


class DvCard(BaseModel):
    """Structured DV detail surfaced when the AIS MCP bridge handles a get_dv."""
    kind: Literal["dv"] = "dv"
    name: str
    control_number: Optional[str] = None
    payee: str = ""
    amount: float = 0.0
    workflow_status: str = ""
    posting_date: Optional[str] = None
    fund_cluster: Optional[str] = None
    ors_burs_reference: Optional[str] = None
    dv_type: Optional[str] = None
    desk_url: str
    # Phase 2A Wave 2 — frontend round-trips this as expected_modified on
    # /ais/write so the server-side optimistic-lock check can catch stale
    # writes when another session changed the DV between read and confirm.
    modified: Optional[str] = None


class TableColumn(BaseModel):
    key: str
    label: str
    align: Optional[Literal["left", "right", "center"]] = None


class TableCard(BaseModel):
    """Multi-row table for list/find/group results.
    Rows are keyed by column.key (matches the existing frontend TableCard
    component which accesses `row[c.key]`)."""
    kind: Literal["table"] = "table"
    title: str = ""
    columns: List[TableColumn] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    footer: Optional[str] = None
    total_rows: Optional[int] = None


ChatCard = Annotated[
    Union[DirectoryCard, MapCard, DvCard, TableCard],
    Field(discriminator="kind"),
]


class UacsContext(BaseModel):
    kind: Literal["object", "funding_source", "responsibility_center"]
    query: Optional[str] = None


class ChatContext(BaseModel):
    """Conversation memory the frontend can surface as a 'talking about …' chip."""
    dv: Optional[str] = None
    uacs: Optional[UacsContext] = None
    report: Optional[str] = None


class SourceCitation(BaseModel):
    """Provenance for a curated intent reply — the official document (Citizens'
    Charter page or official-site URL) the intent's answer is grounded in.
    Populated from data/intent_sources.json for intent-tier replies; the RAG
    and LLM tiers already carry citations inside their prose."""
    kind: Literal["charter", "site"]
    locator: str                 # charter page number, or site URL
    label: Optional[str] = None  # doc title for site refs
    citation: str                # rendered one-line citation


class ChatResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    # Identity — message_id is None when logging is best-effort and didn't yield
    # an id (log_chat swallows errors and returns None); a chat reply must never
    # 500 just because the audit write failed.
    message_id: Optional[int] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    # Content
    text: str
    summary: Optional[str] = None

    # Classification + provenance
    intent: str
    confidence: float = 0.0
    source: ResponseSource
    refusal_reason: Optional[RefusalReason] = None

    # Structured attachments — empty list if none
    cards: List[ChatCard] = Field(default_factory=list)
    context: Optional[ChatContext] = None
    suggestions: List[str] = Field(default_factory=list)
    # Official source(s) backing a curated intent reply — empty for RAG/LLM
    # tiers (which cite inline) and for refusals/fallback.
    sources: List[SourceCitation] = Field(default_factory=list)

    # Layout hint for the renderer
    display_hint: DisplayHint = DisplayHint.DEFAULT

class IntentInfo(BaseModel):
    tag: str
    pattern_count: int
    response_count: int
    sample_patterns: List[str]

class ModelInfo(BaseModel):
    """Sanitized model info — no internal details exposed."""
    model_config = ConfigDict(protected_namespaces=())
    total_intents: int

class FeedbackRequest(BaseModel):
    message_id: Optional[int] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    intent: Optional[str] = None
    rating: Optional[int] = None          # 1–5
    helpful: Optional[bool] = None
    reason: Optional[str] = None          # structured taxonomy code (see /feedback/reasons)
    comment: Optional[str] = None         # free-text qualitative detail
    suggested_intent: Optional[str] = None
    user_message: Optional[str] = None    # store query directly when message_id unavailable


# Structured reason taxonomy. Keep in sync with the frontends
# (web/app/components/ChatMessage.tsx and web/web_interface.html).
FEEDBACK_REASONS = {
    "positive": [
        {"code": "accurate",   "label": "Got my answer"},
        {"code": "clear",      "label": "Easy to understand"},
        {"code": "helpful",    "label": "Pointed me the right way"},
        {"code": "other",      "label": "Something else"},
    ],
    "negative": [
        {"code": "wrong_info",   "label": "Contains incorrect information"},
        {"code": "wrong_topic",  "label": "Answered something else"},
        {"code": "incomplete",   "label": "Missing key details"},
        {"code": "outdated",     "label": "Looks out of date"},
        {"code": "confusing",    "label": "Hard to understand"},
        {"code": "other",        "label": "Something else"},
    ],
}
_VALID_REASON_CODES = {r["code"] for group in FEEDBACK_REASONS.values() for r in group}

class FeedbackAnalyzeRequest(BaseModel):
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    intent: Optional[str] = None
    min_rating: Optional[int] = None
    max_rating: Optional[int] = None
    helpful: Optional[bool] = None
    limit: int = 5000
    apply: bool = False                   # write changes to cavsu_intents.json

# Note: HybridChatbot is now imported from hybrid_chatbot.py
# It combines Naive Bayes (fast) + Neural Network (accurate)

# ============================================================================
# Initialize Chatbot and Logger
# ============================================================================
MODEL_DIR = "models"

# Initialize Hybrid Chatbot (Naive Bayes + Neural Network)
chatbot = HybridChatbot(
    model_dir=MODEL_DIR,
    responses_path=os.path.join(MODEL_DIR, "responses_map.json")
)

# Initialize chat logger
chat_logger = ChatLogger(log_dir="logs", db_path="logs/chat_history.db")

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/", tags=["Health"])
async def root():
    """Root endpoint - API status."""
    return {
        "service": "DIWA API",
        "status": "active",
    }

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint. Reports model and LLM readiness without leaking internals."""
    llm_ok = bool(chatbot.llm and chatbot.llm.available)
    return {
        "status": "healthy",
        "classifier_ready": chatbot.nb_model is not None,
        "llm_provider": chatbot.llm_provider,
        "llm_ready": llm_ok,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Response-shape helpers (v2 envelope)
# ─────────────────────────────────────────────────────────────────────────────

# Intents whose answer is about *where* something is — the map should render
# above the text in those cases. Kept in sync with the frontend's
# MAP_FIRST_INTENT_RE in ChatMessage.tsx.
_MAP_FIRST_INTENT_RE = re.compile(r"direction|location|where|map|find_place|navigate|route", re.I)

# Maps the `model_used` string the chatbot returns into the public-facing
# (source, refusal_reason) pair on ChatResponse.
def _classify_source(model_used: Optional[str]) -> tuple[ResponseSource, Optional[RefusalReason]]:
    if not model_used:
        return ResponseSource.FALLBACK, None
    m = model_used
    if m.startswith("Naive Bayes"):
        return ResponseSource.NAIVE_BAYES, None
    if m == "Neural Network":
        return ResponseSource.NEURAL_NETWORK, None
    if m == "ais_mcp":
        return ResponseSource.AIS_MCP, None
    if m == "connectors_mcp":
        return ResponseSource.CONNECTORS_MCP, None
    if m.startswith("Charter RAG"):
        return ResponseSource.CHARTER_RAG, None
    if m.startswith("Site RAG"):
        return ResponseSource.SITE_RAG, None
    if m.startswith("Intent Retrieval"):
        return ResponseSource.INTENT_RETRIEVAL, None
    if m.startswith("NonsenseGate"):
        return ResponseSource.REFUSAL, RefusalReason.NONSENSE
    if m.startswith("ScopeGate") or "(out-of-scope)" in m:
        return ResponseSource.REFUSAL, RefusalReason.OUT_OF_SCOPE
    if m.startswith("Claude"):
        return ResponseSource.LLM_CLAUDE, None
    if m.startswith("Local LLM") or m.startswith("Ollama"):
        return ResponseSource.LLM_LOCAL, None
    return ResponseSource.FALLBACK, None


# Tiers that serve a *curated* response with no inline citation — these get an
# appended "Source:" block + structured `sources` from the per-intent bindings.
# The RAG/LLM tiers already cite their passages in-prose, so they are excluded.
_INTENT_TIER_SOURCES = frozenset({
    ResponseSource.NAIVE_BAYES,
    ResponseSource.NEURAL_NETWORK,
    ResponseSource.INTENT_RETRIEVAL,
})


def _intent_grounding_for(
    intent: Optional[str], source: ResponseSource
) -> tuple[str, List[SourceCitation]]:
    """(citation_block_text, sources) for a curated intent reply, else ("", []).

    Only intent-tier replies with a verified binding are cited; everything else
    (RAG, LLM, refusal, fallback, unbound intents) returns no citation."""
    if source not in _INTENT_TIER_SOURCES:
        return "", []
    index = _intent_grounding.get_index()
    if index is None:
        return "", []
    refs = index.refs_for(intent)
    if not refs:
        return "", []
    cards = [
        SourceCitation(
            kind=r.kind, locator=r.locator,
            label=r.label or None, citation=r.citation(),
        )
        for r in refs
    ]
    return _intent_grounding.citation_block(refs), cards


def _context_from_ais(ctx: Optional[Dict[str, Any]]) -> Optional[ChatContext]:
    """Lift the AIS bridge's free-form context dict into a typed ChatContext."""
    if not ctx:
        return None
    uacs: Optional[UacsContext] = None
    if ctx.get("uacs_kind"):
        try:
            uacs = UacsContext(kind=ctx["uacs_kind"], query=ctx.get("uacs_query"))
        except Exception:
            uacs = None
    return ChatContext(dv=ctx.get("dv") or None, uacs=uacs, report=ctx.get("report") or None)


def _table_from_dict(t: Optional[Dict[str, Any]]) -> Optional[TableCard]:
    """Convert the AIS bridge's raw table dict into a typed TableCard."""
    if not t:
        return None
    try:
        cols = [
            TableColumn(key=c.get("key", ""), label=c.get("label", c.get("key", "")), align=c.get("align"))
            for c in t.get("columns", [])
        ]
        return TableCard(
            title=t.get("title", ""),
            columns=cols,
            rows=t.get("rows", []) or [],
            footer=t.get("footer"),
            total_rows=t.get("total_rows"),
        )
    except Exception as exc:
        _logger.warning("Could not coerce AIS table into TableCard: %s", exc)
        return None


def _build_attachments(
    *,
    message: str,
    intent: str,
    ais_dv_card: Optional[DvCard],
    ais_table: Optional[Dict[str, Any]],
    ais_context_set: Optional[Dict[str, Any]],
) -> tuple[List[ChatCard], Optional[ChatContext], DisplayHint]:
    """Resolve every typed card the reply should carry, plus the layout hint."""
    cards: List[ChatCard] = []

    # AIS card / table take top billing when present.
    if ais_dv_card is not None:
        cards.append(ais_dv_card)
    table_card = _table_from_dict(ais_table)
    if table_card is not None:
        cards.append(table_card)

    # Directory card (office contact). Resolved from the intent.
    dir_data = _resolve_directory(intent)
    if dir_data is not None:
        cards.append(
            DirectoryCard(
                office=dir_data.office,
                location=dir_data.location,
                place_id=dir_data.place_id,
                email=dir_data.email,
                phone=dir_data.phone,
                hours=dir_data.hours,
            )
        )

    # Map preview / accordion.
    map_data = _resolve_map_data(message, intent)
    if map_data is not None:
        is_map_first = bool(intent and _MAP_FIRST_INTENT_RE.search(intent))
        cards.append(
            MapCard(place_id=map_data.place_id, label=map_data.label, default_open=is_map_first)
        )

    # Display hint
    if ais_dv_card is not None or table_card is not None:
        hint = DisplayHint.CARD_FIRST
    elif map_data is not None and _MAP_FIRST_INTENT_RE.search(intent or ""):
        hint = DisplayHint.MAP_FIRST
    elif not cards:
        hint = DisplayHint.TEXT_ONLY
    else:
        hint = DisplayHint.DEFAULT

    return cards, _context_from_ais(ais_context_set), hint


def _short_circuit_response(
    request: "ChatRequest",
    log_message: str,
    start_time: float,
    *,
    text: str,
    intent: str,
    source: ResponseSource,
    model_used: str,
    refusal_reason: Optional[RefusalReason] = None,
    suggestions: Optional[List[str]] = None,
) -> ChatResponse:
    """Build + log a terminal (no-cards) reply for a gate that answers before
    the NLU cascade (safety refusal, campus clarify). `log_message` is the
    user's ORIGINAL text — request.message may have been mutated by a gate."""
    message_id = chat_logger.log_chat(
        user_id=request.user_id or "anonymous",
        user_message=log_message,
        bot_response=text,
        intent=intent,
        confidence=1.0,
        model_used=model_used,
        session_id=request.session_id,
        response_time_ms=(time.time() - start_time) * 1000,
    )
    return ChatResponse(
        message_id=message_id,
        user_id=request.user_id,
        session_id=request.session_id,
        text=text,
        summary=None,
        intent=intent,
        confidence=1.0,
        source=source,
        refusal_reason=refusal_reason,
        cards=[],
        context=None,
        suggestions=suggestions or [],
        display_hint=DisplayHint.TEXT_ONLY,
    )


def _safety_block(category: str, model_used: str) -> dict:
    """ChatResponse fields for a safety refusal (self_harm / threat / abuse)."""
    return {
        "text": _safety.RESPONSES[category],
        "intent": f"safety_{category}",
        "source": ResponseSource.REFUSAL,
        "model_used": model_used,
        "refusal_reason": (
            RefusalReason.ABUSIVE if category == "abuse" else RefusalReason.SAFETY
        ),
        "suggestions": _safety.SUGGESTIONS[category],
    }


async def _safety_screen(message: str, session_id: Optional[str]):
    """Front-door SafetyGate, shared by /chat and /batch so neither is an
    unscreened path. Returns (block, effective_message):
      • block is a dict of ChatResponse fields when the message must be
        refused (self-harm / threat / abuse), else None;
      • effective_message is the message to process downstream (sanitized when
        profanity was mere seasoning, otherwise unchanged)."""
    result = _safety.classify(message)
    if result.category in ("self_harm", "threat", "abuse"):
        _safety.record(result.category, message, session_id, result.max_severity)
        return _safety_block(result.category, f"SafetyGate ({result.category})"), message
    if result.category == "intensifier":
        _safety.record("intensifier", message, session_id, result.max_severity)
        return None, result.sanitized
    # Lexicon said safe — the opt-in LLM second opinion catches paraphrased
    # self-harm/threats the wordlists can't (no-op unless enabled + prefilter).
    llm_cat = await _safety.llm_second_opinion(message)
    if llm_cat:
        _safety.record(llm_cat, message, session_id)
        return _safety_block(llm_cat, f"SafetyGate (llm:{llm_cat})"), message
    return None, message


@app.post("/chat", response_model=ChatResponse, tags=["Chat"],
          responses={400: {"description": "Message cannot be empty"}})
async def chat_endpoint(request: ChatRequest, http_request: Request):
    """
    Send a message to the chatbot (Hierarchical Hybrid Model).

    Uses Naive Bayes first (fast), falls back to Neural Network if uncertain.

    Request:
        message (str): User's question or input
        user_id (str, optional): Track conversation per user
        session_id (str, optional): Track conversation per session

    Returns:
        response (str): Chatbot's response
        intent (str): Classified intent
        confidence (float): Confidence score (0-1)
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Rate-limit by session_id (so a single tab can't hammer us) with a
    # client-IP fallback for sessionless callers. Raises 429 when exceeded.
    rl_key = request.session_id or (http_request.client.host if http_request.client else "anon")
    _check_chat_rate_limit(f"chat:{rl_key}")

    if request.intent_hint is not None or request.intent_args is not None:
        internal_key = os.getenv("INTERNAL_KEY", "")
        provided = http_request.headers.get("X-Internal-Key", "")
        if not (internal_key and secrets.compare_digest(provided, internal_key)):
            raise HTTPException(
                status_code=403,
                detail="intent_hint requires a valid X-Internal-Key header",
            )

    # Measure response time
    start_time = time.time()
    # The gates below may rewrite request.message (profanity sanitize, campus
    # grounding); keep the user's original for the audit log.
    original_message = request.message

    # Safety screen — runs before the MCP bridges and the intent tiers so no
    # path answers an abusive/threatening message cheerfully (the Nonsense/
    # Scope gates only guard the LLM tier). See docs/moderation_plan.md.
    block, request.message = await _safety_screen(request.message, request.session_id)
    if block:
        return _short_circuit_response(request, original_message, start_time, **block)

    # Campus context — CvSU has 11 campuses; "where is the campus located?"
    # is ambiguous. Remember the session's campus, clarify when unknown
    # (suggestions become clickable campus chips), and rewrite follow-ups to
    # carry the campus so intents/charter-RAG/LLM retrieve the right one.
    campus_routing = _campus.resolve(
        request.session_id or request.user_id, request.message
    )
    if campus_routing.action == "clarify":
        return _short_circuit_response(
            request, original_message, start_time,
            text=_campus.CLARIFY_TEXT,
            intent="campus_disambiguation",
            source=ResponseSource.FALLBACK,
            model_used="CampusContext (clarify)",
            suggestions=_campus.CLARIFY_SUGGESTIONS,
        )
    campus_grounded = campus_routing.action in ("augment", "answer_pending")
    if campus_grounded:
        # A canned intent answer would drop the campus the rewrite added —
        # send these to the deep tiers (charter RAG + LLM) instead.
        request.message = campus_routing.message

    # AIS MCP short-circuit — if the query looks like a finance/accounting
    # lookup (DV name, budget balance, RAPAL/RAOD report, UACS lookup),
    # answer it from the AIS MCP server instead of the student-facing NLU.
    # On transient AIS failure (timeout/transport error), `failure_note` is
    # set and we fall back to NLU with a discreet annotation prepended —
    # better UX than dumping a stack trace into the chat bubble.
    ais_reply = await _try_ais(
        request.message,
        session_id=request.session_id,
        intent_hint=request.intent_hint,
        intent_args=request.intent_args,
    )
    ais_dv_card: Optional[DvCard] = None
    ais_failure_note: Optional[str] = None
    ais_context_set: Optional[Dict[str, Any]] = None
    ais_table: Optional[Dict[str, Any]] = None
    ais_suggestions: Optional[List[str]] = None
    if ais_reply is not None and ais_reply.get("text") is not None:
        ais_dv_card = DvCard(**ais_reply["dv_card"]) if ais_reply.get("dv_card") else None
        ais_context_set = ais_reply.get("context_set")
        ais_table = ais_reply.get("table")
        ais_suggestions = ais_reply.get("suggestions") or None
        intent, response, confidence, model_used, nlu_data = (
            "ais_mcp", ais_reply["text"], 1.0, "ais_mcp", {},
        )
    else:
        if ais_reply is not None:
            ais_failure_note = ais_reply.get("failure_note")
        # Connectors MCP short-circuit — university-services lookups (helpdesk
        # ticket tracking, course-catalog queries) served by the shared
        # read-only diwa-connectors server. Runs only when AIS didn't handle
        # the turn, so the two bridges are mutually exclusive and the table /
        # suggestions slots below are owned by whichever one answered.
        conn_reply = await _try_connectors(request.message, session_id=request.session_id)
        if conn_reply is not None and conn_reply.get("text") is not None:
            ais_table = conn_reply.get("table")
            ais_suggestions = conn_reply.get("suggestions") or None
            intent, response, confidence, model_used, nlu_data = (
                "connectors_mcp", conn_reply["text"], 1.0, "connectors_mcp", {},
            )
        else:
            if conn_reply is not None and not ais_failure_note:
                ais_failure_note = conn_reply.get("failure_note")
            intent, response, confidence, model_used, nlu_data = chatbot.chat(
                request.message,
                user_id=request.user_id,
                session_id=request.session_id,
                skip_intents=campus_grounded,
            )
            if ais_failure_note:
                response = f"{ais_failure_note}\n\n{response}"

    response_time_ms = (time.time() - start_time) * 1000

    # Log the chat — record the user's ORIGINAL text, not the gate-rewritten
    # message (sanitized profanity / campus-grounded), so the moderation and
    # audit trail reflects what was actually sent.
    message_id = chat_logger.log_chat(
        user_id=request.user_id or "anonymous",
        user_message=original_message,
        bot_response=response,
        intent=intent,
        confidence=confidence,
        model_used=model_used,
        session_id=request.session_id,
        response_time_ms=response_time_ms
    )

    # Logged verbatim above (audit trail); redacted for display. The summary
    # is drawn from the redacted prose; only `text` gets display structuring.
    response = _redact_office_phones(response, intent)

    cards, context, hint = _build_attachments(
        message=request.message,
        intent=intent,
        ais_dv_card=ais_dv_card,
        ais_table=ais_table,
        ais_context_set=ais_context_set,
    )
    source, refusal_reason = _classify_source(model_used)

    # Per-intent grounding — append the official source line to the display
    # text and surface it structurally. Summary/log stay citation-free.
    cite_block, sources = _intent_grounding_for(intent, source)
    display_text = _format_display_text(response)
    if cite_block:
        display_text += cite_block

    return ChatResponse(
        message_id=message_id,
        user_id=request.user_id,
        session_id=request.session_id,
        text=display_text,
        summary=_extract_summary(response),
        intent=intent,
        confidence=confidence,
        source=source,
        refusal_reason=refusal_reason,
        cards=cards,
        context=context,
        suggestions=ais_suggestions or [],
        display_hint=hint,
        sources=sources,
    )

@app.get("/intents", tags=["Intents"])
async def get_intents():
    """Get list of all available intent categories."""
    intents = chatbot.get_all_intents()
    return {
        "total_intents": len(intents),
        "intents": intents
    }


@app.get("/ais_mcp_stats", tags=["Admin"], dependencies=[Depends(require_admin)])
async def ais_mcp_stats():
    """Per-tool call counts, p50/p95 latency, error rate, and circuit-breaker
    status for the AIS MCP bridge. In-memory aggregates only — no persistence.
    Gated by `X-Admin-Pin` (matches DASHBOARD_PIN)."""
    snapshot = _ais_metrics_snapshot()
    snapshot["sessions"] = {"active_ais_logins": _ais_auth.session_count()}
    return snapshot


@app.get("/connectors_mcp_stats", tags=["Admin"], dependencies=[Depends(require_admin)])
async def connectors_mcp_stats():
    """Routing counts (regex vs LLM), per-tool call/ok counts, and transport
    failures for the connectors MCP bridge. In-memory aggregates only.
    Gated by `X-Admin-Pin` (matches DASHBOARD_PIN)."""
    return _connectors_metrics_snapshot()


# ─────────────────────────────────────────────────────────────────────────────
# Admin LLM toggle — switch which model answers, at runtime, no restart.
# ─────────────────────────────────────────────────────────────────────────────


class LlmToggleRequest(BaseModel):
    provider: Literal["ollama", "claude", "none"]
    model: Optional[str] = None  # e.g. "llama3.2:3b", "qwen3:8b", "claude-haiku-4-5"


async def _ollama_local_models() -> List[str]:
    """Best-effort list of locally pulled Ollama models (for a UI dropdown)."""
    import httpx

    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            resp = await http.get(f"{base}/api/tags")
            resp.raise_for_status()
            return sorted(m.get("name", "") for m in resp.json().get("models", []))
    except Exception:
        return []


_LOGS_DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "web" / "logs_dashboard.html"


@app.get("/admin/logs", tags=["Admin"], include_in_schema=False)
async def logs_dashboard():
    """Serve the logs dashboard HTML from the API origin.

    Not PIN-gated: the page is a static shell with no secrets, and browsers
    can't attach the `X-Admin-Pin` header on a plain navigation. Serving it
    same-origin means the data endpoints (all still PIN-gated) need no CORS
    exception — open http://<api-host>/admin/logs and enter the PIN in-page.
    """
    if not _LOGS_DASHBOARD_HTML.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(_LOGS_DASHBOARD_HTML, media_type="text/html")


@app.get("/admin/moderation", tags=["Admin"], dependencies=[Depends(require_admin)])
async def moderation_stats():
    """SafetyGate counters per category + the last 20 flagged messages
    (truncated). Gated by `X-Admin-Pin`."""
    return _safety.snapshot()


_START_TIME = time.time()


@app.get("/admin/status", tags=["Admin"], dependencies=[Depends(require_admin)])
async def admin_status():
    """Single-pane operational status across every subsystem — the brain tiers,
    the LLM tier + model, the two MCP bridges, moderation, and campus context.
    Gated by `X-Admin-Pin`. Each subsystem is best-effort: a failure in one
    reports {error: ...} rather than sinking the whole call."""
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — never let one subsystem 500 the status
            return {"error": exc.__class__.__name__}

    llm = _safe(chatbot.llm_status, {})
    return {
        "service": "DIWA API",
        "uptime_seconds": int(time.time() - _START_TIME),
        "brain": {
            "classifier_ready": chatbot.nb_model is not None,
            "neural_net_ready": getattr(chatbot, "nn_model", None) is not None,
            "charter_rag": _safe(lambda: {
                "available": (idx := _charter_rag.get_index()) is not None,
                "chunks": len(idx._chunks) if idx else 0,
            }, {}),
            "intent_grounding": _safe(lambda: (
                gi.snapshot() if (gi := _intent_grounding.get_index()) else {"available": False}
            ), {}),
            "usage": _safe(chatbot.get_usage_stats, {}),
        },
        "llm": {**llm, "second_opinion": os.getenv("SAFETY_LLM_SECOND_OPINION", "0") == "1"},
        "moderation": _safe(lambda: _safety.snapshot()["counts"], {}),
        "connectors_bridge": _safe(_connectors_metrics_snapshot, {}),
        "ais_bridge": _safe(_ais_circuit_status, {}),
        "campus_context": _safe(_campus.snapshot, {}),
    }


@app.get("/admin/llm", tags=["Admin"], dependencies=[Depends(require_admin)])
async def get_llm_config():
    """Which LLM tier is live (provider, model, reachable) plus the locally
    available Ollama models. Gated by `X-Admin-Pin`."""
    return {**chatbot.llm_status(), "ollama_models": await _ollama_local_models()}


@app.post("/admin/llm", tags=["Admin"], dependencies=[Depends(require_admin)])
async def set_llm_config(request: LlmToggleRequest):
    """Hot-swap the responding LLM: provider ollama/claude/none, optional
    model override. Also steers the AIS/connectors LLM routers (they follow
    LLM_PROVIDER per call). Takes effect on the next chat turn — no restart."""
    status = chatbot.set_llm(request.provider, request.model)
    _logger.info(
        "admin llm toggle: provider=%s model=%s available=%s",
        status["provider"], status["model"], status["available"],
    )
    return status


# ============================================================================
# AIS Auth Endpoints (Phase 2A, Wave 2)
# ============================================================================
# These let a Sevi web user log into AIS as their actual Frappe identity so
# subsequent write actions run with their SoD-relevant role instead of the
# shared bot identity. Tokens live in-memory keyed by `session_id` and
# evaporate on uvicorn restart.

class AuthLoginRequest(BaseModel):
    session_id: str
    username: str
    password: str


class AuthLogoutRequest(BaseModel):
    session_id: str


def _auth_handle(handler):
    """Translate AuthError into the standard FastAPI HTTPException pattern.
    Keeps the endpoint bodies trivial."""
    async def wrapper(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except _ais_auth.AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return wrapper


@app.post("/auth/login", tags=["AIS Auth"])
async def auth_login(request: AuthLoginRequest):
    """Exchange CvSU credentials for an AIS OAuth token cached under
    session_id. Subsequent /ais/write calls with the same session_id will
    act as this user. Returns the user's identity claims (NOT the token)."""
    return await _auth_handle(_ais_auth.login)(
        request.session_id, request.username, request.password,
    )


@app.post("/auth/logout", tags=["AIS Auth"])
async def auth_logout(request: AuthLogoutRequest):
    """Drop the cached AIS token for this session. Idempotent — succeeds
    even if there was no session to begin with."""
    await _ais_auth.logout(request.session_id)
    return {"ok": True}


@app.get("/auth/whoami", tags=["AIS Auth"])
async def auth_whoami(session_id: str):
    """Identity snapshot for an active session. Returns {"logged_in": false}
    when no session is cached so the frontend can decide whether to show
    the login modal — no error, just a fact."""
    snapshot = await _ais_auth.whoami(session_id)
    if snapshot is None:
        return {"logged_in": False}
    return {"logged_in": True, **snapshot}


# ============================================================================
# AIS Write Endpoint (Phase 2A, Wave 2)
# ============================================================================
# Out-of-band entry point for confirmed DV write actions from the Sevi web
# UI. Distinct from /chat by design — /chat is anonymous and free-form;
# /ais/write requires an authenticated session AND a UI-driven confirm
# modal AND a tool-specific arg shape. There is intentionally NO natural-
# language path to writes anywhere.

_WRITE_ACTION_TOOL_MAP: Dict[str, str] = {
    "approve_dv":    "approve_dv",
    "post_dv":       "post_dv",
    "cancel_dv":     "cancel_dv",
    "set_dv_status": "set_dv_status",
}


class AisWriteRequest(BaseModel):
    session_id: str
    action: str                          # one of _WRITE_ACTION_TOOL_MAP keys
    name: str                            # DV name
    idempotency_key: str
    expected_modified: Optional[str] = None
    reason: Optional[str] = None         # cancel_dv requires this
    new_status: Optional[str] = None     # set_dv_status requires this


@app.post("/ais/write", tags=["AIS Write"])
async def ais_write(request: AisWriteRequest):
    """Invoke an AIS write tool as the end user behind `session_id`.

    Steps:
      1. Resolve the session_id → cached AIS access token (refreshing if
         expiring). 401 if no session.
      2. Translate the action into the MCP tool name and assemble the
         tool's required args (incl. the hidden `__auth_token`).
      3. Call the MCP write tool. The MCP server enforces the kill switch
         (returning PILOT when off) and translates Frappe 403s into a
         clean FORBIDDEN response.
      4. Return the tool's response verbatim — the frontend treats `ok`
         as the success flag.
    """
    if request.action not in _WRITE_ACTION_TOOL_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")

    # 1. Token resolution (may refresh).
    try:
        token = await _ais_auth.get_user_token(request.session_id)
    except _ais_auth.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # 2. Assemble args. Drop None values so the tool's schema validation
    # doesn't complain about extra fields it doesn't expect for THIS action.
    args: Dict[str, Any] = {
        "name": request.name,
        "confirm": True,                  # /ais/write is invoked from a confirm modal — implicit
        "idempotency_key": request.idempotency_key,
        "__auth_token": token,
    }
    if request.expected_modified:
        args["expected_modified"] = request.expected_modified
    if request.reason and request.action == "cancel_dv":
        args["reason"] = request.reason
    if request.new_status and request.action == "set_dv_status":
        args["new_status"] = request.new_status

    # 3. Call. Three failure modes to distinguish:
    #    - ToolCallError: Frappe rejected the action (validation, SoD, stale
    #      lock, missing certs). Return a structured non-200 response with
    #      the sanitized message so the modal can show it.
    #    - Other Exception: transport / SDK / circuit-open → 503.
    #    - Success: forward the tool's response verbatim.
    tool_name = _WRITE_ACTION_TOOL_MAP[request.action]
    try:
        result = await _ais_call_tool(tool_name, args)
    except _AisToolCallError as exc:
        # ToolCallError = MCP reachable, Frappe said no. Sanitize the
        # message (strips the "Error calling X: TypeName: " wrapper and
        # extracts the meaningful exception text from Frappe's JSON body).
        user_text, treat_as_unreachable = _ais_sanitize_tool_error(str(exc))
        if treat_as_unreachable:
            _logger.exception("ais_write tool=%s name=%s reason=server_5xx", tool_name, request.name)
            raise HTTPException(status_code=503, detail="AIS is temporarily unreachable — try again.") from exc
        _logger.info("ais_write tool=%s name=%s reason=tool_rejected msg=%s",
                     tool_name, request.name, user_text[:200])
        return {"ok": False, "error_code": "REJECTED", "message": user_text}
    except Exception as exc:  # noqa: BLE001 — transport/SDK/circuit failure
        _logger.exception("ais_write tool=%s name=%s reason=transport", tool_name, request.name)
        raise HTTPException(status_code=503, detail="AIS write failed — try again.") from exc

    return result


# ============================================================================
# Campus Map Endpoints
# ============================================================================

@app.get("/map", tags=["Map"])
async def get_campus_map():
    """Return the full set of campus places, the gate position, and the SVG viewBox.

    Edit `api/campus_places.py` server-side to change labels, geometry, walk
    times, or directions without recompiling the frontend.
    """
    return _campus_map_payload()

@app.get("/map/coords", tags=["Map"])
async def get_map_coords():
    """Return all current effective marker coords + just the override layer.

    The `coords` block is what /map serves (defaults merged with admin
    overrides). The `overrides` block is the DB (or JSON-file) overrides.
    """
    payload = _campus_map_payload()
    coords = {
        p.place_id: {"x": p.x, "y": p.y}
        for p in payload["places"]
        if p.x is not None and p.y is not None
    }
    return {"coords": coords, "overrides": _list_coord_overrides()}


@app.put("/map/coords", tags=["Map"], dependencies=[Depends(require_admin)])
async def put_map_coords(body: CoordsUpdate):
    """Admin: persist marker (x, y) edits to DB (and JSON fallback).

    Existing overrides are merged with the body so partial updates don't
    overwrite untouched entries. Bad place_ids are silently ignored.
    """
    payload = {pid: {"x": e.x, "y": e.y} for pid, e in body.coords.items()}
    applied = _save_coord_overrides(payload)
    return {"status": "saved", "applied": applied, "overrides": _list_coord_overrides()}


@app.delete("/map/coords", tags=["Map"], dependencies=[Depends(require_admin)])
async def delete_map_coords():
    """Admin: clear all marker overrides from DB and JSON file."""
    _reset_coord_overrides()
    return {"status": "reset"}


@app.get("/map/waypoints", tags=["Map"])
async def get_map_waypoints():
    """Return waypoint overrides from DB (falls back to JSON file).
    Frontend merges these onto its bundled WAYPOINTS graph for routing.
    """
    return {"overrides": _list_waypoint_overrides()}


@app.put("/map/waypoints", tags=["Map"], dependencies=[Depends(require_admin)])
async def put_map_waypoints(body: WaypointsUpdate):
    """Admin: persist waypoint (x, y) edits to DB and JSON file.
    Entries for custom waypoints may include a `neighbors` array."""
    payload: Dict[str, Dict[str, Any]] = {}
    for wid, e in body.coords.items():
        entry: Dict[str, Any] = {"x": e.x, "y": e.y}
        if e.neighbors is not None:
            entry["neighbors"] = list(e.neighbors)
        payload[wid] = entry
    count = _save_waypoint_overrides(payload)
    return {"status": "saved", "overrides": _list_waypoint_overrides(), "total": count}


@app.delete("/map/waypoints", tags=["Map"], dependencies=[Depends(require_admin)])
async def delete_map_waypoints():
    """Admin: clear all waypoints from DB and JSON file."""
    _reset_waypoint_overrides()
    return {"status": "reset"}


@app.delete("/map/waypoints/{waypoint_id}", tags=["Map"], dependencies=[Depends(require_admin)])
async def delete_map_waypoint(waypoint_id: str):
    """Admin: drop a single waypoint from DB and JSON file."""
    removed = _delete_waypoint_override(waypoint_id)
    return {"status": "deleted" if removed else "not_found", "waypoint_id": waypoint_id}


# ---------------------------------------------------------------------------
# Custom markers — admin-created buildings beyond the canonical 48.
# ---------------------------------------------------------------------------

@app.get("/map/custom_markers", tags=["Map"])
async def get_custom_markers():
    """Return custom markers from DB (falls back to JSON file)."""
    return {"markers": _list_custom_markers()}


@app.post("/map/custom_markers", tags=["Map"], dependencies=[Depends(require_admin)])
async def post_custom_marker(body: CustomMarkerCreate):
    """Admin: add or update a custom marker in DB and JSON file."""
    try:
        entry = _upsert_custom_marker(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "saved", "marker": entry, "markers": _list_custom_markers()}


@app.delete("/map/custom_markers/{marker_id}", tags=["Map"], dependencies=[Depends(require_admin)])
async def remove_custom_marker(marker_id: str):
    """Admin: delete a custom marker from DB and JSON file."""
    removed = _delete_custom_marker(marker_id)
    return {"status": "deleted" if removed else "not_found", "marker_id": marker_id}


@app.get("/map/{place_id}", response_model=PlaceMeta, tags=["Map"],
         responses={404: {"description": "Place not found"}})
async def get_place(place_id: str):
    """Return canonical metadata for a single campus place."""
    if not _has_place(place_id):
        raise HTTPException(status_code=404, detail=f"Place '{place_id}' not found")
    return _build_place_meta(place_id)

@app.get("/intents/{intent_tag}", tags=["Intents"],
         responses={404: {"description": "Intent not found"}})
async def get_intent(intent_tag: str):
    """Get details about a specific intent."""
    details = chatbot.get_intent_details(intent_tag)

    if not details:
        raise HTTPException(status_code=404, detail=f"Intent '{intent_tag}' not found")

    return details

@app.get("/model/info", response_model=ModelInfo, tags=["Model"])
async def model_info():
    """Get sanitized model information (no internal details exposed)."""
    return ModelInfo(
        total_intents=chatbot.total_intents,
    )

@app.post("/model/reload", tags=["Model"], dependencies=[Depends(require_admin)])
async def reload_model():
    """Hot-reload all model artifacts from disk without restarting the server."""
    global chatbot
    chatbot = HybridChatbot(
        model_dir=MODEL_DIR,
        responses_path=os.path.join(MODEL_DIR, "responses_map.json")
    )
    # Retrieval tiers read the same artifacts (intents DB, site corpus) —
    # rebuild them so a retrain/resync is picked up in the same call.
    _intent_retrieval.reload_index()
    _site_rag.reload_index()
    _intent_grounding.reload_index()
    return {"status": "reloaded"}


@app.post("/admin/site_corpus/sync", tags=["Admin"], dependencies=[Depends(require_admin)])
async def sync_site_corpus():
    """Re-pull the official website into the site-RAG corpus and reindex.

    Fetches posts + pages (with metadata) from the portal named by
    SITE_CORPUS_URL via the WordPress REST API, rewrites docs/site_corpus.txt,
    and rebuilds the retrieval index. Gated by `X-Admin-Pin`."""
    try:
        stats = await asyncio.to_thread(_site_rag.sync_corpus)
    except Exception as exc:
        raise HTTPException(502, f"Site corpus sync failed: {exc}")
    index = _site_rag.reload_index()
    stats["index_available"] = index is not None
    return stats

@app.get("/conversation/{user_id}", tags=["Conversation"])
async def get_conversation_history(user_id: str):
    """Get conversation history for a user."""
    history = chatbot.conversation_history.get(user_id, [])
    return {
        "user_id": user_id,
        "message_count": len(history),
        "conversation": history
    }

@app.delete("/conversation/{user_id}", tags=["Conversation"])
async def clear_conversation(user_id: str):
    """Clear conversation history for a user."""
    if user_id in chatbot.conversation_history:
        del chatbot.conversation_history[user_id]
        return {"status": "cleared", "user_id": user_id}
    return {"status": "no_history", "user_id": user_id}

_BATCH_MAX = int(os.getenv("BATCH_MAX", "20"))


@app.post("/batch", tags=["Chat"])
async def batch_chat(requests: List[ChatRequest]):
    """
    Process multiple chat requests in batch.

    Useful for integration with web apps that need multiple responses.
    """
    if len(requests) > _BATCH_MAX:
        raise HTTPException(status_code=413, detail=f"Batch too large (max {_BATCH_MAX})")
    results = []
    for request in requests:
        start_time = time.time()
        # Same front-door SafetyGate as /chat — /batch must not be an
        # unscreened second entrance for abusive/self-harm messages.
        original_message = request.message
        block, request.message = await _safety_screen(request.message, request.session_id)
        if block:
            results.append(_short_circuit_response(request, original_message, start_time, **block))
            continue
        intent, response, confidence, model_used, nlu_data = chatbot.chat(
            request.message,
            user_id=request.user_id,
            session_id=request.session_id
        )
        response_time_ms = (time.time() - start_time) * 1000

        # Log each message (original text — request.message may be sanitized)
        message_id = chat_logger.log_chat(
            user_id=request.user_id or "anonymous",
            user_message=original_message,
            bot_response=response,
            intent=intent,
            confidence=confidence,
            model_used=model_used,
            session_id=request.session_id,
            response_time_ms=response_time_ms
        )

        # Logged verbatim above (audit trail); redacted for display. The summary
        # is drawn from the redacted prose; only `text` gets display structuring.
        response = _redact_office_phones(response, intent)

        cards, context, hint = _build_attachments(
            message=request.message,
            intent=intent,
            ais_dv_card=None,
            ais_table=None,
            ais_context_set=None,
        )
        source, refusal_reason = _classify_source(model_used)

        cite_block, sources = _intent_grounding_for(intent, source)
        display_text = _format_display_text(response)
        if cite_block:
            display_text += cite_block

        results.append(ChatResponse(
            message_id=message_id,
            user_id=request.user_id,
            session_id=request.session_id,
            text=display_text,
            summary=_extract_summary(response),
            intent=intent,
            confidence=confidence,
            source=source,
            refusal_reason=refusal_reason,
            cards=cards,
            context=context,
            display_hint=hint,
            sources=sources,
        ))
    return {"count": len(results), "results": results}

# ============================================================================
# Logging Endpoints
# ============================================================================

@app.get("/logs/user/{user_id}", tags=["Logging"], dependencies=[Depends(require_admin)])
async def get_user_logs(user_id: str, limit: int = 50):
    """Get chat history for a specific user"""
    history = chat_logger.get_user_history(user_id, limit)
    return {
        "user_id": user_id,
        "message_count": len(history),
        "messages": history
    }

@app.get("/logs/session/{session_id}", tags=["Logging"], dependencies=[Depends(require_admin)])
async def get_session_logs(session_id: str):
    """Get all messages in a specific session"""
    history = chat_logger.get_session_history(session_id)
    return {
        "session_id": session_id,
        "message_count": len(history),
        "messages": history
    }

@app.get("/logs/recent", tags=["Logging"], dependencies=[Depends(require_admin)])
async def get_recent_logs(limit: int = 20):
    """Get the most recent messages across all users (dashboard 'Messages' tab)."""
    limit = max(1, min(limit, 200))
    messages = chat_logger.get_recent_messages(limit)
    return {
        "count": len(messages),
        "messages": messages
    }

@app.get("/logs/intents", tags=["Logging"], dependencies=[Depends(require_admin)])
async def get_intent_logs():
    """Get statistics for all intents"""
    stats = chat_logger.get_intent_statistics()
    return {
        "total_intents": len(stats),
        "intents": stats
    }

@app.get("/logs/sessions", tags=["Logging"], dependencies=[Depends(require_admin)])
async def get_sessions_list(user_id: Optional[str] = None, limit: int = 20):
    """Get list of sessions"""
    sessions = chat_logger.get_session_list(user_id, limit)
    return {
        "user_id": user_id,
        "session_count": len(sessions),
        "sessions": sessions
    }

@app.get("/logs/today", tags=["Logging"], dependencies=[Depends(require_admin)])
async def get_today_statistics():
    """Get today's chat statistics"""
    stats = chat_logger.get_today_stats()
    return stats

@app.get("/logs/search", tags=["Logging"], dependencies=[Depends(require_admin)])
async def search_logs(query: str, limit: int = 20):
    """Search logs by message content"""
    results = chat_logger.search_logs(query, limit)
    return {
        "query": query,
        "results_count": len(results),
        "results": results
    }

@app.post("/logs/export/{user_id}", tags=["Logging"],
          dependencies=[Depends(require_admin)],
          responses={500: {"description": "Export failed"}})
async def export_user_logs(user_id: str):
    """Export all data for a user as JSON file"""
    filepath = chat_logger.export_user_data(user_id)
    if filepath:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Export failed")

@app.delete("/logs/cleanup", tags=["Logging"], dependencies=[Depends(require_admin)])
async def cleanup_old_logs(days: int = 30):
    """Delete logs older than specified days"""
    deleted = chat_logger.cleanup_old_logs(days)
    return {
        "status": "success",
        "days": days,
        "deleted_entries": deleted,
        "message": f"Deleted {deleted} log entries older than {days} days"
    }

# ============================================================================
# Feedback Endpoints
# ============================================================================

def _extract_new_patterns(
    entries: List[Dict],
    intent_map: Dict[str, Any],
    existing_patterns: Dict[str, set],
) -> Dict[str, List[str]]:
    """Return new patterns derived from feedback entries, grouped by intent tag."""
    additions: Dict[str, List[str]] = defaultdict(list)
    for entry in entries:
        target = entry.get("suggested_intent")
        query = (entry.get("user_message") or "").strip()
        helpful = entry.get("helpful")
        rating = entry.get("rating")

        if not target and helpful is True and rating is not None and rating <= 3:
            target = entry.get("intent")

        if not target or not query or target not in intent_map:
            continue
        if query in existing_patterns[target]:
            continue

        intent_map[target]["patterns"].append(query)
        existing_patterns[target].add(query)
        additions[target].append(query)
    return additions


@app.post(
    "/feedback",
    tags=["Feedback"],
    responses={
        422: {"description": "rating must be an integer between 1 and 5"},
        500: {"description": "Failed to store feedback"},
    },
)
async def submit_feedback(request: FeedbackRequest):
    """
    Submit feedback for a bot response.

    Stores a rating, helpful flag, optional comment, and an optional
    suggested_intent correction for misclassified messages.
    Pass user_message when message_id is unavailable so the analyze
    endpoint can still extract training patterns.
    """
    if request.rating is not None and not (1 <= request.rating <= 5):
        raise HTTPException(status_code=422, detail="rating must be between 1 and 5")

    if request.reason is not None and request.reason not in _VALID_REASON_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"reason must be one of {sorted(_VALID_REASON_CODES)}"
        )

    feedback_id = chat_logger.log_feedback(
        message_id=request.message_id,
        user_id=request.user_id,
        session_id=request.session_id,
        intent=request.intent,
        rating=request.rating,
        helpful=request.helpful,
        comment=request.comment,
        suggested_intent=request.suggested_intent,
        user_message=request.user_message,
        reason=request.reason,
    )

    if feedback_id is None:
        raise HTTPException(status_code=500, detail="Failed to store feedback")

    return {"status": "ok", "feedback_id": feedback_id}


@app.get("/feedback", tags=["Feedback"], dependencies=[Depends(require_admin)])
async def get_feedback(
    limit: int = 100,
    helpful: Optional[bool] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    intent: Optional[str] = None,
    min_rating: Optional[int] = None,
    max_rating: Optional[int] = None,
):
    """
    Retrieve feedback entries, joined with the original chat message.

    All filter parameters are optional and can be combined.
    """
    entries = chat_logger.get_feedback_entries(
        limit=limit,
        helpful=helpful,
        user_id=user_id,
        session_id=session_id,
        intent=intent,
        min_rating=min_rating,
        max_rating=max_rating,
    )
    return {"count": len(entries), "feedback": entries}


@app.get("/feedback/reasons", tags=["Feedback"])
async def get_feedback_reasons():
    """
    Return the structured reason taxonomy used by /feedback. Frontends should
    fetch this once and render chips/buttons so the codes stay in lockstep
    with the backend's allow-list.
    """
    return FEEDBACK_REASONS


@app.get("/feedback/stats", tags=["Feedback"], dependencies=[Depends(require_admin)])
async def get_feedback_stats():
    """
    Aggregated feedback statistics: overall totals, per-intent breakdown,
    lowest-rated intents, and the 10 most recent comments.
    """
    stats = chat_logger.get_feedback_stats()
    return stats


@app.get("/feedback/fallbacks", tags=["Feedback"], dependencies=[Depends(require_admin)])
async def get_feedback_fallbacks(limit: int = 100):
    """
    Return recent messages that triggered the nlu_fallback intent.
    Useful for manually identifying missing training patterns.
    """
    examples = chat_logger.get_fallback_examples(limit=limit)
    return {"count": len(examples), "fallbacks": examples}


@app.post("/feedback/analyze", tags=["Feedback"],
          dependencies=[Depends(require_admin)],
          responses={500: {"description": "Operation failed"}})
async def analyze_feedback(request: FeedbackAnalyzeRequest):
    """
    Batch feedback analysis — identify misclassified utterances from stored
    feedback and optionally patch them back into the intent dataset.

    Workflow:
    1. Pull feedback entries matching the supplied filters.
    2. For each unhelpful or low-rated entry that carries a suggested_intent,
       add the original user_message as a new training pattern for that intent
       (deduplication is automatic).
    3. If apply=true, overwrite data/cavsu_intents.json (a timestamped backup
       is created first) and rebuild the SQLite intent database.
       If apply=false (default), write a preview file instead so you can
       review changes before committing.

    Returns a summary of how many patterns were added per intent.
    """
    entries = chat_logger.get_feedback_entries(
        limit=request.limit,
        helpful=request.helpful,
        user_id=request.user_id,
        session_id=request.session_id,
        intent=request.intent,
        min_rating=request.min_rating,
        max_rating=request.max_rating,
    )

    if not entries:
        return {
            "status": "no_data",
            "message": "No feedback entries matched the supplied filters.",
            "filters": request.model_dump(),
            "patterns_added": 0,
            "by_intent": {}
        }

    # Resolve paths relative to the project root (two levels up from api/)
    root = Path(__file__).resolve().parents[1]
    intents_path = root / "data" / "cavsu_intents.json"
    db_path = root / "data" / "cavsu_intents.db"

    if not intents_path.exists():
        raise HTTPException(status_code=500, detail="Intent dataset unavailable")

    raw = await asyncio.to_thread(intents_path.read_text, encoding="utf-8")
    intents_doc = json.loads(raw)

    intent_map: Dict[str, Any] = {i["tag"]: i for i in intents_doc["intents"]}
    existing_patterns: Dict[str, set] = {
        tag: set(i.get("patterns", [])) for tag, i in intent_map.items()
    }
    additions = _extract_new_patterns(entries, intent_map, existing_patterns)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result: Dict[str, Any] = {
        "run_id": run_id,
        "entries_analyzed": len(entries),
        "patterns_added": sum(len(v) for v in additions.values()),
        "by_intent": dict(sorted(additions.items())),
        "apply": request.apply,
    }

    if not additions:
        result["status"] = "no_changes"
        result["message"] = "No new patterns identified from the filtered feedback."
        return result

    if request.apply:
        backup_path = root / "data" / f"cavsu_intents.backup_{run_id}.json"
        original_raw = await asyncio.to_thread(intents_path.read_text, encoding="utf-8")
        await asyncio.to_thread(backup_path.write_text, original_raw, encoding="utf-8")
        updated = json.dumps(intents_doc, indent=2, ensure_ascii=False)
        await asyncio.to_thread(intents_path.write_text, updated, encoding="utf-8")

        # Rebuild the SQLite intent database
        try:
            import sys
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from intents_db import create_intents_database
            await asyncio.to_thread(
                create_intents_database,
                json_path=str(intents_path),
                db_path=str(db_path),
                recreate=True,
            )
            result["db_rebuilt"] = True
        except Exception as exc:
            result["db_rebuilt"] = False
            result["db_error"] = str(exc)

        result["status"] = "applied"
        result["restart_api_required"] = True
    else:
        preview_path = root / "data" / f"cavsu_intents_preview_{run_id}.json"
        preview_text = json.dumps(intents_doc, indent=2, ensure_ascii=False)
        await asyncio.to_thread(preview_path.write_text, preview_text, encoding="utf-8")
        result["status"] = "preview"
        result["message"] = "Dry-run complete. Set apply=true to commit changes."

    return result


# ============================================================================
# Topic recommendation (seasonal homepage cards)
# ============================================================================

@app.get("/topics/recommended", tags=["Topics"])
async def get_recommended_topics():
    """Return today's recommended topic tags, ranked.

    Filters against the active intent set so the homepage never recommends
    an intent that wouldn't actually answer. The frontend turns each tag
    into a TopicCard using its own catalog.
    """
    try:
        intents = chatbot.get_all_intents() if hasattr(chatbot, "get_all_intents") else []
    except Exception:
        intents = []
    return _recommend_topics(available_tags=intents)


# ============================================================================
# Admin: candidate intent sanitation (content moderation + training safety)
# ============================================================================

class CandidateIntent(BaseModel):
    """Payload for POST /admin/intents/sanitize."""
    tag: str
    patterns: List[str]
    responses: List[str]


def _predict_proba_dict(text: str) -> Dict[str, float]:
    """Run an arbitrary string through the loaded NB classifier and return
    a {tag: probability} map for collision detection."""
    nb = getattr(chatbot, "nb_model", None)
    if nb is None or not hasattr(nb, "pipeline"):
        return {}
    try:
        cleaned = nb._preprocess(text)
        proba = nb.pipeline.predict_proba([cleaned])[0]
        classes = list(nb.pipeline.classes_)
        return {tag: float(p) for tag, p in zip(classes, proba)}
    except Exception:
        return {}


class PinRequest(BaseModel):
    pin: str

@app.post("/admin/verify", tags=["Admin"])
async def verify_admin_pin(body: PinRequest, request: Request):
    """Verify dashboard access PIN (rate-limited)."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)
    if not DASHBOARD_PIN:
        raise HTTPException(503, "Admin access not configured")
    if body.pin != DASHBOARD_PIN:
        _record_attempt(client_ip)
        raise HTTPException(401, "Invalid PIN")
    return {"status": "ok"}


@app.post("/admin/intents/sanitize", tags=["Admin"], dependencies=[Depends(require_admin)])
async def sanitize_intent(body: CandidateIntent):
    """Pre-flight checks before onboarding a new intent.

    Returns a structured report listing every encoding issue, pattern
    duplicate, response too-short/too-long, profanity hit, and classifier
    collision (cases where the *current* model would have confidently sent
    one of the new patterns to a different intent — that's a regression
    risk).
    """
    try:
        from intents_db import load_intents as _load_intents
        existing = _load_intents()
    except Exception:
        existing = []

    candidate = body.model_dump()
    report = _sanitize_candidate_intent(
        candidate,
        existing_intents=existing,
        classifier_predict_proba=_predict_proba_dict,
    )
    return report.to_dict()


@app.post("/admin/intents", tags=["Admin"], dependencies=[Depends(require_admin)])
async def create_intent(body: CandidateIntent, force: bool = False):
    """Apply a candidate intent: append to cavsu_intents.json, rebuild the
    SQLite DB, and ask the caller to re-run training to activate it in the
    live model.

    Sanitation is re-run server-side; we refuse on any *error* finding
    unless ?force=true is passed (so the admin can still ship a known
    collision intentionally — e.g. when later renaming an existing intent).
    Warnings don't block. The model is NOT retrained synchronously — that
    would block the API for minutes — the response tells the caller to run
    `python training/train_naive_bayes.py`.
    """
    import json as _json
    from pathlib import Path as _Path
    try:
        from intents_db import load_intents as _load_intents
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Intent database unavailable")

    existing = _load_intents()
    candidate = body.model_dump()
    report = _sanitize_candidate_intent(
        candidate, existing_intents=existing,
        classifier_predict_proba=_predict_proba_dict,
    )
    if report.has_errors and not force:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Sanitation errors block the write. Fix them or retry with ?force=true.",
                "report": report.to_dict(),
            },
        )

    # Write directly to DB (source of truth)
    try:
        import sqlite3
        db_path = _Path("data/cavsu_intents.db")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO intents (tag, description, active) VALUES (?, ?, ?)",
            (body.tag, "", 1),
        )
        intent_id = cur.execute(
            "SELECT id FROM intents WHERE tag = ?", (body.tag,)
        ).fetchone()["id"]
        for pattern in body.patterns:
            cur.execute(
                "INSERT INTO patterns (intent_id, pattern_text) VALUES (?, ?)",
                (intent_id, pattern),
            )
        for response in body.responses:
            cur.execute(
                "INSERT INTO responses (intent_id, response_text) VALUES (?, ?)",
                (intent_id, response),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        _logger.error("DB write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save intent")

    # Also sync to JSON as a backup export
    json_path = _Path("data/cavsu_intents.json")
    try:
        if json_path.exists():
            raw = _json.loads(json_path.read_text(encoding="utf-8"))
        else:
            raw = {"intents": []}
        raw.setdefault("intents", []).append({
            "tag": body.tag,
            "patterns": body.patterns,
            "responses": body.responses,
        })
        tmp = json_path.with_suffix(json_path.suffix + ".tmp")
        tmp.write_text(_json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(json_path)
    except Exception:
        pass  # JSON backup is best-effort; DB is the source of truth

    return {
        "status": "saved",
        "tag": body.tag,
        "warnings": report.has_warnings,
        "report": report.to_dict(),
        "next_step": (
            "Run `python training/train_naive_bayes.py` and restart the API "
            "to activate this intent for routing."
        ),
    }


# ============================================================================
# Error Handlers
# ============================================================================

# Safe error messages by status code — never leak internals to the client.
_SAFE_ERROR_MESSAGES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    422: "Validation error",
    429: "Too many requests",
    500: "Internal server error",
    503: "Service unavailable",
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return generic error messages. Internal details are logged server-side only."""
    # Log full detail for debugging, but never send it to the client
    _logger.warning("HTTP %s on %s: %s", exc.status_code, request.url.path, exc.detail)
    safe_message = _SAFE_ERROR_MESSAGES.get(exc.status_code, "Request failed")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "message": safe_message},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: log the real error, return a generic 500."""
    _logger.exception("Unhandled exception on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": True, "message": "Internal server error"},
    )

# ============================================================================
# Run Server
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=False
    )

