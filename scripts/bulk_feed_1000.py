"""
Bulk feeder: send 1000 varied prompts to the running API and report model split.
"""
import random
import time
from collections import Counter

import requests

URL = "http://127.0.0.1:8000/chat"
N = 1000
USER_ID = f"bulk1k-{int(time.time())}"

random.seed(42)

# Templates grouped by intent so we can generate variety
TEMPLATES = {
    "greeting":           ["hello", "hi", "hi sevi", "hey", "good morning", "good afternoon",
                            "good evening", "kumusta", "kamusta po", "magandang umaga",
                            "hello po", "hi there", "yo", "magandang hapon"],
    "thanks":             ["thank you", "thanks", "salamat", "salamat po", "maraming salamat",
                            "thank you po", "thanks a lot", "appreciate it"],
    "goodbye":            ["goodbye", "bye", "see you", "see you later", "paalam",
                            "ingat", "bye sevi", "until next time"],
    "about_cvsu":         ["what is CvSU", "tell me about Cavite State University",
                            "describe CvSU", "info about the university",
                            "ano po ang CvSU", "history of CvSU", "background of the university",
                            "what kind of school is CvSU"],
    "admissions":         ["how do I apply", "admission requirements", "how to apply for admission",
                            "what are the requirements to enroll", "paano mag apply",
                            "I want to apply", "application process", "freshmen requirements",
                            "transferee requirements", "I want to transfer", "transfer student",
                            "paano lumipat ng school", "graduate student admissions"],
    "exam":               ["when is the admission exam", "what is the admission test",
                            "when is CvSUAT", "schedule of admission exam",
                            "kailan ang exam", "exam date", "entrance exam",
                            "qualifying exam schedule"],
    "tuition":            ["how much is tuition", "tuition fees", "magkano po tuition",
                            "magkano ang bayad", "bayad sa CvSU", "what are the fees",
                            "is CvSU free", "free tuition", "RA 10931 free higher education",
                            "do I need to pay", "miscellaneous fees"],
    "scholarship":        ["scholarships available", "are there scholarships",
                            "list of scholarships", "scholarship programs",
                            "paano mag apply ng scholarship", "financial aid",
                            "academic scholarship", "iskolar"],
    "courses":            ["list of courses", "courses offered", "available programs",
                            "what programs do you have", "ano ang mga kurso",
                            "anu-anong courses", "degree programs", "majors offered",
                            "BS Information Technology", "BS Computer Science",
                            "BS Agriculture", "BS Education", "BS Nursing"],
    "campus_specific":    ["CvSU Imus", "CvSU Naic", "CvSU Silang", "CvSU Rosario",
                            "CvSU Trece Martires", "CvSU Tanza", "CvSU Carmona",
                            "CvSU Bacoor", "CvSU General Trias", "CvSU Cavite City",
                            "main campus", "Indang campus", "what programs in Naic",
                            "programs in Imus campus", "satellite campuses"],
    "location":           ["where is CvSU", "campus address", "location of main campus",
                            "saan ang CvSU", "directions to CvSU", "how to get to CvSU",
                            "nearest campus to me"],
    "registrar":          ["how do I contact the registrar", "registrar office",
                            "office hours", "request transcript", "request records",
                            "TOR request", "good moral certificate"],
    "enrollment":         ["how to enroll", "online enrollment", "enrollment procedure",
                            "kailan ang enrollment", "late enrollment", "drop subject",
                            "add subject", "shifting course"],
    "facilities":         ["library hours", "gym facilities", "dormitory", "clinic",
                            "wifi access", "canteen", "computer lab"],
    "edge":               ["asdfgh", "qwerty", "12345", "...", "???",
                            "hahahaha", "wtf", "nothing", "test test test",
                            "i forgot my student ID", "I lost my ID",
                            "I need help with my account", "my grade is wrong",
                            "complaint", "I want to speak to the dean"],
}

# Build flat list and pad
all_prompts = []
for variants in TEMPLATES.values():
    all_prompts.extend(variants)

# Generate N prompts by sampling with light noise (typos/casing)
def jitter(s: str) -> str:
    r = random.random()
    if r < 0.15:
        return s.lower()
    if r < 0.25:
        return s.upper()
    if r < 0.35:
        return s + "?"
    if r < 0.45:
        return s + " po"
    if r < 0.50 and len(s) > 4:
        i = random.randint(1, len(s)-2)
        return s[:i] + s[i+1:]  # drop one char (typo)
    return s

prompts = [jitter(random.choice(all_prompts)) for _ in range(N)]

print(f"Feeding {N} prompts to {URL}")
print(f"user_id = {USER_ID}")
start = time.time()

errors = 0
intent_counts = Counter()
latencies = []

for i, p in enumerate(prompts, 1):
    t0 = time.time()
    try:
        r = requests.post(URL, json={"message": p, "user_id": USER_ID, "session_id": USER_ID}, timeout=20)
        d = r.json()
        intent_counts[d.get("intent", "ERROR")] += 1
        latencies.append((time.time() - t0) * 1000)
    except Exception as e:
        errors += 1
        if errors <= 3:
            print(f"  ERROR #{errors}: {e}")
    if i % 100 == 0:
        elapsed = time.time() - start
        print(f"  {i:4d}/{N}  elapsed={elapsed:5.1f}s  rate={i/elapsed:5.1f} msg/s")

elapsed = time.time() - start
print(f"\nDone in {elapsed:.1f}s. Errors: {errors}")
print(f"Avg latency: {sum(latencies)/len(latencies):.1f}ms  p95: {sorted(latencies)[int(0.95*len(latencies))]:.1f}ms")
print(f"\nTop intents:")
for intent, n in intent_counts.most_common(10):
    print(f"  {intent:30s} {n:4d}")
