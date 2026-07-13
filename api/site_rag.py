"""Official-website retrieval tier (the "site tier" of the hybrid brain).

TF-IDF retrieval over docs/site_corpus.txt — a doc-marked dump of the official
CvSU website (WordPress posts + pages with their metadata), produced by
sync_corpus() from the portal's REST API. Two consumers in
HybridChatbot.predict(), mirroring charter_rag:

  • LLM augmentation — when the site has a passage relevant to the question,
    it is handed to the LLM (alongside any charter passage) so the answer is
    grounded in the official website with a title/URL citation.
  • Verbatim fallback — when NO LLM is available and a passage scores above a
    higher threshold, the passage itself (with citation) is returned instead
    of the static "I didn't understand" fallback.

Corpus format (one block per post/page):

    ----- DOC | <title> | <url> | <date> -----
    <plain text>

Env:
    SITE_RAG_ENABLED    — "0" disables both consumers (default "1")
    SITE_RAG_PATH       — override the corpus location
    SITE_CORPUS_URL     — portal base URL for sync_corpus; required, deployment-
                          local, never committed (e.g. an internal WordPress
                          mirror)
    SITE_PUBLIC_BASE_URL — public domain substituted into stored links/
                          citations so the internal portal host never reaches
                          the corpus file or end users (default
                          https://cvsu.edu.ph)
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger("diwa.site_rag")

_ENABLED = os.environ.get("SITE_RAG_ENABLED", "1") == "1"
_DEFAULT_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
	"docs", "site_corpus.txt",
)
_PATH = os.environ.get("SITE_RAG_PATH", _DEFAULT_PATH)
# The portal host is deployment-local (often an internal mirror) and must be
# configured via env — never committed. Stored links/citations are rewritten
# onto the public domain so internal hosts never reach users or the corpus.
_DEFAULT_BASE_URL = os.environ.get("SITE_CORPUS_URL", "")
_PUBLIC_BASE_URL = os.environ.get("SITE_PUBLIC_BASE_URL", "https://cvsu.edu.ph").rstrip("/")

# Linear-time (no backtracking): fields are |-free — sync_corpus sanitizes
# the title, and URLs/ISO dates never contain the delimiter.
_DOC_MARKER_RE = re.compile(
	r"^----- DOC \| ([^|\n]*) \| ([^|\n]*) \| ([^|\n]*) -----[ \t]*$", re.MULTILINE
)
_CHUNK_CHARS = 700
_OVERLAP_LINES = 3

# Coverage-weighted cosine thresholds, calibrated on the synced portal corpus
# (president/WELA/seagrass/WURI/AACCUP queries: 0.125-0.264; off-topic probes
# top out at 0.121 with zero bigram hits). Off-topic queries are additionally
# blocked by the Nonsense/Scope gates before either consumer fires, and the
# verbatim tier requires >= 1 bigram hit.
AUGMENT_MIN_SCORE = 0.10
QUOTE_MIN_SCORE = 0.15

# WordPress boilerplate that must not enter the retrieval corpus.
_SKIP_TITLES = {"sample page", "hello world!"}


@dataclass(frozen=True)
class Passage:
	score: float
	title: str
	url: str
	date: str
	text: str
	bigram_hits: int = 0

	def citation(self) -> str:
		when = f", {self.date}" if self.date else ""
		return f"“{self.title}” — official CvSU site{when} ({self.url})"


def _clean(text: str) -> str:
	text = text.replace("’", "'").replace("‘", "'")
	text = re.sub(r"[ \t]+", " ", text)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()


def _strip_html(markup: str) -> str:
	markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
	markup = re.sub(r"<br\s*/?>|</p>|</h[1-6]>|</li>|</tr>|</div>", "\n", markup, flags=re.I)
	markup = re.sub(r"<[^>]+>", " ", markup)
	return _clean(html.unescape(markup))


def _split_docs(raw: str) -> list[tuple[str, str, str, str]]:
	"""Return (title, url, date, text) tuples from the doc-marked corpus."""
	docs: list[tuple[str, str, str, str]] = []
	matches = list(_DOC_MARKER_RE.finditer(raw))
	for i, m in enumerate(matches):
		end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
		body = _clean(raw[m.end():end])
		if body:
			docs.append((m.group(1), m.group(2), m.group(3), body))
	return docs


def _chunk_docs(docs: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
	"""Sliding line-windows of ~_CHUNK_CHARS per doc, with line overlap.

	The title is prepended to every chunk of its doc — post titles carry the
	strongest retrieval signal ("CvSU ranks 217th in WURI's top 500 ...").
	"""
	chunks: list[tuple[str, str, str, str]] = []
	for title, url, date, body in docs:
		lines = [ln for ln in body.split("\n") if ln.strip()]
		if not lines:
			continue
		start = 0
		while start < len(lines):
			size = 0
			end = start
			while end < len(lines) and size < _CHUNK_CHARS:
				size += len(lines[end]) + 1
				end += 1
			chunk = "\n".join(lines[start:end]).strip()
			if chunk:
				chunks.append((title, url, date, f"{title}\n{chunk}"))
			if end >= len(lines):
				break
			start = max(end - _OVERLAP_LINES, start + 1)
	return chunks


class SiteIndex:
	def __init__(self, path: str = _PATH):
		self._chunks: list[tuple[str, str, str, str]] = []
		self._vectorizer = None
		self._matrix = None
		try:
			with open(path, encoding="utf-8") as fh:
				raw = fh.read()
		except OSError as exc:
			_logger.warning("site corpus not readable (%s) — site tier disabled", exc)
			return
		self._chunks = _chunk_docs(_split_docs(raw))
		if not self._chunks:
			return
		from sklearn.feature_extraction.text import TfidfVectorizer

		self._vectorizer = TfidfVectorizer(
			lowercase=True, ngram_range=(1, 2), sublinear_tf=True, min_df=1,
			stop_words="english",
		)
		self._matrix = self._vectorizer.fit_transform(t for *_, t in self._chunks)
		_logger.info("site index ready: %d chunks", len(self._chunks))

	@property
	def available(self) -> bool:
		return self._matrix is not None

	def retrieve(self, query: str, k: int = 3) -> list[Passage]:
		if not self.available or not query or not query.strip():
			return []
		from sklearn.metrics.pairwise import linear_kernel

		q = self._vectorizer.transform([query])
		scores = linear_kernel(q, self._matrix)[0]
		# Coverage penalty — same rationale as charter_rag: cosine over
		# TF-IDF ignores out-of-vocabulary query terms, so scale by the
		# fraction of the query's content words the passage contains.
		analyzer = self._vectorizer.build_analyzer()
		query_terms = list(analyzer(query))
		terms = {t for t in query_terms if " " not in t}
		query_bigrams = {t for t in query_terms if " " in t}
		order = scores.argsort()[::-1][: max(k * 4, 8)]
		passages = []
		for i in order:
			if scores[i] <= 0.0:
				break
			title, url, date, text = self._chunks[i]
			text_lc = text.lower()
			coverage = (
				sum(1 for t in terms if t in text_lc) / len(terms) if terms else 0.0
			)
			chunk_terms = set(analyzer(text))
			passages.append(
				Passage(
					score=float(scores[i]) * (0.5 + 0.5 * coverage),
					title=title,
					url=url,
					date=date,
					text=text,
					bigram_hits=len(query_bigrams & chunk_terms),
				)
			)
		passages.sort(key=lambda p: p.score, reverse=True)
		return passages[:k]


_index: Optional[SiteIndex] = None


def get_index() -> Optional[SiteIndex]:
	"""Lazy singleton; returns None when disabled or the corpus is missing."""
	global _index
	if not _ENABLED:
		return None
	if _index is None:
		_index = SiteIndex()
	return _index if _index.available else None


def reload_index() -> Optional[SiteIndex]:
	"""Drop the singleton and rebuild from disk (used after sync_corpus)."""
	global _index
	_index = None
	return get_index()


def verbatim_reply(passage: Passage) -> str:
	"""Format a passage as a direct answer for the no-LLM fallback path."""
	text = passage.text
	if len(text) > 900:
		text = text[:900].rsplit(" ", 1)[0] + " …"
	return (
		f"From the official CvSU website — {passage.citation()}:\n\n{text}\n\n"
		"(Quoted from the official site — see the linked page for the full article.)"
	)


# ---------------------------------------------------------------------------
# Corpus sync — pulls the portal's posts + pages (with metadata) via the
# WordPress REST API and writes the doc-marked corpus file. Pages built with
# page builders return empty REST content, so those fall back to scraping the
# rendered page HTML.
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 30) -> bytes:
	req = urllib.request.Request(url, headers={"User-Agent": "DIWA-site-sync/1.0"})
	with urllib.request.urlopen(req, timeout=timeout) as resp:
		return resp.read()


def _fetch_collection(base_url: str, kind: str) -> list[dict]:
	items: list[dict] = []
	page = 1
	while True:
		url = (
			f"{base_url}/wp-json/wp/v2/{kind}?per_page=100&page={page}"
			"&_fields=title,link,date,content"
		)
		try:
			batch = json.loads(_http_get(url).decode("utf-8"))
		except Exception:
			if page == 1:
				raise
			break
		if not isinstance(batch, list) or not batch:
			break
		items.extend(batch)
		if len(batch) < 100:
			break
		page += 1
	return items


def _is_safe_fetch_target(url: str, base_url: str) -> bool:
	"""Only fetch http(s) URLs on the configured portal host.

	sync_corpus() follows the `link` field the portal's own REST response
	supplies for the page-builder fallback. That value is attacker-influenced
	if the portal is ever compromised, and urllib.request.urlopen follows
	file:// and other schemes by default — so without this check a malicious
	`link` could make the server read local files or reach internal-only
	network endpoints, whose content then gets stored in the corpus and
	quoted verbatim to end users. Restrict strictly to the http(s) scheme and
	the exact host we were told to sync from.
	"""
	try:
		parts = urllib.parse.urlsplit(url)
		base = urllib.parse.urlsplit(base_url)
	except ValueError:
		return False
	return parts.scheme in ("http", "https") and parts.hostname == base.hostname and bool(parts.hostname)


def _rendered_page_text(url: str, base_url: str) -> str:
	"""Scrape the rendered HTML of a page whose REST content is empty."""
	if not _is_safe_fetch_target(url, base_url):
		_logger.warning("refusing to fetch page outside the configured portal host: %s", url)
		return ""
	raw = _http_get(url).decode("utf-8", "replace")
	m = re.search(
		r"<main[^>]*>(.*?)</main>|<article[^>]*>(.*?)</article>",
		raw, flags=re.S | re.I,
	)
	body = next((g for g in (m.groups() if m else []) if g), None)
	return _strip_html(body if body else raw)


def _public_link(link: str, base_url: str) -> str:
	"""Swap the (possibly internal) portal host for the public domain.

	Replaces scheme+host unconditionally via URL parsing rather than a
	string-prefix match against base_url — WordPress's own `siteurl` can
	differ from SITE_CORPUS_URL by a trailing slash, scheme, or port, and a
	prefix mismatch there must never leave the internal host in a link shown
	to users. base_url is only used to detect "this is a portal-relative
	link" when siteurl already differs.
	"""
	if not link:
		return link
	public = urllib.parse.urlsplit(_PUBLIC_BASE_URL)
	parts = urllib.parse.urlsplit(link)
	if parts.netloc:
		# Absolute link: always rehost onto the public domain, whatever the
		# original scheme/host was (covers siteurl != base_url).
		return urllib.parse.urlunsplit(
			(public.scheme, public.netloc, parts.path, parts.query, parts.fragment)
		)
	if base_url:
		# Portal-relative link ("/2026/..."): anchor it under the public base.
		return _PUBLIC_BASE_URL + link if link.startswith("/") else f"{_PUBLIC_BASE_URL}/{link}"
	return link


def _item_block(item: dict, base_url: str) -> Optional[str]:
	"""One doc-marked corpus block for a REST item, or None when skippable."""
	title = _strip_html(item.get("title", {}).get("rendered", "")).strip()
	if title.lower() in _SKIP_TITLES:
		return None
	portal_link = (item.get("link") or "").strip()
	text = _strip_html(item.get("content", {}).get("rendered", ""))
	if not text and portal_link:
		# Page-builder pages return empty REST content — scrape the
		# rendered page from the portal itself.
		try:
			text = _rendered_page_text(portal_link, base_url)
		except Exception:
			text = ""
	if not text:
		return None
	# "|" is the doc-marker delimiter; keep titles from ever forging it.
	title = title.replace("|", "/")
	link = _public_link(portal_link, base_url)
	date = (item.get("date") or "")[:10]
	return f"----- DOC | {title} | {link} | {date} -----\n{text}\n"


def sync_corpus(base_url: str = "", out_path: str = _PATH) -> dict:
	"""Fetch posts + pages from the portal and rewrite the corpus file.

	Stored links are rewritten onto SITE_PUBLIC_BASE_URL so citations shown
	to users (and the corpus file itself) never carry the internal portal
	host. Returns {"docs": n, "skipped": n, "path": out_path}. Raises on a
	dead or unconfigured portal so callers can report the failure instead of
	silently truncating the corpus.
	"""
	base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
	if not base_url:
		raise ValueError(
			"SITE_CORPUS_URL is not configured — set the portal base URL in the local .env"
		)
	blocks: list[str] = []
	skipped = 0
	for kind in ("pages", "posts"):
		for item in _fetch_collection(base_url, kind):
			block = _item_block(item, base_url)
			if block is None:
				skipped += 1
			else:
				blocks.append(block)
	os.makedirs(os.path.dirname(out_path), exist_ok=True)
	with open(out_path, "w", encoding="utf-8") as fh:
		fh.write("\n".join(blocks))
	_logger.info("site corpus synced: %d docs (%d skipped) -> %s", len(blocks), skipped, out_path)
	return {"docs": len(blocks), "skipped": skipped, "path": out_path}
