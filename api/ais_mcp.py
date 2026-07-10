"""AIS MCP bridge for Diwa.

Selectively routes finance/accounting queries to the CvSU AIS MCP server.
Anything that doesn't look like an AIS query returns None and is handled by
Diwa's normal NLU pipeline.

Configuration via env vars:
    AIS_MCP_URL   — SSE endpoint, e.g. http://127.0.0.1:8765/sse (default)
    AIS_MCP_ENABLED — "0" disables routing entirely (default "1")

Identity note: the MCP server runs with a single OAuth identity attached to
its tokens.json. Every Diwa user shares that identity — segregation of
duties does NOT apply to MCP-routed queries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import AsyncExitStack
from typing import Any, Optional

import httpx

# mcp SDK is optional — if missing, all calls return None and Diwa falls
# back to its normal pipeline. Install with: pip install "mcp[sse]"
try:
	from mcp import ClientSession
	from mcp.client.sse import sse_client
	_MCP_AVAILABLE = True
except ImportError:
	_MCP_AVAILABLE = False

# anthropic SDK is optional — used only by the opt-in LLM router fallback.
try:
	import anthropic
	_ANTHROPIC_AVAILABLE = True
except ImportError:
	_ANTHROPIC_AVAILABLE = False


_logger = logging.getLogger("diwa.ais_mcp")

_MCP_URL = os.environ.get("AIS_MCP_URL", "http://127.0.0.1:8765/sse")
_ENABLED = os.environ.get("AIS_MCP_ENABLED", "1") == "1"
# Hard ceiling on any single MCP call. Caps the worst-case time we make a
# user wait when the MCP server hangs.
_CALL_TIMEOUT_SECONDS = float(os.environ.get("AIS_MCP_TIMEOUT_SECONDS", "8.0"))
# Frontend uses this to deep-link DV names back to the Desk form. Override
# in production if Desk is served from a different host (e.g. behind nginx).
_DESK_URL = os.environ.get("AIS_DESK_URL", "http://accounting.localhost:8002").rstrip("/")
# Opt-in: when regex misses on an AIS-shaped message, ask an LLM to extract
# {tool, args}. Provider chosen by LLM_PROVIDER env (ollama|claude); when
# unset, prefers Ollama (local, free) then falls back to Anthropic.
_LLM_ROUTER_ENABLED = os.environ.get("AIS_MCP_LLM_ROUTER", "0") == "1"

# Ollama config — uses the OpenAI-compatible /v1/chat/completions endpoint
# so we get structured tool_calls back. Any chat model that supports tool
# calling works (qwen2.5+, qwen3, llama3.1+, etc.).
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


# Router models are read PER CALL so the admin LLM toggle (which rewrites
# OLLAMA_MODEL / CLAUDE_MODEL) steers this router. An explicit AIS_MCP_*
# override still wins when set.
def _router_ollama_model() -> str:
	return os.environ.get("AIS_MCP_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen3:8b"


def _router_anthropic_model() -> str:
	return (
		os.environ.get("AIS_MCP_LLM_MODEL")
		or os.environ.get("CLAUDE_MODEL")
		or "claude-haiku-4-5-20251001"
	)

# Conversation context — keeps the last DV/UACS/report mentioned per session
# so the user can say "what's its status" without retyping the DV name.
_CONTEXT_TTL_SECONDS = float(os.environ.get("AIS_MCP_CONTEXT_TTL", "600"))  # 10 min default

# Circuit breaker — after N consecutive transport failures, short-circuit
# new MCP calls for COOLDOWN_SECONDS so a downed MCP server doesn't cost
# every chat the full timeout (~8s).
_BREAKER_THRESHOLD       = int(os.environ.get("AIS_MCP_BREAKER_THRESHOLD", "3"))
_BREAKER_COOLDOWN_SECONDS = float(os.environ.get("AIS_MCP_BREAKER_COOLDOWN", "30"))

# Session pooling — reuse the SSE stream + ClientSession across requests
# to skip the ~30ms handshake per call. Set to "0" to disable.
_SESSION_POOL_ENABLED = os.environ.get("AIS_MCP_POOL_SESSION", "1") == "1"


def desk_url(dv_name: str) -> str:
	"""Build a Desk deep-link for a DV record."""
	return f"{_DESK_URL}/app/ais-disbursement-voucher/{dv_name}"


def bir_2307_desk_url(name: str) -> str:
	"""Build a Desk deep-link for a BIR 2307 record."""
	return f"{_DESK_URL}/app/ais-bir-2307/{name}"


# ── conversation context ─────────────────────────────────────────────────
# Tiny in-memory cache so the user can say "what's its status" after
# "show DV-2026-00001" without retyping. Keyed by session_id; entries
# older than _CONTEXT_TTL_SECONDS are evicted on read. Process-local —
# multi-worker deployments will need an external store.

_session_context: dict[str, dict[str, Any]] = {}


def _get_context(session_id: Optional[str]) -> dict[str, Any]:
	if not session_id:
		return {}
	ctx = _session_context.get(session_id)
	if not ctx:
		return {}
	if time.time() - ctx.get("_ts", 0.0) > _CONTEXT_TTL_SECONDS:
		_session_context.pop(session_id, None)
		return {}
	return ctx


def _set_context(session_id: Optional[str], **updates: Any) -> None:
	if not session_id:
		return
	ctx = _session_context.setdefault(session_id, {})
	ctx.update(updates)
	ctx["_ts"] = time.time()


def _public_context(session_id: Optional[str]) -> Optional[dict[str, Any]]:
	"""Strip internal fields (_ts) before sending to the client."""
	ctx = _get_context(session_id)
	if not ctx:
		return None
	out = {k: v for k, v in ctx.items() if not k.startswith("_")}
	return out or None


# ── intent router ────────────────────────────────────────────────────────
# Mirrors the regex patterns the in-Desk chat panel uses (accounting/
# accounting_information_system/api/mcp/chat.py:_stub_relay). Keeping them
# in sync avoids surprises: if a query routes to MCP in one surface, it
# should route the same way in the other.

_DV_NAME_RE = re.compile(r"\b(DV-\d{4}-\d+|TRUST-\d{4}-\d+)\b", re.IGNORECASE)
# BIR 2307 records use naming series AIS-2307-YYYY-##### per CLAUDE.md §0.5.
# Also accept "2307-2026-00012" (without the AIS- prefix) since that's how
# users naturally refer to BIR forms.
_BIR_2307_NAME_RE = re.compile(
	r"\b(AIS-)?2307-\d{4}-\d+\b",
	re.IGNORECASE,
)
_REPORT_RE = re.compile(
	r"\b(RAPAL|RAOD-PS|RAOD-MOOE|RAOD-FE|RAOD-CO|RBUD-PS|RBUD-MOOE|RBUD-FE|RBUD-CO|RANCA|FAR\s*1|FAR\s*4)\b",
	re.IGNORECASE,
)
_BUDGET_RE = re.compile(
	r"^\s*(?:budget|balance|check)\s+(allotment|appropriation|nca|ncas)\s+(.+?)\s*$",
	re.IGNORECASE,
)
_UACS_RE = re.compile(
	r"^\s*(?:lookup|uacs|find|search)\s+(funding|funding[_ ]source|pap|location)\s*(.*)\s*$",
	re.IGNORECASE,
)

# Verbs/phrases that, paired with a DV name, mean "fetch that DV" — the
# original gate was just "show/get/open/view"; expanded to cover natural
# questions like "what's the status of DV-…", "tell me about DV-…", etc.
_DV_DETAIL_GATE_RE = re.compile(
	r"\b("
	r"show|get|open|view|fetch|display|"
	r"tell\s+me\s+about|what(?:'s|\s+is)?(?:\s+the)?\s+(?:status|state|details?|info|amount|payee)|"
	r"how\s+much\s+(?:is|for)|"
	r"who(?:'s|\s+is)\s+(?:the\s+)?(?:payee|owner|approver)|"
	r"is\s+\S+\s+(?:approved|pending|closed|posted|released|cancelled)|"
	r"details?\s+(?:of|about|on|for)|"
	r"status\s+of|"
	r"(?:total\s+)?amount\s+(?:of|for)|cost\s+of|"
	r"check"
	r")\b",
	re.IGNORECASE,
)

# Aggregation intent — "total/sum/count/average ... DVs/amounts/disbursements"
# without a specific DV name. Routes to the dv_totals MCP tool with an
# optional group_by extracted from the message ("by fund cluster" etc.).
_AGG_WORDS_RE   = re.compile(r"\b(total|totals|sum|average|count|how\s+(?:much|many))\b", re.IGNORECASE)
_AGG_TARGETS_RE = re.compile(r"\b(dvs?|vouchers?|disburs\w*|amounts?|allotments?|obligations?|spent|spending)\b", re.IGNORECASE)

# "by X" / "per X" group-by extractor. Order matters: longest phrases first
# so "by fiscal year" wins over "by year".
_GROUP_BY_PATTERNS: list[tuple[re.Pattern, str]] = [
	(re.compile(r"\b(?:by|per)\s+fund\s+cluster\b", re.IGNORECASE),  "fund_cluster"),
	(re.compile(r"\b(?:by|per)\s+(?:workflow\s+)?status\b", re.IGNORECASE), "workflow_status"),
	(re.compile(r"\b(?:by|per)\s+payee\b", re.IGNORECASE),           "payee"),
	(re.compile(r"\b(?:by|per)\s+(?:dv\s+)?type\b", re.IGNORECASE),  "dv_type"),
	(re.compile(r"\b(?:by|per)\s+month\b|\bmonthly\b", re.IGNORECASE), "month"),
	(re.compile(r"\b(?:by|per)\s+fiscal\s+year\b", re.IGNORECASE),   "fiscal_year"),
]


def _parse_date_range(text: str) -> dict[str, str]:
	"""Extract `posting_date_from` / `posting_date_to` from natural language.

	Recognized phrases (case-insensitive):
	  • "this month" / "this year" / "this quarter"
	  • "last month" / "last year" / "last N days"
	  • "Q1 [YYYY]" / "Q2" / "Q3" / "Q4" (assumes current year if year omitted)
	  • "in YYYY" / "for YYYY" / "fiscal year YYYY"

	Returns the matching subset of {posting_date_from, posting_date_to} as
	YYYY-MM-DD strings. Empty dict when nothing matches.
	"""
	from datetime import date, timedelta
	low = text.lower()
	today = date.today()
	out: dict[str, str] = {}

	# "in YYYY" / "for YYYY" / "fiscal year YYYY"
	m = re.search(r"\b(?:in|for|fiscal\s+year)\s+(20\d{2})\b", low)
	if m:
		yr = int(m.group(1))
		out["posting_date_from"] = f"{yr}-01-01"
		out["posting_date_to"] = f"{yr}-12-31"
		return out

	# "Q1" / "Q2" / "Q3" / "Q4" (optional year)
	m = re.search(r"\bq([1-4])(?:\s+(20\d{2}))?\b", low)
	if m:
		quarter = int(m.group(1))
		yr = int(m.group(2)) if m.group(2) else today.year
		start_month = (quarter - 1) * 3 + 1
		end_month = start_month + 2
		# Last day of end_month: jump to first of next month, subtract a day.
		next_first = date(yr + (end_month // 12), (end_month % 12) + 1, 1)
		end_day = (next_first - timedelta(days=1)).day
		out["posting_date_from"] = f"{yr}-{start_month:02d}-01"
		out["posting_date_to"] = f"{yr}-{end_month:02d}-{end_day:02d}"
		return out

	if re.search(r"\bthis\s+year\b", low):
		out["posting_date_from"] = f"{today.year}-01-01"
		out["posting_date_to"] = f"{today.year}-12-31"
		return out
	if re.search(r"\blast\s+year\b", low):
		yr = today.year - 1
		out["posting_date_from"] = f"{yr}-01-01"
		out["posting_date_to"] = f"{yr}-12-31"
		return out
	if re.search(r"\bthis\s+month\b", low):
		next_first = date(today.year + (today.month // 12), (today.month % 12) + 1, 1)
		out["posting_date_from"] = today.replace(day=1).isoformat()
		out["posting_date_to"] = (next_first - timedelta(days=1)).isoformat()
		return out
	if re.search(r"\blast\s+month\b", low):
		first_of_this = today.replace(day=1)
		last_of_last = first_of_this - timedelta(days=1)
		out["posting_date_from"] = last_of_last.replace(day=1).isoformat()
		out["posting_date_to"] = last_of_last.isoformat()
		return out
	m = re.search(r"\blast\s+(\d{1,3})\s+days?\b", low)
	if m:
		days = int(m.group(1))
		out["posting_date_from"] = (today - timedelta(days=days)).isoformat()
		out["posting_date_to"] = today.isoformat()
		return out

	return out


def _aggregation_route(text: str) -> Optional[tuple[str, dict]]:
	"""If the message is aggregate-shaped (no specific DV name), return a
	``("dv_totals", args)`` tuple with group_by + date-range hints extracted.
	Else None.

	Fires in two cases:
	  1. AGG_WORDS + AGG_TARGETS — explicit ("total disbursements", "sum of DVs")
	  2. AGG_WORDS + GROUP_BY hint — implicit ("total by fiscal year"); the
	     grouping phrase tells us this is an aggregation even without an
	     explicit target noun.
	"""
	if _DV_NAME_RE.search(text):
		return None
	if not _AGG_WORDS_RE.search(text):
		return None

	group_by: Optional[str] = None
	for pattern, gb in _GROUP_BY_PATTERNS:
		if pattern.search(text):
			group_by = gb
			break

	date_range = _parse_date_range(text)

	# Trigger when ANY of: explicit AIS noun, group-by hint, or date phrase.
	# "total this year" has no target noun but the date phrase makes it
	# unambiguously an AIS aggregation query.
	if group_by is None and not date_range and not _AGG_TARGETS_RE.search(text):
		return None  # e.g. "total cost" alone — too vague, let NLU handle

	args: dict[str, Any] = {}
	if group_by is not None:
		args["group_by"] = group_by
	args.update(date_range)
	return "dv_totals", args

# Pronouns / referring expressions that point back at the last context.
# Matches naturally — "what's its status?", "is it approved?", "show that one again".
_PRONOUN_RE = re.compile(
	r"\b(it|its|it's|this|that|that\s+one|the\s+dv|same\s+dv|the\s+last\s+one)\b",
	re.IGNORECASE,
)


def route(text: str, session_id: Optional[str] = None) -> Optional[tuple[str, dict]]:
	"""Return (tool_name, arguments) if the query is AIS-shaped, else None.

	Resolves pronouns against per-session context when ``session_id`` is
	provided (e.g. "what's its status" after "show DV-2026-00001").
	"""
	if not text:
		return None
	low = text.lower()
	ctx = _get_context(session_id)

	# ── DV by name + detail-asking gate ──────────────────────────────────
	if (m := _DV_NAME_RE.search(text)) and _DV_DETAIL_GATE_RE.search(low):
		return "get_dv", {"name": m.group(1).upper()}

	# ── pronoun resolution — "what's its status" + context has a DV ──────
	# Requires both a pronoun AND a question/detail word so we don't grab
	# bare "it" in unrelated chatter.
	if ctx.get("dv") and _PRONOUN_RE.search(low) and (
		_DV_DETAIL_GATE_RE.search(low)
		or re.search(r"\b(show|tell|what|how|when|where|is|status|details?|amount|payee)\b", low)
	):
		return "get_dv", {"name": ctx["dv"]}

	if re.search(r"\b(pending|inbox|awaiting|to ?do|to-do|my\s+queue|waiting\s+(?:on|for)\s+me)\b", low):
		return "list_pending_dvs", {}

	if m := _BUDGET_RE.match(text):
		kind = m.group(1).rstrip("s").lower()
		return "budget_balance", {"kind": kind, "name": m.group(2).strip()}

	if m := _UACS_RE.match(text):
		kind_raw = m.group(1).lower().replace(" ", "_")
		kind = "funding_source" if "funding" in kind_raw else kind_raw
		return "lookup_uacs", {"kind": kind, "query": (m.group(2) or "").strip()}

	if m := _REPORT_RE.search(text):
		name = re.sub(r"\s+", " ", m.group(1).upper()).strip()
		name = name.replace("FAR1", "FAR 1").replace("FAR4", "FAR 4")
		return "run_report", {"report_name": name}

	# ── BIR 2307 routing (Phase 2D) ──────────────────────────────────────
	# Checked BEFORE the find_dv catch-all so "find 2307-2026-00012" goes
	# to find_bir_2307, not find_dv.
	if m := _BIR_2307_NAME_RE.search(text):
		# Normalize: ensure AIS- prefix is present (canonical Frappe name).
		raw = m.group(0).upper()
		canonical = raw if raw.startswith("AIS-") else f"AIS-{raw}"
		# Detail gate: same vocabulary as DV. "show 2307-...", "what's the
		# status of 2307-...", "tell me about 2307-..." all qualify.
		if _DV_DETAIL_GATE_RE.search(low):
			return "get_bir_2307", {"name": canonical}
		# Bare BIR 2307 name (no verb) → treat as a search by exact name.
		return "find_bir_2307", {"query": canonical}
	# "list 2307s", "show pending 2307s", "list bir certs" → list_bir_2307.
	if re.search(r"\b(list|recent|show)\s+(?:all\s+)?(?:bir\s+)?2307s?\b", low) \
	   or re.search(r"\bbir\s+(?:2307s?|withholding)\b", low):
		return "list_bir_2307", {}
	# "find 2307 by Joe" / "search 2307 PLDT" → find_bir_2307 with query.
	m = re.match(
		r"^\s*(?:find|search|look\s*for)\s+(?:bir\s+)?2307s?\s+(.+?)\s*\??\s*$",
		text, flags=re.IGNORECASE,
	)
	if m:
		return "find_bir_2307", {"query": m.group(1).strip()}

	# find_dv routing has three tiers, narrow to broad:
	#   1. Message contains a literal DV name → search for it.
	#   2. "find/search/look for ... dv|voucher" → explicit DV search.
	#   3. "^(find|search|look for) <token>" with no question word → assume
	#      payee/control-number search (e.g. "find PLDT", "search Joe").
	#      Skipped when the rest looks like a non-finance question so we
	#      don't grab "find a campus" / "search for admission requirements".
	_NON_AIS_TOKENS_RE = re.compile(
		r"\b(campus|admission|enrollment|scholarship|registrar|tuition|"
		r"professor|course|program|building|requirement|deadline|schedule)\b",
		re.IGNORECASE,
	)
	if _DV_NAME_RE.search(text) or re.search(r"\b(find|search|look\s*for)\b.+(dv|voucher)", low):
		query = re.sub(r"^\s*(?:find|search|look\s*for)\s+", "", text, flags=re.IGNORECASE).strip()
		return "find_dv", {"query": query}
	bare = re.match(r"^\s*(?:find|search|look\s*for)\s+(.+?)\s*\??\s*$", text, flags=re.IGNORECASE)
	if bare and not _NON_AIS_TOKENS_RE.search(bare.group(1)):
		token = bare.group(1).strip()
		# Skip if the token looks like a question (starts with what/where/etc.).
		if not re.match(r"^(what|when|where|how|why|who|which)\b", token, flags=re.IGNORECASE):
			return "find_dv", {"query": token}

	return None


# ── client ───────────────────────────────────────────────────────────────


class ToolCallError(RuntimeError):
	"""Raised when the MCP tool returns an explicit error result (isError=True).

	The MCP server already returns curated frappe.throw text for these — we
	can safely surface the message to the user.
	"""


class CircuitOpenError(RuntimeError):
	"""Raised when the circuit breaker has tripped — MCP is presumed down."""


# ── circuit breaker state ────────────────────────────────────────────────
# Tracks consecutive transport failures. After _BREAKER_THRESHOLD failures
# in a row, the breaker opens and any further call_tool() invocations raise
# CircuitOpenError immediately for _BREAKER_COOLDOWN_SECONDS. The first
# success after a cooldown resets the counter. Tool-side errors (isError=
# True / ToolCallError) do NOT count — those mean MCP is alive and reachable.

_breaker_failures = 0
_breaker_opened_at = 0.0  # epoch seconds; 0 = breaker closed

# ── metrics state ────────────────────────────────────────────────────────
# Per-tool rolling counters + the last N latency samples so we can report
# p50/p95 without an external metrics system. Bounded to avoid leaks.
_METRICS_LATENCY_SAMPLES = 200

_metrics: dict[str, dict[str, Any]] = {}  # tool_name -> {ok, fail, latencies}


def _metrics_record(tool_name: str, elapsed_ms: int, ok: bool) -> None:
	entry = _metrics.setdefault(tool_name, {"ok": 0, "fail": 0, "latencies": []})
	if ok:
		entry["ok"] += 1
	else:
		entry["fail"] += 1
	lat = entry["latencies"]
	lat.append(elapsed_ms)
	if len(lat) > _METRICS_LATENCY_SAMPLES:
		# Drop the oldest sample (FIFO) — bounded memory.
		del lat[0]


# ── session pool ─────────────────────────────────────────────────────────
# Lazy-initialized cache of an open (sse_client + ClientSession) pair so we
# don't pay the ~30ms handshake on every call. Auto-recovers if the session
# breaks (e.g. MCP server restart): the next call detects the failure, drops
# the cache, and reconnects. Single-process / single-event-loop only.

_pool_lock: asyncio.Lock = asyncio.Lock()
_pool_session: Any = None
_pool_stack: Optional[AsyncExitStack] = None


async def _get_pooled_session() -> Any:
	"""Return the cached MCP ClientSession, creating it on first call."""
	global _pool_session, _pool_stack
	if _pool_session is not None:
		return _pool_session
	async with _pool_lock:
		# Double-check after the lock — another coroutine may have raced us.
		if _pool_session is not None:
			return _pool_session
		stack = AsyncExitStack()
		read, write = await stack.enter_async_context(sse_client(_MCP_URL))
		session = await stack.enter_async_context(ClientSession(read, write))
		await session.initialize()
		_pool_session = session
		_pool_stack = stack
		return session


async def _drop_pooled_session() -> None:
	"""Close and clear the pool. Call on transport error so the next request
	rebuilds the session against a (hopefully) recovered MCP server."""
	global _pool_session, _pool_stack
	stack = _pool_stack
	_pool_session = None
	_pool_stack = None
	if stack is not None:
		try:
			await stack.aclose()
		except Exception:  # noqa: BLE001 — already-dead session; just drop it
			pass


async def close_pool() -> None:
	"""Public shutdown helper — call from FastAPI lifespan on app shutdown."""
	await _drop_pooled_session()


def metrics_snapshot() -> dict:
	"""Public snapshot for the metrics endpoint. Computes p50/p95 lazily."""
	def _percentile(samples: list[int], pct: float) -> Optional[int]:
		if not samples:
			return None
		ordered = sorted(samples)
		idx = min(len(ordered) - 1, int(round(pct / 100 * len(ordered))))
		return ordered[idx]
	tools: dict[str, dict[str, Any]] = {}
	for name, entry in _metrics.items():
		lat = entry["latencies"]
		total = entry["ok"] + entry["fail"]
		tools[name] = {
			"calls":        total,
			"ok":           entry["ok"],
			"fail":         entry["fail"],
			"error_rate":   round(entry["fail"] / total, 4) if total else 0.0,
			"p50_ms":       _percentile(lat, 50),
			"p95_ms":       _percentile(lat, 95),
			"samples":      len(lat),
		}
	return {
		"tools":   tools,
		"circuit": circuit_status(),
	}


def _circuit_is_open() -> bool:
	if _breaker_opened_at <= 0:
		return False
	if time.time() - _breaker_opened_at < _BREAKER_COOLDOWN_SECONDS:
		return True
	# Cooldown elapsed — half-open: allow the next call through.
	return False


def _circuit_record_failure() -> None:
	global _breaker_failures, _breaker_opened_at
	_breaker_failures += 1
	if _breaker_failures >= _BREAKER_THRESHOLD and _breaker_opened_at <= 0:
		_breaker_opened_at = time.time()
		_logger.warning(
			"ais_mcp circuit OPEN after %d failures — cooldown=%.1fs",
			_breaker_failures, _BREAKER_COOLDOWN_SECONDS,
		)


def _circuit_record_success() -> None:
	global _breaker_failures, _breaker_opened_at
	if _breaker_opened_at > 0:
		_logger.info("ais_mcp circuit CLOSED after successful call")
	_breaker_failures = 0
	_breaker_opened_at = 0.0


def circuit_status() -> dict:
	"""Public snapshot for the metrics endpoint."""
	return {
		"open": _circuit_is_open(),
		"consecutive_failures": _breaker_failures,
		"opened_at": _breaker_opened_at if _breaker_opened_at > 0 else None,
		"cooldown_seconds": _BREAKER_COOLDOWN_SECONDS,
		"threshold": _BREAKER_THRESHOLD,
	}


# Keys redacted when logging tool args — anything that could carry user
# credentials, OAuth tokens, or PII. Match by suffix/substring so future
# additions (e.g. `password`, `api_key`) are caught without code change.
_SENSITIVE_ARG_KEY_RE = re.compile(r"(token|secret|password|api_key|auth)", re.IGNORECASE)


def _redact_args(args: dict | None) -> dict | None:
	"""Return a shallow copy of ``args`` with sensitive values masked.
	Used everywhere args are interpolated into log strings — write tools
	carry ``__auth_token`` which MUST NOT land in plaintext logs."""
	if not args:
		return args
	return {
		k: ("***REDACTED***" if _SENSITIVE_ARG_KEY_RE.search(k) else v)
		for k, v in args.items()
	}


async def call_tool(name: str, arguments: dict) -> Any:
	"""Open an MCP session, call one tool, return the parsed result.

	Opens a fresh SSE connection per call. Cheap for localhost — measured
	~30ms overhead. If you want connection pooling, hoist the session into
	a FastAPI lifespan; for now per-call keeps the code self-contained.

	Caps total wall time at ``_CALL_TIMEOUT_SECONDS`` so a hung MCP server
	never blocks a chat request indefinitely. Raises ``ToolCallError`` when
	the MCP result carries ``isError=True`` so the caller can distinguish
	"tool said no" from "transport blew up".
	"""
	if not _MCP_AVAILABLE:
		raise RuntimeError("mcp SDK not installed — pip install 'mcp[sse]'")
	if not _ENABLED:
		raise RuntimeError("AIS_MCP_ENABLED=0 — routing disabled")
	if _circuit_is_open():
		raise CircuitOpenError("MCP circuit breaker is open")

	async def _do_call_pooled() -> Any:
		session = await _get_pooled_session()
		try:
			return await session.call_tool(name, arguments)
		except Exception:
			# Session is probably dead (MCP server restart, network blip).
			# Drop the cache so the NEXT call rebuilds — but re-raise so this
			# call still surfaces the error / trips the breaker correctly.
			await _drop_pooled_session()
			raise

	async def _do_call_unpooled() -> Any:
		async with sse_client(_MCP_URL) as (read, write):
			async with ClientSession(read, write) as session:
				await session.initialize()
				return await session.call_tool(name, arguments)

	do_call = _do_call_pooled if _SESSION_POOL_ENABLED else _do_call_unpooled
	result = await asyncio.wait_for(do_call(), timeout=_CALL_TIMEOUT_SECONDS)

	first = result.content[0] if result.content else None
	if first is None:
		text = ""
	elif hasattr(first, "text"):
		text = first.text
	else:
		text = str(first)

	if getattr(result, "isError", False):
		raise ToolCallError(text or "MCP tool returned an error with no message")

	if not text:
		return None
	try:
		return json.loads(text)
	except json.JSONDecodeError:
		return text


# User-safe failure messages. Internal exception text NEVER appears in chat —
# it goes to the server log via _logger.exception(). These strings are what
# end users (including anonymous internet users on public deployments) see.
_TIMEOUT_NOTE     = "(AIS is taking too long to respond right now — answering from general knowledge below.)"
_UNREACHABLE_NOTE = "(AIS is temporarily unreachable — answering from general knowledge below.)"

# Compact AIS glossary served in degraded mode. Lets the LLM answer
# "what is a DV?" / "what does ORS mean?" without live data — better than
# dropping the user into the campus NLU which only knows admissions topics.
_AIS_GLOSSARY = """\
AIS = CvSU Accounting Information System (Frappe app 'accounting').
DV = Disbursement Voucher — the payment authorization document. Drafted by a
    clerk, certified by budget + IA, approved by the accountant, then posted.
ORS / BURS = Obligation Request and Status / Budget Utilization Request and
    Status — registers an obligation against an allotment. ORS for Regular
    Agency Fund, BURS for STF (internally generated funds).
LDDAP-ADA = bank transmittal that releases approved DV payments to suppliers
    (List of Due and Demandable Accounts Payable — Advice to Debit Account).
BIR 2307 = withholding tax certificate issued per DV to the payee.
NCA = Notice of Cash Allocation — DBM authority to draw against the Bureau
    of Treasury for MDS payments.
RAPAL / RAOD / RBUD / RANCA / FAR = budget execution reports.
UACS = Unified Accounts Code Structure: funding_source + pap_code +
    location_code + expense_class (PS/MOOE/FE/CO) + account.
Fund clusters: 01 Regular Agency, 05 STF, 07 Trust. STF cannot fund PS."""

_LLM_GLOSSARY_SYSTEM = (
	"You are the CvSU AIS assistant operating in degraded mode — live AIS "
	"data is unavailable. Answer the user from the glossary below in 1-3 "
	"sentences. If the question asks about a specific record (DV name, "
	"report run, balance) say you can't look it up while AIS is down and "
	"suggest retrying. Never invent record names, numbers, or amounts.\n\n"
	f"AIS glossary:\n{_AIS_GLOSSARY}"
)

# MCP server wraps tool failures as "Error calling {tool}: {ExcType}: {detail}".
# Frappe's http_client further wraps as "{method_path} returned {NNN}: {json}"
# OR "Rate limit on {path}: {json}" for 417 (Frappe uses 417 for ALL
# frappe.throw() calls, not just rate limits — the http_client mapping is
# misleadingly named; treat that path the same as a 4xx with JSON body).
# This sanitizer peels both layers and substitutes friendly text for the
# common 4xx/5xx cases. JSON parsing is best-effort — we never raise here.
_MCP_WRAP_RE      = re.compile(r"^Error calling \w+: \w+: (.*)$", re.DOTALL)
_HTTP_STATUS_RE   = re.compile(r"returned (\d{3}):\s*(\{.*)?$", re.DOTALL)
_RATE_LIMIT_PFX_RE = re.compile(r"^Rate limit on [\w\.]+:\s*(\{.*)$", re.DOTALL)
_SERVER_5XX_RE    = re.compile(r"returned 5\d\d:", re.IGNORECASE)

# Extract just the "exception":"..." string from a Frappe error body.
# Single-pass regex is more reliable than json.loads because Frappe's
# response body can contain nested JSON-escaped tracebacks that confuse
# strict parsers; we only need the human-readable exception text.
_FRAPPE_EXCEPTION_RE = re.compile(r'"exception"\s*:\s*"((?:[^"\\]|\\.)*)"')
_FRAPPE_EXC_TYPE_RE  = re.compile(r'"exc_type"\s*:\s*"([^"]+)"')

# Friendly substitutes per (HTTP status, exc_type). exc_type comes from the
# JSON body Frappe returns; the catch-all on status alone handles cases where
# the body isn't parseable JSON.
_FRIENDLY_BY_TYPE: dict[str, str] = {
	"DoesNotExistError":   "Not found in AIS — check the name and try again.",
	"ValidationError":     "AIS rejected the request — the arguments didn't pass validation.",
	"PermissionError":     "You don't have permission to view that record.",
	"DuplicateEntryError": "That record already exists.",
}
_FRIENDLY_BY_STATUS: dict[int, str] = {
	400: "AIS rejected the request as malformed.",
	403: "You don't have permission to view that record.",
	404: "Not found in AIS — check the name and try again.",
	417: "Rate limit exceeded — please retry in a moment.",
}


def _sanitize_tool_error(raw: str) -> tuple[str, bool]:
	"""Turn the MCP server's verbose error text into something a user can read.

	Returns ``(user_text, treat_as_unreachable)``. When ``treat_as_unreachable``
	is True the caller should fall back to NLU with the unreachable note
	instead of surfacing the message — server-side 5xx errors leak internals.
	"""
	# Server-side 5xx → blow up to unreachable, don't surface the dump.
	if _SERVER_5XX_RE.search(raw):
		return "", True

	# Strip the MCP "Error calling X: TypeName: " wrapper if present.
	m = _MCP_WRAP_RE.match(raw.strip())
	cleaned = (m.group(1) if m else raw).strip()

	# If what's left is the Frappe HTTP wrapper "<method> returned 4xx: {json}"
	# OR "Rate limit on <path>: {json}" (417 path), extract the underlying
	# exception text and surface that directly. Frappe's ValidationError
	# messages are already user-friendly; we just need to dig them out.
	status_match = _HTTP_STATUS_RE.search(cleaned)
	rl_match     = _RATE_LIMIT_PFX_RE.match(cleaned)
	body = None
	status: Optional[int] = None
	if status_match:
		status = int(status_match.group(1))
		body = status_match.group(2) or ""
	elif rl_match:
		# 417 path — treat like a 4xx with a JSON body.
		status = 417
		body = rl_match.group(1) or ""

	if body is not None:
		# Extract "exc_type" and "exception" via single-pass regex — full
		# json.loads chokes on Frappe's nested traceback escaping. We only
		# need the meaningful exception text, not the stacktrace.
		exc_type = ""
		exc_msg  = ""
		t_match = _FRAPPE_EXC_TYPE_RE.search(body)
		if t_match:
			exc_type = t_match.group(1)
		e_match = _FRAPPE_EXCEPTION_RE.search(body)
		if e_match:
			raw_exc = e_match.group(1)
			# Strip "frappe.exceptions.ValidationError: " or similar prefix
			# so we surface just the curated message text.
			if ": " in raw_exc:
				exc_msg = raw_exc.split(": ", 1)[1]
			else:
				exc_msg = raw_exc
			# Decode JSON escape sequences that survived the regex extract
			# (\uXXXX, \n, \", \', \\). codecs.decode handles \uXXXX + \n;
			# the manual replaces cover the cases it doesn't.
			try:
				exc_msg = (
					exc_msg
					.encode("ascii", errors="backslashreplace")
					.decode("unicode_escape")
				)
			except (UnicodeDecodeError, UnicodeEncodeError):
				pass  # leave raw if decode fails; still readable
			exc_msg = exc_msg.replace(r"\'", "'").replace(r'\"', '"').strip()
		# Prefer the actual exception message over the generic friendly
		# substitute when the exception is a curated frappe.throw — those
		# are already designed for end users (e.g. "DV cannot proceed to
		# Closed — missing certifications: …").
		if exc_msg and exc_type in {"ValidationError", "RateLimitExceededError"}:
			if len(exc_msg) > 280:
				exc_msg = exc_msg[:277] + "…"
			return exc_msg, False
		if exc_type and exc_type in _FRIENDLY_BY_TYPE:
			return _FRIENDLY_BY_TYPE[exc_type], False
		if status in _FRIENDLY_BY_STATUS:
			return _FRIENDLY_BY_STATUS[status], False
		return f"AIS returned HTTP {status}.", False

	# Anything else — curated frappe.throw text. Truncate runaway output.
	if len(cleaned) > 280:
		cleaned = cleaned[:277] + "…"
	return cleaned, False


# ── LLM router fallback (opt-in) ─────────────────────────────────────────
# When regex misses but the message clearly looks AIS-shaped, ask Claude
# to extract a tool + args. Costs ~1s + an API call per miss, so off by
# default. Enable with AIS_MCP_LLM_ROUTER=1 and ANTHROPIC_API_KEY set.

# Cheap prefilter — avoid the LLM call when the message has zero AIS signal.
# Permissive on plurals/stems so things like "vouchers", "allotments",
# "funding sources" all pass through. False positives are cheap (one local
# Ollama call); false negatives silently skip the router.
_AIS_SHAPED_RE = re.compile(
	r"\b(?:"
	r"dvs?|vouchers?|disburs\w*|"
	r"budgets?|allotments?|appropriations?|obligations?|"
	r"funding|fund\s+cluster|nca|"
	r"rapal|raod|rbud|ranca|far\s*\d|"
	r"payees?|uacs|"
	r"registr(?:y|ies)|ledgers?|"
	r"pap\s+code|location\s+code|"
	r"pending|inbox|approvers?|posted|released|cancelled|"
	r"waiting\s+(?:on|for)\s+me|my\s+queue"
	r")\b",
	re.IGNORECASE,
)

# Tool schemas advertised to Claude — kept tight on purpose so Claude
# returns one of these or nothing. Mirrors the regex router's tool set.
_LLM_TOOLS: list[dict[str, Any]] = [
	{
		"name": "get_dv",
		"description": "Fetch one DV (Disbursement Voucher) by its name like DV-2026-00001 or TRUST-2026-00042.",
		"input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
	},
	{
		"name": "list_pending_dvs",
		"description": "List DVs that are pending the current user's action (in their approval queue).",
		"input_schema": {"type": "object", "properties": {}},
	},
	{
		"name": "find_dv",
		"description": "Search DVs by free-text query — control number, payee name, ORS/BURS reference, or partial DV name.",
		"input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
	},
	{
		"name": "budget_balance",
		"description": "Get remaining balance for an allotment, appropriation, or NCA by its name.",
		"input_schema": {
			"type": "object",
			"properties": {
				"kind": {"type": "string", "enum": ["allotment", "appropriation", "nca"]},
				"name": {"type": "string"},
			},
			"required": ["kind", "name"],
		},
	},
	{
		"name": "lookup_uacs",
		"description": "Look up UACS codes — funding source, PAP code, or location code — by partial name.",
		"input_schema": {
			"type": "object",
			"properties": {
				"kind": {"type": "string", "enum": ["funding_source", "pap", "location"]},
				"query": {"type": "string"},
			},
			"required": ["kind"],
		},
	},
	{
		"name": "dv_totals",
		"description": (
			"Aggregate sum(amount) and count over Disbursement Vouchers. Use for "
			"questions like 'how much have we disbursed', 'total of all DVs', "
			"'spending by fund cluster', 'total by status'. Omit group_by for a "
			"grand total; set it to 'fund_cluster', 'workflow_status', 'dv_type', "
			"'payee', 'month', or 'fiscal_year' for a per-group breakdown."
		),
		"input_schema": {
			"type": "object",
			"properties": {
				"group_by": {
					"type": "string",
					"enum": ["fund_cluster", "workflow_status", "dv_type", "payee", "month", "fiscal_year"],
				},
				"fund_cluster": {"type": "string"},
				"workflow_status": {
					"type": "string",
					"enum": ["Draft", "Submitted", "IA Audit Required", "Approved", "Posted", "Released", "Closed"],
				},
				"payee": {"type": "string"},
				"fiscal_year": {"type": "string"},
				"posting_date_from": {"type": "string"},
				"posting_date_to": {"type": "string"},
			},
		},
	},
	{
		"name": "run_report",
		"description": (
			"Run a named CvSU AIS / Frappe Script Report. The user often refers to "
			"reports by their long name — map the request to one of these short codes "
			"and pass that as report_name:\n"
			"  RAPAL = Registry of Allotments, Obligations and Disbursements (overall)\n"
			"  RAOD-PS / RAOD-MOOE / RAOD-FE / RAOD-CO = Registry of Allotments and "
			"Obligations - Disbursements per expense class (Personnel Services, "
			"MOOE, Financial Expenses, Capital Outlay)\n"
			"  RBUD-PS / RBUD-MOOE / RBUD-FE / RBUD-CO = Registry of Budget, "
			"Utilization and Disbursements (STF / internally generated funds)\n"
			"  RANCA = Registry of Notice of Cash Allocation\n"
			"  FAR 1 = Statement of Appropriations, Allotments, Obligations\n"
			"  FAR 4 = Monthly Report of Disbursements"
		),
		"input_schema": {"type": "object", "properties": {"report_name": {"type": "string"}}, "required": ["report_name"]},
	},
	# ── BIR 2307 read tools (Phase 2D) ───────────────────────────────────
	{
		"name": "list_bir_2307",
		"description": (
			"List recent BIR 2307 (tax withholding) certificates, newest "
			"first. Payee TIN is redacted to last 4 digits."
		),
		"input_schema": {"type": "object", "properties": {}},
	},
	{
		"name": "get_bir_2307",
		"description": (
			"Fetch one BIR 2307 by name (e.g. 'AIS-2307-2026-00012'). "
			"Returns the full record with TIN + address redacted."
		),
		"input_schema": {
			"type": "object",
			"properties": {"name": {"type": "string"}},
			"required": ["name"],
		},
	},
	{
		"name": "find_bir_2307",
		"description": (
			"Search BIR 2307 by free-text query (payee name, 2307 name, "
			"or DV reference) and optional filters. Use this for "
			"questions like 'find 2307 for PLDT' or 'show me last quarter's "
			"2307s'. TIN matches must be exact (no partial matches)."
		),
		"input_schema": {
			"type": "object",
			"properties": {
				"query":           {"type": "string"},
				"payee_tin":       {"type": "string"},
				"approval_status": {"type": "string"},
				"period_from":     {"type": "string"},
				"period_to":       {"type": "string"},
			},
		},
	},
]

_LLM_SYSTEM = (
	"You route user messages to AIS accounting tools. If the message clearly "
	"matches one of the available tools, call exactly that tool with the "
	"correct arguments. If it doesn't match any tool, do not call any tool "
	"and respond with a short empty text. Never invent DV names or budget "
	"identifiers that aren't in the message."
)

# OpenAI-style tool schemas — Ollama's /v1/chat/completions accepts these.
# Derived from _LLM_TOOLS so the two providers stay in sync automatically.
_LLM_TOOLS_OPENAI = [
	{
		"type": "function",
		"function": {
			"name": t["name"],
			"description": t["description"],
			"parameters": t["input_schema"],
		},
	}
	for t in _LLM_TOOLS
]

_KNOWN_TOOL_NAMES = {t["name"] for t in _LLM_TOOLS}


async def _llm_route(text: str) -> Optional[tuple[str, dict]]:
	"""Pick a provider and ask it to extract (tool, args) for an AIS query.

	Provider selection order:
	  1. ``LLM_PROVIDER`` env var if set explicitly (ollama | claude/anthropic).
	  2. Else: prefer Ollama (local, free); fall back to Anthropic if Ollama
	     isn't configured.

	Never raises — any LLM/transport failure returns ``None`` so the caller
	can fall through to the NLU pipeline.
	"""
	if not _LLM_ROUTER_ENABLED:
		return None
	if not _AIS_SHAPED_RE.search(text):
		return None
	provider = (os.environ.get("LLM_PROVIDER") or "").lower()
	if provider == "ollama":
		return await _llm_route_ollama(text)
	if provider in ("claude", "anthropic"):
		return await _llm_route_anthropic(text)
	# Auto: Ollama first (local, no API cost), then Anthropic.
	if _OLLAMA_BASE_URL:
		return await _llm_route_ollama(text)
	return await _llm_route_anthropic(text)


async def _llm_route_anthropic(text: str) -> Optional[tuple[str, dict]]:
	if not _ANTHROPIC_AVAILABLE or not os.environ.get("ANTHROPIC_API_KEY"):
		return None
	t0 = time.monotonic()
	try:
		client = anthropic.AsyncAnthropic()
		resp = await client.messages.create(
			model=_router_anthropic_model(),
			max_tokens=256,
			system=_LLM_SYSTEM,
			tools=_LLM_TOOLS,
			messages=[{"role": "user", "content": text}],
		)
	except Exception:  # noqa: BLE001 — never block chat on LLM failure
		_logger.exception("ais_mcp llm_route provider=anthropic failed text=%r", text[:200])
		return None
	elapsed_ms = int((time.monotonic() - t0) * 1000)
	for block in resp.content:
		if getattr(block, "type", None) == "tool_use":
			tool_name = block.name
			args = dict(block.input) if block.input else {}
			_logger.info(
				"ais_mcp llm_route provider=anthropic elapsed=%dms tool=%s args=%s text=%r",
				elapsed_ms, tool_name, args, text[:120],
			)
			return tool_name, args
	_logger.info(
		"ais_mcp llm_route provider=anthropic elapsed=%dms tool=None text=%r",
		elapsed_ms, text[:120],
	)
	return None


async def _llm_route_ollama(text: str) -> Optional[tuple[str, dict]]:
	"""Ask the local Ollama model to pick a tool via OpenAI-style tool calling.

	qwen2.5+, qwen3, and llama3.1+ all support tool calling through Ollama's
	``/v1/chat/completions`` endpoint. Model name comes from
	``AIS_MCP_OLLAMA_MODEL`` (else ``OLLAMA_MODEL``, default ``qwen3:8b``).
	"""
	t0 = time.monotonic()
	try:
		async with httpx.AsyncClient(timeout=15.0) as http:
			resp = await http.post(
				f"{_OLLAMA_BASE_URL}/v1/chat/completions",
				json={
					"model": _router_ollama_model(),
					"messages": [
						{"role": "system", "content": _LLM_SYSTEM},
						{"role": "user", "content": text},
					],
					"tools": _LLM_TOOLS_OPENAI,
					"tool_choice": "auto",
					"max_tokens": 256,
				},
			)
			resp.raise_for_status()
			data = resp.json()
	except Exception:  # noqa: BLE001 — never block chat on LLM failure
		_logger.exception("ais_mcp llm_route provider=ollama failed text=%r", text[:200])
		return None
	elapsed_ms = int((time.monotonic() - t0) * 1000)

	choices = data.get("choices") or []
	msg = (choices[0].get("message") if choices else None) or {}
	tool_calls = msg.get("tool_calls") or []
	if not tool_calls:
		_logger.info(
			"ais_mcp llm_route provider=ollama elapsed=%dms tool=None text=%r",
			elapsed_ms, text[:120],
		)
		return None

	call = tool_calls[0]
	fn = call.get("function") or {}
	tool_name = fn.get("name")
	args_raw = fn.get("arguments")

	# Validate against our schema — qwen3 occasionally hallucinates tool
	# names not in the advertised set; reject those so we fall through to NLU.
	if tool_name not in _KNOWN_TOOL_NAMES:
		_logger.warning(
			"ais_mcp llm_route provider=ollama hallucinated_tool=%s text=%r",
			tool_name, text[:120],
		)
		return None

	if isinstance(args_raw, dict):
		args = args_raw
	else:
		try:
			args = json.loads(args_raw or "{}")
		except json.JSONDecodeError:
			_logger.warning(
				"ais_mcp llm_route provider=ollama bad_args tool=%s args=%r",
				tool_name, args_raw,
			)
			return None

	_logger.info(
		"ais_mcp llm_route provider=ollama elapsed=%dms tool=%s args=%s text=%r",
		elapsed_ms, tool_name, args, text[:120],
	)
	return tool_name, args


async def _llm_glossary_reply(text: str) -> Optional[str]:
	"""Degraded-mode answer using only the AIS glossary — no tools, no data.

	Same provider selection as _llm_route; returns plain text or None on
	failure. Used when MCP is unreachable but the query was AIS-shaped, so
	the user gets a useful definition instead of the campus NLU's "not in
	glossary" reply.
	"""
	explicit = (os.environ.get("LLM_PROVIDER") or "").lower().strip()
	use_ollama = bool(_OLLAMA_BASE_URL) and explicit in ("", "ollama")
	if explicit in ("claude", "anthropic"):
		use_ollama = False

	t0 = time.monotonic()
	out = ""
	if use_ollama:
		try:
			async with httpx.AsyncClient(timeout=12.0) as http:
				resp = await http.post(
					f"{_OLLAMA_BASE_URL}/v1/chat/completions",
					json={
						"model": _router_ollama_model(),
						"messages": [
							{"role": "system", "content": _LLM_GLOSSARY_SYSTEM},
							{"role": "user", "content": text},
						],
						"max_tokens": 220,
					},
				)
				resp.raise_for_status()
				data = resp.json()
			choices = data.get("choices") or []
			msg = (choices[0].get("message") if choices else None) or {}
			out = (msg.get("content") or "").strip()
		except Exception:  # noqa: BLE001 — never block chat on LLM failure
			_logger.exception("ais_mcp glossary_reply provider=ollama failed text=%r", text[:200])
			return None
	else:
		if not (_ANTHROPIC_AVAILABLE and os.environ.get("ANTHROPIC_API_KEY")):
			return None
		try:
			client = anthropic.AsyncAnthropic()
			resp = await client.messages.create(
				model=_router_anthropic_model(),
				max_tokens=220,
				system=_LLM_GLOSSARY_SYSTEM,
				messages=[{"role": "user", "content": text}],
			)
			out = "".join(
				getattr(b, "text", "") for b in resp.content
				if getattr(b, "type", None) == "text"
			).strip()
		except Exception:  # noqa: BLE001
			_logger.exception("ais_mcp glossary_reply provider=anthropic failed text=%r", text[:200])
			return None

	elapsed_ms = int((time.monotonic() - t0) * 1000)
	_logger.info("ais_mcp glossary_reply elapsed=%dms ok=%s", elapsed_ms, bool(out))
	return out or None


async def _degraded_reply(message: str, note: str) -> dict:
	"""Assemble a degraded-mode reply: glossary LLM answer if available,
	else the historical text=None+failure_note shape that app.py falls
	through to the campus NLU with."""
	answer = await _llm_glossary_reply(message)
	if answer:
		return {
			"text": f"{note}\n\n{answer}",
			"dv_card": None,
			"context_set": None,
		}
	return {"text": None, "failure_note": note}


def _update_context_after_call(session_id: Optional[str], tool_name: str, args: dict, data: Any) -> None:
	"""Remember the latest entity per session for pronoun follow-ups."""
	if not session_id:
		return
	if tool_name == "get_dv" and isinstance(data, dict) and "name" in data:
		_set_context(session_id, dv=data["name"])
	elif tool_name == "find_dv" and isinstance(data, dict):
		rows = data.get("rows") or []
		if len(rows) == 1 and rows[0].get("name"):
			_set_context(session_id, dv=rows[0]["name"])
	elif tool_name == "lookup_uacs":
		_set_context(session_id, uacs_kind=args.get("kind"), uacs_query=args.get("query", ""))
	elif tool_name == "run_report":
		_set_context(session_id, report=args.get("report_name"))


async def try_handle(
	message: str,
	session_id: Optional[str] = None,
	intent_hint: Optional[str] = None,
	intent_args: Optional[dict] = None,
) -> Optional[dict]:
	"""End-to-end helper: detect AIS intent, call the tool, format reply.

	Routing order:
	  1. ``intent_hint`` (external client bypass) — if set, call that tool directly.
	  2. Regex router (with pronoun resolution from session context).
	  3. LLM router fallback (opt-in via AIS_MCP_LLM_ROUTER=1).

	Return shapes:
	  • ``None`` — query was not AIS-shaped; Diwa's normal NLU pipeline runs.
	  • ``{"text": str, "dv_card": dict | None, "context_set": dict | None}``
	    — AIS handled the query.
	  • ``{"text": None, "failure_note": str}`` — transport failed; caller
	    should run NLU and prepend the note.
	"""
	if not (_MCP_AVAILABLE and _ENABLED):
		return None

	# 1. Explicit hint from external client — skip routing entirely.
	if intent_hint:
		tool_name, args = intent_hint, intent_args or {}
	else:
		# 2. Regex — pronoun resolution + known patterns. Aggregation queries
		#    are detected here too and route to dv_totals with an optional
		#    group_by extracted from "by fund cluster" / "per status" / etc.
		routed = route(message, session_id=session_id) or _aggregation_route(message)
		# 3. LLM fallback.
		if routed is None:
			routed = await _llm_route(message)
		if routed is None:
			return None
		tool_name, args = routed

	t0 = time.monotonic()
	try:
		data = await call_tool(tool_name, args)
	except CircuitOpenError:
		# Fast-fail: MCP is presumed down; skip the 8s timeout dance.
		_metrics_record(tool_name, 0, ok=False)
		_logger.info(
			"ais_mcp tool=%s args=%s ok=False reason=circuit_open",
			tool_name, _redact_args(args),
		)
		return await _degraded_reply(message, _UNREACHABLE_NOTE)
	except asyncio.TimeoutError:
		_circuit_record_failure()
		elapsed_ms = int((time.monotonic() - t0) * 1000)
		_metrics_record(tool_name, elapsed_ms, ok=False)
		_logger.warning(
			"ais_mcp tool=%s args=%s elapsed=%dms ok=False reason=timeout limit=%.1fs",
			tool_name, _redact_args(args), elapsed_ms, _CALL_TIMEOUT_SECONDS,
		)
		return await _degraded_reply(message, _TIMEOUT_NOTE)
	except ToolCallError as exc:
		# Tool-side error (validation, not-found, etc.) — MCP is healthy.
		# Treat as a success for the breaker so transient app-level errors
		# don't trip it. Counts as a "fail" in metrics so we can monitor.
		_circuit_record_success()
		elapsed_ms = int((time.monotonic() - t0) * 1000)
		_metrics_record(tool_name, elapsed_ms, ok=False)
		_logger.info(
			"ais_mcp tool=%s args=%s elapsed=%dms ok=False reason=tool_error msg=%s",
			tool_name, _redact_args(args), elapsed_ms, exc,
		)
		user_text, treat_as_unreachable = _sanitize_tool_error(str(exc))
		if treat_as_unreachable:
			# Server-side 5xx — the message contains internals we shouldn't leak.
			# Use degraded-mode glossary reply instead of NLU fallthrough.
			return await _degraded_reply(message, _UNREACHABLE_NOTE)
		return {"text": user_text, "dv_card": None, "context_set": _public_context(session_id)}
	except Exception:  # noqa: BLE001 — transport/SDK failure; classify as unreachable
		_circuit_record_failure()
		elapsed_ms = int((time.monotonic() - t0) * 1000)
		_metrics_record(tool_name, elapsed_ms, ok=False)
		_logger.exception(
			"ais_mcp tool=%s args=%s elapsed=%dms ok=False reason=exception",
			tool_name, _redact_args(args), elapsed_ms,
		)
		return await _degraded_reply(message, _UNREACHABLE_NOTE)

	_circuit_record_success()
	_update_context_after_call(session_id, tool_name, args, data)
	elapsed_ms = int((time.monotonic() - t0) * 1000)
	_metrics_record(tool_name, elapsed_ms, ok=True)
	_logger.info(
		"ais_mcp tool=%s args=%s elapsed=%dms ok=True",
		tool_name, _redact_args(args), elapsed_ms,
	)
	return {
		"text": _format_reply(tool_name, data),
		"dv_card": _build_dv_card(tool_name, data),
		"table": _build_table(tool_name, data),
		"suggestions": _build_suggestions(tool_name, args, data),
		"context_set": _public_context(session_id),
	}


_SUGGEST_BY_CLUSTER = "total by fund cluster"


def _build_suggestions(tool_name: str, args: dict, data: Any) -> list[str]:
	"""Return prompts the user is likely to want next. Frontend shows these
	as clickable chips that re-submit the prompt as a new chat message.

	Kept short (≤3 chips per result) and grounded in the data we just
	returned — avoids generic "show pending DVs" suggestions on every reply.
	"""
	out: list[str] = []
	if not isinstance(data, dict):
		return out

	if tool_name == "get_dv" and "name" in data:
		out.append("show the audit trail for this DV")
		payee_name = data.get("payee_name") or data.get("payee")
		if payee_name:
			out.append(f"find other DVs by {payee_name}")
		fund = data.get("fund_cluster")
		if fund:
			out.append(_SUGGEST_BY_CLUSTER)
		return out[:3]

	if tool_name == "list_pending_dvs":
		out.append("total amount per status")
		out.append(_SUGGEST_BY_CLUSTER)
		out.append("total spending by payee")
		return out

	if tool_name == "find_dv":
		rows = data.get("rows") or []
		if len(rows) == 1 and rows[0].get("name"):
			out.append(f"show {rows[0]['name']}")
		out.append("total spending by payee")
		out.append("list pending")
		return out[:3]

	if tool_name == "dv_totals":
		gb = data.get("group_by")
		if not gb:
			out.append(_SUGGEST_BY_CLUSTER)
			out.append("total by status")
			out.append("monthly disbursement totals")
		else:
			other_dims = ["fund_cluster", "workflow_status", "month", "payee", "fiscal_year"]
			for dim in other_dims:
				if dim != gb:
					label = dim.replace("_", " ")
					out.append(f"total by {label}")
				if len(out) >= 3:
					break
		return out

	if tool_name == "lookup_uacs":
		matches = data.get("matches") or []
		if matches:
			out.append(_SUGGEST_BY_CLUSTER)
		return out

	if tool_name == "run_report":
		report = args.get("report_name", "")
		# Suggest the per-expense-class variants when on RAPAL (the overview).
		if report == "RAPAL":
			out.append("run RAOD-PS")
			out.append("run RAOD-MOOE")
			out.append("run RAOD-CO")
		else:
			out.append("run RAPAL")
		return out[:3]

	if tool_name == "get_bir_2307" and "name" in data:
		dv_ref = data.get("dv_reference")
		if dv_ref:
			out.append(f"show {dv_ref}")
		out.append("list bir 2307")
		payee = data.get("payee_name")
		if payee:
			out.append(f"find 2307 by {payee}")
		return out[:3]

	if tool_name in ("list_bir_2307", "find_bir_2307"):
		rows = data.get("rows") or []
		if len(rows) == 1 and rows[0].get("name"):
			out.append(f"show {rows[0]['name']}")
		out.append("list pending")
		out.append("total spending by payee")
		return out[:3]

	return out


def _build_table(tool_name: str, data: Any) -> Optional[dict]:
	"""Return a structured table payload for multi-row results, else None.

	Shape:
	  {
	    title?: str,
	    columns: [{key, label, align?: "left"|"right"|"center"}],
	    rows:    [{<key>: cell, ...}],   # cell: str | num | {text, href?}
	    footer?: str,                    # e.g. "…and 51 more"
	  }
	"""
	if not isinstance(data, dict):
		return None

	# DV list (list_pending_dvs, find_dv) — rows have name/payee/amount/status.
	if "rows" in data and tool_name in ("list_pending_dvs", "find_dv"):
		rows_in = data.get("rows") or []
		if not rows_in:
			return None
		total = data.get("total_count", len(rows_in))
		columns = [
			{"key": "name",   "label": "DV",     "align": "left"},
			{"key": "payee",  "label": "Payee",  "align": "left"},
			{"key": "amount", "label": "Amount", "align": "right"},
			{"key": "status", "label": "Status", "align": "left"},
		]
		rows = []
		for r in rows_in[:25]:
			name = r.get("name") or ""
			amt = float(r.get("gross_amount") or r.get("amount") or 0)
			rows.append({
				"name":   {"text": name, "href": desk_url(name)} if name else "",
				"payee":  (r.get("payee_name") or r.get("payee") or "")[:48],
				"amount": f"₱{amt:,.2f}",
				"status": r.get("workflow_status") or "",
			})
		footer = None
		if total > len(rows):
			footer = f"…and {total - len(rows)} more — refine the search to narrow."
		title = f"{len(rows)} of {total} result(s)"
		return {"title": title, "columns": columns, "rows": rows, "footer": footer}

	# BIR 2307 list (list_bir_2307, find_bir_2307) — rows are tax certificates
	# with payee + period + amounts. TIN is already redacted by the serializer.
	if "rows" in data and tool_name in ("list_bir_2307", "find_bir_2307"):
		rows_in = data.get("rows") or []
		if not rows_in:
			return None
		total = data.get("total_count", len(rows_in))
		columns = [
			{"key": "name",     "label": "2307",          "align": "left"},
			{"key": "payee",    "label": "Payee",         "align": "left"},
			{"key": "tin",      "label": "TIN (last 4)",  "align": "left"},
			{"key": "period",   "label": "Period",        "align": "left"},
			{"key": "gross",    "label": "Gross",         "align": "right"},
			{"key": "ewt",      "label": "EWT",           "align": "right"},
			{"key": "net",      "label": "Net",           "align": "right"},
			{"key": "status",   "label": "Status",        "align": "left"},
		]
		rows = []
		for r in rows_in[:25]:
			name = r.get("name") or ""
			period_from = r.get("period_from") or ""
			period_to   = r.get("period_to") or ""
			period = (
				f"{period_from} → {period_to}" if (period_from and period_to)
				else (period_from or period_to or "")
			)
			rows.append({
				"name":   {"text": name, "href": bir_2307_desk_url(name)} if name else "",
				"payee":  (r.get("payee_name") or "")[:48],
				"tin":    r.get("payee_tin_redacted") or "—",
				"period": period,
				"gross":  f"₱{float(r.get('gross_amount') or 0):,.2f}",
				"ewt":    f"₱{float(r.get('ewt_amount') or 0):,.2f}",
				"net":    f"₱{float(r.get('net_amount') or 0):,.2f}",
				"status": r.get("approval_status") or "",
			})
		footer = None
		if total > len(rows):
			footer = f"…and {total - len(rows)} more — refine the search to narrow."
		title = f"{len(rows)} of {total} BIR 2307 result(s)"
		return {"title": title, "columns": columns, "rows": rows, "footer": footer}

	# dv_totals grouped — {group_by, rows: [{key, amount, count}], total_amount, count}
	if "total_amount" in data and "count" in data and data.get("group_by") and data.get("rows"):
		group_by = data["group_by"]
		rows_in = data.get("rows") or []
		columns = [
			{"key": "key",    "label": group_by.replace("_", " ").title(), "align": "left"},
			{"key": "amount", "label": "Amount", "align": "right"},
			{"key": "count",  "label": "DVs",    "align": "right"},
		]
		rows = [
			{
				"key":    str(r.get("key") or "(unset)"),
				"amount": f"₱{float(r.get('amount') or 0):,.2f}",
				"count":  f"{int(r.get('count') or 0):,}",
			}
			for r in rows_in[:25]
		]
		footer = None
		if data.get("truncated"):
			footer = "Results capped — narrow the filter for full detail."
		elif len(rows_in) > 25:
			footer = f"…and {len(rows_in) - 25} more group(s)."
		title = f"Total: ₱{float(data['total_amount']):,.2f} across {int(data['count']):,} DV(s)"
		return {"title": title, "columns": columns, "rows": rows, "footer": footer}

	# lookup_uacs — {matches: [{code, description, ...}]}
	if "matches" in data and tool_name == "lookup_uacs":
		matches = data.get("matches") or []
		if not matches:
			return None
		columns = [
			{"key": "code",        "label": "Code",        "align": "left"},
			{"key": "description", "label": "Description", "align": "left"},
		]
		rows = [
			{
				"code":        str(m.get("code") or "?"),
				"description": (m.get("description") or "")[:120],
			}
			for m in matches[:25]
		]
		footer = None
		if len(matches) > 25:
			footer = f"…and {len(matches) - 25} more — narrow the query."
		title = f"{len(rows)} match(es)"
		return {"title": title, "columns": columns, "rows": rows, "footer": footer}

	return None


def _build_dv_card(tool_name: str, data: Any) -> Optional[dict]:
	"""Return the structured card payload for a single DV, else None."""
	if tool_name != "get_dv" or not isinstance(data, dict) or "name" not in data:
		return None
	return {
		"name": data["name"],
		"control_number": data.get("control_number") or None,
		"payee": data.get("payee_name") or data.get("payee") or "",
		"amount": float(data.get("gross_amount") or data.get("amount") or 0),
		"workflow_status": data.get("workflow_status") or "",
		"posting_date": data.get("posting_date") or None,
		"fund_cluster": data.get("fund_cluster") or None,
		"ors_burs_reference": data.get("ors_burs_reference") or None,
		"dv_type": data.get("dv_type") or None,
		"desk_url": desk_url(data["name"]),
		# Phase 2A Wave 2 — the frontend passes this back as expected_modified
		# on /ais/write so the optimistic-lock guard at write.py can catch
		# stale writes (someone else changed the DV between read and confirm).
		# Frappe's serializer emits ISO format ('2026-05-07T17:58:54.x') but
		# the optimistic-lock check at api/mobile/_dv_helpers.py does
		# `str(dv_doc.modified) != str(expected_modified)` — `str(datetime)`
		# gives a SPACE separator, not T. Normalize to space here so the
		# round-trip equals the Frappe-side comparator.
		"modified": (
			str(data.get("modified")).replace("T", " ")
			if data.get("modified") else None
		),
	}


def _dv_link(name: str) -> str:
	"""Markdown link target — renders as clickable in Sevi's FormattedText."""
	return f"[{name}]({desk_url(name)})"


# Per-tool friendly text for zero-result responses. Anything not listed gets
# a generic "no results" fallback.
_NO_RESULTS_DEFAULT = "No results."

_EMPTY_MESSAGES: dict[str, str] = {
	"list_pending_dvs": "No DVs are waiting on you right now.",
	"find_dv":          "No DVs matched. Try a different control number, payee name, or date range.",
	"lookup_uacs":      "No UACS codes matched that query.",
	"run_report":       "Report returned no rows.",
	"list_bir_2307":    "No BIR 2307 certificates found.",
	"find_bir_2307":    "No BIR 2307 matched. Try a different payee name, exact TIN, or period.",
}


def _format_reply(tool_name: str, data: Any) -> str:
	"""Compact one-screen renderer. Matches in-Desk chat panel style.

	DV names are emitted as markdown links so the Sevi web FormattedText
	parser turns them into clickable Desk deep-links.
	"""
	if data is None:
		return _EMPTY_MESSAGES.get(tool_name, _NO_RESULTS_DEFAULT)
	if isinstance(data, dict):
		# dv_totals returns either {total_amount, count} (grand total) or
		# {group_by, total_amount, count, rows: [{key, amount, count}], truncated}
		# (grouped). Detected before the bare-rows branch because grouped
		# totals also have "rows" but those rows have a {key, amount, count}
		# shape that the DV-list branch can't render.
		if "total_amount" in data and "count" in data:
			total = float(data.get("total_amount") or 0)
			count = int(data.get("count") or 0)
			group_by = data.get("group_by")
			if not group_by:
				return (
					f"Total across {count:,} DV(s): ₱{total:,.2f}.\n"
					"  (Cancelled DVs excluded. Use filters like `by fund cluster` "
					"to break this down.)"
				)
			rows = data.get("rows") or []
			if not rows:
				return f"No DVs matched. (group_by={group_by})"
			label = group_by.replace("_", " ")
			lines = [f"Total by {label}: ₱{total:,.2f} across {count:,} DV(s)."]
			for r in rows[:10]:
				key = r.get("key") or "(unset)"
				amt = float(r.get("amount") or 0)
				cnt = int(r.get("count") or 0)
				lines.append(f"  {key[:30]:<30}  ₱{amt:>14,.2f}  ({cnt:,} DV{'s' if cnt != 1 else ''})")
			if len(rows) > 10:
				lines.append(f"  …and {len(rows) - 10} more group(s).")
			if data.get("truncated"):
				lines.append("  Note: results capped — narrow the filter for full detail.")
			return "\n".join(lines)
		# Reports return {"columns": [...], "rows": [...], "report_name": ...}.
		# Must run BEFORE the bare-rows branch because reports also have "rows"
		# but those rows have report-specific fields, not DV fields.
		if "columns" in data and "rows" in data:
			cols = data.get("columns") or []
			rows = data.get("rows") or []
			report_name = data.get("report_name", tool_name)
			if not rows:
				return f"{report_name}: report returned no rows."
			lines = [f"{report_name}: {len(rows)} row(s)."]
			if cols:
				labels = [c.get("label") or c.get("fieldname") or "" for c in cols[:6]]
				lines.append("  Columns: " + " | ".join(labels))
			for row in rows[:5]:
				if isinstance(row, dict):
					preview = " | ".join(
						str(row.get(c.get("fieldname", ""), ""))[:18] for c in cols[:5]
					)
				elif isinstance(row, (list, tuple)):
					preview = " | ".join(str(v)[:18] for v in row[:5])
				else:
					preview = str(row)[:90]
				lines.append(f"  {preview}")
			if len(rows) > 5:
				lines.append(f"  …and {len(rows) - 5} more — open the report in Desk for the full view.")
			return "\n".join(lines)
		if "rows" in data:
			rows = data["rows"]
			total = data.get("total_count", len(rows))
			if not rows:
				return _EMPTY_MESSAGES.get(tool_name, _NO_RESULTS_DEFAULT)
			shown = min(len(rows), 5)
			head = f"{tool_name}: showing {shown} of {total} result(s)."
			lines = [head]
			for r in rows[:5]:
				name = r.get("name", "?")
				payee = (r.get("payee_name") or r.get("payee") or "")[:30]
				amt = r.get("gross_amount") or r.get("amount") or 0
				status = r.get("workflow_status") or ""
				lines.append(f"  {_dv_link(name)}  {payee}  ₱{amt:,.2f}  {status}")
			# Use `total` (not `len(rows)`) because MCP may have server-side
			# paginated — total tells us if more exist beyond what came back.
			if total > shown:
				lines.append(f"  …and {total - shown} more — refine search to narrow.")
			return "\n".join(lines)
		if "matches" in data:
			matches = data["matches"]
			if not matches:
				return _EMPTY_MESSAGES.get(tool_name, _NO_RESULTS_DEFAULT)
			lines = [f"{tool_name}: {len(matches)} match(es)."]
			for m in matches[:8]:
				lines.append(f"  {m.get('code', '?')}  {(m.get('description') or '')[:60]}")
			if len(matches) > 8:
				lines.append(f"  …and {len(matches) - 8} more — refine search to narrow.")
			return "\n".join(lines)
		if "name" in data and "workflow_status" in data:
			return (
				f"{_dv_link(data['name'])}: {data.get('payee_name') or data.get('payee') or ''} — "
				f"₱{(data.get('gross_amount') or data.get('amount') or 0):,.2f} — {data['workflow_status']}"
			)
		# Single BIR 2307 from get_bir_2307 — has payee_name + amounts + period
		# + approval_status but no `workflow_status` field. TIN is already redacted.
		if "name" in data and "approval_status" in data and data.get("payee_name") is not None:
			nm = data["name"]
			link = f"[{nm}]({bir_2307_desk_url(nm)})"
			tin = data.get("payee_tin_redacted") or "—"
			net = float(data.get("net_amount") or 0)
			period = (
				f"{data.get('period_from', '')} → {data.get('period_to', '')}".strip(" →")
				or ""
			)
			return (
				f"{link}: {data.get('payee_name', '')} (TIN {tin}) — "
				f"₱{net:,.2f} net — {data['approval_status']}"
				f"{(' — ' + period) if period else ''}"
			)
		if "found" in data:
			if not data["found"]:
				return _EMPTY_MESSAGES.get(tool_name, "Not found.")
			fields = ", ".join(
				f"{k}=₱{v:,.2f}" if isinstance(v, (int, float)) else f"{k}={v}"
				for k, v in data.items()
				if k not in ("kind", "found")
			)
			return f"{tool_name}: {fields}"
	# Unrecognized response shape — log full payload server-side, surface a
	# safe message to the user. Never dump raw structure to chat.
	_logger.warning("ais_mcp unrecognized response shape tool=%s data=%r", tool_name, data)
	return "AIS responded but the format wasn't recognized — please escalate to support."
