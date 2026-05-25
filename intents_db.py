import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_JSON_PATH = "data/cavsu_intents.json"
DEFAULT_DB_PATH = "data/cavsu_intents.db"

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

