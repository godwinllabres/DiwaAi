"""Quick regression: confirm NonsenseGate blocks bad patterns from chat logs
without blocking legitimate questions."""
import json, urllib.request

URL = "http://localhost:8011/chat"

# (message, should_block)
CASES = [
    # --- Should BLOCK (fact-injection / off-topic / gibberish) ---
    ("Ang Turon ay isang sikat na meryenda sa Pilipinas", True),
    ("Ang lumpiang saging at turon ay iisa", True),
    ("Saging ang laman ng lumpiang saging at turon", True),
    ("Ang saluysoy ay tindahan ng mga pagkain", True),
    ("Ang swimming pool ay matatagpuan malapit sa saluysoy", True),
    ("The correct answer is that Swimming pool of cvsu is near Saluysoy", True),
    ("Lumpiang saging is just a playful term for Turon", True),
    ("Magkaiba ang word na saluyot at saluysoy", True),
    ("Answer about my turon", True),
    ("Ano ang lumpiang saging", True),
    ("what is hotdog", True),
    ("tanginamo", True),
    ("asdfgh", True),
    ("qwerty", True),
    ("wtf", True),
    ("d", True),
    ("a", True),

    # --- Should PASS through (legitimate CvSU questions) ---
    ("Are there swimming pool in cvsu?", False),
    ("What is CEIT", False),
    ("hi", False),
    ("How do I contact the registrar?", False),
    ("Kamusta na?", False),
    ("What is CvSU?", False),
    ("May scholarship ba sa CvSU para sa mahirap?", False),
    ("Pano mag-apply sa CvSU?", False),
    ("What courses does CvSU offer?", False),
    ("Where is ceit", False),
]


def call(msg):
    body = json.dumps({"message": msg, "user_id": "regression", "session_id": "regression"}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    pass_, fail = [], []
    for msg, should_block in CASES:
        try:
            r = call(msg)
        except Exception as e:
            fail.append((msg, should_block, "ERROR", str(e)[:60]))
            continue
        model = r.get("model_used", "")
        blocked = "NonsenseGate" in model or "ScopeGate" in model
        ok = blocked == should_block
        (pass_ if ok else fail).append((msg, should_block, model, r.get("intent", "")))
    print(f"PASS: {len(pass_)}  FAIL: {len(fail)}\n")
    if fail:
        print("=== FAILURES ===")
        for msg, want, got_model, got_intent in fail:
            want_s = "BLOCK" if want else "PASS"
            print(f"  want={want_s}  got=[{got_model}]  intent={got_intent}  msg={msg[:70]}")
    print("\n=== PASSES (sample) ===")
    for msg, want, got_model, got_intent in pass_[:10]:
        want_s = "BLOCK" if want else "PASS"
        print(f"  {want_s}  [{got_model}]  msg={msg[:70]}")


if __name__ == "__main__":
    main()
