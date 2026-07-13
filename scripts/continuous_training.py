import argparse
import datetime
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DB = Path("logs/chat_history.db")
DEFAULT_LOG = Path("logs/continuous_training.log")
DEFAULT_RETRAIN_CMD = [sys.executable, "train_naive_bayes.py"]
DEFAULT_HYBRID_CMD = [sys.executable, "train_hybrid.py"]


def log(message: str, log_file: Path = DEFAULT_LOG):
    timestamp = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    line = f"[{timestamp}] {message}"
    print(line)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_intent_statistics(db_path: Path):
    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        # Same env seam as api/logger.py — read intent_stats from Postgres.
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(database_url) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT intent, count, avg_confidence FROM intent_stats")
                rows = cursor.fetchall()
    else:
        if not db_path.exists():
            raise FileNotFoundError(f"Intent log database not found: {db_path}")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT intent, count, avg_confidence FROM intent_stats")
        rows = cursor.fetchall()
        conn.close()

    stats = []
    total_count = 0
    weighted_confidence = 0.0
    fallback_count = 0

    for row in rows:
        intent = row["intent"]
        count = row["count"] or 0
        avg_confidence = row["avg_confidence"] or 0.0
        stats.append({
            "intent": intent,
            "count": count,
            "avg_confidence": avg_confidence,
        })
        total_count += count
        weighted_confidence += count * avg_confidence
        if intent == "nlu_fallback":
            fallback_count = count

    overall_avg_confidence = (weighted_confidence / total_count) if total_count > 0 else 0.0
    fallback_rate = (fallback_count / total_count) if total_count > 0 else 0.0

    return {
        "total_count": total_count,
        "fallback_count": fallback_count,
        "fallback_rate": fallback_rate,
        "overall_avg_confidence": overall_avg_confidence,
        "intent_stats": stats,
    }


def should_retrain(stats, fallback_threshold, confidence_threshold, weak_confidence_threshold, weak_min_count):
    fallback_rate = stats["fallback_rate"]
    overall_conf = stats["overall_avg_confidence"]
    weak_intents = [
        intent for intent in stats["intent_stats"]
        if intent["intent"] != "nlu_fallback"
        and intent["count"] >= weak_min_count
        and intent["avg_confidence"] < weak_confidence_threshold
    ]

    return {
        "retrain": (
            fallback_rate >= fallback_threshold
            or overall_conf < confidence_threshold
            or len(weak_intents) > 0
        ),
        "fallback_rate": fallback_rate,
        "overall_confidence": overall_conf,
        "weak_intents": weak_intents,
    }


def run_command(command, description):
    log(f"Starting: {description} -> {' '.join(command)}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        log(f"Success: {description}")
        return True
    log(f"Failed: {description}")
    log(result.stdout)
    log(result.stderr)
    return False


def retrain_pipeline(retrain_hybrid):
    success = run_command(DEFAULT_RETRAIN_CMD, "Train Naive Bayes model")
    if not success:
        return False

    if retrain_hybrid:
        return run_command(DEFAULT_HYBRID_CMD, "Train Hybrid model")

    return True


def parse_args():
    parser = argparse.ArgumentParser(
        description="Continuously monitor logs and retrain the chatbot model when quality drops."
    )
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between checks")
    parser.add_argument("--fallback-threshold", type=float, default=0.10, help="Fallback ratio threshold to trigger retraining")
    parser.add_argument("--confidence-threshold", type=float, default=0.70, help="Overall average confidence threshold to trigger retraining")
    parser.add_argument("--weak-confidence-threshold", type=float, default=0.65, help="Per-intent confidence threshold for weak intents")
    parser.add_argument("--weak-min-count", type=int, default=10, help="Minimum intent examples before flagging weak intents")
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DB), help="Path to chat history SQLite database")
    parser.add_argument("--retrain-hybrid", action="store_true", help="Also retrain the hybrid neural network model")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    log(f"Continuous training started. interval={args.interval}s, fallback_threshold={args.fallback_threshold:.2%}, confidence_threshold={args.confidence_threshold:.2%}")

    while True:
        try:
            stats = get_intent_statistics(Path(args.db_path))
            decision = should_retrain(
                stats,
                fallback_threshold=args.fallback_threshold,
                confidence_threshold=args.confidence_threshold,
                weak_confidence_threshold=args.weak_confidence_threshold,
                weak_min_count=args.weak_min_count,
            )

            log(f"Evaluation: total={stats['total_count']}, fallback_rate={decision['fallback_rate']:.2%}, overall_confidence={decision['overall_confidence']:.2%}, weak_intents={len(decision['weak_intents'])}")

            if decision["retrain"]:
                log("Retraining triggered based on model quality metrics")
                retrain_pipeline(args.retrain_hybrid)
            else:
                log("No retraining needed at this time")

        except Exception as exc:
            log(f"Error during evaluation: {exc}")

        if args.once:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
