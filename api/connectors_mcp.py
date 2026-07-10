"""Diwa bridge to the shared read-only diwa-connectors MCP server.

Sibling of ais_mcp.py, deliberately leaner: no session pool, no circuit
breaker, no LLM router yet. Routing is a small set of conservative regexes so
FAQ traffic is never stolen from the NLU tiers — a query only routes here when
it unambiguously targets a connectors tool (a ticket number to track, a
subject code to look up, a "what programs are offered" ask).

The server side lives in connectors_mcp/ (package `diwa-connectors`, default
port 8766) and hosts one namespaced tool group per system: `courses_*`,
`orps_*`. Its tools all reply with one envelope: {"ok": true, "data": ...} or
{"ok": false, "error": "..."}.

Configuration via env vars:
    CONNECTORS_MCP_URL              — SSE endpoint (default http://127.0.0.1:8766/sse)
    CONNECTORS_MCP_ENABLED          — "0" disables routing entirely (default "1")
    CONNECTORS_MCP_TIMEOUT_SECONDS  — hard ceiling per call (default 8.0)

Known v1 limitation: prerequisite questions route to courses_find_subject
(surfacing the subject), not the full prerequisite chain — chaining
find_subject → curriculum-subject id → prerequisite-subjects needs the LLM
router, which arrives with the multi-server router work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

# mcp SDK is optional — if missing, all calls return None and Diwa falls
# back to its normal pipeline. Install with: pip install "mcp[sse]"
try:
	from mcp import ClientSession
	from mcp.client.sse import sse_client
	_MCP_AVAILABLE = True
except ImportError:
	_MCP_AVAILABLE = False

_logger = logging.getLogger("diwa.connectors_mcp")

_MCP_URL = os.environ.get("CONNECTORS_MCP_URL", "http://127.0.0.1:8766/sse")
_ENABLED = os.environ.get("CONNECTORS_MCP_ENABLED", "1") == "1"
_CALL_TIMEOUT_SECONDS = float(os.environ.get("CONNECTORS_MCP_TIMEOUT_SECONDS", "8.0"))

_FAILURE_NOTE = (
	"(The campus services lookup is temporarily unreachable — answering from "
	"general knowledge below.)"
)

# ─────────────────────────────────────────────────────────────────────────────
# Routing — conservative regexes only. When in doubt, return None and let the
# NLU tiers answer.
# ─────────────────────────────────────────────────────────────────────────────

_TICKET_WORD_RE = re.compile(r"\b(tickets?|help ?desk|online request)\b", re.IGNORECASE)
# Strict ORPS ticket format (verified against the Ticket Requests Tracker
# naming series): HTKT/NTKT/STKT/ISTKT - month - counter. A token in this
# exact shape routes even without the word "ticket".
_ORPS_FORMAT_RE = re.compile(r"\b((?:H|N|S|IS)TKT-\d{2}-\d+)\b", re.IGNORECASE)
# An id-ish token: contains a digit, at least 3 chars, allows TT-0001 / #12345.
_TICKET_TOKEN_RE = re.compile(r"#?\b(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{2,}\b")
# Tokens that look like ids but are almost always part of the sentence, not a
# ticket number (years, ordinals like 1st/2nd).
_TICKET_TOKEN_SKIP_RE = re.compile(r"^(19|20)\d{2}$|^\d{1,2}(st|nd|rd|th)$", re.IGNORECASE)

# Document tracking (DTS): needs a document word AND a dashed reference token
# containing a digit — "where are my documents" alone stays with the NLU.
_DOC_WORD_RE = re.compile(
	r"\b(documents?|communication|purchase (?:request|order)|job order|voucher)\b",
	re.IGNORECASE,
)
_DOC_REF_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*-)[A-Za-z0-9][A-Za-z0-9-]{3,63}\b")

_PROGRAMS_RE = re.compile(
	r"(\b(programs?|degrees?|courses? offered)\b.*\b(offer\w*|available|list)\b)"
	r"|(\b(what|which|list)\b.*\b(programs?|degrees?)\b)",
	re.IGNORECASE,
)

# "subject COSC 101" / "prerequisites of DCIT 26" — a subject code is 2-6
# letters followed by a 1-3 digit number.
_SUBJECT_CODE_RE = re.compile(r"\b([A-Za-z]{2,6})\s?-?\s?(\d{1,3})\b")
_SUBJECT_WORD_RE = re.compile(r"\b(subjects?|prerequisites?|pre-?req\w*)\b", re.IGNORECASE)


def route(text: str) -> Optional[tuple[str, dict[str, Any]]]:
	"""Map a message to (tool_name, args), or None when not connectors-shaped."""
	if not text:
		return None

	# Exact ORPS ticket format wins outright, word "ticket" or not.
	orps_match = _ORPS_FORMAT_RE.search(text)
	if orps_match:
		return "orps_track_ticket", {"ticket_number": orps_match.group(1).upper()}

	if _TICKET_WORD_RE.search(text):
		for match in _TICKET_TOKEN_RE.finditer(text):
			token = match.group(0).lstrip("#")
			if _TICKET_TOKEN_SKIP_RE.match(token):
				continue
			if token.lower() in {"ticket", "tickets"}:
				continue
			return "orps_track_ticket", {"ticket_number": token}

	if _DOC_WORD_RE.search(text):
		for match in _DOC_REF_RE.finditer(text):
			token = match.group(0)
			if _TICKET_TOKEN_SKIP_RE.match(token):
				continue
			return "dts_track_document", {"reference_number": token}

	if _SUBJECT_WORD_RE.search(text):
		code = _SUBJECT_CODE_RE.search(text)
		if code:
			return "courses_find_subject", {"search": f"{code.group(1).upper()} {code.group(2)}"}

	if _PROGRAMS_RE.search(text):
		return "courses_list_programs", {}

	return None


# ─────────────────────────────────────────────────────────────────────────────
# MCP call — one fresh SSE session per call, hard timeout. Low traffic makes
# pooling unnecessary here; if this bridge grows hot, lift the session pool
# and circuit breaker from ais_mcp.py.
# ─────────────────────────────────────────────────────────────────────────────


async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
	async def _inner() -> dict[str, Any]:
		async with sse_client(_MCP_URL) as (read_stream, write_stream):
			async with ClientSession(read_stream, write_stream) as session:
				await session.initialize()
				result = await session.call_tool(name, arguments)
		raw = result.content[0].text if result.content else "{}"
		return json.loads(raw)

	return await asyncio.wait_for(_inner(), timeout=_CALL_TIMEOUT_SECONDS)


# ─────────────────────────────────────────────────────────────────────────────
# Reply builders — text + optional table dict in the shape app.py's
# _table_from_dict expects ({title, columns:[{key,label,align}], rows, ...}).
# ─────────────────────────────────────────────────────────────────────────────


def _ticket_reply(args: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
	number = args.get("ticket_number", "")
	if not envelope.get("ok"):
		return {
			"text": (
				"I can't reach the ICT Helpdesk tracking service right now — "
				"please try again in a few minutes."
			),
			"table": None,
		}
	data = envelope.get("data") or {}
	if not data.get("found"):
		return {
			"text": (
				f"I couldn't find a helpdesk ticket with number “{number}”. "
				"Please double-check the number on your request receipt."
			),
			"table": None,
		}
	status = data.get("ticket_status", "Unknown")
	logs = data.get("logs") or []
	table = None
	if logs:
		table = {
			"title": f"Ticket {data.get('ticket_number', number)} — history",
			"columns": [
				{"key": "ticket_status", "label": "Status"},
				{"key": "timestamp", "label": "When"},
				{"key": "changed_by", "label": "By"},
				{"key": "remarks", "label": "Remarks"},
			],
			"rows": logs,
			"total_rows": len(logs),
		}
	return {
		"text": f"Helpdesk ticket **{data.get('ticket_number', number)}** is currently **{status}**.",
		"table": table,
		"suggestions": ["Track another ticket"],
	}


_LIST_COLUMN_PREFERENCE = ("code", "name", "title", "level", "units", "year_implemented", "id")
_LIST_MAX_COLUMNS = 5


def _listing_table(title: str, listing: dict[str, Any]) -> Optional[dict[str, Any]]:
	items = listing.get("items") or []
	if not items or not isinstance(items[0], dict):
		return None
	keys = [k for k in _LIST_COLUMN_PREFERENCE if k in items[0]]
	keys += [k for k in items[0] if k not in keys and isinstance(items[0][k], (str, int, float))]
	keys = keys[:_LIST_MAX_COLUMNS]
	footer = None
	if listing.get("truncated"):
		footer = f"Showing the first {len(items)} of {listing.get('total')} matches."
	return {
		"title": title,
		"columns": [{"key": k, "label": k.replace("_", " ").title()} for k in keys],
		"rows": [{k: it.get(k) for k in keys} for it in items],
		"footer": footer,
		"total_rows": listing.get("total"),
	}


def _courses_unreachable_text() -> dict[str, Any]:
	return {
		"text": (
			"I can't reach the course catalog right now — please try again in "
			"a few minutes, or check the official curriculum with the Registrar."
		),
		"table": None,
	}


def _programs_reply(envelope: dict[str, Any]) -> dict[str, Any]:
	if not envelope.get("ok"):
		return _courses_unreachable_text()
	listing = envelope.get("data") or {}
	total = listing.get("total", 0)
	if not total:
		return {"text": "The course catalog returned no degree programs for that query.", "table": None}
	return {
		"text": f"The CvSU course catalog lists **{total}** degree program{'s' if total != 1 else ''}.",
		"table": _listing_table("Degree programs", listing),
		"suggestions": ["Subjects in a curriculum", "Find a subject by code"],
	}


def _subject_reply(args: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
	if not envelope.get("ok"):
		return _courses_unreachable_text()
	listing = envelope.get("data") or {}
	query = args.get("search", "")
	total = listing.get("total", 0)
	if not total:
		return {
			"text": (
				f"I couldn't find a subject matching “{query}” in the course "
				"catalog. Try the exact subject code, e.g. “COSC 101”."
			),
			"table": None,
		}
	return {
		"text": f"Found **{total}** subject{'s' if total != 1 else ''} matching “{query}”.",
		"table": _listing_table(f"Subjects matching {query}", listing),
	}


def _document_reply(args: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
	reference = args.get("reference_number", "")
	if not envelope.get("ok"):
		return {
			"text": (
				"I can't reach the Document Tracking System right now — "
				"please try again in a few minutes."
			),
			"table": None,
		}
	data = envelope.get("data") or {}
	if not data.get("found"):
		note = data.get("note")
		detail = f" ({note})" if note else ""
		return {
			"text": (
				f"I couldn't find a document with reference number “{reference}”"
				f"{detail}. Please double-check the number on your document."
			),
			"table": None,
		}
	doc_type = data.get("document_type") or "Document"
	status = data.get("current_status") or "status unavailable"
	when = data.get("last_update")
	as_of = f" (as of {when})" if when else ""
	movements = data.get("movements") or []
	table = None
	if movements:
		table = {
			"title": f"{reference} — movement history",
			"columns": [
				{"key": "status", "label": "Status"},
				{"key": "when", "label": "When"},
				{"key": "remarks", "label": "Remarks"},
			],
			"rows": movements,
			"total_rows": data.get("total_movements"),
			"footer": (
				f"Showing the latest {len(movements)} of {data.get('total_movements')} movements."
				if (data.get("total_movements") or 0) > len(movements)
				else None
			),
		}
	return {
		"text": f"**{reference}** ({doc_type}): **{status}**{as_of}.",
		"table": table,
		"suggestions": ["Track another document"],
	}


_REPLY_BUILDERS = {
	"orps_track_ticket": _ticket_reply,
	"courses_list_programs": lambda args, env: _programs_reply(env),
	"courses_find_subject": _subject_reply,
	"dts_track_document": _document_reply,
}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point — same contract as ais_mcp.try_handle
# ─────────────────────────────────────────────────────────────────────────────


async def try_handle(message: str, session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
	"""Detect a connectors-shaped query, call the tool, format the reply.

	Return shapes (mirrors ais_mcp.try_handle):
	  • ``None`` — not connectors-shaped; Diwa's normal NLU pipeline runs.
	  • ``{"text": str, "table": dict | None, "suggestions": list | None}``
	    — the connectors server handled the query.
	  • ``{"text": None, "failure_note": str}`` — the query routed but the MCP
	    transport failed; caller runs NLU and prepends the note.
	"""
	if not (_MCP_AVAILABLE and _ENABLED):
		return None
	routed = route(message)
	if routed is None:
		return None
	tool_name, args = routed

	try:
		envelope = await _call_tool(tool_name, args)
	except asyncio.TimeoutError:
		_logger.warning(
			"connectors_mcp tool=%s ok=False reason=timeout limit=%.1fs",
			tool_name, _CALL_TIMEOUT_SECONDS,
		)
		return {"text": None, "failure_note": _FAILURE_NOTE}
	except Exception as exc:  # noqa: BLE001 — transport/SDK failure
		_logger.warning(
			"connectors_mcp tool=%s ok=False reason=transport err=%s",
			tool_name, exc.__class__.__name__,
		)
		return {"text": None, "failure_note": _FAILURE_NOTE}

	reply = _REPLY_BUILDERS[tool_name](args, envelope)
	_logger.info(
		"connectors_mcp tool=%s ok=%s", tool_name, bool(envelope.get("ok")),
	)
	return reply
