"""Relevance gate for the retrieval tiers (site_rag + charter_rag).

Decides, per corpus, whether a retrieved passage may be handed to the LLM as
evidence (AUGMENT) or quoted verbatim to the user (QUOTE). Replaces the
absolute coverage-weighted-cosine floors (site AUGMENT_MIN_SCORE 0.12 /
charter 0.08, QUOTE_MIN_SCORE 0.15 + one bigram) that _gather_grounding() and
_verbatim_document_reply() applied until 2026-09.

WHY THE SCORE FLOOR HAD TO GO
    UAT 2026-09-02: "wow, you can answer questions like that?" retrieved a GAD
    accomplishment-report workshop page at 0.126 — over the floor — and the
    LLM parroted it, citation and all. "can you answer questions?" scores 0.214
    with a bigram hit and was QUOTED verbatim on the no-LLM path. The beta chat
    DB has the charter side of the same bug: "Indang (Main Campus)" quoted the
    President's office page seven times, "Who is the head of cashiers office"
    quoted a scholarship-claiming procedure. Meanwhile "when does enrollment
    start" scored 0.074 and got nothing. Cosine over a 700-char chunk measures
    lexical overlap, not whether the passage answers the message; a one-word
    utterance against a chunk that repeats that word scores HIGHER than an
    eight-word question whose answer is spread across the chunk. Every absolute
    floor is on the wrong axis (measured: conversational top-passage cosine
    p50 0.13 / p95 0.20 overlaps gold p25 0.17 / p50 0.23).

HOW IT DECIDES INSTEAD — three layers
  1. QUERY PROFILE (analyze_query). What is being asked, and how specifically?
     Tokens are the index analyzer's own; the curated lexicons below remove
     conversational / meta / feedback / pointer words, Filipino function words
     and the institution's own name, leaving TOPICAL tokens. Shape regexes add
     predicate (interrogative, request verb, "?"), declarative (first-person
     statement), bot-address ("are you a bot", "do you store my data"),
     follow-up opener ("and the fees?") and present-state who-question
     ("who is the current dean") flags. From that, need in {0, 1, 2}:
        0  no topical token, a bare single mention ("noted", "the second one"),
           or a bot-directed question — NOT an information need: the serving
           code answers conversationally and never calls retrieval or the LLM
        1  underspecified: keywords without a predicate ("enrollment
           schedule"), a statement ("I passed the board exam"), a bot question
           carrying campus nouns — excerpts MAY reach the LLM (which is told to
           say when they do not answer); a verbatim quote is never allowed
        2  a real question with >= 2 topical tokens — full grounding
     Measured on the gate-design sets: all 248 mirror_qa gold questions are
     need 2; 71/75 conversational probes are need 0.
  2. PASSAGE RELEVANCE (relevance) — one scalar in [0, 1), comparable across
     the two corpora:
        rel = matched_mass / (query_mass + k)
        query_mass  = Σ w(anchor) + β·Σ w(anchor bigram) + γ·|OOV| + δ·w(top unmatched)
        w(term)     = BM25 idf(term) / ln(1 + N)      (corpus-normalised surprisal)
     "How much of what the message asks for does this passage contain",
     weighted by how surprising each asked-for term is in THIS corpus. The
     division by the corpus's own ln(1+N) is what makes site and charter values
     comparable, so this replaces hybrid_chatbot._cross_corpus_rank_key. OOV
     topical words (γ) count against a passage — a word the corpus never saw is
     evidence the question is about something the corpus lacks — except
     Filipino-affixed verbs (kumuha, ginanap), which are OOV in an English
     corpus without saying anything about the topic.
     Candidates are ORDERED by score·(0.5 + 0.5·rel): rel alone rewards
     aggregator pages that merely mention many terms (measured: the University
     Calendar page lost both slots to admission posts under rel-only order).
  3. DECISIONS (decide)
     AUGMENT  need >= 1, rel >= THETA_AUG, retrieval score above a noise floor,
              not an aggregator/front-matter passage, one chunk per document,
              a single-anchor query must find its anchor in the title or twice
              in the text; best two.
     QUOTE    only for a need-2 question that is not a statement, follow-up or
              bot question, and only when the top passage clears ALL of:
              rel >= THETA_QUOTE, >= 3 distinct anchors matched, one anchor
              bigram literally adjacent in the text, no hard-OOV anchor
              ("UAAP" must not quote a wheelchair-basketball article), every
              year/month/ordinal in the question present in the passage (the
              date-shift check: 14 of 24 "what was the rate in <year> <exam>"
              asks were quoting a different year's article — 0 after), a
              passage long enough to be an answer, a rel lead of QUOTE_MARGIN
              over the best passage of any OTHER document, and — for a
              present-state who-question — an undated page that actually names
              a person (no dated news post, never the charter).

MEASURED (scripts/eval_grounding_gate.py — harness + held-out; see the
commit and test_grounding_gate.py for the numbers this ships with).

WHAT WAS TRIED AND REJECTED (so nobody re-runs it)
  * IDF-only anchor selection, no lexicon: on this corpus "yes", "tell",
    "explain", "yung", "lang" are RARER than "tuition" — rarity is not
    topicality. (Matches charter_rag's earlier note on the high-IDF filter.)
  * Any absolute cosine/score floor as the admission test — the legacy failure.
  * Query-relative statistics alone (peakiness, top1/mean, z-scores): near-
    duplicate chunks of one article make every real question look "flat".
  * Ranking admitted passages by rel alone: +3 gold in-sample, −6 on fresh
    questions and 25 aggregator slots.
  * Zeroing all grounding on follow-up openers: blocks "So how do I get my
    TOR?", "But how much is the dorm fee?" — the LLM has the conversation, so
    openers only forbid the QUOTE.
  * Capitalisation as the bare-entity signal: "ADMISSION REQUIREMENTS FOR
    FRESHMEN" was need 0 while the lowercase form was need 1.

Env:
    GROUNDING_GATE — "relevance" (default) or "legacy" (the absolute floors,
                     kept as the roll-back switch; see hybrid_chatbot).
"""
from __future__ import annotations

import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Tunables. Every value was swept on scripts/eval_grounding_gate.py (in-sample
# sets) and then checked on the held-out set built from the adversarial
# critics' fresh utterances. Change one, re-run both.
# ---------------------------------------------------------------------------
PARAMS = dict(
    beta=0.5,            # anchor-bigram mass relative to unigram mass
    gamma=1.0,           # each hard-OOV topical word adds this to query mass
    gamma_soft=0.35,     # ... a Filipino-affixed OOV verb adds only this
    delta=0.5,           # the most specific UNMATCHED anchor counts this much extra
    k=1.0,               # prior mass every question carries (caps 1-anchor rel ~0.45)
    hapax_df=2,          # a term in fewer chunks than this is not evidence (chat words, typos)
    title_bonus=0.15,    # ranking: per matched anchor in the page title (max 3) — pages ABOUT the topic
                         # beat pages that mention it ("College of Nursing Officials" vs a 2020 news post)
    theta_aug=0.22,      # AUGMENT: rel floor
    aug_score_floor=0.04,   # AUGMENT: retrieval-score noise floor
    theta_quote=0.55,    # QUOTE: rel floor (measured trap boundary ~0.50)
    quote_min_match=3,   # QUOTE: distinct anchors matched
    quote_margin=0.03,   # QUOTE: rel lead over the best passage of another document
    quote_min_chars=250, # QUOTE: a 3-line cover page is never an answer
    quote_score_floor=0.10,
)


# Candidates each corpus hands the gate: the retrieval tiers' default k=3 was
# the legacy floor's reach; the gate re-ranks, so it sees the top 8.
RETRIEVE_K = 8


def mode() -> str:
    """GROUNDING_GATE resolved: "relevance" (default) or "legacy"."""
    value = os.environ.get("GROUNDING_GATE", "relevance").strip().lower()
    return value if value in ("relevance", "legacy") else "relevance"


# ---------------------------------------------------------------------------
# Curated lexicons. Small, grouped by the utterance class that motivated each
# group, and REMOVED FROM THE QUERY'S TOPICAL TOKENS ONLY — the TF-IDF
# vectorisation and passage.score are untouched. Words that are also real
# campus topics are deliberately absent: "start" (enrollment start), "second"
# (second semester), "number" (contact number), "time"/"open" (office hours),
# "free" (free tuition), "information" (ICTO), "program", "list" (dean's
# list), "working" (working days), "available" (available courses), "language"
# (language requirements), "staff", "age", "old". A bare one of those without a
# predicate is still need 0 by the bare-mention rule; inside a question it is
# the topic.
# ---------------------------------------------------------------------------
CONVERSATIONAL_EN = frozenset("""
bot chatbot robot ai assistant sevi diwa human person real name made make create created built
build developer creator version model machine boss trained source memory accuracy rating
answer answers answered answering question questions ask asked asking asks help helped helpful
helping helps talk talking chat chatting speak speaking understand understood know knew knows
knowing mean meant means meaning say said saying says tell told telling tells explain explained
explaining explanation elaborate clarify clarification describe repeat again translate translation
english tagalog filipino bisaya summarize summary recap conversation message reply respond
response earlier previous previously mentioned discuss discussed continue proceed wait hold stop
skip exit quit restart reset menu options option choose pick select
good great nice awesome amazing excellent perfect wonderful brilliant cool smart clever impressive
wow bad wrong incorrect correct right true false sure certain really seriously exactly
unhelpful useless useful better best worse worst terrible stupid dumb boring bored interesting funny
quick fast slow
thanks thank thx ty tysm appreciate appreciated welcome sorry apologies oops okay ok oki alright
fine yes yeah yep yup no nope nah noted got gotcha roger copy agree agreed disagree like love hate
doubt believe think thought guess hmm hm huh haha hahaha lol lmao omg uh um er ah oh hey hi hello
helo yo bye goodbye morning afternoon evening night please pls plz kindly
congrats congratulations merry christmas happy birthday cheers
one ones other others another else more less this that these those example examples sample samples
instance specifically specific particular exact above below following latter former same different
difference differences similar compare comparison
thing things stuff something anything everything nothing kind kinds type types sort sorts way ways
lot lots bit matter case point idea sense anyone someone somebody anybody
map maps direction directions navigate gps weather temperature forecast joke jokes song songs
music game games play today tomorrow yesterday tonight now luck meet
lie lying confused confusing feelings mind forget testing typo waste
""".split())

# Words that say what KIND of answer is wanted, not what it is about ("What
# does WELA stand for?" is about WELA). "current"/"latest" are frame words too;
# the present-state rule below reads them separately for the quote decision.
QUESTION_FRAME_EN = frozenset("""
stand stands acronym abbreviation define definition actually current currently latest recent
recently does did do kind type types info details detail difference different
""".split())

# Request verbs and topic introducers: they mark a predicate ("tell me about
# X") and are never the subject.
REQUEST_EN = frozenset("""
tell list enumerate provide describe define explain show give find search look check need want
know wanna help about regarding concerning tungkol patungkol re info details detail share send
recommend suggest guide assist
""".split())

# Question words and question-forming auxiliaries (English + Filipino).
INTERROGATIVE = frozenset("""
what whats who whos whom whose where wheres when whens why which how hows
ano anong sino sinong saan san kailan kelan paano pano papaano bakit ilan ilang alin aling
magkano mgkano kanino nasaan nasan asan gaano
does do did is are was were can could may might will would should have has pwede puwede meron
mayroon ba
""".split())

# Filipino / Taglish particles, pronouns, modals, linkers, light verbs and the
# conversational Filipino of chat ("may tanong ako", "salamat po", "ang galing",
# "ulitin mo"). These are RARE in the mostly-English corpus and therefore
# HIGH-idf — exactly why "totoo ba yan" used to retrieve OPISYAL NA PAHAYAG.
# Spanish name particles are here too: "la" and "los" were the matched
# "anchors" behind the De La Salle / Los Baños leaks.
FILIPINO_FUNCTION = frozenset("""
ang ng mga sa ay na nang at o ba po ho opo ko mo ka ako akong ikaw siya sya kami tayo kayo sila ito
iyan yan iyon yun yon yung ung yong iyong ni kay para kung kapag pag habang anong sinong may
mayroon meron wala walang hindi di oo lang lamang din rin pa naman daw raw kasi dahil pero ngayon
ngayong dito diyan dyan doon dun rito riyan roon nito niyan nyan noon nun nung noong nila niya nya
natin namin ninyo nyo niyo kong mong akin iyo kanya kanyang amin atin inyo kanila sayo saiyo sakin
sakanya pwede puwede pwedeng puwedeng dapat gusto gustong sana talaga pala ha nga muna tapos eh e
yata siguro kaya saka tsaka pati ganun ganon ganito ganyan ganitong lahat iba isa dalawa tatlo marami
konti kaunti medyo sobra masyado palagi lagi minsan uli ulit ulitin kumusta kamusta musta salamat
sige ge ok okay oki sori sorry pasensya paki pakiulit pakibigay pakisabi ipaliwanag sabihin sabi
tanong tanungin magtanong itatanong itanong katanungan sagot sagutin sumagot alam kilala gawa gawin
ginawa gumawa tulong tulungan tumulong mali tama totoo sigurado galing astig ayos mabuti maganda
magandang maraming pasalamat magpasalamat ingat paalam hanggang muli mamaya bukas kahapon kanina
mahirap madali hala grabe weh sus naku ayaw ayoko wag huwag tulad parang kagaya
kailangan maaari maaaring maging pang pinaka intindi maintindihan naiintindihan gets aywan ewan
teka sandali pangalan umaga hapon gabi biro patawa kanta halimbawa
tao usap kausap makipag ibig bilis dami mong buti nakakainis labo bait nakakatuwa naintindihan
mag nag naka maka pina magpa ipa um in an
de la del los las y san sta sto
""".split())

# Every document in both corpora is about this institution; its name cannot
# separate a relevant passage from any other. Counted as a topical token for
# the NEED decision ("how many campuses does CvSU have?" is a question) but
# never as an anchor for relevance ("Is CvSU better than UP Los Baños" reached
# the quote line on cvsu+banos before this).
SELF_NAME = frozenset("cvsu cavite state university pamantasan campus campuses".split())

# ---------------------------------------------------------------------------
# Shape regexes — applied to the NFKC-normalised, casefolded message.
# ---------------------------------------------------------------------------

# The message is about the assistant itself ("are you a bot", "do you store my
# data", "what's your name", "am i talking to a person", "who made you", "ikaw
# ba si sevi", "may kausap ba akong tao"). A "can/could you <request verb>"
# opener is a request, not a bot question, and is excluded. Measured on the
# critics' bot-directed set: leaks 61 -> 19 with zero gold cost — the shape
# generalises where adding nouns to a list did not ("data", "policy", "app",
# "email", "website" are all real campus words).
_REQUEST_VERBS = ("tell|give|show|explain|list|help|provide|find|answer|say|share|describe|"
                  "enumerate|recommend|suggest|guide|assist|please|kindly|pls|plz|search|look|"
                  "confirm|clarify|elaborate|summarize|repeat|translate|send|point")
_BOT_ADDRESS_RE = re.compile(
    r"^\W*(?:hi|hello|hey|ok|okay|so|sevi|diwa)?\W*"
    r"(?:(?:are|were|am|is)\s+(?:you|u|this|sevi|diwa)\b"
    r"|(?:do|did|does|can|could|will|would|should|have|has)\s+(?:you|u)\s+(?!(?:" + _REQUEST_VERBS + r")\b)"
    r"|(?:what|whats|what's|who|whos|who's|where|when|how)\s+(?:is|are|was|were)?\s*(?:your|ur)\b"
    r"|(?:what|whats|what's)\s+(?:can|do)\s+(?:you|u)\s+(?:do|know)\b"
    r"|who\s+(?:made|created|built|programmed|developed|trained|designed|owns)\s+(?:you|u)\b"
    r"|am\s+i\s+(?:talking|chatting|speaking)\b"
    r"|is\s+there\s+(?:a\s+)?(?:human|person|someone|somebody|anyone|anybody)\b"
    r"|(?:ikaw|ka)\s+ba\b|ikaw\s+ay\b|sino\s+(?:ang\s+)?(?:gumawa|nag-?program|nagprogram|may\s+gawa)\b"
    r"|may\s+kausap\b|makipag-?usap\b|marunong\s+ka\b|nakaka(?:intindi|alam)\s+ka\b"
    r"|kaya\s+mo\s+ba\b|alam\s+mo\s+ba\b)",
    re.IGNORECASE,
)

# First-person / declarative statement ("I passed the September 2025 board
# exam", "we had our seminar yesterday", "nakapasa ako sa board exam"). A
# statement is not a question: excerpts may reach the LLM, nothing is quoted.
_DECLARATIVE_RE = re.compile(
    r"\b(?:i|i'm|im|i've|ive|we|we're|my|our|me|us|mine|ours"
    r"|ako|kami|namin|natin|akin|ko|amin|nakapasa|pumasa|nag-?enroll|natapos|nakapag\w*|nag\w{3,})\b"
    r"|\b(?:passed|failed|graduated|enrolled|attended|finished|submitted|paid|applied|joined|"
    r"had|got|went|took|received|congrats|congratulations)\b",
    re.IGNORECASE,
)

# Follow-up openers: the message depends on earlier turns the gate cannot see.
# Only forbids the QUOTE (the LLM has the conversation). Over-matching is cheap.
_FOLLOWUP_RE = re.compile(
    r"^\W*(?:and|or|but|also|then|so|same|what about|how about|what of|"
    r"eh|e|tapos|saka|pati|paano naman|ano naman|pano naman)\b",
    re.IGNORECASE,
)

# Thanks / feedback / greeting opener: the message is an acknowledgement that
# happens to repeat topic words ("salamat sa info about sa criminology board
# exam" quoted the July 2024 criminology article verbatim). Never a quote;
# excerpts may still reach the LLM, which reads the whole message.
_ACK_OPENER_RE = re.compile(
    r"^\W*(?:thanks|thank you|thank u|thankyou|ty|tnx|thx|salamat|maraming salamat|wow|nice|"
    r"good|great|cool|ok|okay|oki|sige|galing|ang galing|got it|noted|alright|awesome|amazing|"
    r"perfect|haha|hahaha|lol|congrats|congratulations|hi|hello|hey|good morning|good afternoon|"
    r"good evening|magandang|happy|merry|good luck|welcome|cheers)\b",
    re.IGNORECASE,
)

# Campus names: a present-state who-question may not be quoted from another
# campus's officials page ("who is the head of HRDO" -> "Trece Martires City
# Campus Officials"). If the page title names a campus, the question must too.
_CAMPUS_RE = re.compile(
    r"\b(indang|main|imus|bacoor|rosario|silang|naic|trece|tanza|carmona|cavite city|"
    r"gen(?:eral)?\s*trias|ccat|maragondon)\b",
    re.IGNORECASE,
)

# Present-state "who is the <role>" question: English opener, or a recency word
# anywhere. Filipino "sino ang/yung/si ..." counts unless the question carries
# an actor-focus past-tense verb (sino ang NAGBIGAY, sino ang DUMALO) — an event
# question a dated news post answers correctly. The past-verb check runs only
# on the Filipino branch so English words never defeat it (the earlier
# any-branch version matched "Human", "number", "summary").
_PRESENT_WHO_EN_RE = re.compile(
    r"^\W*(?:who\s+(?:is|are|'s)|whos|who's)\b|\b(?:current|currently|incumbent|kasalukuyan|"
    r"kasalukuyang|ngayon|ngayong|present)\b",
    re.IGNORECASE,
)
_SINO_RE = re.compile(r"^\W*sino\s+(?:po\s+)?(?:ang|yung|ung|si|sina)\b", re.IGNORECASE)
_FIL_PAST_VERB_RE = re.compile(
    r"\b(?:nag|nakipag|nakapag|naka|ipina|pina|itin|isin|bin|gin|tin|kin|hin|din|sin|pin|"
    r"nam|nan)[a-z]{3,}\b"
    r"|\b(?:d|t|p|k|s|l|h|b|g|y)um(?:[aeiou])[a-z]{2,}\b",
    re.IGNORECASE,
)
_FIL_PAST_VERB_EXCEPT = frozenset("human humanities humanity humane tumor humor humid humidity".split())

# Name-like token in a passage ("Dr. Hernando D. Robles", "Prof. Ana Cruz",
# "Juan M. Dela Cruz"): a present-state who-question may only be quoted from a
# page that names somebody.
_PERSON_RE = re.compile(
    r"\b(?:Dr|Prof|Engr|Atty|Mr|Ms|Mrs|Hon|Dean|Director)\.?\s+[A-Z][a-z]+"
    r"|\b[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+"
)

# Year / month / ordinal / academic-year tokens that discriminate editions of a
# recurring post (licensure sittings, rankings, anniversaries, olympics).
_MONTHS = ("january february march april may june july august september october november "
           "december jan feb mar apr jun jul aug sep sept oct nov dec enero pebrero marso abril "
           "mayo hunyo hulyo agosto setyembre oktubre nobyembre disyembre").split()
_DATE_TOKEN_RE = re.compile(r"^(?:(?:19|20)\d{2}|\d{1,3}(?:st|nd|rd|th)|" + "|".join(_MONTHS) + r")$")

# Filipino verb morphology on an OOV token: kumuha, ginanap, ipinost, nagpa-...
# Applied to OOV tokens only, so English corpus words never reach it.
_FIL_AFFIX_RE = re.compile(
    r"^(?:mag|nag|pag|ipag|ipa|ipina|pina|naka|maka|nakaka|makaka|nakapag|makapag|magpa|nagpa|"
    r"paki|pinaka|magka|nagka|naki|maki|napa|mapa|isina|ini|itina|ipin|isin|itin|"
    r"nan|nang|man|mang|kina|gina|sina|dina|tina|pinag|ipinag|nasa)[a-z]{3,}$"
    r"|^[a-z](?:um|in)[aeiou][a-z]{2,}$"
)

# Site pages that are aggregators or shells, never an answer.
_JUNK_TITLES = frozenset({"", "home", "home page", "mockup", "sample page", "sample duplicate page",
                          "events", "news", "announcements", "gad updates", "news updates"})

_DATED_URL_RE = re.compile(r"/(?:19|20)\d{2}/\d{2}/")


# ---------------------------------------------------------------------------
# Query profile
# ---------------------------------------------------------------------------
@dataclass
class QueryProfile:
    raw: str
    topical: list           # topical tokens, query order (in-vocab or not)
    anchors: list           # topical tokens the corpus knows (df >= hapax_df)
    oov_hard: list          # topical tokens the corpus lacks — evidence of an off-corpus topic
    oov_soft: list          # OOV tokens with Filipino verb morphology — say nothing about the topic
    bigrams: list           # in-vocab bigrams of two anchors
    weights: dict           # normalised surprisal per anchor / bigram
    date_terms: list        # year / month / ordinal tokens that must appear in a quoted passage
    predicate: bool
    declarative: bool
    bot_address: bool
    followup: bool
    present_who: bool
    need: int
    reason: str
    ack_opener: bool = False

    @property
    def information_need(self) -> bool:
        return self.need >= 1

    @property
    def quote_ok(self) -> bool:
        """The message is a real, self-contained question — the only shape a
        verbatim quote is ever allowed to answer."""
        return (self.need == 2 and self.predicate and not self.declarative
                and not self.bot_address and not self.followup and not self.ack_opener)


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _is_topical(tok: str) -> bool:
    return not (tok in CONVERSATIONAL_EN or tok in QUESTION_FRAME_EN or tok in REQUEST_EN
                or tok in INTERROGATIVE or tok in FILIPINO_FUNCTION or tok in SELF_NAME)


_RAW_TOKEN_RE = re.compile(r"(?u)\w+")


def _shape(text: str) -> dict:
    # Raw tokens, NOT the analyzer's: sklearn's english stop list removes
    # "how", "who", "do", "can", "is" — the very words that mark a question.
    lows = set(_RAW_TOKEN_RE.findall(text))
    predicate = (bool(lows & INTERROGATIVE) or bool(lows & REQUEST_EN)
                 or text.rstrip().endswith("?"))
    sino = bool(_SINO_RE.search(text))
    fil_past = any(m.group(0).lower() not in _FIL_PAST_VERB_EXCEPT
                   for m in _FIL_PAST_VERB_RE.finditer(text))
    present_who = bool(_PRESENT_WHO_EN_RE.search(text)) or (sino and not fil_past)
    # A first-person or past-tense marker makes a STATEMENT only when nothing
    # asks: "How many examinees passed?" and "how do I get my TOR" are
    # questions; "I passed the board exam" and "nakapasa ako" are not.
    declarative = bool(_DECLARATIVE_RE.search(text)) and not (lows & INTERROGATIVE)
    return dict(
        predicate=predicate,
        declarative=declarative,
        bot_address=bool(_BOT_ADDRESS_RE.search(text)),
        followup=bool(_FOLLOWUP_RE.search(text)),
        present_who=present_who,
        ack_opener=bool(_ACK_OPENER_RE.search(text)),
    )


def _need(topical_all: list, anchors: list, shape: dict) -> tuple[int, str]:
    n = len(topical_all)
    if n == 0:
        return 0, "no-topical"
    if shape["bot_address"] and n <= 2:
        return 0, "bot-address"
    if n == 1 and not shape["predicate"]:
        return 0, "bare-mention"
    if shape["ack_opener"] and not shape["predicate"] and n <= 2:
        # "happy teachers day po", "good luck sa board exam", "Hi! I'm a first
        # year student" — a greeting or thanks that names a topic is not asking
        # about it. Three or more topic words ("ok tuition fee bsit") still
        # count as an underspecified ask.
        return 0, "ack-with-topic"
    if shape["bot_address"]:
        return 1, "bot-address-long"
    if shape["declarative"] and not shape["predicate"]:
        return 1, "statement"
    if shape["predicate"] and n >= 2:
        return 2, "question"
    if n >= 3 and not shape["declarative"]:
        return 2, "keywords"
    return 1, "underspecified"


def analyze_query(query: str, index=None, p: dict = PARAMS) -> QueryProfile:
    """Layer 1. With an index, anchors/weights come from that corpus; without
    one (the pre-retrieval switch in hybrid_chatbot) every topical token is an
    anchor, so `need` is corpus-independent."""
    text = _normalise(query)
    if index is not None and getattr(index, "_vectorizer", None) is not None:
        analyzer = index._vectorizer.build_analyzer()
        vocab = index._vectorizer.vocabulary_
        bm25 = getattr(index, "_bm25", None)
        idf = bm25._idf if bm25 is not None else {}
        postings = bm25._postings if bm25 is not None else {}
        lognorm = math.log(1.0 + max(2, len(getattr(index, "_chunks", []) or [])))
    else:
        analyzer = _fallback_analyzer
        vocab, idf, postings, lognorm = None, {}, {}, 1.0
    toks = list(analyzer(text))
    uni = list(dict.fromkeys(t for t in toks if " " not in t))
    topical = [t for t in uni if _is_topical(t)]
    # SELF_NAME words count toward "is this a question about something" but
    # never toward relevance.
    topical_all = topical + [t for t in uni if t in SELF_NAME]
    # Date terms come from the RAW tokens: sklearn's stop list drops "may", so
    # "May 2026" would lose its month. "may" counts as a month only next to a
    # year or a day number — "may I know the rate" is the modal.
    raw = _RAW_TOKEN_RE.findall(text)
    date_terms = [t for t in dict.fromkeys(raw) if _DATE_TOKEN_RE.match(t) and t != "may"]
    if re.search(r"\bmay\s+(?:19|20)\d{2}\b|\b\d{1,2}\s+may\b|\bmay\s+\d{1,2}\b", text):
        date_terms.append("may")

    def _df(t: str) -> int:
        post = postings.get(t)
        return int(post[0].size) if post is not None else (1 if vocab and t in vocab else 0)

    if vocab is None:
        anchors, oov_hard, oov_soft = list(topical), [], []
    else:
        anchors = [t for t in topical if t in vocab and _df(t) >= p["hapax_df"]]
        oov = [t for t in topical if t not in anchors]
        oov_soft = [t for t in oov if _FIL_AFFIX_RE.match(t)]
        oov_hard = [t for t in oov if t not in oov_soft]
    aset = set(anchors)
    bigrams = [t for t in dict.fromkeys(toks) if " " in t and (vocab is None or t in vocab)
               and all(part in aset for part in t.split(" "))]
    weights = {t: (idf.get(t, 0.0) / lognorm) if idf else 1.0 for t in anchors + bigrams}
    shape = _shape(text)
    need, reason = _need(topical_all, anchors, shape)
    return QueryProfile(query or "", topical, anchors, oov_hard, oov_soft, bigrams, weights,
                        date_terms, shape["predicate"], shape["declarative"],
                        shape["bot_address"], shape["followup"], shape["present_who"],
                        need, reason, ack_opener=shape["ack_opener"])


_FALLBACK_TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")


def _fallback_analyzer(text: str) -> list:
    """sklearn's default word analyzer shape (unigrams + bigrams, english stop
    words) for the index-free path."""
    try:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as _stop
    except Exception:  # pragma: no cover
        _stop = frozenset()
    words = [w for w in _FALLBACK_TOKEN_RE.findall(text) if w not in _stop]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def information_need(query: str) -> bool:
    """Corpus-independent: does this message ask for information at all?
    False for conversational / meta / feedback / pointer / bot-directed
    utterances and bare single mentions. hybrid_chatbot uses it to answer
    those turns conversationally instead of retrieving and calling the LLM."""
    return analyze_query(query, None).information_need


# ---------------------------------------------------------------------------
# Passage signals and relevance
# ---------------------------------------------------------------------------
def _matches(term: str, text_lc: str, tokens: set) -> bool:
    if term in tokens:
        return True
    if len(term) >= 5 and term in text_lc:
        return True
    if term.endswith("s") and len(term) >= 5:
        stem = term[:-2] if term.endswith("es") else term[:-1]
        return stem in tokens or (len(stem) >= 5 and stem in text_lc)
    return False


@dataclass
class Scored:
    passage: object
    rel: float
    key: float                 # ranking key: score * (0.5 + 0.5 * rel)
    matched: list
    matched_bigrams: int       # anchor bigrams literally adjacent in the text
    title_hit: bool
    tf_max: int
    doc: object                # url (site) or page (charter)
    junk: bool
    chars: int


def _corpus_of(passage) -> str:
    return "site" if hasattr(passage, "url") and hasattr(passage, "title") else "charter"


def _is_front_matter(page: int) -> bool:
    try:
        from . import charter_pages
        info = charter_pages.describe(int(page))
        return not (info.title or info.office)
    except Exception:  # noqa: BLE001 — structure file missing: nothing is front matter
        return False


def score_passages(prof: QueryProfile, index, passages: list, p: dict = PARAMS) -> list:
    """Layer 2: [Scored ...] best-first by key."""
    analyzer = index._vectorizer.build_analyzer()
    w = prof.weights
    q_mass = (sum(w[a] for a in prof.anchors) + p["beta"] * sum(w[b] for b in prof.bigrams)
              + p["gamma"] * len(prof.oov_hard) + p["gamma_soft"] * len(prof.oov_soft))
    out = []
    for ps in passages:
        text_lc = _normalise(ps.text)
        toks = set(analyzer(text_lc))
        matched = [a for a in prof.anchors if _matches(a, text_lc, toks)]
        mset = set(matched)
        m_bg = sum(1 for b in prof.bigrams if b in text_lc)
        unmatched_w = max([w[a] for a in prof.anchors if a not in mset], default=0.0)
        matched_bg_mass = sum(w[b] for b in prof.bigrams if b in text_lc)
        denom = q_mass + p["delta"] * unmatched_w + p["k"]
        rel = (sum(w[a] for a in matched) + p["beta"] * matched_bg_mass) / denom if denom > 0 else 0.0
        corpus = _corpus_of(ps)
        title_hits = 0
        if corpus == "site":
            title_lc = _normalise(getattr(ps, "title", ""))
            junk = title_lc.strip() in _JUNK_TITLES
            ttoks = set(analyzer(title_lc))
            title_hits = sum(1 for a in matched if _matches(a, title_lc, ttoks))
            doc = getattr(ps, "url", None)
        else:
            junk = _is_front_matter(getattr(ps, "page", 0))
            doc = getattr(ps, "page", None)
        tf_max = max([text_lc.count(a) for a in matched], default=0)
        key = (float(ps.score) * (0.5 + 0.5 * rel)
               * (1.0 + p["title_bonus"] * min(title_hits, 3)))
        out.append(Scored(ps, rel, key, matched, m_bg, title_hits >= 1, tf_max, doc, junk, len(ps.text)))
    out.sort(key=lambda s: (s.key, s.rel), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
@dataclass
class Verdict:
    augment: list = field(default_factory=list)     # [(passage, rel)] best-first, <= 2
    quote: Optional[tuple] = None                   # (passage, rel) or None
    profile: Optional[QueryProfile] = None
    reason: str = ""


def _quote_blockers(prof: QueryProfile, top: Scored, others_best_rel: float, p: dict) -> list:
    why = []
    if not prof.quote_ok:
        why.append("shape")
    if top.junk:
        why.append("junk")
    if top.rel < p["theta_quote"]:
        why.append(f"rel<{p['theta_quote']}")
    if len(top.matched) < p["quote_min_match"]:
        why.append(f"nm{len(top.matched)}")
    if top.matched_bigrams < 1:
        why.append("bg0")
    if prof.oov_hard:
        why.append("oov")
    if top.chars < p["quote_min_chars"]:
        why.append("short")
    if top.passage.score < p["quote_score_floor"]:
        why.append("score")
    if top.rel - others_best_rel < p["quote_margin"]:
        why.append(f"margin{top.rel - others_best_rel:.2f}")
    text_lc = _normalise(top.passage.text)
    if any(d not in text_lc for d in prof.date_terms):
        why.append("date")
    if prof.present_who:
        if _corpus_of(top.passage) != "site":
            why.append("who-charter")
        else:
            url = getattr(top.passage, "url", "") or ""
            title = getattr(top.passage, "title", "") or ""
            if _DATED_URL_RE.search(url):
                why.append("who-dated")
            elif not _PERSON_RE.search(top.passage.text):
                why.append("who-noname")
            else:
                asked = {m.group(1).lower() for m in _CAMPUS_RE.finditer(prof.raw)}
                named = {m.group(1).lower() for m in _CAMPUS_RE.finditer(title)}
                if named and not (named & asked):
                    why.append("who-campus")
    return why


def decide(query: str, index, passages: list, p: dict = PARAMS) -> Verdict:
    """Layer 3. `passages` is index.retrieve(query, k=8) for this corpus."""
    if index is None or getattr(index, "_vectorizer", None) is None or not passages:
        return Verdict(reason="no-index-or-candidates")
    prof = analyze_query(query, index, p)
    if prof.need == 0:
        return Verdict(profile=prof, reason=f"need=0:{prof.reason}")
    scored = score_passages(prof, index, passages, p)
    single = len(prof.anchors) == 1
    augment, seen = [], set()
    for s in scored:
        if s.junk or s.rel < p["theta_aug"] or s.passage.score < p["aug_score_floor"]:
            continue
        if not s.matched:
            continue
        if single and not (s.title_hit or s.tf_max >= 2):
            continue
        if s.doc in seen:
            continue
        seen.add(s.doc)
        augment.append((s.passage, s.rel))
        if len(augment) == 2:
            break
    quote = None
    why: list = ["no-augment"]
    if augment:
        top = next(s for s in scored if s.passage is augment[0][0])
        others = max([s.rel for s in scored if s.doc != top.doc and not s.junk], default=0.0)
        why = _quote_blockers(prof, top, others, p)
        if not why:
            quote = (top.passage, top.rel)
    reason = (f"need={prof.need}:{prof.reason} anchors={len(prof.anchors)} oov={len(prof.oov_hard)}"
              f"+{len(prof.oov_soft)}soft aug={len(augment)} "
              f"quote={'yes' if quote else 'no:' + ','.join(why)}")
    return Verdict(augment=augment, quote=quote, profile=prof, reason=reason)
