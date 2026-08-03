"""
Train Naive Bayes classifier for CvSU Chatbot
Updated with expanded training patterns
"""

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, accuracy_score
import joblib

from intents_db import load_intents, inject_system_responses

import nltk
from nltk.stem import WordNetLemmatizer

# Ensure NLTK resources are available (idempotent — no-op if already present)
for resource, kind in [('punkt_tab', 'tokenizers'), ('wordnet', 'corpora')]:
    try:
        nltk.data.find(f'{kind}/{resource}')
    except (LookupError, OSError):
        nltk.download(resource, quiet=True)

lemmatizer = WordNetLemmatizer()

def preprocess_text(text):
    """Preprocess text: lowercase, remove punctuation, tokenize, lemmatize"""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    tokens = nltk.word_tokenize(text)
    return " ".join([lemmatizer.lemmatize(t) for t in tokens])

def train_naive_bayes():
    """Train Naive Bayes classifier on intents"""
    print("=" * 70)
    print("  NAIVE BAYES CLASSIFIER TRAINING")
    print("=" * 70)

    # Load intents from SQLite DB if available, otherwise fallback to JSON
    print("\n[1/4] Loading intents...")
    intents = load_intents()

    training_patterns = []
    training_labels = []
    responses_map = defaultdict(list)

    for intent in intents:
        tag = intent["tag"]
        responses_map[tag] = intent["responses"]
        for pattern in intent["patterns"]:
            training_patterns.append(pattern)
            training_labels.append(tag)

    print(f"[OK] Loaded {len(intents)} intent categories")
    print(f"     Total training patterns: {len(training_patterns)}")
    print("\n     Intent breakdown:")
    for intent in intents:
        count = len(intent["patterns"])
        print(f"       {intent['tag']:<30} {count:>3} patterns")

    # Preprocess patterns
    print("\n[2/4] Preprocessing {0} patterns...".format(len(training_patterns)))
    preprocessed_patterns = [preprocess_text(p) for p in training_patterns]
    print("[OK] Preprocessing complete")

    # Train model
    print("\n[3/4] Training Naive Bayes classifier...")
    pipeline = Pipeline([
        ('tfidf', TfidfVectorizer(
            max_features=5000,
            lowercase=True,
            stop_words='english',
            ngram_range=(1, 2),
        )),
        ('classifier', MultinomialNB(alpha=0.1)),
    ], memory=None)

    pipeline.fit(preprocessed_patterns, training_labels)
    print("[OK] Model trained")

    # Evaluate
    print("\n[4/4] Evaluating model...")
    y_pred = pipeline.predict(preprocessed_patterns)
    accuracy = accuracy_score(training_labels, y_pred)
    print(f"[OK] Training Accuracy: {accuracy:.2%}")

    print("\nDetailed Classification Report:")
    print(classification_report(training_labels, y_pred))

    # Save model and responses
    print("\nSaving artifacts...")
    os.makedirs("models", exist_ok=True)

    joblib.dump(pipeline, "models/CvSU_classifier.pkl")
    inject_system_responses(responses_map)
    with open("models/responses_map.json", "w", encoding="utf-8") as f:
        json.dump(dict(responses_map), f, ensure_ascii=False, indent=2)

    model_size = os.path.getsize("models/CvSU_classifier.pkl") / 1024
    print("[OK] Model saved to models/")
    print(f"     CvSU_classifier.pkl ({model_size:.1f} KB)")
    print("     responses_map.json")
    print("\n[!] The API's integrity gate pins this artifact's hash and will")
    print("    refuse the new classifier until you re-pin it:")
    print("        python scripts/update_trusted_hashes.py")

    print("\n" + "=" * 70)
    print("  TRAINING COMPLETE")
    print("=" * 70)
    print("\nFinal Model Performance:")
    print(f"  Accuracy: {accuracy:.2%}")
    print(f"  Patterns: {len(training_patterns)}")
    print(f"  Intents:  {len(intents)}")
    print("\nThe API will automatically use this trained model.")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    train_naive_bayes()

