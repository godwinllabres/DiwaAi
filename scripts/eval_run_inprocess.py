"""In-process full-pipeline eval with the NEW models, WITHOUT redeploying.

Instantiates HybridChatbot from the repo (new NB+NN+responses_map, charter/site
RAG, and the SAME host Ollama the live api uses) and runs predict() over the
dataset. This exercises the redesigned path (NB -> NN -> intent_retrieval ->
LLM-grounded -> fallback). It does NOT replicate the endpoint-level gates
(safety/campus/AIS) — those are unchanged by the redesign.

Reads : data/eval/mirror_qa.json
Writes: data/eval/responses_new.json  (same shape as responses.json)

Usage: python scripts/eval_run_inprocess.py [--limit N]
"""
import argparse
import json
import os
import re
import sys
import time
import uuid

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--model", default=None, help="override OLLAMA_MODEL")
ap.add_argument("--out", default="data/eval/responses_new.json")
ap.add_argument("--dataset", default="data/eval/mirror_qa.json")
_args = ap.parse_args()

os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
if _args.model:
    os.environ["OLLAMA_MODEL"] = _args.model
os.environ.setdefault("OLLAMA_MODEL", "llama3.2:3b")
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.pop("DATABASE_URL", None)  # avoid Postgres; not needed for predict()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from api.hybrid_chatbot import HybridChatbot  # noqa: E402

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def strip_think(text):
    """Remove reasoning-model <think> blocks (qwen3 etc.) so only the answer is graded."""
    return _THINK_RE.sub("", text).strip() if isinstance(text, str) else text


def main():
    args = _args

    data = json.load(open(args.dataset, encoding="utf-8"))
    if args.limit:
        data = data[: args.limit]

    print("Instantiating HybridChatbot (new models)...")
    bot = HybridChatbot("models", "models/responses_map.json")
    print(f"Loaded {len(bot.responses_map)} intents. Running {len(data)} questions...\n")

    out = []
    for i, item in enumerate(data):
        uid = f"inproc-{uuid.uuid4().hex}"
        t0 = time.time()
        try:
            intent, response, confidence, model_used, _nlu = bot.predict(item["question"], user_id=uid)
            resp = {
                "ok": True,
                "text": strip_think(response),
                "intent": intent,
                "confidence": float(confidence),
                "source": model_used,
                "latency_ms": round((time.time() - t0) * 1000),
            }
        except Exception as e:  # noqa: BLE001
            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        out.append({**item, "response": resp})
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(data)}")

    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    errs = sum(1 for r in out if not r["response"].get("ok"))
    print(f"\ndone: {len(out)} responses ({errs} errors) -> {args.out}")


if __name__ == "__main__":
    main()
