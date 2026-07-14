"""
Advanced NLU Engine
Entity extraction, context tracking, intent resolution with confidence boosting
"""

import re
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import numpy as np


class EntityExtractor:
    """Extract entities (dates, programs, fees, etc.) from user input"""

    # Entity patterns
    ENTITY_PATTERNS = {
        "date": [
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
            r"\b(\d{1,2}/\d{1,2}(/\d{2,4})?\b)",
            r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(next\s+semester|this\s+semester|today|tomorrow|next\s+week)\b",
        ],
        "program": [
            r"\b(bs\s+computer\s+science|bscs|bs\s+information\s+technology|bsit|bachelor\s+of|degree)\b",
            r"\b(engineering|education|agriculture|business|hospitality|veterinary)\b",
            r"\b(computer\s+science|information\s+technology|it|cs)\b",
        ],
        "fee": [
            r"\b(tuition|fee|cost|price|payment|amount|peso|php|₱|currency)\b",
            r"\b(how\s+much|how\s+expensive|cost\s+how)\b",
        ],
        "document": [
            r"\b(form\s+138|transcript|diploma|tor|certificate|credential)\b",
            r"\b(document|paper|requirement|file)\b",
        ],
        "time": [
            r"\b(\d{1,2}:\d{2}|morning|afternoon|evening|noon|midnight)\b",
            r"\b(semester|year|month|week|day|hour)\b",
        ],
        "facility": [
            r"\b(library|gym|dormitory|canteen|cafeteria|clinic|chapel)\b",
            r"\b(facility|building|room|office|campus)\b",
        ],
        "contact": [
            r"\b(phone|call|email|address|contact|reach|call|telephone)\b",
            r"\b(hotline|number|number|extension)\b",
        ],
    }

    @classmethod
    def extract(cls, text: str) -> Dict[str, List[str]]:
        """Extract entities from text"""
        entities = defaultdict(list)
        text_lower = text.lower()

        for entity_type, patterns in cls.ENTITY_PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text_lower)
                if matches:
                    # Handle both single strings and tuples from regex groups
                    for match in matches:
                        entity_value = match[0] if isinstance(match, tuple) else match
                        if entity_value not in entities[entity_type]:
                            entities[entity_type].append(entity_value)

        return dict(entities)


class ConversationContext:
    """Track conversation history and context"""

    def __init__(self, max_history: int = 10):
        self.history: List[Dict] = []
        self.max_history = max_history
        self.intent_frequency = defaultdict(int)
        self.last_intent = None
        self.user_focus = None

    def add_turn(self, user_message: str, intent: str, confidence: float, entities: Dict):
        """Add a turn to conversation history"""
        turn = {
            "user_message": user_message,
            "intent": intent,
            "confidence": confidence,
            "entities": entities,
        }

        self.history.append(turn)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        # Update tracking
        if intent != "nlu_fallback":
            self.intent_frequency[intent] += 1
            self.last_intent = intent
            # Infer user focus from repeated intents
            if self.intent_frequency[intent] >= 2:
                self.user_focus = intent

    def get_context_boost(self, current_intent: str, confidence: float) -> float:
        """Boost confidence based on conversation context"""
        boost = 1.0

        # If same intent is repeated, boost confidence
        if current_intent == self.last_intent and len(self.history) > 0:
            boost += 0.15  # +15% boost

        # If user is focused on a topic, boost related intents
        if self.user_focus == current_intent:
            boost += 0.20  # +20% boost

        # If confidence is borderline, context can help
        if 0.45 < confidence < 0.65:
            boost += 0.10  # +10% boost for uncertain cases

        return min(confidence * boost, 1.0)  # Cap at 100%

    def is_follow_up(self) -> bool:
        """Check if this is likely a follow-up question"""
        return len(self.history) > 0

    def get_last_entities(self) -> Dict[str, List[str]]:
        """Get entities from last user message"""
        if self.history:
            return self.history[-1].get("entities", {})
        return {}

    def clear(self):
        """Clear conversation history"""
        self.history.clear()
        self.intent_frequency.clear()
        self.last_intent = None
        self.user_focus = None


class IntentConfidenceBooster:
    """Boost confidence using multiple strategies"""

    # Keyword confidence multipliers
    KEYWORD_BOOSTERS = {
        "admissions_requirements": ["requirements", "admission", "apply", "need", "document"],
        "tuition_fees": ["tuition", "fee", "cost", "price", "pay", "expensive"],
        "courses_offered": ["course", "program", "study", "major", "offered"],
        "contact_info": ["contact", "phone", "email", "call", "reach"],
        "campus_location": ["where", "location", "address", "campus", "get to"],
        "enrollment_procedure": ["enroll", "register", "procedure", "process", "how"],
        "events": ["event", "activity", "happening", "sportsfest", "celebration"],
        "scholarship": ["scholarship", "financial", "aid", "grant", "free"],
    }

    @classmethod
    def boost_confidence(
        cls,
        text: str,
        predicted_intent: str,
        confidence: float,
        context: Optional[ConversationContext] = None,
    ) -> Tuple[str, float]:
        """Adjust confidence — downward only, never inflated.

        The classifier's calibrated probability is the ceiling. Earlier
        versions inflated confidence on keyword hits, question marks, and
        conversation stickiness (same-intent / user-focus boosts). That pushed
        borderline and off-topic queries past the tier gate at a fake-high
        confidence — e.g. a courses-themed chat inflated an unrelated
        "available strands in senior high" question from 0.63 to 1.0 and served
        the wrong intent. Topic momentum must not override what the current
        message actually says, so the only adjustment we keep is the negation
        reducer. (`context` is retained for signature stability and is still
        tracked by enhance_prediction for entities/history.)
        """
        text_lower = text.lower()
        factor = 1.0

        # Negation detection: the sole confidence adjustment, and it only lowers.
        if any(word in text_lower for word in ["not", "don't", "doesn't", "won't"]):
            factor *= 0.85  # -15% if negation detected

        return predicted_intent, confidence * factor

    @classmethod
    def ensemble_predict(
        cls,
        predictions: List[Tuple[str, float]],
    ) -> Tuple[str, float]:
        """Combine multiple predictions using ensemble method"""

        if not predictions:
            return "nlu_fallback", 0.0

        # Weight by confidence
        total_weight = sum(conf for _, conf in predictions)
        if total_weight == 0:
            return "nlu_fallback", 0.0

        # Calculate weighted average confidence per intent
        intent_scores = defaultdict(float)
        intent_counts = defaultdict(int)

        for intent, conf in predictions:
            intent_scores[intent] += conf
            intent_counts[intent] += 1

        # Find best intent
        best_intent = max(intent_scores, key=intent_scores.get)
        avg_confidence = intent_scores[best_intent] / intent_counts[best_intent]

        return best_intent, avg_confidence


class AdvancedNLUEngine:
    """Complete NLU engine with all enhancements"""

    def __init__(self):
        self.entity_extractor = EntityExtractor()
        self.confidence_booster = IntentConfidenceBooster()
        self.contexts = {}  # user_id -> ConversationContext

    def get_context(self, user_id: str) -> ConversationContext:
        """Get or create user context"""
        if user_id not in self.contexts:
            self.contexts[user_id] = ConversationContext()
        return self.contexts[user_id]

    def enhance_prediction(
        self,
        text: str,
        predicted_intent: str,
        confidence: float,
        user_id: Optional[str] = None,
    ) -> Dict:
        """Enhance a prediction with NLU enhancements"""

        # Extract entities
        entities = self.entity_extractor.extract(text)

        # Get context if available
        context = self.get_context(user_id) if user_id else None

        # Boost confidence
        boosted_intent, boosted_confidence = self.confidence_booster.boost_confidence(
            text, predicted_intent, confidence, context
        )

        # Update context
        if context:
            context.add_turn(text, boosted_intent, boosted_confidence, entities)

        return {
            "intent": boosted_intent,
            "confidence": boosted_confidence,
            "entities": entities,
            "original_confidence": confidence,
            "confidence_boost": boosted_confidence - confidence,
            "is_follow_up": context.is_follow_up() if context else False,
            "context_entities": context.get_last_entities() if context else {},
        }

    def extract_slots(self, text: str, intent: str) -> Dict:
        """Extract slots relevant to the intent"""

        entities = self.entity_extractor.extract(text)
        slots = {}

        # Map entities to intent-specific slots
        intent_slots = {
            "admissions_requirements": ["date", "program", "document"],
            "tuition_fees": ["program", "fee", "time"],
            "courses_offered": ["program"],
            "enrollment_procedure": ["date", "program"],
            "events": ["date", "time", "facility"],
            "contact_info": ["contact"],
            "campus_location": ["facility"],
        }

        if intent in intent_slots:
            for slot_type in intent_slots[intent]:
                if slot_type in entities:
                    slots[slot_type] = entities[slot_type]

        return slots

    def resolve_ambiguity(
        self,
        text: str,
        similar_intents: List[Tuple[str, float]],
        user_id: Optional[str] = None,
    ) -> Tuple[str, float]:
        """Resolve ambiguous intent prediction using context and entities"""

        context = self.get_context(user_id) if user_id else None

        # If we have context and repeated intent, prefer it
        if context and context.last_intent:
            for intent, conf in similar_intents:
                if intent == context.last_intent:
                    return intent, min(conf + 0.25, 1.0)

        # Use entity hints
        entities = self.entity_extractor.extract(text)
        if entities:
            # Score intents by entity relevance
            intent_entity_scores = defaultdict(float)
            for intent, conf in similar_intents:
                # Count matching entities for this intent
                entity_count = len(entities)
                if entity_count > 0:
                    intent_entity_scores[intent] = conf * (1 + 0.1 * entity_count)

            if intent_entity_scores:
                best_intent = max(intent_entity_scores, key=intent_entity_scores.get)
                return best_intent, min(intent_entity_scores[best_intent], 1.0)

        # Default to highest confidence
        if similar_intents:
            return similar_intents[0]

        return "nlu_fallback", 0.0

    def clear_context(self, user_id: str):
        """Clear user context (e.g., on new session)"""
        if user_id in self.contexts:
            self.contexts[user_id].clear()
