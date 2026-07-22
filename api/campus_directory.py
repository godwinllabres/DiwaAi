"""Per-campus address directory — the deterministic answer for
"where is CvSU <campus>?".

Why this exists: the charter RAG's per-campus sections rank COVER PAGES
(title text, zero address content) above anything useful for location
questions, and the only campus map we have is Indang's — so a General Trias
question used to get a quoted title page plus the Don Severino map card.
Location questions about a known campus should never reach retrieval at all.

Grounding: every address/phone/email below is copied from the official
contact table in the CvSU Citizens' Charter, FY 2026 edition, "Cavite State
University's Contact Information", pp. 2024–2026 (docs/citizens_charter_text.txt
— the same document the RAG tier serves). Strings are kept as printed there;
update this table only from an official source, and cite it.

Keys match api/campus_context.py CAMPUSES canonical names, so the campus a
session resolves to indexes straight into this table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    from . import campus_context as _campus
except ImportError:  # imported as a top-level module (scripts, tests)
    import campus_context as _campus

SOURCE_CITATION = (
    "CvSU Citizens' Charter, FY 2026 edition — Contact Information, "
    "pp. 2024–2026"
)

MAIN_CAMPUS = "Indang (Main Campus)"


@dataclass(frozen=True)
class CampusInfo:
    display_name: str
    address: str
    phone: Optional[str] = None
    email: Optional[str] = None


DIRECTORY: dict[str, CampusInfo] = {
    MAIN_CAMPUS: CampusInfo(
        "CvSU Main Campus (Don Severino delas Alas Campus)",
        "Brgy. Bancod, Indang, Cavite",
        "(046) 483 9250 (trunkline)",
        None,  # per-office emails; the trunkline directory card covers these
    ),
    "Bacoor City Campus": CampusInfo(
        "CvSU Bacoor City Campus",
        "Molino VI, Bacoor City, Cavite",
        "(046) 476-50-29",
        "cvsubacoor@cvsu.edu.ph",
    ),
    "Carmona Campus": CampusInfo(
        "CvSU Carmona Campus",
        "Carmona, Cavite",
        "(046) 487-6328",
        "cvsucarmona@cvsu.edu.ph",
    ),
    "Cavite City Campus": CampusInfo(
        "CvSU Cavite City Campus",
        "Brgy. VIII, Pulo II, Dalahican, Cavite City",
        "(046) 527-8624",
        "cvsucavitecity@cvsu.edu.ph",
    ),
    "CvSU-CCAT Campus (Rosario)": CampusInfo(
        "CvSU-CCAT Campus (Rosario)",
        "Rosario, Cavite",
        "(046) 437-9505",
        None,  # the charter lists per-office emails only for CCAT
    ),
    "General Trias City Campus": CampusInfo(
        "CvSU General Trias City Campus",
        "Brgy. Vibora, General Trias City, Cavite",
        "(046) 509-4148",
        "cvsugeneraltrias@cvsu.edu.ph",
    ),
    "Imus City Campus": CampusInfo(
        "CvSU Imus City Campus",
        "LTO Compound, Imus City, Cavite",
        "(046) 471-6607 / (046) 436-6584",
        "cvsuimus@cvsu.edu.ph",
    ),
    "Maragondon Campus": CampusInfo(
        "CvSU Maragondon Campus",
        "Sta. Mercedes Ville, Maragondon, Cavite",
        "0916-323-8752",
        "cvsumaragondon@cvsu.edu.ph",
    ),
    "Naic Campus": CampusInfo(
        "CvSU Naic Campus",
        "Naic, Cavite",
        "(046) 423-8225",
        None,  # the charter lists per-office @cvsu-naic.edu.ph emails only
    ),
    "Silang Campus": CampusInfo(
        "CvSU Silang Campus",
        "Brgy. Biga I, Silang, Cavite",
        "(046) 513-3965 / 0917-805-3602",
        "cvsusilang@cvsu.edu.ph",
    ),
    "Tanza Campus": CampusInfo(
        "CvSU Tanza Campus",
        "Brgy. Bagtas, Tanza, Cavite",
        "(046) 414-3979",
        "cvsutanza@cvsu.edu.ph",
    ),
    "Trece Martires City Campus": CampusInfo(
        "CvSU Trece Martires City Campus",
        "Brgy. Gregorio, Trece Martires City, Cavite",
        "0977-803-3809",
        "cvsutrecemartires@cvsu.edu.ph",
    ),
}

SUGGESTIONS = ["Courses offered", "Admission requirements", "Contact CvSU"]


def get(campus: Optional[str]) -> Optional[CampusInfo]:
    return DIRECTORY.get(campus) if campus else None


def is_satellite(campus: Optional[str]) -> bool:
    """True for a known campus that ISN'T the main (Indang) campus — the one
    campus the map actually depicts."""
    return bool(campus) and campus != MAIN_CAMPUS and campus in DIRECTORY


def is_directory_turn(message: str, campus: Optional[str]) -> bool:
    """Should this turn be answered from the directory instead of the cascade?
    Satellite campuses only: the main campus keeps its richer canned answer +
    map card from the campus_location intent tier."""
    return is_satellite(campus) and _campus.is_campus_location_question(message)


def build_answer(campus: str) -> tuple[str, CampusInfo]:
    """Response text + structured info for a satellite-campus location ask."""
    info = DIRECTORY[campus]
    parts = [f"The {info.display_name} is located at {info.address}."]
    contact_bits = [b for b in (info.phone, info.email) if b]
    if contact_bits:
        parts.append("You can reach the campus at " + " or ".join(contact_bits) + ".")
    parts.append(
        "For office-level concerns, the campus can point you to the right "
        f"unit. (Source: {SOURCE_CITATION}.)"
    )
    return " ".join(parts), info
