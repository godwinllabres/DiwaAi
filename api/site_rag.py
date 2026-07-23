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

import hashlib
import html
import json
import logging
import os
import re
import time
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
# Overlap in CHARACTERS, not lines. WordPress bodies are paragraph-per-line, so
# the old 3-line overlap was frequently more than half a chunk — it inflated the
# index and let two near-identical chunks of one article take both grounding
# slots the LLM is given.
_OVERLAP_CHARS = 150

# Coverage-weighted cosine thresholds, calibrated on the synced portal corpus
# (president/WELA/seagrass/WURI/AACCUP queries: 0.125-0.264; off-topic probes
# top out at 0.121 with zero bigram hits). Off-topic queries are additionally
# blocked by the Nonsense/Scope gates before either consumer fires, and the
# verbatim tier requires >= 1 bigram hit.
AUGMENT_MIN_SCORE = 0.10
QUOTE_MIN_SCORE = 0.15

# WordPress boilerplate that must not enter the retrieval corpus.
_SKIP_TITLES = {"sample page", "hello world!"}

# ---------------------------------------------------------------------------
# Corpus inclusion filters.
#
# These exist because the portal is a real university website, not a curated
# knowledge base: ~14% of its pages are page-builder shells that strip to zero
# text, ~31% of its posts are procurement notices, and several plugin pages
# contain a single word. Unfiltered, all of that competes for the two grounding
# slots the LLM gets, and short junk wins on cosine precisely BECAUSE it is
# short (a one-word doc matches a one-word query perfectly).
# ---------------------------------------------------------------------------

# Minimum plaintext length, applied AFTER the page-builder fallback so it never
# fights the rescue path. 150 sits inside the measured empty band: the portal's
# real docs bottom out at 154 chars (a University Calendar page) while the
# shells top out at 122. Measured effect: /events-2/, whose entire text is the
# word "CONTENTS", scored 0.324 — above QUOTE_MIN_SCORE — and would have been
# quoted verbatim as an official answer; at 8 chars it is now dropped.
# NOT 200: that also deletes the University Calendar pages (154/187/193), and
# "when does enrollment start" is one of the most-asked live questions.
MIN_PLAINTEXT_CHARS = 150

# Post categories excluded server-side via `categories_exclude`. Verified
# against the portal: these four hold 410 posts, the remaining 902 are news,
# announcements and board-exam results — 410 + 902 == 1312 exactly.
# Slugs, not ids: ids are portal-local and mean nothing on a different mirror.
_SKIP_CATEGORY_SLUGS = ("bidding", "transparency-seal", "opportunities",
                        "request-for-quotation")

# Plugin/commerce furniture that clears the length gate but is never an answer.
# Matched on the exact last path segment so it cannot swallow a real slug.
# /my-events/ leaks a raw `[events-calendar-templates ...]` shortcode.
#
# The second group is AGGREGATOR PAGES. These matter more than they look:
# _SKIP_CATEGORY_SLUGS excludes procurement POSTS server-side, but pages carry
# no categories, so the same procurement text walks back in through pages that
# list every notice. Measured on the public portal, these four alone were
# 436 KB — 17.7% of the whole corpus — and /request-for-quotation-archive/ was
# a single 298 KB document. The listing pages (GAD/news/announcements) are
# headline lists whose underlying articles are already ingested as posts.
_SKIP_SLUGS = frozenset({
	# plugin / commerce furniture
	"cart", "checkout", "my-account", "shop",
	"events", "events-2", "my-events",
	# procurement aggregators (pages have no categories to exclude on)
	"request-for-quotation", "request-for-quotation-archive",
	"invitation-to-bid", "invitation-to-bid-archive",
	# headline listings that duplicate already-ingested posts
	"gad-updates", "news-updates", "announcements",
})

# Titles that mark a document as an index or an unpublished duplicate rather
# than content. Measured: 38 month archives ("July 2017") worth 76 KB of pure
# link lists, and 2 "... Draft" pages that near-duplicate their published twin
# ("Research Center Draft" 25,813 chars vs "Research Center" 25,780).
_SKIP_TITLE_RES = (
	re.compile(r"^[A-Z][a-z]+ \d{4}$"),        # month/year archive page
	re.compile(r"\bdraft$", re.IGNORECASE),    # unpublished duplicate
)

# Scrape the rendered HTML of pages whose REST content is empty.
#
# Default ON, and it must stay on: EVERY page on the internal mirror returns
# empty content.rendered, so this path is the only reason Admission,
# Administration, History, Mandate/Mission/Vision and Transparency Seal are in
# the corpus at all. Turning it off silently drops all eight mirror pages —
# including the one carrying the university President, which the retrieval
# tier test asserts on.
#
# Its yield is portal-dependent: on the public site 32 of 226 pages trigger it
# and recover ~0 usable characters (page-builder markup with no text nodes),
# costing ~32 requests per sync. That waste is bounded and one-off per sync,
# and MIN_PLAINTEXT_CHARS discards whatever it fails to rescue — so the
# correct trade is to pay it rather than lose the mirror's curated pages.
_SCRAPE_FALLBACK = os.environ.get("SITE_SCRAPE_FALLBACK", "1") == "1"

# Collections to sync. Deliberately NOT tribe_events: /wp/v2/tribe_events is
# registered but returns [] (x-wp-total 0), so adding it here would silently
# ingest nothing, and the working /tribe/events/v1/events base has 0 upcoming
# events with a newest start_date of 2024-08-13 — ingesting it would let Sevi
# cite two-year-old events as current. Also NOT jobpost (staff vacancies).
_SYNC_KINDS = ("pages", "posts")

# Courtesy delay between portal requests during a sync.
_SYNC_DELAY_SECONDS = float(os.environ.get("SITE_SYNC_DELAY", "1.0"))


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


def _budgeted_lines(body: str, budget: int) -> list[str]:
	"""Non-blank lines, with any single line longer than `budget` hard-split.

	A WordPress paragraph can exceed the whole chunk budget on its own, and a
	line-window chunker cannot subdivide it — that one line becomes an
	oversized chunk no matter how the window is sized. Split on the last space
	inside the budget so words stay intact.
	"""
	out: list[str] = []
	for raw in body.split("\n"):
		line = raw.strip()
		if not line:
			continue
		while len(line) > budget:
			cut = line.rfind(" ", 0, budget)
			if cut <= 0:
				cut = budget
			out.append(line[:cut].strip())
			line = line[cut:].strip()
		if line:
			out.append(line)
	return out


def _overlap_start(lines: list[str], start: int, end: int) -> int:
	"""Index to resume from so ~_OVERLAP_CHARS of the last chunk repeat."""
	back = end - 1
	overlap = 0
	while back > start and overlap + len(lines[back]) + 1 <= _OVERLAP_CHARS:
		overlap += len(lines[back]) + 1
		back -= 1
	return back


def _chunk_docs(docs: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
	"""Sliding line-windows of ~_CHUNK_CHARS per doc, with line overlap.

	The title is prepended to every chunk of its doc — post titles carry the
	strongest retrieval signal ("CvSU ranks 217th in WURI's top 500 ...").
	"""
	chunks: list[tuple[str, str, str, str]] = []
	for title, url, date, body in docs:
		# The title is prepended to every chunk, so it has to come out of the
		# budget — otherwise the emitted chunk overruns _CHUNK_CHARS by exactly
		# the title length.
		budget = max(_CHUNK_CHARS - len(title) - 1, 200)
		lines = _budgeted_lines(body, budget)
		if not lines:
			continue
		start = 0
		while start < len(lines):
			size = 0
			end = start
			# Stop BEFORE crossing the budget. The old loop tested `size <
			# _CHUNK_CHARS` and then added the line, so it stopped AFTER
			# crossing: 94% of chunks overran, median 836 chars. That matters
			# because the LLM is handed only text[:700] — the tail was scored
			# on but never shown, so a chunk could win retrieval on words it
			# was then unable to quote.
			while end < len(lines) and (end == start or size + len(lines[end]) + 1 <= budget):
				size += len(lines[end]) + 1
				end += 1
			chunk = "\n".join(lines[start:end]).strip()
			if chunk:
				chunks.append((title, url, date, f"{title}\n{chunk}"))
			if end >= len(lines):
				break
			start = max(_overlap_start(lines, start, end), start + 1)
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
	# Identifiable UA: a sync now runs against the public university site, and
	# that traffic should be attributable to this bot rather than to a generic
	# urllib client.
	req = urllib.request.Request(
		url, headers={"User-Agent": "SeviBot/1.0 (+https://cvsu.edu.ph; CvSU virtual assistant corpus sync)"}
	)
	with urllib.request.urlopen(req, timeout=timeout) as resp:
		return resp.read()


def _skip_category_ids(base_url: str) -> str:
	"""Resolve _SKIP_CATEGORY_SLUGS to a `categories_exclude` id list.

	Fails SOFT: a portal without this taxonomy (or with the endpoint disabled)
	must still yield a corpus, just an unfiltered one. Slugs are resolved at
	sync time because category ids are portal-local — the ids that mean
	"bidding" on the public site mean nothing on an internal mirror.
	"""
	try:
		raw = _http_get(f"{base_url}/wp-json/wp/v2/categories?per_page=100&_fields=id,slug")
		cats = json.loads(raw.decode("utf-8"))
		ids = sorted(str(c["id"]) for c in cats if c.get("slug") in _SKIP_CATEGORY_SLUGS)
	except Exception as exc:  # noqa: BLE001 — filtering is best-effort
		_logger.warning("category filter unavailable (%s); syncing unfiltered", exc)
		return ""
	if ids:
		_logger.info("excluding post categories %s -> ids %s",
					 ",".join(_SKIP_CATEGORY_SLUGS), ",".join(ids))
	return ",".join(ids)


def _collection_url(base_url: str, kind: str, page: int, exclude_ids: str) -> str:
	url = (
		f"{base_url}/wp-json/wp/v2/{kind}?per_page=100&page={page}"
		"&_fields=title,link,date,content"
	)
	# Server-side exclusion: cheaper than fetching 410 procurement notices and
	# discarding them locally. Only posts carry these categories.
	if exclude_ids and kind == "posts":
		url += f"&categories_exclude={exclude_ids}"
	return url


def _fetch_collection(base_url: str, kind: str, exclude_ids: str = "") -> list[dict]:
	items: list[dict] = []
	page = 1
	while True:
		url = _collection_url(base_url, kind, page, exclude_ids)
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
		if _SYNC_DELAY_SECONDS > 0:
			time.sleep(_SYNC_DELAY_SECONDS)
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


def _slug_of(link: str) -> str:
	"""Last path segment of a portal link, lowercased ('' when there is none)."""
	path = urllib.parse.urlsplit(link).path.strip("/")
	return path.rsplit("/", 1)[-1].lower() if path else ""


def _item_block(item: dict, base_url: str) -> Optional[str]:
	"""One doc-marked corpus block for a REST item, or None when skippable."""
	title = _strip_html(item.get("title", {}).get("rendered", "")).strip()
	if title.lower() in _SKIP_TITLES:
		return None
	if any(rx.search(title) for rx in _SKIP_TITLE_RES):
		return None
	portal_link = (item.get("link") or "").strip()
	if _slug_of(portal_link) in _SKIP_SLUGS:
		return None
	text = _strip_html(item.get("content", {}).get("rendered", ""))
	if _SCRAPE_FALLBACK and not text and portal_link:
		# Page-builder pages return empty REST content — scrape the
		# rendered page from the portal itself. Off by default; see
		# _SCRAPE_FALLBACK for the measurements behind that default.
		try:
			text = _rendered_page_text(portal_link, base_url)
		except Exception:
			text = ""
	# Length gate LAST, so it judges whatever the fallback managed to rescue.
	if len(text) < MIN_PLAINTEXT_CHARS:
		return None
	# "|" is the doc-marker delimiter; keep titles from ever forging it.
	title = title.replace("|", "/")
	link = _public_link(portal_link, base_url)
	date = (item.get("date") or "")[:10]
	return f"----- DOC | {title} | {link} | {date} -----\n{text}\n"


def _sync_one_source(
	source: str,
	blocks: list[str],
	seen_slugs: set[str],
	seen_hashes: set[str],
) -> tuple[int, int]:
	"""Append one portal's blocks to `blocks`. Returns (kept, skipped)."""
	kept = dropped = 0
	exclude_ids = _skip_category_ids(source)
	for kind in _SYNC_KINDS:
		for item in _fetch_collection(source, kind, exclude_ids):
			block = _item_block(item, source)
			if block is None:
				dropped += 1
				continue
			slug = _slug_of((item.get("link") or "").strip())
			body = re.sub(r"\s+", " ", block.split("-----\n", 1)[-1]).strip()
			digest = hashlib.sha256(body.encode("utf-8"), usedforsecurity=False).hexdigest()
			if (slug and slug in seen_slugs) or digest in seen_hashes:
				dropped += 1
				continue
			if slug:
				seen_slugs.add(slug)
			seen_hashes.add(digest)
			blocks.append(block)
			kept += 1
	return kept, dropped


def sync_corpus(base_url: str = "", out_path: str = _PATH) -> dict:
	"""Fetch posts + pages from the portal and rewrite the corpus file.

	Stored links are rewritten onto SITE_PUBLIC_BASE_URL so citations shown
	to users (and the corpus file itself) never carry the internal portal
	host. Returns {"docs": n, "skipped": n, "path": out_path}. Raises on a
	dead or unconfigured portal so callers can report the failure instead of
	silently truncating the corpus.
	"""
	# SITE_CORPUS_URL may name SEVERAL portals, comma-separated, in priority
	# order. This is not decoration: the internal mirror carries curated pages
	# the public site does not expose under any slug — Administration (which is
	# where the university President lives), Admission, and Mandate/Mission/
	# Vision among them — while the public site carries ~900 documents the
	# mirror has never had. Syncing either alone loses real answers, so the
	# corpus is the UNION, deduped by slug with the earliest source winning.
	sources = [u.strip().rstrip("/") for u in (base_url or _DEFAULT_BASE_URL).split(",") if u.strip()]
	if not sources:
		raise ValueError(
			"SITE_CORPUS_URL is not configured — set the portal base URL in the local .env"
		)
	blocks: list[str] = []
	skipped = 0
	# Two dedup keys. Slug: the same page served by two portals is one
	# document, first source wins. Content hash: exact-plaintext only —
	# deliberately NOT fuzzy or title-normalised, because CvSU republishes one
	# post per board-exam sitting under an identical title and those are
	# DIFFERENT answers (measured cosine 0.311 between two same-titled LEPT
	# posts); a "keep newest by title" rule would delete 28 of every 100 posts.
	seen_slugs: set[str] = set()
	seen_hashes: set[str] = set()
	per_source: dict[str, int] = {}
	for source in sources:
		kept, dropped = _sync_one_source(source, blocks, seen_slugs, seen_hashes)
		per_source[source] = kept
		skipped += dropped
	os.makedirs(os.path.dirname(out_path), exist_ok=True)
	with open(out_path, "w", encoding="utf-8") as fh:
		fh.write("\n".join(blocks))
	_logger.info("site corpus synced: %d docs (%d skipped) from %s -> %s",
				 len(blocks), skipped,
				 ", ".join(f"{u}={n}" for u, n in per_source.items()), out_path)
	return {"docs": len(blocks), "skipped": skipped, "sources": per_source, "path": out_path}
