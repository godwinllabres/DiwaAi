"""
CvSU Chatbot REST API
FastAPI-based endpoint for integration with web applications
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime

import joblib
import nltk
from nltk.stem import WordNetLemmatizer

# Import logger
from api.logger import ChatLogger
# Import hybrid chatbot
from hybrid_chatbot import HybridChatbot

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
You are Sevi, the CvSU Virtual Assistant - a helpful, friendly guide for Cavite State University.

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
app = FastAPI(
    title="CvSU Chatbot API",
    description="REST API for Sevi, the CvSU Virtual Assistant",
    version="1.0.0"
)

# Enable CORS for web app integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the web interface as static files
app.mount("/web", StaticFiles(directory="web"), name="web")

# ============================================================================
# Request/Response Models
# ============================================================================
class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None

# Campus map (48 official locations) — see api/campus_places.py
from api.campus_places import (
    MapData,
    PlaceMeta,
    resolve_map_data as _resolve_map_data,
    build_place_meta as _build_place_meta,
    campus_map_payload as _campus_map_payload,
    has_place as _has_place,
)

# ============================================================================
# ChatResponse v2 envelope — typed cards + provenance
# Mirrors SeviWeb app/lib/api.ts. The flat v1 shape (response / map_data) is
# preserved as compat aliases so the Python web/ UI and Postman keep working.
# ============================================================================
from typing import Literal, Union

ResponseSource = Literal[
    "naive_bayes", "neural_network", "llm_local", "llm_claude",
    "ais_mcp", "fallback", "refusal",
]
RefusalReason = Literal["nonsense", "out_of_scope", "prohibited"]
DisplayHint = Literal["default", "map_first", "card_first", "text_only"]


class DirectoryCard(BaseModel):
    kind: Literal["directory"] = "directory"
    office: str
    location: Optional[str] = None
    place_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[str] = None


class MapCard(BaseModel):
    kind: Literal["map"] = "map"
    place_id: str
    label: str
    default_open: Optional[bool] = None


class TableColumn(BaseModel):
    key: str
    label: str
    align: Optional[Literal["left", "right", "center"]] = None


class TableCard(BaseModel):
    kind: Literal["table"] = "table"
    title: str
    columns: List[TableColumn]
    rows: List[dict]
    footer: Optional[str] = None
    total_rows: Optional[int] = None


# DvCard is defined in api.ts for the AIS surface; the CvSU chatbot never
# emits one, so it is intentionally omitted here (add if AIS routes land).
ChatCard = Union[DirectoryCard, MapCard, TableCard]


class ChatResponse(BaseModel):
    # Identity
    message_id: Optional[int] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    # Content
    text: str
    summary: Optional[str] = None

    # Classification + provenance
    intent: str
    confidence: float
    source: ResponseSource = "fallback"
    refusal_reason: Optional[RefusalReason] = None

    # Structured attachments
    cards: List[ChatCard] = []
    context: Optional[dict] = None
    suggestions: List[str] = []

    # Layout hint for the renderer
    display_hint: DisplayHint = "default"

    # ── v1 compat aliases (kept so web/ UI + Postman still parse) ──
    response: Optional[str] = None
    map_data: Optional[MapData] = None


# model_used strings from hybrid_chatbot.predict() → v2 source + refusal_reason.
def _source_from_model_used(model_used: str) -> tuple[ResponseSource, Optional[RefusalReason]]:
    mu = (model_used or "").lower()
    if mu.startswith("naive bayes"):
        return "naive_bayes", None
    if mu.startswith("neural network"):
        return "neural_network", None
    if mu.startswith("claude"):
        # "Claude LLM" answered, or "Claude (out-of-scope)" refused
        return ("refusal", "out_of_scope") if "out-of-scope" in mu else ("llm_claude", None)
    if mu.startswith("local llm") or mu.startswith("ollama"):
        return ("refusal", "out_of_scope") if "out-of-scope" in mu else ("llm_local", None)
    if mu.startswith("scopegate"):
        # "ScopeGate (nonsense)" / "(out_of_scope)" / "(prohibited)"
        for reason in ("nonsense", "prohibited", "out_of_scope"):
            if reason in mu:
                return "refusal", reason  # type: ignore[return-value]
        return "refusal", "out_of_scope"
    # "context" (multi-turn college pick), "Fallback", anything else
    return "fallback", None


# Intent tags that are themselves refusals — regardless of which tier produced
# them (the NN can classify a query straight into `out_of_scope`). Maps the
# intent tag to the refusal_reason the UI renders.
_REFUSAL_INTENTS = {
    "out_of_scope": "out_of_scope",
    "prohibited": "prohibited",
    "nonsense": "nonsense",
}

# Contact-style intents render as a structured directory (object list of
# offices with email/phone) instead of a wall of text. Verified official
# contact points — same source as the responses_map contact copy.
_CONTACT_INTENTS = {"contact_info", "directory"}

_CVSU_DIRECTORY: List[dict] = [
    {
        "office": "Office of Student Affairs and Services (OSAS) — University Registrar",
        "location": "CvSU Main, Don Severino delas Alas Campus, Indang, Cavite",
        "email": "osasmain.guidance@cvsu.edu.ph",
        "place_id": "osas",
    },
    {
        "office": "Office of Admissions",
        "location": "CvSU Main, Indang, Cavite",
        "email": "admission@cvsu.edu.ph",
        "place_id": "admin",
    },
    {
        "office": "Office of the VP for Research and Extension (OVPRE)",
        "location": "CvSU Main, Indang, Cavite",
        "phone": "(046) 862-0850 / +63 998 937 2020",
    },
]


def _directory_cards() -> "List[ChatCard]":
    return [DirectoryCard(**entry) for entry in _CVSU_DIRECTORY]


def _build_chat_response(
    *, text: str, intent: str, confidence: float, model_used: str,
    message: str, user_id, session_id, message_id,
) -> ChatResponse:
    """Assemble the v2 envelope from the chatbot's flat tuple."""
    source, refusal_reason = _source_from_model_used(model_used)

    # A refusal *intent* is a refusal even when a classifier tier answered it.
    if intent in _REFUSAL_INTENTS:
        source = "refusal"
        refusal_reason = _REFUSAL_INTENTS[intent]  # type: ignore[assignment]

    cards: List[ChatCard] = []
    display_hint: DisplayHint = "default"
    md = None

    if intent in _CONTACT_INTENTS:
        # Contact/directory → structured object list, not a routing map.
        cards.extend(_directory_cards())
        display_hint = "card_first"
    else:
        md = _resolve_map_data(message, intent)
        if md is not None:
            # Attach the map as a collapsed card. display_hint stays "default":
            # the UI shows a coachmark on first trigger rather than auto-opening
            # the map modal.
            cards.append(MapCard(place_id=md.place_id, label=md.label))

    return ChatResponse(
        message_id=message_id,
        user_id=user_id,
        session_id=session_id,
        text=text,
        intent=intent,
        confidence=confidence,
        source=source,
        refusal_reason=refusal_reason,
        cards=cards,
        suggestions=[],
        display_hint=display_hint,
        # compat
        response=text,
        map_data=md,
    )


class FeedbackRequest(BaseModel):
    message_id: Optional[int] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    intent: Optional[str] = None
    rating: Optional[int] = None          # 1–5 star quality score
    helpful: Optional[bool] = None        # thumbs up / thumbs down
    comment: Optional[str] = None         # freeform note or correction
    suggested_intent: Optional[str] = None  # user-supplied intent correction

class IntentInfo(BaseModel):
    tag: str
    pattern_count: int
    response_count: int
    sample_patterns: List[str]

class ModelInfo(BaseModel):
    model_name: str
    accuracy: float
    total_intents: int
    total_patterns: int
    model_size_kb: float
    system_instructions: str

# Note: HybridChatbot is now imported from hybrid_chatbot.py
# It combines Naive Bayes (fast) + Neural Network (accurate)

# ============================================================================
# COLLEGE COURSES DATA — for multi-turn selection conversation
# ============================================================================

COLLEGE_COURSES = {
    1: {
        "name": "College of Agriculture, Food, Environment and Natural Resources (CAFENR)",
        "programs": [
            "BS Agriculture (majors: Animal Science, Crop Science, Agricultural Economics, Soil Science)",
            "BS Agricultural Engineering",
            "BS Environmental Science",
            "BS Forestry",
            "BS Food Technology",
            "BS Agribusiness",
        ],
        "contact": "cafenr@cvsu.edu.ph",
    },
    2: {
        "name": "College of Arts and Sciences (CAS)",
        "programs": [
            "BA Communication / Journalism",
            "BA Psychology",
            "BS Biology",
            "BS Mathematics",
            "BS Statistics",
            "BS Physical Science",
            "BS Social Work",
            "BA Political Science",
            "BA Sociology",
        ],
        "contact": "cas@cvsu.edu.ph",
    },
    3: {
        "name": "College of Criminal Justice (CCJ)",
        "programs": [
            "BS Criminology",
        ],
        "contact": "ccj@cvsu.edu.ph",
    },
    4: {
        "name": "College of Economics, Management and Development Studies (CEMDS)",
        "programs": [
            "BS Business Administration (majors: Marketing, Financial Management, Human Resource Development)",
            "BS Entrepreneurship",
            "BS Public Administration",
            "BS Office Administration",
            "BS Economics",
            "BS Accountancy",
            "BS Management Accounting",
        ],
        "contact": "cemds@cvsu.edu.ph",
    },
    5: {
        "name": "College of Education (CED)",
        "programs": [
            "Bachelor of Elementary Education (BEEd)",
            "Bachelor of Secondary Education (BSEd) — majors: Math, English, Filipino, Science, Social Studies, TLE, MAPEH",
            "Bachelor of Early Childhood Education (BECEd)",
            "Bachelor of Special Needs Education (BSNEd)",
            "Bachelor of Physical Education (BPEd)",
            "Bachelor of Technical-Vocational Teacher Education (BTVTEd)",
        ],
        "contact": "ced@cvsu.edu.ph",
    },
    6: {
        "name": "College of Engineering and Information Technology (CEIT)",
        "programs": [
            "BS Computer Science (BSCS)",
            "BS Information Technology (BSIT)",
            "BS Computer Engineering (BSCpE)",
            "BS Electronics Engineering (BSECE)",
            "BS Civil Engineering (BSCE)",
            "BS Mechanical Engineering (BSME)",
            "BS Electrical Engineering (BSEE)",
            "BS Chemical Engineering (BSChE)",
        ],
        "contact": "ceit@cvsu.edu.ph",
    },
    7: {
        "name": "College of Medicine (COM) — established 2022",
        "programs": [
            "Doctor of Medicine (MD)",
        ],
        "contact": "com@cvsu.edu.ph",
        "note": "Requires a pre-med undergraduate degree. NMAT score is required for admission.",
    },
    8: {
        "name": "College of Nursing (CON)",
        "programs": [
            "BS Nursing (BSN)",
        ],
        "contact": "con@cvsu.edu.ph",
    },
    9: {
        "name": "College of Sports, Physical Education and Recreation (CSPER)",
        "programs": [
            "BS Exercise and Sports Science",
            "BS Physical Education",
            "BS Recreation Management",
        ],
        "contact": "csper@cvsu.edu.ph",
    },
    10: {
        "name": "College of Veterinary Medicine and Biomedical Sciences (CVMBS)",
        "programs": [
            "Doctor of Veterinary Medicine (DVM)",
            "BS Veterinary Technology",
            "BS Biomedical Sciences",
        ],
        "contact": "cvmbs@cvsu.edu.ph",
    },
    11: {
        "name": "College of Tourism and Hospitality Management (CTHM) — newest, 2025",
        "programs": [
            "BS Tourism Management",
            "BS Hospitality Management",
            "BS Travel Management",
        ],
        "contact": "cthm@cvsu.edu.ph",
    },
}

def _build_college_response(num: int) -> str:
    c = COLLEGE_COURSES[num]
    lines = [f"📚 {c['name']}", "", "PROGRAMS OFFERED:"]
    lines += [f"  • {p}" for p in c["programs"]]
    if "note" in c:
        lines += ["", f"NOTE: {c['note']}"]
    lines += ["", f"Contact: {c['contact']}"]
    lines += ["", "Type another number (1–11) to explore a different college, or ask me anything else!"]
    return "\n".join(lines)

# Per-user conversation context — tracks pending multi-turn states
# Structure: { user_id: { "pending": "college_selection" } }
conversation_contexts: dict = {}

# ============================================================================
# Initialize Chatbot and Logger
# ============================================================================
MODEL_DIR = "models"

# Initialize Hybrid Chatbot (Naive Bayes + Neural Network)
chatbot = HybridChatbot(
    model_dir=MODEL_DIR,
    responses_path=os.path.join(MODEL_DIR, "responses_map.json"),
    intents_db_path="data/cavsu_intents.db",
    intents_json_path="data/cavsu_intents.json"
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
        "service": "CvSU Chatbot API",
        "assistant_name": "Sevi",
        "status": "active",
        "version": "1.0.0",
        "documentation": "/docs"
    }

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model_loaded": chatbot.nb_model is not None,
        "intents_available": chatbot.total_intents
    }

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat_endpoint(request: ChatRequest):
    """
    Send a message to the chatbot (Hierarchical Hybrid Model).

    Uses Naive Bayes first (fast), falls back to Neural Network if uncertain.
    Supports multi-turn college selection: after listing colleges, a number
    reply (1-11) returns that college's programs without going through the
    intent classifier.

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

    user_id = request.user_id or "anonymous"
    start_time = time.time()

    # ------------------------------------------------------------------
    # Multi-turn context: intercept numbered selection after college list
    # ------------------------------------------------------------------
    ctx = conversation_contexts.get(user_id, {})
    if ctx.get("pending") == "college_selection":
        num_match = re.fullmatch(r"\s*(\d+)\s*", request.message.strip())
        if num_match:
            num = int(num_match.group(1))
            if 1 <= num <= 11:
                # Keep the pending state so the user can keep browsing colleges
                college_response = _build_college_response(num)
                response_time_ms = (time.time() - start_time) * 1000
                message_id = chat_logger.log_chat(
                    user_id=user_id,
                    user_message=request.message,
                    bot_response=college_response,
                    intent="courses_offered",
                    confidence=1.0,
                    model_used="context",
                    session_id=request.session_id,
                    response_time_ms=response_time_ms,
                )
                return ChatResponse(
                    text=college_response,
                    response=college_response,
                    intent="courses_offered",
                    confidence=1.0,
                    source="fallback",
                    user_id=request.user_id,
                    session_id=request.session_id,
                    message_id=message_id,
                )
            else:
                # Number out of range — gently re-prompt, keep state
                reprompt = "Please enter a number between 1 and 11 to select a college, or ask me something else!"
                return ChatResponse(
                    text=reprompt,
                    response=reprompt,
                    intent="courses_offered",
                    confidence=1.0,
                    source="fallback",
                    user_id=request.user_id,
                    session_id=request.session_id,
                    message_id=None,
                )
        else:
            # Non-numeric reply — clear the pending state and fall through normally
            conversation_contexts.pop(user_id, None)

    # ------------------------------------------------------------------
    # Normal chatbot processing
    # ------------------------------------------------------------------
    intent, response, confidence, model_used = chatbot.chat(
        request.message,
        user_id=request.user_id,
        session_id=request.session_id
    )

    response_time_ms = (time.time() - start_time) * 1000

    # If the bot just listed the colleges, set the selection context
    if intent == "courses_offered":
        conversation_contexts[user_id] = {"pending": "college_selection"}

    # Log the chat
    message_id = chat_logger.log_chat(
        user_id=user_id,
        user_message=request.message,
        bot_response=response,
        intent=intent,
        confidence=confidence,
        model_used=model_used,
        session_id=request.session_id,
        response_time_ms=response_time_ms
    )

    return _build_chat_response(
        text=response,
        intent=intent,
        confidence=confidence,
        model_used=model_used,
        message=request.message,
        user_id=request.user_id,
        session_id=request.session_id,
        message_id=message_id,
    )

# ============================================================================
# Campus Map Endpoints
# ============================================================================

@app.get("/map", tags=["Map"])
async def get_campus_map():
    """Return canonical campus places, gate position, and the SVG viewBox.

    The web UI renders the static schematic from this payload — no Google
    Maps dependency. Edit `api/campus_places.py` to update labels, geometry,
    walk times, or directions without changing the frontend.
    """
    return _campus_map_payload()

@app.get("/map/{place_id}", response_model=PlaceMeta, tags=["Map"],
         responses={404: {"description": "Place not found"}})
async def get_place(place_id: str):
    """Return canonical metadata for a single campus place."""
    if not _has_place(place_id):
        raise HTTPException(status_code=404, detail=f"Place '{place_id}' not found")
    return _build_place_meta(place_id)

@app.get("/intents", tags=["Intents"])
async def get_intents():
    """Get list of all available intent categories."""
    intents = chatbot.get_all_intents()
    return {
        "total_intents": len(intents),
        "intents": intents
    }

@app.get("/intents/{intent_tag}", tags=["Intents"])
async def get_intent(intent_tag: str):
    """Get details about a specific intent."""
    details = chatbot.get_intent_details(intent_tag)

    if not details:
        raise HTTPException(status_code=404, detail=f"Intent '{intent_tag}' not found")

    return details

@app.get("/model/info", response_model=ModelInfo, tags=["Model"])
async def model_info():
    """Get information about the trained model."""
    return ModelInfo(
        model_name=chatbot.model_name,
        accuracy=chatbot.accuracy,
        total_intents=chatbot.total_intents,
        total_patterns=chatbot.total_patterns,
        model_size_kb=chatbot.model_size_kb,
        system_instructions=chatbot.system_instructions
    )

@app.get("/model/instructions", tags=["Model"])
async def get_instructions():
    """Get agent system instructions."""
    return {
        "instructions": chatbot.system_instructions,
        "version": "1.0.0"
    }

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

@app.post("/batch", tags=["Chat"])
async def batch_chat(requests: List[ChatRequest]):
    """
    Process multiple chat requests in batch.

    Useful for integration with web apps that need multiple responses.
    """
    results = []
    for request in requests:
        start_time = time.time()
        intent, response, confidence, model_used = chatbot.chat(request.message)
        response_time_ms = (time.time() - start_time) * 1000

        # Log each message
        chat_logger.log_chat(
            user_id=request.user_id or "anonymous",
            user_message=request.message,
            bot_response=response,
            intent=intent,
            confidence=confidence,
            model_used=model_used,
            session_id=request.session_id,
            response_time_ms=response_time_ms
        )

        results.append(_build_chat_response(
            text=response,
            intent=intent,
            confidence=confidence,
            model_used=model_used,
            message=request.message,
            user_id=request.user_id,
            session_id=request.session_id,
            message_id=None,
        ))
    return {"count": len(results), "results": results}

# ============================================================================
# Logging Endpoints
# ============================================================================

@app.get("/logs/user/{user_id}", tags=["Logging"])
async def get_user_logs(user_id: str, limit: int = 50):
    """Get chat history for a specific user"""
    history = chat_logger.get_user_history(user_id, limit)
    return {
        "user_id": user_id,
        "message_count": len(history),
        "messages": history
    }

@app.get("/logs/session/{session_id}", tags=["Logging"])
async def get_session_logs(session_id: str):
    """Get all messages in a specific session"""
    history = chat_logger.get_session_history(session_id)
    return {
        "session_id": session_id,
        "message_count": len(history),
        "messages": history
    }

@app.get("/logs/intents", tags=["Logging"])
async def get_intent_logs():
    """Get statistics for all intents"""
    stats = chat_logger.get_intent_statistics()
    return {
        "total_intents": len(stats),
        "intents": stats
    }

@app.get("/logs/sessions", tags=["Logging"])
async def get_sessions_list(user_id: Optional[str] = None, limit: int = 20):
    """Get list of sessions"""
    sessions = chat_logger.get_session_list(user_id, limit)
    return {
        "user_id": user_id,
        "session_count": len(sessions),
        "sessions": sessions
    }

@app.get("/logs/today", tags=["Logging"])
async def get_today_statistics():
    """Get today's chat statistics"""
    stats = chat_logger.get_today_stats()
    return stats

@app.get("/logs/search", tags=["Logging"])
async def search_logs(query: str, limit: int = 20):
    """Search logs by message content"""
    results = chat_logger.search_logs(query, limit)
    return {
        "query": query,
        "results_count": len(results),
        "results": results
    }

@app.get("/logs/fallbacks", tags=["Logging"])
async def get_fallback_logs(limit: int = 100):
    """Get recent fallback user utterances for learning and analysis."""
    fallback_examples = chat_logger.get_fallback_examples(limit)
    return {
        "fallback_count": len(fallback_examples),
        "fallbacks": fallback_examples
    }

# ============================================================================
# Feedback Endpoints
# ============================================================================

@app.post("/feedback", tags=["Feedback"])
async def submit_feedback(request: FeedbackRequest):
    """
    Submit user feedback for a specific bot response.

    Body fields:
        message_id (int, optional): ID of the chat_messages row being rated
        rating (int, optional): Quality score 1–5
        helpful (bool, optional): True = thumbs up, False = thumbs down
        comment (str, optional): Freeform note or correction
        suggested_intent (str, optional): User-supplied intent correction
        user_id / session_id (str, optional): For cross-referencing
        intent (str, optional): Intent tag of the rated response
    """
    if request.rating is not None and not (1 <= request.rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be between 1 and 5")
    if request.rating is None and request.helpful is None and not request.comment:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: rating, helpful, or comment"
        )

    feedback_id = chat_logger.log_feedback(
        message_id=request.message_id,
        user_id=request.user_id,
        session_id=request.session_id,
        intent=request.intent,
        rating=request.rating,
        helpful=request.helpful,
        comment=request.comment,
        suggested_intent=request.suggested_intent
    )

    if feedback_id is None:
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    return {"status": "ok", "feedback_id": feedback_id}


@app.get("/feedback/stats", tags=["Feedback"])
async def get_feedback_stats():
    """
    Aggregate feedback statistics.

    Returns:
        total_feedback: Total number of feedback entries
        avg_rating: Overall average star rating
        helpful_pct: Percentage of 'helpful' responses
        by_intent: Per-intent breakdown (count, avg_rating, helpful_pct, unhelpful_count)
        low_rated_intents: Worst-performing intents (min 3 ratings)
        recent_comments: Last 10 freeform comments with metadata
    """
    return chat_logger.get_feedback_stats()


@app.get("/feedback", tags=["Feedback"])
async def get_feedback(
    limit: int = 100,
    helpful: Optional[bool] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    intent: Optional[str] = None,
    min_rating: Optional[int] = None,
    max_rating: Optional[int] = None
):
    """Retrieve feedback records, optionally joined with the source message."""
    limit = max(1, min(limit, 5000))
    entries = chat_logger.get_feedback_entries(
        limit=limit,
        helpful=helpful,
        user_id=user_id,
        session_id=session_id,
        intent=intent,
        min_rating=min_rating,
        max_rating=max_rating
    )
    return {
        "count": len(entries),
        "feedback": entries
    }

@app.post("/logs/export/{user_id}", tags=["Logging"])
async def export_user_logs(user_id: str):
    """Export all data for a user as JSON file"""
    filepath = chat_logger.export_user_data(user_id)
    if filepath:
        return {
            "status": "success",
            "user_id": user_id,
            "filepath": filepath,
            "message": f"User data exported to {filepath}"
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to export user data")

@app.delete("/logs/cleanup", tags=["Logging"])
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
# Error Handlers
# ============================================================================
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler.

    Must return a Response — returning a bare dict makes Starlette try to
    call it as an ASGI app ('dict' object is not callable → 500), which
    turned every legitimate 400 (e.g. empty /chat message) into a 500.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status_code": exc.status_code,
            "message": exc.detail,
        },
    )

# ============================================================================
# Run Server
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
