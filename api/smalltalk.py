"""Benign small talk — engage briefly, stay in scope, never generate.

Sevi's fallback is graded, not binary. Harmful input goes to api/safety.py
(referral + support), coursework goes to off_topic_homework (declines and says
why), genuinely off-topic asks get a scope refusal. But a student asking for a
joke is not any of those: flatly refusing reads as cold from a campus
assistant, while answering as if it were a CvSU question is wrong too. This
module is the middle tier — acknowledge the boundary, give the harmless thing
anyway, then steer back.

    Reads : nothing (content is inline and reviewed)
    Writes: nothing
    Usage : from api.smalltalk import smalltalk_reply

WHY THE CONTENT IS A STATIC LIST AND NOT LLM-GENERATED
------------------------------------------------------
CvSU is a state university, so its public-facing content falls under the
Philippine Gender and Development (GAD) mandate — the Magna Carta of Women
(RA 9710) and the PCW's gender-fair media guidelines, alongside the Safe
Spaces Act (RA 11313) which the university already cites in its own
anti-harassment responses. A generated joke cannot be certified against any of
that before a student sees it; a fixed list can be read, approved, and signed
off by the campus GAD Focal Point System exactly like every other curated
response in this repo. Cost of the static list is that it repeats. That is the
correct trade for content the university is accountable for.

GAD SCREEN — every entry below must satisfy ALL of these. Anything added later
must be re-screened, and material changes should go back to the GAD Focal
Point System before deployment:
  1. No gendered subject, role, pronoun, or stereotype. Subjects are objects,
     plants, or an ungendered "student"/"they".
  2. No appearance, body, weight, age, or ability as the punchline.
  3. No ethnicity, religion, region, civil status, sexuality, or economic
     status as the punchline.
  4. No romantic or sexual framing, and nothing about relationships between
     students and staff.
  5. Punches sideways or at the situation — never down at a person or group.
  6. Safe read aloud in a classroom by anyone, to anyone.
Themes are drawn from CvSU's own agricultural and academic identity so the
humour still belongs to the institution.
"""
import random
import re
from typing import Optional

# Curated, GAD-screened. See the module docstring before editing.
JOKES_EN = [
    "I asked the library for a book about procrastination. They said they'd get back to me.",
    "Why do plants make such calm classmates? Whatever the weather, they just keep growing.",
    "I told a seed a secret. It never leaked — it just sprouted.",
    "Why did the soil science report get top marks? It went deep.",
    "The campus WiFi and I agree on one thing: we both perform better near the library.",
    "Why did the rice plant get invited to every study group? It was very well-grained.",
    "My plan was to study early this semester. My plan is now an elective.",
]

JOKES_TL = [
    "Bakit hindi nagagalit ang halaman? Kasi sikat lang ng araw, tuloy na ang photosynthesis.",
    "Sabi ko sa binhi, sekreto ito. Hindi naman nagsalita — tumubo lang.",
    "Bakit palaging maaga ang magsasaka? Kasi ang pananim, ayaw maghintay.",
    "Ang plano ko sanang mag-aral nang maaga. Ngayon, plano pa rin.",
]

_SCOPE_NOTE_EN = (
    "Telling jokes isn't really what I'm built for — I'm the CvSU assistant, "
    "so I'm best with admissions, enrollment, tuition, scholarships, and campus "
    "services. But here's one anyway:"
)
_SCOPE_NOTE_TL = (
    "Hindi talaga jokes ang specialty ko — CvSU assistant ako, kaya mas magaling "
    "ako sa admissions, enrollment, tuition, scholarships, at campus services. "
    "Pero heto na nga:"
)
_REDIRECT_EN = "Now — anything CvSU-related I can help you with?"
_REDIRECT_TL = "O siya — may maitutulong ba ako tungkol sa CvSU?"

# Whole-message asks only. "joke" appearing inside a real question ("is this a
# joke of a policy") must not trigger it, so each alternative is a request verb
# plus the noun, or the bare noun on its own.
_JOKE_RE = re.compile(
    r"^\s*(?:may\s+)?(?:(?:can|could|will|would)\s+you\s+)?"
    r"(?:please\s+)?"
    r"(?:(?:tell|say|give|share|crack|drop)\s+(?:me\s+|us\s+|a\s+|an\s+|some\s+)*)?"
    r"(?:another\s+|one\s+more\s+|funny\s+|good\s+|clean\s+)*"
    r"(?:joke|jokes|patawa|biro|pabiro|magbiro|banat)"
    r"(?:\s+(?:please|po|nga|naman|pls|daw|ka|ba|mo|kayo))*\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def is_joke_request(text: str) -> bool:
    """True when the whole message is a request for a joke."""
    return bool(text) and bool(_JOKE_RE.match(text))


def joke_reply(filipino: bool = False, rng: Optional[random.Random] = None) -> str:
    """Scope note + a screened joke + a redirect back to CvSU topics."""
    picker = rng or random
    if filipino:
        return f"{_SCOPE_NOTE_TL}\n\n{picker.choice(JOKES_TL)}\n\n{_REDIRECT_TL}"
    return f"{_SCOPE_NOTE_EN}\n\n{picker.choice(JOKES_EN)}\n\n{_REDIRECT_EN}"


def smalltalk_reply(text: str, filipino: bool = False,
                    rng: Optional[random.Random] = None) -> Optional[str]:
    """Reply for a benign social ask, or None to let the cascade handle it."""
    if is_joke_request(text):
        return joke_reply(filipino=filipino, rng=rng)
    return None
