"""
Automatically expand intents.json with more patterns
Generates variations of existing patterns to improve training data
"""

import json
import re

# Pattern variations generator
PATTERN_VARIATIONS = {
    "admission": [
        "admission requirements",
        "admission process",
        "admission information",
        "how to get admitted",
        "how to apply",
        "application process",
        "application requirements",
        "enrollment requirements",
        "how to enroll",
        "enrollment process",
        "enrollment information",
    ],
    "exam": [
        "entrance exam",
        "entrance examination",
        "entrance test",
        "admission exam",
        "admission test",
        "entrance exam schedule",
        "when is the exam",
        "exam registration",
        "exam information",
        "exam details",
    ],
    "course": [
        "courses",
        "course offerings",
        "available courses",
        "programs",
        "degree programs",
        "educational programs",
        "academic programs",
        "what to study",
        "major offerings",
        "specializations",
    ],
    "tuition": [
        "tuition",
        "tuition fees",
        "school fees",
        "enrollment fee",
        "educational cost",
        "cost",
        "price",
        "expenses",
        "fee structure",
        "payment",
    ],
    "scholarship": [
        "scholarship",
        "scholarships",
        "financial assistance",
        "financial aid",
        "grants",
        "aid",
        "subsidy",
        "free tuition",
        "sponsor",
        "assistance program",
    ],
    "contact": [
        "contact",
        "call",
        "phone",
        "email",
        "reach",
        "connect",
        "communicate",
        "get in touch",
        "information",
        "help",
    ],
    "location": [
        "location",
        "address",
        "where",
        "campus",
        "situated",
        "found",
        "based",
        "headquarters",
        "main office",
        "direction",
    ],
    "facility": [
        "facility",
        "facilities",
        "amenities",
        "resources",
        "equipment",
        "infrastructure",
        "services",
        "available resources",
        "campus resources",
        "student services",
    ],
    "library": [
        "library",
        "library services",
        "library resources",
        "books",
        "research materials",
        "online database",
        "e-books",
        "digital resources",
        "collection",
        "reading materials",
    ],
    "event": [
        "event",
        "events",
        "activities",
        "program",
        "gathering",
        "celebration",
        "conference",
        "seminar",
        "workshop",
        "meeting",
    ],
    "calendar": [
        "calendar",
        "schedule",
        "dates",
        "timeline",
        "academic dates",
        "semester dates",
        "holidays",
        "vacation",
        "break",
        "term dates",
    ],
}

def expand_intents_json(input_file="data/cavsu_intents.json", output_file="data/cavsu_intents_expanded.json"):
    """Expand intents with more pattern variations"""

    print("=" * 70)
    print("  INTENT EXPANSION TOOL")
    print("=" * 70)

    # Load existing intents
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    intents = data["intents"]
    print(f"\n[1/3] Loaded {len(intents)} intents")

    # Generate variations for each intent
    print(f"[2/3] Generating pattern variations...")

    for intent in intents:
        tag = intent["tag"]
        original_patterns = intent["patterns"]

        # Skip if already has many patterns
        if len(original_patterns) > 15:
            continue

        # Generate new variations based on tag keywords
        new_patterns = set(original_patterns)

        # Add variations for specific intents
        if "admission" in tag:
            new_variations = [
                "What are the admission requirements?",
                "How can I apply to CvSU?",
                "Admission process details",
                "CvSU admission information",
                "What do I need to apply?",
                "Requirements to attend CvSU",
            ]
            new_patterns.update(new_variations)

        elif "exam" in tag or "test" in tag:
            new_variations = [
                "When is the entrance exam?",
                "Entrance examination schedule",
                "How to register for the exam?",
                "What about the entrance test?",
                "Exam registration details",
            ]
            new_patterns.update(new_variations)

        elif "course" in tag or "program" in tag:
            new_variations = [
                "What degree programs are available?",
                "What can I major in?",
                "Available majors and specializations",
                "What are the available programs?",
                "Program and course list",
            ]
            new_patterns.update(new_variations)

        elif "tuition" in tag or "fee" in tag:
            new_variations = [
                "What is the cost of studying?",
                "Tuition fee information",
                "Do I have to pay for tuition?",
                "What are the school expenses?",
                "Fee structure details",
            ]
            new_patterns.update(new_variations)

        elif "scholarship" in tag or "financial" in tag:
            new_variations = [
                "What scholarships are available?",
                "How can I get financial aid?",
                "Scholarship application process",
                "Are there grants available?",
                "Free tuition options",
            ]
            new_patterns.update(new_variations)

        elif "contact" in tag:
            new_variations = [
                "How do I contact CvSU?",
                "What is the phone number?",
                "How can I reach the admissions office?",
                "Where do I call for information?",
                "CvSU contact details",
            ]
            new_patterns.update(new_variations)

        elif "location" in tag or "campus" in tag:
            new_variations = [
                "Where is CvSU located?",
                "How do I get to the campus?",
                "What is the campus address?",
                "Where can I find CvSU?",
                "Campus location and directions",
            ]
            new_patterns.update(new_variations)

        elif "facility" in tag or "amenity" in tag:
            new_variations = [
                "What facilities are available?",
                "What amenities does CvSU have?",
                "Campus resources and services",
                "What is available on campus?",
                "Student facilities overview",
            ]
            new_patterns.update(new_variations)

        elif "library" in tag:
            new_variations = [
                "What are library hours?",
                "How do I access the library?",
                "Library services available",
                "Online library resources",
                "What is in the library?",
            ]
            new_patterns.update(new_variations)

        elif "event" in tag or "activity" in tag:
            new_variations = [
                "What events are happening?",
                "Upcoming activities at CvSU",
                "What student activities are there?",
                "Event schedule",
                "What can students participate in?",
            ]
            new_patterns.update(new_variations)

        elif "calendar" in tag or "schedule" in tag:
            new_variations = [
                "What is the academic calendar?",
                "When does the semester start?",
                "Academic year schedule",
                "Important dates and deadlines",
                "School calendar information",
            ]
            new_patterns.update(new_variations)

        elif "greeting" in tag:
            new_variations = [
                "Hi there", "Good day", "Hellos",
                "What's happening", "How goes it", "Yo",
            ]
            new_patterns.update(new_variations)

        # Update the intent with expanded patterns
        intent["patterns"] = list(new_patterns)

        pattern_count = len(intent["patterns"])
        print(f"  {tag:<30} {len(original_patterns):>2} -> {pattern_count:>2} patterns")

    # Save expanded intents
    print(f"\n[3/3] Saving expanded intents...")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total_patterns = sum(len(intent["patterns"]) for intent in intents)

    print("\n" + "=" * 70)
    print("[SUCCESS] EXPANSION COMPLETE")
    print("=" * 70)
    print(f"\nTotal patterns: {total_patterns}")
    print(f"Output file: {output_file}")
    print("\nNext steps:")
    print("  1. mv data/cavsu_intents_expanded.json data/cavsu_intents.json")
    print("  2. python train_naive_bayes.py")
    print("  3. Restart API: python -m uvicorn api.app:app --port 8000")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    expand_intents_json()

