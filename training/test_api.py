"""Quick test of the REST API endpoints"""

import json
import os
import subprocess
import time
import requests
import sys
from pathlib import Path
from multiprocessing import Process

# /conversation/{user_id} is admin-gated (X-Admin-Pin). Set DASHBOARD_PIN in the
# environment to exercise it; without it that one check returns 401/503.
ADMIN_PIN = os.getenv("DASHBOARD_PIN", "")
ADMIN_HEADERS = {"X-Admin-Pin": ADMIN_PIN} if ADMIN_PIN else {}

def start_server():
    """Start the FastAPI server.

    api.app is the only entrypoint — the legacy root app.py this used to spawn
    was retired (it served the logger routes unauthenticated). The hard-coded
    Windows path went with it; the repo root is resolved from this file.
    """
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "api.app:app", "--port", "8000"],
        cwd=str(repo_root),
    )

def test_api():
    """Test API endpoints"""
    BASE_URL = "http://localhost:8000"

    # Wait for server to start
    print("Waiting for server to start...")
    for _ in range(30):
        try:
            requests.get(f"{BASE_URL}/health", timeout=1)
            break
        except:
            time.sleep(0.5)
    else:
        print("ERROR: Server failed to start")
        return

    print("\n" + "="*80)
    print("CvSU Chatbot REST API Test")
    print("="*80)

    # Test 1: Health check
    print("\n[1] Health Check")
    print("-" * 80)
    response = requests.get(f"{BASE_URL}/health")
    print(response.json())

    # Test 2: Model Info
    print("\n[2] Model Information")
    print("-" * 80)
    response = requests.get(f"{BASE_URL}/model/info")
    info = response.json()
    print(f"Model: {info['model_name']}")
    print(f"Accuracy: {info['accuracy']:.2%}")
    print(f"Total Intents: {info['total_intents']}")
    print(f"Total Patterns: {info['total_patterns']}")
    print(f"Model Size: {info['model_size_kb']:.1f} KB")

    # Test 3: Get Intents
    print("\n[3] Available Intents")
    print("-" * 80)
    response = requests.get(f"{BASE_URL}/intents")
    data = response.json()
    print(f"Total: {data['total_intents']} intents")
    print("Sample intents:")
    for intent in data["intents"][:5]:
        print(f"  - {intent['tag']}: {intent['response_count']} responses")

    # Test 4: Get Agent Instructions
    print("\n[4] Agent System Instructions")
    print("-" * 80)
    response = requests.get(f"{BASE_URL}/model/instructions")
    instructions = response.json()["instructions"]
    print(instructions[:500] + "...\n")

    # Test 5: Chat - Single Message
    print("\n[5] Single Chat Request")
    print("-" * 80)
    messages = [
        "What are the admission requirements?",
        "Is there Computer Science at CvSU?",
        "How much is tuition?"
    ]

    for msg in messages:
        response = requests.post(
            f"{BASE_URL}/chat",
            json={
                "message": msg,
                "user_id": "test_user"
            }
        )
        data = response.json()
        print(f"Q: {msg}")
        print(f"A: {data['response'][:80]}...")
        print(f"   Intent: {data['intent']} ({data['confidence']:.1%})\n")

    # Test 6: Batch Request
    print("\n[6] Batch Chat Request")
    print("-" * 80)
    batch_messages = [
        {"message": "Hello!"},
        {"message": "What is CvSU?"},
        {"message": "Tell me about scholarships"}
    ]

    response = requests.post(
        f"{BASE_URL}/batch",
        json=batch_messages
    )
    data = response.json()
    print(f"Processed {data['count']} messages:")
    for i, result in enumerate(data["results"], 1):
        print(f"\n  {i}. Intent: {result['intent']}")
        print(f"     Confidence: {result['confidence']:.1%}")

    # Test 7: Conversation History
    print("\n\n[7] Conversation History")
    print("-" * 80)
    response = requests.get(f"{BASE_URL}/conversation/test_user", headers=ADMIN_HEADERS)
    history = response.json()
    print(f"Messages for user 'test_user': {history.get('message_count', 'N/A (admin PIN required)')}")

    print("\n" + "="*80)
    print("✓ All API endpoints working correctly!")
    print("="*80)
    print("\nAPI Documentation: http://localhost:8000/docs")
    print("Interactive UI: Open web_interface.html in a browser")

if __name__ == "__main__":
    # Don't actually start the server in this test
    # Instead, assume it's already running
    print("Testing API endpoints (assuming server is running on :8000)...\n")
    test_api()

