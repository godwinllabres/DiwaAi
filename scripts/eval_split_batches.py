"""Split responses.json into small batch files for the judge workflow
(agents read a batch, grade every item in it, write a verdict batch).

Reads : data/eval/responses.json
Writes: data/eval/batches/batch_XX.json  (list of items incl. their response)
Prints: the batch ids (JSON array) for the workflow args.

Usage: python scripts/eval_split_batches.py [--size 8]
"""
import argparse
import json
import os

# Defaults kept for the original baseline run; override to grade a rerun
# without clobbering the batches an earlier comparison still depends on.
SRC = "data/eval/responses.json"
OUT_DIR = "data/eval/batches"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()
    src, out_dir = args.src, args.out_dir

    data = json.load(open(src, encoding="utf-8"))
    os.makedirs(out_dir, exist_ok=True)
    # clear stale batches
    for f in os.listdir(out_dir):
        if f.startswith("batch_"):
            os.remove(os.path.join(out_dir, f))

    ids = []
    for i in range(0, len(data), args.size):
        bid = f"{i // args.size:02d}"
        # keep only the fields the judge needs
        batch = [{
            "uid": it["uid"],
            "question": it["question"],
            "gold_answer": it["gold_answer"],
            "expected_intent": it["expected_intent"],
            "category": it.get("category"),
            "subtype": it.get("subtype"),
            "volatility": it.get("volatility"),
            "source_url": it.get("source_url"),
            "response": it.get("response", {}),
        } for it in data[i:i + args.size]]
        json.dump(batch, open(os.path.join(out_dir, f"batch_{bid}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        ids.append(bid)

    print(f"{len(data)} items -> {len(ids)} batches of {args.size} in {out_dir}")
    print(json.dumps(ids))


if __name__ == "__main__":
    main()
