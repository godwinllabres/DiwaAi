"""Offline regression for the Place Resolver wayfinding tier — run:
    python test_place_resolver.py

Locks the fix for the screenshot failure where "Saan yung saluysoy" got the
nlu_fallback text ("I'm not sure I understand") while the map card correctly
resolved Saluysoy — the place lexicon lived only in the card layer:
  • location ask + known place name resolves deterministically;
  • short bare-entity asks ("saluysoy?") resolve too;
  • amenity/category asks ("saan pwede kumain?") route to the right place;
  • the resolver and the map card agree on the place (single lexicon);
  • generic campus asks ("saan ang cvsu") stay with the richer intent tiers;
  • satellite-campus sessions never get an Indang wayfinding answer.
"""
import sys

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api import campus_directory  # noqa: E402
from api.campus_places import (  # noqa: E402
    place_answer,
    resolve_map_data,
    resolve_place_query,
)

checks = 0
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks, failures
    checks += 1
    if condition:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}  {detail}")


# ─── named-place asks (the screenshot case) ──────────────────────────────────
print("[resolver] named places")
for text, expected in [
    ("Saan yung saluysoy", "saluysoy"),
    ("saluysoy?", "saluysoy"),                    # bare entity, no location kw
    ("where is the lagoon", "lagoon"),
    ("paano pumunta sa icon", "icon"),
    ("nasaan ang baño resort", "bano_resort"),
    ("how do i get to the oval", "oval"),
    ("where is the registrar", "osas"),           # rescue if classifiers miss
]:
    pq = resolve_place_query(text)
    check(f"place: {text!r} -> {expected}", pq is not None and pq.place_id == expected,
          f"got={pq.place_id if pq else None}")

# ─── amenity/category asks ───────────────────────────────────────────────────
print("\n[resolver] categories")
for text, expected in [
    ("saan pwede kumain?", "mall"),
    ("Where can I eat lunch?", "mall"),
    ("saan pwede magbayad", "admin"),
    ("where can I study", "library"),
    ("saan pwede magsimba", "chapel"),
]:
    pq = resolve_place_query(text)
    check(f"category: {text!r} -> {expected}",
          pq is not None and pq.place_id == expected and pq.kind == "category",
          f"got={pq.place_id if pq else None}")

# ─── non-wayfinding asks stay with the cascade ───────────────────────────────
print("\n[resolver] negatives")
for text in [
    "how do I enroll?",
    "what are the admission requirements",
    "saan ang cvsu",                      # 'main' catch-all excluded by design
    "tuition fees for BSIT",
    "the saluysoy area was beautiful during the fair last year",  # no ask
    "thank you!",
]:
    pq = resolve_place_query(text)
    check(f"none: {text!r}", pq is None, f"got={pq.place_id if pq else None}")

# ─── answer text comes from the same PlaceMeta as the map pin ────────────────
print("\n[answer] template content")
pq = resolve_place_query("Saan yung saluysoy")
ans = place_answer(pq)
for needle in ["Saluysoy", "#36", "9-minute", "Gate 1", "map below"]:
    check(f"saluysoy answer mentions {needle!r}", needle in ans, f"answer={ans!r}")

pq = resolve_place_query("saan pwede kumain?")
ans = place_answer(pq)
for needle in ["University Mall", "canteen", "map below"]:
    check(f"kumain answer mentions {needle!r}", needle in ans, f"answer={ans!r}")

# ─── text and map card can never disagree ────────────────────────────────────
print("\n[coherence] resolver vs map card")
for text in ["Saan yung saluysoy", "where is the lagoon", "saan pwede kumain?"]:
    pq = resolve_place_query(text)
    md = resolve_map_data(text, "nlu_fallback")
    check(f"same place for {text!r}",
          pq is not None and (md is None or md.place_id == pq.place_id),
          f"resolver={pq.place_id if pq else None} map={md.place_id if md else None}")

# ─── pipeline tier: satellite sessions are skipped ───────────────────────────
print("\n[tier] satellite gating")
try:
    from api.hybrid_chatbot import HybridChatbot
except Exception as exc:  # heavy optional deps (TF/sklearn) may be absent
    print(f"SKIP  hybrid_chatbot not importable here ({exc})")
else:
    satellite = next(c for c in campus_directory.DIRECTORY
                     if c != campus_directory.MAIN_CAMPUS)
    # _place_resolver_result touches no instance state — call it unbound.
    hit = HybridChatbot._place_resolver_result(None, "Saan yung saluysoy", None)
    check("no campus context -> resolves", hit is not None and hit[0] == "saluysoy")
    hit = HybridChatbot._place_resolver_result(None, "Saan yung saluysoy",
                                               campus_directory.MAIN_CAMPUS)
    check("main campus -> resolves", hit is not None and hit[0] == "saluysoy")
    hit = HybridChatbot._place_resolver_result(None, "Saan yung saluysoy", satellite)
    check(f"satellite ({satellite}) -> skipped", hit is None)

print(f"\n{checks - failures}/{checks} passed")
sys.exit(1 if failures else 0)
