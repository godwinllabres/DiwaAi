#!/usr/bin/env python3
"""
Training script for Hybrid Chatbot
Trains both Naive Bayes (already exists) and Neural Network
"""

import os
import sys
from hybrid_chatbot import NeuralNetworkTrainer, HybridChatbot

def main():
    """Train the hybrid model"""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  CvSU HYBRID CHATBOT TRAINING".center(58) + "║")
    print("║" + "  Naive Bayes + Neural Network".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "═" * 58 + "╝")
    print()

    intents_path = "data/cavsu_intents.json"
    model_dir = "models"

    # Check if intents file exists
    if not os.path.exists(intents_path):
        print(f"✗ Error: {intents_path} not found")
        sys.exit(1)

    print(f"Intents file: {intents_path}")
    print(f"Models directory: {model_dir}")
    print()

    # Train Neural Network
    print("Step 1: Training Neural Network")
    print("-" * 60)
    try:
        model, tokenizer, label_encoder = NeuralNetworkTrainer.train(
            intents_path, model_dir
        )
        print("✓ Neural Network trained successfully!")
    except Exception as e:
        print(f"✗ Error training neural network: {e}")
        sys.exit(1)

    # Test Hybrid Chatbot
    print("\n" + "=" * 60)
    print("Step 2: Testing Hybrid Chatbot")
    print("=" * 60)

    try:
        hybrid = HybridChatbot(model_dir, os.path.join(model_dir, "responses_map.json"))

        # Test queries
        test_queries = [
            "What are the admission requirements?",
            "Does CvSU have Computer Science?",
            "How much is tuition?",
            "Are there scholarships?",
            "Tell me about CvSU",
            "When is enrollment?",
            "What facilities does CvSU have?",
        ]

        print("\nTesting with sample queries:\n")
        print(f"{'Query':<40} {'Model':<15} {'Confidence':>10}")
        print("-" * 65)

        for query in test_queries:
            intent, response, confidence, model = hybrid.chat(query, user_id="test_user")
            print(
                f"{query[:40]:<40} {model:<15} {confidence:>9.1%}"
            )

        # Print statistics
        print("\n" + "=" * 60)
        print("Model Usage Statistics")
        print("=" * 60)

        stats = hybrid.get_usage_stats()
        print(f"\nTotal Predictions: {stats['total_predictions']}")
        print(f"  Naive Bayes:     {stats['naive_bayes_used']:3d} ({stats['naive_bayes_percentage']:5.1f}%)")
        print(f"  Neural Network:  {stats['neural_network_used']:3d} ({stats['neural_network_percentage']:5.1f}%)")
        print(f"  Fallback:        {stats['fallback_used']:3d} ({stats['fallback_percentage']:5.1f}%)")

        print("\n" + "=" * 60)
        print("✓ Hybrid Chatbot Training Complete!")
        print("=" * 60)
        print("\nTo use the hybrid chatbot:")
        print("  1. Update app.py to use HybridChatbot")
        print("  2. Start API: python -m uvicorn api.app:app --port 8000")
        print("  3. Chatbot will automatically use both models hierarchically")
        print()

    except Exception as e:
        print(f"✗ Error testing hybrid chatbot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

