import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

MODEL_DIR = Path("models")
INTENT_DB = Path("data/cavsu_intents.db")
INTENT_JSON = Path("data/cavsu_intents.json")
RESPONSES_PATH = MODEL_DIR / "responses_map.json"

SAMPLE_MESSAGE = "What are the admission requirements?"


def try_api(url, message):
    if not requests:
        return None
    try:
        resp = requests.post(url, json={"message": message, "user_id": "test_request"}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}", "text": resp.text}
    except Exception as e:
        return {"error": str(e)}


def direct_chat(message):
    from hybrid_chatbot import HybridChatbot

    bot = HybridChatbot(
        model_dir=str(MODEL_DIR),
        responses_path=str(RESPONSES_PATH),
        intents_db_path=str(INTENT_DB),
        intents_json_path=str(INTENT_JSON),
    )
    intent, response, confidence, model_used = bot.chat(message, user_id="test_request")
    return {
        "intent": intent,
        "response": response,
        "confidence": confidence,
        "model_used": model_used,
    }


def run_training():
    print("\n=== Running training: train_naive_bayes.py ===")
    result = subprocess.run([sys.executable, "train_naive_bayes.py"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("train_naive_bayes.py failed")
    print("=== Training complete ===\n")


def main():
    print("Starting request-train-request cycle")
    api_urls = [
        "http://127.0.0.1:8000/chat",
        "http://127.0.0.1:8001/chat",
    ]

    api_result = None
    for url in api_urls:
        if requests:
            api_result = try_api(url, SAMPLE_MESSAGE)
            if api_result is None or api_result.get("error"):
                print(f"API at {url} not available or returned error: {api_result}")
                api_result = None
            else:
                print(f"Request sent to API {url}")
                break

    if api_result:
        print("\n--- API response before training ---")
        print(json.dumps(api_result, indent=2, ensure_ascii=False))
    else:
        print("\nAPI not available, using direct model invocation before training.")
        api_result = direct_chat(SAMPLE_MESSAGE)
        print(json.dumps(api_result, indent=2, ensure_ascii=False))

    run_training()

    print("\nUsing direct model invocation after training.")
    post_train_result = direct_chat(SAMPLE_MESSAGE)
    print("\n--- Response after training ---")
    print(json.dumps(post_train_result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
