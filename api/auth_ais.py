"""Diwa-side AIS user authentication (Phase 2A, Wave 2).

Exchanges CvSU credentials for an AIS OAuth access+refresh token via
Frappe's password grant, caches the token per Diwa session_id, and
refreshes silently on expiry. Tokens are NEVER written to disk — they
live in-memory only and evaporate on uvicorn restart.

Why password grant: CvSU has dev SSO but no prod config yet. Password
grant is the bootstrap path; Wave 4 swaps the issuance to JWT bearer
once SSO lands (token cache and downstream `/ais/write` plumbing stay
unchanged).

Design notes:
- One token per session_id, not per user_id, because a user can have
  multiple Sevi web tabs each with its own session.
- Lock per session_id to serialize concurrent refreshes (rotating refresh
  tokens would otherwise invalidate each other).
- Cache ALSO holds {user, full_name, roles} so /auth/whoami doesn't need
  another OAuth call.
- This module knows nothing about MCP write tools — it just hands a
  bearer token back to whatever needs to call AIS as the human user.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

_logger = logging.getLogger("diwa.auth_ais")

# AIS Desk base URL — same value used by the deep-link helper in ais_mcp.py.
# We hit the OAuth endpoint at this origin. Diwa's server-side egress to
# this URL must NOT be intercepted by Cloudflare Access (open question #2
# in the plan — verify on first real call).
_AIS_DESK_URL = os.environ.get("AIS_DESK_URL", "http://accounting.localhost:8002").rstrip("/")
# When AIS_BASE_URL is set (matches the MCP server's convention), prefer it
# for the OAuth token endpoint so Diwa can reach Frappe directly without
# going through the public tunnel — avoids the CF Access concern entirely.
_AIS_BASE_URL = os.environ.get("AIS_BASE_URL", _AIS_DESK_URL).rstrip("/")
_AIS_HOST_HEADER = os.environ.get("AIS_HOST_HEADER")  # e.g. "accounting.localhost:8002"

# OAuth Client credentials. For now this reuses the same client_id used by
# the MCP server (`diwa-bot` flow). Wave 3 provisions a SEPARATE Client
# configured for password grant (`cvsu-ais-mcp-userflow`) — until then,
# the same client works because Frappe issues per-grant-type tokens.
_OAUTH_CLIENT_ID = os.environ.get("AIS_OAUTH_CLIENT_ID", "")
_OAUTH_CLIENT_SECRET = os.environ.get("AIS_OAUTH_CLIENT_SECRET", "")

# Token refresh buffer — refresh when <60s left so calls don't race expiry.
_REFRESH_BUFFER_SECONDS = 60


class AuthError(Exception):
	"""Raised on any authentication failure surfacing to /auth/* endpoints."""
	def __init__(self, status_code: int, message: str):
		super().__init__(message)
		self.status_code = status_code
		self.message = message


class _SessionToken:
	"""One per session_id. Holds the token set + identity claims + a per-
	session lock to serialize refreshes (rotating refresh tokens require
	this — concurrent refresh would invalidate one of them)."""

	__slots__ = ("access_token", "refresh_token", "expires_at",
	             "user", "full_name", "roles", "lock")

	def __init__(self, access_token: str, refresh_token: str, expires_at: float,
	             user: str, full_name: str, roles: list[str]) -> None:
		self.access_token = access_token
		self.refresh_token = refresh_token
		self.expires_at = expires_at
		self.user = user
		self.full_name = full_name
		self.roles = roles
		self.lock = asyncio.Lock()

	def is_expiring(self) -> bool:
		return self.expires_at - time.time() < _REFRESH_BUFFER_SECONDS

	def public(self) -> dict:
		"""Safe-to-return identity snapshot (NEVER includes the tokens)."""
		return {"user": self.user, "full_name": self.full_name,
		        "roles": self.roles, "expires_in": max(0, int(self.expires_at - time.time()))}


# session_id -> _SessionToken. Process-local; vanishes on uvicorn restart.
_sessions: dict[str, _SessionToken] = {}


def _frappe_headers() -> dict[str, str]:
	"""Common headers for any direct call to Frappe — propagates the Host
	override when AIS_BASE_URL is an IP (Windows .localhost workaround)."""
	h = {"Accept": "application/json"}
	if _AIS_HOST_HEADER:
		h["Host"] = _AIS_HOST_HEADER
	return h


def _decode_jwt_claims_unsafe(id_token: str) -> dict:
	"""Decode the unsigned middle segment of a JWT.

	UNSAFE — we don't verify the signature here because:
	  (a) Frappe just minted this token over an authenticated session;
	  (b) the token wasn't transmitted across an untrusted boundary;
	  (c) we use only the user/name/email/roles claims for caching, not
	      for any security decision (those live at the Frappe layer).
	If this ever needs to verify, use joserfc or the anthropic-installed
	authlib JWS verifier with Frappe's OAuth public key.
	"""
	import base64, json
	try:
		parts = id_token.split(".")
		if len(parts) < 2:
			return {}
		pad = "=" * (-len(parts[1]) % 4)
		return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
	except Exception:  # noqa: BLE001 — best-effort; never block on decode failure
		return {}


async def _request_token(grant: dict) -> dict:
	"""POST to Frappe's token endpoint. Returns parsed payload or raises AuthError."""
	if not _OAUTH_CLIENT_ID or not _OAUTH_CLIENT_SECRET:
		raise AuthError(503, "OAuth client credentials not configured on Diwa.")
	data = {**grant, "client_id": _OAUTH_CLIENT_ID, "client_secret": _OAUTH_CLIENT_SECRET}
	try:
		async with httpx.AsyncClient(timeout=15.0) as http:
			resp = await http.post(
				f"{_AIS_BASE_URL}/api/method/frappe.integrations.oauth2.get_token",
				data=data,
				headers=_frappe_headers(),
			)
	except httpx.HTTPError as exc:
		_logger.exception("auth_ais token-endpoint transport error grant=%s", grant.get("grant_type"))
		raise AuthError(503, f"AIS auth endpoint unreachable: {exc.__class__.__name__}") from exc
	if resp.status_code == 401 or resp.status_code == 403:
		raise AuthError(401, "Invalid credentials.")
	if resp.status_code >= 400:
		_logger.warning("auth_ais token-endpoint %d body=%s", resp.status_code, resp.text[:300])
		raise AuthError(502, f"AIS auth endpoint returned {resp.status_code}.")
	return resp.json()


def _store_session(session_id: str, payload: dict, previous_refresh: str | None = None,
                   identity: dict | None = None) -> _SessionToken:
	"""Persist a fresh token set in the per-session cache and return it.

	Identity (user, full_name, roles) comes from one of two sources:
	  - The id_token JWT (authorization_code grant always includes it).
	  - An explicit ``identity`` dict the caller fetched out-of-band
	    (password grant doesn't issue id_token, so login() does the
	    follow-up call after the token exchange).
	"""
	claims = _decode_jwt_claims_unsafe(payload.get("id_token", ""))
	# Identity sources, in priority order: explicit (passed by login()) →
	# id_token claims → unknown placeholders.
	identity = identity or {}
	tok = _SessionToken(
		access_token=payload["access_token"],
		refresh_token=payload.get("refresh_token") or previous_refresh or "",
		expires_at=time.time() + int(payload.get("expires_in", 3600)),
		user=identity.get("user") or claims.get("email") or claims.get("name") or "unknown",
		full_name=identity.get("full_name") or claims.get("name") or "",
		roles=list(identity.get("roles") or claims.get("roles") or []),
	)
	_sessions[session_id] = tok
	return tok


async def _fetch_identity(access_token: str) -> dict:
	"""Resolve user/full_name/roles from a freshly-minted access token.

	Used after password grant (which doesn't include id_token). Best-effort:
	any failure here returns a partial dict — login still succeeds with
	a placeholder identity, just less useful whoami / role-gated UI.
	"""
	headers = {
		"Authorization": f"Bearer {access_token}",
		"Accept": "application/json",
		**_frappe_headers(),
	}
	out: dict = {}
	try:
		async with httpx.AsyncClient(timeout=10.0) as http:
			# get_logged_user returns just the email — but it confirms the
			# token is valid AND tells us who Frappe thinks we are.
			r = await http.get(
				f"{_AIS_BASE_URL}/api/method/frappe.auth.get_logged_user",
				headers=headers,
			)
			if r.status_code == 200:
				out["user"] = (r.json() or {}).get("message") or ""

			# Pull full_name + roles via the User doctype. Skipped if we
			# couldn't establish the user above.
			if out.get("user"):
				r2 = await http.get(
					f"{_AIS_BASE_URL}/api/method/frappe.client.get_value",
					headers=headers,
					params={
						"doctype": "User",
						"filters": '{"name":"' + out["user"] + '"}',
						"fieldname": '["full_name"]',
					},
				)
				if r2.status_code == 200:
					out["full_name"] = ((r2.json() or {}).get("message") or {}).get("full_name", "")

				# Roles via Has Role child table.
				r3 = await http.get(
					f"{_AIS_BASE_URL}/api/method/frappe.client.get_list",
					headers=headers,
					params={
						"doctype": "Has Role",
						"filters": '{"parent":"' + out["user"] + '"}',
						"fields": '["role"]',
						"limit_page_length": "100",
					},
				)
				if r3.status_code == 200:
					rows = (r3.json() or {}).get("message") or []
					out["roles"] = [r["role"] for r in rows if r.get("role")]
	except httpx.HTTPError:
		_logger.exception("auth_ais _fetch_identity transport error — login continues with partial identity")
	return out


# ── public API ───────────────────────────────────────────────────────────

async def login(session_id: str, username: str, password: str) -> dict:
	"""Exchange CvSU credentials for an AIS token. Caches under session_id.

	Returns the public identity snapshot (user, full_name, roles, expires_in).
	Raises AuthError on bad credentials / transport failure.
	"""
	if not session_id or not username or not password:
		raise AuthError(400, "session_id, username, and password are all required.")
	payload = await _request_token({
		"grant_type": "password",
		"username": username,
		"password": password,
		"scope": "openid all",
	})
	# Password grant doesn't include id_token — fetch identity explicitly
	# using the newly-minted access token so whoami / role-gated UI works.
	identity = await _fetch_identity(payload["access_token"])
	tok = _store_session(session_id, payload, identity=identity)
	_logger.info("auth_ais login session=%s user=%s roles=%d",
	             session_id, tok.user, len(tok.roles))
	return tok.public()


async def logout(session_id: str) -> None:
	"""Drop the cached token for this session. No-op if there isn't one.
	We don't bother revoking the token at Frappe — it expires within an
	hour anyway, and revocation adds a round-trip that can fail."""
	_sessions.pop(session_id, None)


async def whoami(session_id: str) -> Optional[dict]:
	"""Return the cached identity snapshot, or None when not logged in."""
	tok = _sessions.get(session_id)
	if tok is None:
		return None
	return tok.public()


async def get_user_token(session_id: str) -> str:
	"""Hand back a valid access token for the session, refreshing if needed.

	Raises AuthError(401) when there's no session, AuthError(502) on
	refresh failure. Callers (specifically the /ais/write endpoint) pass
	the returned string as __auth_token on the MCP write tool call.
	"""
	tok = _sessions.get(session_id)
	if tok is None:
		raise AuthError(401, "No active AIS session — log in first.")
	if not tok.is_expiring():
		return tok.access_token
	async with tok.lock:  # serialize refreshes for this session
		# Re-check inside lock: another coroutine may have refreshed.
		tok = _sessions.get(session_id)
		if tok is None:
			raise AuthError(401, "Session evicted during refresh.")
		if not tok.is_expiring():
			return tok.access_token
		try:
			payload = await _request_token({
				"grant_type": "refresh_token",
				"refresh_token": tok.refresh_token,
			})
		except AuthError as exc:
			# Refresh failed (token revoked / rotated / expired). Drop the
			# session so the user is forced back to the login modal.
			_sessions.pop(session_id, None)
			raise AuthError(401, "AIS session expired — please log in again.") from exc
		# Some OAuth servers rotate refresh tokens, others don't. Fall
		# back to the previous refresh_token when the new payload omits it.
		tok = _store_session(session_id, payload, previous_refresh=tok.refresh_token)
		return tok.access_token


def session_count() -> int:
	"""Diagnostic — count of currently-cached sessions. Used by /auth/whoami
	admin variants and the metrics endpoint."""
	return len(_sessions)
