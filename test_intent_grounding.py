"""Offline regression for api/intent_grounding.py + the applied bindings.

Run:  python test_intent_grounding.py   (or: pytest test_intent_grounding.py)

Covers three things:
  1. The GroundingIndex loads data/intent_sources.json, dedups refs by kind,
     orders them by score, and renders charter/site citations correctly.
  2. Conversational / rejected intents stay unbound (no stray citation).
  3. INTEGRITY: every locator in the *applied* bindings resolves to a real
     Citizens' Charter page / official-site URL — so a citation can never
     point at a page that does not exist in the corpus.
"""
import json
from pathlib import Path

from api import intent_grounding as ig

ROOT = Path(__file__).resolve().parent
BINDINGS = json.loads((ROOT / "data" / "intent_sources.json").read_text(encoding="utf-8"))["bindings"]


def _check(cond, msg, fails):
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)


def test_index_loads_and_renders():
    idx = ig.get_index()
    assert idx is not None and idx.available
    # about_cvsu is bound to BOTH a site and a charter ref; refs_for returns
    # one per kind, highest score first.
    refs = idx.refs_for("about_cvsu")
    assert len(refs) == 2
    assert refs[0].score >= refs[1].score
    kinds = {r.kind for r in refs}
    assert kinds == {"charter", "site"}
    # Charter citation shape mirrors charter_rag.Passage.citation().
    charter = next(r for r in refs if r.kind == "charter")
    assert charter.citation() == f"CvSU Citizens' Charter, FY 2026 edition, p. {charter.locator}"
    site = next(r for r in refs if r.kind == "site")
    assert site.locator.startswith("http") and site.locator in site.citation()


def test_unbound_intents_have_no_citation():
    idx = ig.get_index()
    # greeting is conversational (never proposed); diploma_request was
    # verified-REJECTED (charter page was the lost-library-card procedure).
    for tag in ("greeting", "diploma_request", "courses_offered", "not_a_real_intent"):
        assert idx.refs_for(tag) == [], f"{tag} should be unbound"
    assert ig.citation_block([]) == ""


def test_citation_block_singular_vs_plural():
    idx = ig.get_index()
    one = idx.refs_for("foreign_student_admission")   # charter only
    assert len(one) == 1
    block = ig.citation_block(one)
    assert block.startswith("\n\n") and "Source:" in block and "Sources:" not in block
    two = idx.refs_for("about_cvsu")
    assert "Sources:" in ig.citation_block(two)


def test_applied_bindings_resolve_in_corpus():
    """Every applied locator must exist in the live corpus."""
    from api import charter_rag, site_rag
    charter = charter_rag.get_index()
    site = site_rag.get_index()
    # charter._chunks is list[(page, text)]; site._chunks is list[(title, url, date, text)].
    charter_pages = {str(page) for page, _ in charter._chunks} if charter else set()
    site_urls = {url for _t, url, _d, _x in site._chunks} if site else set()
    for tag, refs in BINDINGS.items():
        for ref in refs:
            if ref["kind"] == "charter":
                assert charter is None or ref["locator"] in charter_pages, \
                    f"{tag}: charter p.{ref['locator']} not in corpus"
            elif ref["kind"] == "site":
                assert site is None or ref["locator"] in site_urls, \
                    f"{tag}: site url {ref['locator']} not in corpus"


if __name__ == "__main__":
    fails: list[str] = []
    idx = ig.get_index()
    _check(idx is not None and idx.available, "index loads from data/intent_sources.json", fails)

    refs = idx.refs_for("about_cvsu")
    _check(len(refs) == 2 and refs[0].score >= refs[1].score,
           "about_cvsu -> 2 refs (one per kind), score-ordered", fails)
    _check({r.kind for r in refs} == {"charter", "site"}, "about_cvsu covers both kinds", fails)

    fsa = idx.refs_for("foreign_student_admission")
    _check(len(fsa) == 1 and fsa[0].citation().endswith("p. 581"),
           "foreign_student_admission -> charter p. 581", fails)
    _check("Source:" in ig.citation_block(fsa) and "Sources:" not in ig.citation_block(fsa),
           "single ref -> 'Source:' block", fails)
    _check("Sources:" in ig.citation_block(refs), "two refs -> 'Sources:' block", fails)

    for tag in ("greeting", "diploma_request", "courses_offered", "not_a_real_intent"):
        _check(idx.refs_for(tag) == [], f"{tag} is unbound (no citation)", fails)

    # Corpus integrity
    from api import charter_rag, site_rag
    charter = charter_rag.get_index()
    site = site_rag.get_index()
    charter_pages = {str(page) for page, _ in charter._chunks} if charter else set()
    site_urls = {url for _t, url, _d, _x in site._chunks} if site else set()
    bad = []
    for tag, brefs in BINDINGS.items():
        for ref in brefs:
            if ref["kind"] == "charter" and charter and ref["locator"] not in charter_pages:
                bad.append(f"{tag}:charter p.{ref['locator']}")
            if ref["kind"] == "site" and site and ref["locator"] not in site_urls:
                bad.append(f"{tag}:site {ref['locator']}")
    _check(not bad, f"all {sum(len(r) for r in BINDINGS.values())} applied locators resolve in corpus"
           + (f" — MISSING: {bad}" if bad else ""), fails)

    print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILED'}")
    raise SystemExit(1 if fails else 0)
