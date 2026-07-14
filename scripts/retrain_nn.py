"""Retrain the Neural Network using the api/ trainer (VOCAB=1000, MAX_LEN=20,
DIM=64) so the artifact stays compatible with the LIVE api/hybrid_chatbot loader.
train_hybrid.py imports the root duplicate (3000/30/256) and would produce an
incompatible model — do NOT use it.
"""
import os
import sys

os.environ.setdefault("SEVI_ALLOW_UNVERIFIED_MODELS", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from api.hybrid_chatbot import NeuralNetworkTrainer  # noqa: E402

NeuralNetworkTrainer.train("data/cavsu_intents.json", "models")
print("NN_RETRAIN_DONE")
