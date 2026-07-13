"""Regenerate models/trusted_hashes.json after a legitimate retrain.

hybrid_chatbot.verify_artifact() refuses to load a model whose SHA-256 doesn't
match the pinned value here, so run this whenever the committed artifacts
change (train_naive_bayes.py / train_hybrid.py):

    python scripts/update_trusted_hashes.py

Then review the diff and commit models/trusted_hashes.json alongside the new
model files.
"""

import hashlib
import json
import os

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
MANIFEST = os.path.join(MODELS_DIR, "trusted_hashes.json")

# The artifacts loaded via pickle/joblib/keras (code-executing formats).
ARTIFACTS = [
    "nn_tokenizer.pkl",
    "nn_label_encoder.pkl",
    "CvSU_classifier.pkl",
    "nn_model.h5",
]


def sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main() -> None:
    artifacts = {}
    for name in ARTIFACTS:
        path = os.path.join(MODELS_DIR, name)
        if os.path.exists(path):
            artifacts[name] = sha256(path)
            print(f"  {name}: {artifacts[name]}")
        else:
            print(f"  [skip] {name} not found")

    doc = {
        "_comment": (
            "SHA-256 integrity anchors for model artifacts. hybrid_chatbot loads these "
            "via pickle/joblib/keras, which execute code on load, so a hash mismatch is "
            "treated as tampering and blocks the load (override with "
            "SEVI_ALLOW_UNVERIFIED_MODELS=1). Regenerate after a legitimate retrain: "
            "python scripts/update_trusted_hashes.py"
        ),
        "artifacts": artifacts,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"\nWrote {len(artifacts)} hashes to {os.path.relpath(MANIFEST)}")


if __name__ == "__main__":
    main()
