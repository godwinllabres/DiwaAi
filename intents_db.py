import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_JSON_PATH = "data/cavsu_intents.json"
DEFAULT_DB_PATH = "data/cavsu_intents.db"

# ── Code-owned system replies ────────────────────────────────────────────────
# "llm_unavailable" is served by the cascade's Step 4a (api/hybrid_chatbot.py,
# LLM_UNAVAILABLE_INTENT) when a configured LLM cannot answer the turn. It is
# deliberately NOT an intent in this store: the classifier must never learn to
# PREDICT a degrade state from user text. Every script that REBUILDS
# models/responses_map.json from this store must call inject_system_responses()
# before writing, or the key is silently dropped — which is exactly how the
# honest "LLM is down" reply was lost on 2026-07-30.
SYSTEM_RESPONSES = {
    "llm_unavailable": [
        "Sorry, I'm having trouble reaching my knowledge base right now — "
        "this is on my end, not your question. Please try again in a moment.",
        "Pasensya na po, may problema ako sa pagkuha ng impormasyon sa ngayon "
        "— wala pong mali sa tanong ninyo. Pakisubukang muli mamaya.",
    ],
}


def _usable_variants(value) -> bool:
    """A usable reply entry is a non-empty list of non-empty strings. A bare
    string is NOT usable: _select_response treats the value as a sequence of
    variants, so a string would be ranked and served character by character."""
    return (isinstance(value, list) and len(value) > 0
            and all(isinstance(v, str) and v.strip() for v in value))


def inject_system_responses(responses_map, map_path="models/responses_map.json") -> None:
    """Carry code-owned reply keys through a rebuild of the responses map.

    Prefers the text already in the existing artifact when it has a usable
    shape (so a hand-edit to the deployed copy survives), else seeds the
    canonical default — including when the store produced an empty or
    malformed entry. Tolerates a missing, corrupt, or non-dict previous
    artifact."""
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            previous_map = json.load(f)
    except (OSError, ValueError):
        previous_map = {}
    if not isinstance(previous_map, dict):
        previous_map = {}
    for key, default in SYSTEM_RESPONSES.items():
        if _usable_variants(responses_map.get(key)):
            continue  # the store provided real content — leave it alone
        carried = previous_map.get(key)
        use_carried = _usable_variants(carried)
        responses_map[key] = carried if use_carried else list(default)
        print(f"[OK] System reply '{key}' "
              f"{'carried over from existing map' if use_carried else 'seeded from default'}")

CREATE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT "",
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id INTEGER NOT NULL,
    pattern_text TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES intents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id INTEGER NOT NULL,
    response_text TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES intents(id) ON DELETE CASCADE
);
"""


def _ensure_directory(db_path: str) -> None:
    directory = Path(db_path).parent
    directory.mkdir(parents=True, exist_ok=True)


def _connect(db_path: str) -> sqlite3.Connection:
    _ensure_directory(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def create_intents_database(
    json_path: str = DEFAULT_JSON_PATH,
    db_path: str = DEFAULT_DB_PATH,
    recreate: bool = False
) -> str:
    """Create or refresh the SQLite database from the JSON intents file."""
    if recreate and os.path.exists(db_path):
        os.remove(db_path)

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Intents JSON file not found: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        intents_data = json.load(f)

    conn = _connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(CREATE_SCHEMA_SQL)

    for intent in intents_data.get("intents", []):
        tag = intent.get("tag")
        description = intent.get("description", "")
        active = 1 if intent.get("active", True) else 0

        cursor.execute(
            "INSERT OR IGNORE INTO intents (tag, description, active) VALUES (?, ?, ?)",
            (tag, description, active)
        )
        intent_id = cursor.execute(
            "SELECT id FROM intents WHERE tag = ?",
            (tag,)
        ).fetchone()["id"]

        for pattern in intent.get("patterns", []):
            cursor.execute(
                "INSERT INTO patterns (intent_id, pattern_text) VALUES (?, ?)",
                (intent_id, pattern)
            )

        for response in intent.get("responses", []):
            cursor.execute(
                "INSERT INTO responses (intent_id, response_text) VALUES (?, ?)",
                (intent_id, response)
            )

    conn.commit()
    conn.close()
    return db_path


def load_intents_from_db(db_path: str = DEFAULT_DB_PATH) -> List[Dict]:
    """Load intents from the SQLite database.

    NB: we materialize the outer SELECT into a list before iterating, and use
    a *separate* cursor for the inner pattern/response queries. Reusing one
    cursor for nested SELECTs clobbers the outer rowset after the first
    inner execute, so the loop would silently terminate with just one intent
    — which is exactly what was happening before this fix.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Intent database not found: {db_path}")

    conn = _connect(db_path)
    outer = conn.cursor()
    inner = conn.cursor()

    intent_rows = outer.execute(
        "SELECT id, tag, description, active FROM intents ORDER BY tag"
    ).fetchall()

    intents = []
    for intent_row in intent_rows:
        intent_id = intent_row["id"]
        patterns = [row["pattern_text"] for row in inner.execute(
            "SELECT pattern_text FROM patterns WHERE intent_id = ? ORDER BY id",
            (intent_id,)
        )]
        responses = [row["response_text"] for row in inner.execute(
            "SELECT response_text FROM responses WHERE intent_id = ? ORDER BY id",
            (intent_id,)
        )]

        intents.append({
            "tag": intent_row["tag"],
            "description": intent_row["description"],
            "active": bool(intent_row["active"]),
            "patterns": patterns,
            "responses": responses
        })

    conn.close()
    return intents


def _load_intents_from_json(json_path: str) -> List[Dict]:
    """Load intents directly from JSON without DB fallback."""
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Intents JSON file not found: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        intents_data = json.load(f)

    return intents_data.get("intents", [])


def load_intents(
    json_path: str = DEFAULT_JSON_PATH,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict]:
    """Load intents from DB if available, otherwise fallback to JSON."""
    def _normalized(intents: List[Dict]) -> List[Dict]:
        return sorted(
            [
                {
                    "tag": intent.get("tag"),
                    "description": intent.get("description", ""),
                    "active": bool(intent.get("active", True)),
                    "patterns": intent.get("patterns", []),
                    "responses": intent.get("responses", [])
                }
                for intent in intents
            ],
            key=lambda item: item["tag"]
        )

    if os.path.exists(db_path):
        db_intents = load_intents_from_db(db_path)

        if os.path.exists(json_path):
            json_intents = _load_intents_from_json(json_path)
            if _normalized(db_intents) != _normalized(json_intents):
                # Rebuild stale database from current JSON source
                create_intents_database(json_path=json_path, db_path=db_path, recreate=True)
                return _load_intents_from_json(json_path)

        return db_intents

    return _load_intents_from_json(json_path)


def build_responses_map(intents: List[Dict]) -> Dict[str, List[str]]:
    """Build a tag->responses mapping from intent records."""
    return {intent["tag"]: intent.get("responses", []) for intent in intents}


if __name__ == "__main__":
    db_path = create_intents_database()
    print(f"Intent database created at {db_path}")

