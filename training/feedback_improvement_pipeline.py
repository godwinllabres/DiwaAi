"""
Feedback-driven evaluation and dataset improvement pipeline.

Workflow:
1. Generate labeled requests from the current intent dataset.
2. Send each request to /chat.
3. Assess each response with rule-based scoring.
4. Submit feedback to /feedback for every response.
5. Pull feedback back from GET /feedback and GET /feedback/stats.
6. Add missed phrasing to the correct intent patterns.
7. Rebuild the SQLite intent database and optionally retrain the model.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intents_db import create_intents_database  # noqa: E402

INTENTS_PATH = ROOT / "data" / "cavsu_intents.json"
DB_PATH = ROOT / "data" / "cavsu_intents.db"
RESULTS_DIR = ROOT / "logs"

REQUEST_PREFIXES = [
    "",
    "Please explain ",
    "Can you help me with ",
    "I need information about ",
    "Tell me about ",
]

REQUEST_SUFFIXES = [
    "",
    "?",
    " please.",
    " for new students?",
    " this year?",
]


def load_intents() -> List[Dict]:
    with open(INTENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["intents"]


def normalize_prompt(pattern: str) -> str:
    return " ".join(pattern.strip().split())


def build_query(pattern: str) -> str:
    base = normalize_prompt(pattern)
    trimmed = base.rstrip("?.! ")

    if random.random() < 0.5:
        return base if base.endswith(("?", ".", "!")) else f"{base}?"

    prefix = random.choice(REQUEST_PREFIXES)
    suffix = random.choice(REQUEST_SUFFIXES)
    if not prefix:
        return f"{trimmed}{suffix or '?'}"
    return f"{prefix}{trimmed}{suffix}"


def build_request_plan(intents: List[Dict], total_requests: int) -> List[Dict]:
    active_intents = [
        intent for intent in intents
        if intent.get("active", True) and intent["tag"] != "nlu_fallback"
    ]
    if not active_intents:
        raise ValueError("No active intents available for evaluation.")

    base_count = total_requests // len(active_intents)
    remainder = total_requests % len(active_intents)
    plan = []

    for index, intent in enumerate(active_intents):
        target_count = base_count + (1 if index < remainder else 0)
        patterns = intent.get("patterns", [])
        if not patterns:
            continue

        for _ in range(target_count):
            source_pattern = random.choice(patterns)
            plan.append({
                "expected_intent": intent["tag"],
                "source_pattern": source_pattern,
                "query": build_query(source_pattern)
            })

    random.shuffle(plan)
    return plan


def assess_response(expected_intent: str, actual_intent: str, confidence: float, response_text: str) -> Dict:
    response_text = (response_text or "").strip()
    response_lower = response_text.lower()
    fallback_like = actual_intent == "nlu_fallback" or "rephrase" in response_lower or "clarify" in response_lower

    if not response_text:
        return {
            "helpful": False,
            "rating": 1,
            "comment": "Auto-review: empty response returned by API.",
            "suggested_intent": expected_intent
        }

    if actual_intent == expected_intent and not fallback_like:
        if confidence >= 0.90:
            return {"helpful": True, "rating": 5, "comment": None, "suggested_intent": None}
        if confidence >= 0.70:
            return {
                "helpful": True,
                "rating": 4,
                "comment": "Auto-review: correct intent, but confidence can still improve.",
                "suggested_intent": None
            }
        return {
            "helpful": True,
            "rating": 3,
            "comment": "Auto-review: correct intent, but low confidence suggests more training patterns are needed.",
            "suggested_intent": None
        }

    if fallback_like:
        return {
            "helpful": False,
            "rating": 1,
            "comment": f"Auto-review: expected intent '{expected_intent}' but fallback was returned.",
            "suggested_intent": expected_intent
        }

    rating = 1 if confidence >= 0.85 else 2
    return {
        "helpful": False,
        "rating": rating,
        "comment": (
            f"Auto-review: expected intent '{expected_intent}' but got '{actual_intent}' "
            f"with confidence {confidence:.2f}."
        ),
        "suggested_intent": expected_intent
    }


def ensure_api_available(base_url: str) -> None:
    response = requests.get(f"{base_url}/health", timeout=10)
    response.raise_for_status()


def apply_feedback_to_dataset(feedback_entries: List[Dict], apply_changes: bool, run_id: str) -> Dict:
    intents_doc = load_intents()
    intent_map = {intent["tag"]: intent for intent in intents_doc}
    existing_patterns = {
        tag: set(intent.get("patterns", []))
        for tag, intent in intent_map.items()
    }
    additions = defaultdict(list)

    for entry in feedback_entries:
        target_intent = entry.get("suggested_intent")
        query = (entry.get("user_message") or "").strip()
        helpful = entry.get("helpful")
        rating = entry.get("rating")

        if not target_intent and helpful is True and rating is not None and rating <= 3:
            target_intent = entry.get("intent")

        if not target_intent or not query or target_intent not in intent_map:
            continue

        if query in existing_patterns[target_intent]:
            continue

        intent_map[target_intent]["patterns"].append(query)
        existing_patterns[target_intent].add(query)
        additions[target_intent].append(query)

    output = {
        "patterns_added": sum(len(values) for values in additions.values()),
        "by_intent": {intent: values for intent, values in sorted(additions.items())}
    }

    if not additions:
        return output

    if apply_changes:
        backup_path = ROOT / "data" / f"cavsu_intents.backup_{run_id}.json"
        with open(INTENTS_PATH, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())

        with open(INTENTS_PATH, "w", encoding="utf-8") as f:
            json.dump({"intents": intents_doc}, f, indent=2, ensure_ascii=False)

        output["backup_file"] = str(backup_path)
        output["output_file"] = str(INTENTS_PATH)
    else:
        output_path = ROOT / "data" / f"cavsu_intents_feedback_improved_{run_id}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"intents": intents_doc}, f, indent=2, ensure_ascii=False)
        output["output_file"] = str(output_path)

    return output


def _make_session(admin_pin: Optional[str] = None) -> requests.Session:
    """Session carrying the admin PIN so the gated reads this pipeline relies on
    (GET /feedback, GET /feedback/stats) authenticate. Falls back to the
    DASHBOARD_PIN env var. POST /chat and POST /feedback are public."""
    s = requests.Session()
    pin = admin_pin or os.getenv("DASHBOARD_PIN", "")
    if pin:
        s.headers["X-Admin-Pin"] = pin
    return s


def run_pipeline(base_url: str, total_requests: int, apply_changes: bool, retrain: bool,
                 admin_pin: Optional[str] = None) -> Path:
    ensure_api_available(base_url)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"feedback-loop-{run_id}"
    results_path = RESULTS_DIR / f"feedback_loop_{run_id}.jsonl"
    summary_path = RESULTS_DIR / f"feedback_loop_summary_{run_id}.json"

    intents = load_intents()
    request_plan = build_request_plan(intents, total_requests)

    session = _make_session(admin_pin)
    stats = Counter()
    rating_counts = Counter()
    intent_mismatches = Counter()
    confidence_total = 0.0
    feedback_failures = 0

    for index, item in enumerate(request_plan, start=1):
        user_id = f"{session_id}-user-{index}"
        payload = {
            "message": item["query"],
            "user_id": user_id,
            "session_id": session_id
        }

        chat_response = session.post(f"{base_url}/chat", json=payload, timeout=30)
        chat_response.raise_for_status()
        chat_data = chat_response.json()

        actual_intent = chat_data.get("intent", "")
        confidence = float(chat_data.get("confidence", 0.0) or 0.0)
        message_id = chat_data.get("message_id")
        assessment = assess_response(
            expected_intent=item["expected_intent"],
            actual_intent=actual_intent,
            confidence=confidence,
            response_text=chat_data.get("response", "")
        )

        feedback_payload = {
            "message_id": message_id,
            "user_id": user_id,
            "session_id": session_id,
            "intent": actual_intent or None,
            "rating": assessment["rating"],
            "helpful": assessment["helpful"],
            "comment": assessment["comment"],
            "suggested_intent": assessment["suggested_intent"]
        }

        feedback_response = session.post(f"{base_url}/feedback", json=feedback_payload, timeout=30)
        if not feedback_response.ok:
            feedback_failures += 1

        record = {
            "index": index,
            "query": item["query"],
            "source_pattern": item["source_pattern"],
            "expected_intent": item["expected_intent"],
            "actual_intent": actual_intent,
            "confidence": confidence,
            "message_id": message_id,
            "helpful": assessment["helpful"],
            "rating": assessment["rating"],
            "comment": assessment["comment"],
            "suggested_intent": assessment["suggested_intent"]
        }

        stats["total"] += 1
        confidence_total += confidence
        rating_counts[assessment["rating"]] += 1
        if actual_intent == item["expected_intent"]:
            stats["correct"] += 1
        else:
            stats["incorrect"] += 1
            intent_mismatches[(item["expected_intent"], actual_intent)] += 1
        if assessment["helpful"]:
            stats["helpful"] += 1
        else:
            stats["unhelpful"] += 1

        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        if index % 100 == 0 or index == total_requests:
            print(f"Processed {index} / {total_requests}")

    feedback_entries_response = session.get(
        f"{base_url}/feedback",
        params={"limit": total_requests + 100, "session_id": session_id},
        timeout=30
    )
    feedback_entries_response.raise_for_status()
    feedback_entries = feedback_entries_response.json()["feedback"]

    feedback_stats_response = session.get(f"{base_url}/feedback/stats", timeout=30)
    feedback_stats_response.raise_for_status()
    feedback_stats = feedback_stats_response.json()

    additions = apply_feedback_to_dataset(feedback_entries, apply_changes=apply_changes, run_id=run_id)

    retrained = False
    if apply_changes:
        create_intents_database(json_path=str(INTENTS_PATH), db_path=str(DB_PATH), recreate=True)
        if retrain:
            subprocess.run([sys.executable, str(ROOT / "train_naive_bayes.py")], cwd=str(ROOT), check=True)
            retrained = True

    summary = {
        "run_id": run_id,
        "base_url": base_url,
        "session_id": session_id,
        "total_requests": stats["total"],
        "correct_intent_count": stats["correct"],
        "incorrect_intent_count": stats["incorrect"],
        "intent_accuracy": round(stats["correct"] / max(stats["total"], 1), 4),
        "helpful_count": stats["helpful"],
        "unhelpful_count": stats["unhelpful"],
        "average_confidence": round(confidence_total / max(stats["total"], 1), 4),
        "feedback_failures": feedback_failures,
        "ratings": dict(sorted(rating_counts.items())),
        "top_mismatches": [
            {
                "expected_intent": expected,
                "actual_intent": actual,
                "count": count
            }
            for (expected, actual), count in intent_mismatches.most_common(10)
        ],
        "dataset_additions": additions,
        "feedback_stats_snapshot": feedback_stats,
        "results_file": str(results_path),
        "retrained": retrained,
        "restart_api_required": retrained or apply_changes
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"Summary written to {summary_path}")
    return summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the feedback-driven improvement pipeline.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="Base API URL.")
    parser.add_argument("--requests", type=int, default=1000, help="Number of requests to evaluate.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write recovered patterns back into data/cavsu_intents.json."
    )
    parser.add_argument(
        "--retrain",
        action="store_true",
        help="Retrain the Naive Bayes model after applying dataset changes."
    )
    parser.add_argument(
        "--admin-pin",
        default=os.getenv("DASHBOARD_PIN", ""),
        help="X-Admin-Pin for gated /feedback and /feedback/stats reads (default: DASHBOARD_PIN env var)."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.time()
    summary_path = run_pipeline(
        base_url=args.base_url.rstrip("/"),
        total_requests=args.requests,
        apply_changes=args.apply,
        retrain=args.retrain,
        admin_pin=args.admin_pin,
    )
    elapsed = time.time() - started
    print(f"Pipeline completed in {elapsed:.1f}s")
    print(f"Open summary: {summary_path}")


if __name__ == "__main__":
    main()
