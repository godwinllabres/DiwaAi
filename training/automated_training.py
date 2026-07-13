"""
Automated Training Pipeline
Orchestrates: API startup -> Test -> Analysis -> Expansion -> Retrain -> Verify
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

def run_command(cmd, description, check=True):
    """Run a command (list of args, no shell) and return result"""
    print(f"\n{'-' * 70}")
    print(f"[RUN] {description}")
    print(f"{'-' * 70}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
            check=False
        )

        if check and result.returncode != 0:
            print(f"[ERROR] Failed: {description}")
            return False

        print(f"[SUCCESS] Complete: {description}")
        return True

    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return False

def start_api(port=8001):
    """Start API in background"""
    print(f"\n{'=' * 70}")
    print("  AUTOMATED TRAINING PIPELINE")
    print(f"{'=' * 70}")

    print(f"\n[Step 1/6] Starting API on port {port}...")

    # Start API in background (Popen, no shell); redirect output to api.log
    with open("api.log", "w", encoding="utf-8") as logf:
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            stdout=logf, stderr=subprocess.STDOUT,
        )

    # Wait for API to start
    print(f"Waiting for API to initialize...", end="", flush=True)
    for i in range(30):
        try:
            import requests
            response = requests.get(f"http://127.0.0.1:{port}/health", timeout=1)
            if response.status_code == 200:
                print(" [SUCCESS]")
                return True
        except:
            pass

        print(".", end="", flush=True)
        time.sleep(1)

    print(" [ERROR]")
    return False

def test_intents(port=8001):
    """Run intent tests"""
    print(f"\n[Step 2/6] Testing intents ({10} queries per intent)...")

    cmd = [sys.executable, "test_intents.py", str(port), "10"]
    return run_command(cmd, "Intent Testing", check=False)

def analyze_results():
    """Analyze test results"""
    print(f"\n[Step 3/6] Analyzing results...")

    try:
        with open("intent_test_results.json", "r") as f:
            results = json.load(f)

        overall_acc = results.get("overall_accuracy", 0)
        per_intent = results.get("per_intent", {})

        weak = [
            (intent, data["accuracy"])
            for intent, data in per_intent.items()
            if data["accuracy"] < 80
        ]

        print(f"\n[STATS] Current Performance:")
        print(f"   Overall Accuracy: {overall_acc:.1f}%")
        print(f"   Intents Tested: {len(per_intent)}")

        if weak:
            print(f"\n[WARN]️  Weak Intents ({len(weak)}):")
            for intent, acc in sorted(weak, key=lambda x: x[1]):
                print(f"   - {intent}: {acc:.0f}%")

        return overall_acc, len(weak)

    except Exception as e:
        print(f"[ERROR] Error analyzing results: {e}")
        return 0, 0

def expand_and_retrain():
    """Expand intents and retrain model"""
    print(f"\n[Step 4/6] Expanding training patterns...")

    # Backup original
    shutil.copy("data/cavsu_intents.json", "data/cavsu_intents.backup.json")

    # Expand
    if not run_command([sys.executable, "expand_intents.py"], "Pattern Expansion", check=False):
        print("Warning: Pattern expansion had issues, continuing anyway...")

    # Use expanded if it exists, otherwise keep original
    if Path("data/cavsu_intents_expanded.json").exists():
        shutil.move("data/cavsu_intents_expanded.json", "data/cavsu_intents.json")
        print("[SUCCESS] Using expanded intents")

    # Retrain
    print(f"\n[Step 5/6] Retraining model...")
    return run_command([sys.executable, "train_naive_bayes.py"], "Model Retraining")

def verify_improvement(port=8001):
    """Verify improvement after retraining"""
    print(f"\n[Step 6/6] Verifying improvement...")

    # Restart API
    print("Restarting API with new model...")
    subprocess.run(["pkill", "-f", f"uvicorn.*{port}"], check=False)
    time.sleep(2)

    with open("api.log", "w", encoding="utf-8") as logf:
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            stdout=logf, stderr=subprocess.STDOUT,
        )

    # Wait for API
    for i in range(15):
        try:
            import requests
            requests.get(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except:
            time.sleep(1)

    # Quick test
    print("Running quick verification...")
    cmd = [sys.executable, "test_intents.py", str(port), "3"]
    subprocess.run(cmd, capture_output=True, check=False)

    # Load results
    try:
        with open("intent_test_results.json", "r") as f:
            results = json.load(f)

        new_acc = results.get("overall_accuracy", 0)
        print(f"\n[SUCCESS] New Accuracy: {new_acc:.1f}%")

        return new_acc

    except:
        return 0

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8001"
    port = int(port)

    print("\n" + "=" * 70)
    print("  AUTOMATED CHATBOT TRAINING PIPELINE")
    print("=" * 70)
    print(f"\nThis script will:")
    print("  1. Start the API server")
    print("  2. Test all intents with diverse queries")
    print("  3. Analyze performance and identify weak intents")
    print("  4. Automatically expand training patterns")
    print("  5. Retrain the Naive Bayes model")
    print("  6. Verify improvements with new tests")

    # Step 1: Start API
    if not start_api(port=port):
        print("\n[ERROR] Failed to start API. Check if port {port} is available.")
        sys.exit(1)

    # Step 2: Test
    test_intents(port=port)

    # Step 3: Analyze
    baseline_acc, weak_count = analyze_results()

    if baseline_acc >= 90 and weak_count == 0:
        print(f"\n[SUCCESS] Model already performs well! ({baseline_acc:.1f}%)")
        print("No retraining needed.")
        return

    # Step 4-5: Expand and retrain
    if not expand_and_retrain():
        print("\n[ERROR] Retraining failed.")
        sys.exit(1)

    # Step 6: Verify
    final_acc = verify_improvement(port=port)

    # Summary
    print("\n" + "=" * 70)
    print("  TRAINING PIPELINE COMPLETE")
    print("=" * 70)
    print(f"\n[STATS] Results:")
    print(f"   Baseline Accuracy: {baseline_acc:.1f}%")
    print(f"   Final Accuracy:    {final_acc:.1f}%")
    print(f"   Improvement:       {final_acc - baseline_acc:+.1f}%")

    if final_acc > baseline_acc:
        print(f"\n[SUCCESS] Model improved successfully!")
        print(f"\n[LAUNCH] Next steps:")
        print(f"   1. Keep API running (port {port})")
        print(f"   2. Commit model changes: git add models/ && git commit")
        print(f"   3. Deploy with: docker-compose up --build -d")
    else:
        print(f"\n[WARN]️  Model accuracy unchanged or declined.")
        print(f"   Consider adding more diverse patterns to intents.json")

    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()

