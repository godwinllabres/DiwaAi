"""Offline regression for the agentic workflow tier (POC) — run:
    python test_agentic_workflow.py

Drives api/workflows/ directly (no app import / no models). Covers the
kill-switch, the 2-step advising flow, bad-input re-prompt, cancel, conversation
isolation, and TTL expiry. See docs/agentic_workflows_poc.md.
"""
import asyncio

from api import workflows
from api.workflows import base, state_manager

failures = 0


def check(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


def run(key, msg):
    return asyncio.run(workflows.dispatch(key, msg))


def reset():
    state_manager._active.clear()


# --- kill-switch: disabled tier is fully inert ---
base.ENABLED = False
reset()
check("disabled -> trigger ignored (None)", run("s1", "book an advising appointment") is None)

# Everything below runs with the tier enabled.
base.ENABLED = True

# --- non-trigger falls through to the normal pipeline ---
reset()
check("unrelated message -> None (pipeline continues)", run("s1", "what are the library hours?") is None)

# --- start: a booking-shaped ask opens the workflow ---
reset()
t = run("s1", "I want to book an advising appointment")
check("trigger -> workflow starts", t is not None)
check("start asks for student number", t and "student number" in t.text.lower())
check("start is not terminal", t and not t.done)
check("state now active at step 1", (st := state_manager.get_state("s1")) and st.step == 1)

# --- step 1: bad input re-prompts, does not advance ---
t = run("s1", "uhh I don't know")
check("bad id -> re-prompt", t and "9-digit" in t.text)
check("still at step 1", state_manager.get_state("s1").step == 1)

# --- step 1: a valid 9-digit id advances to the date step ---
t = run("s1", "my number is 202012345")
check("valid id -> asks for date", t and "date" in t.text.lower())
check("advanced to step 2", state_manager.get_state("s1").step == 2)
check("id captured", state_manager.get_state("s1").collected.get("student_id") == "202012345")

# --- step 2: date triggers the (mock) tool and finishes ---
t = run("s1", "October 12")
check("date -> booking confirmed", t and "advising request" in t.text.lower())
check("confirmation ref present", t and "ADV-" in t.text)
check("honest mock disclosure", t and "proof-of-concept" in t.text.lower())
check("terminal turn", t and t.done)
check("state cleared after finish", state_manager.get_state("s1") is None)

# --- cancel releases an active workflow at any step ---
reset()
run("s2", "book advising")
t = run("s2", "nevermind")
check("cancel -> released", t and "cancelled" in t.text.lower() and t.done)
check("state cleared on cancel", state_manager.get_state("s2") is None)
# 'cancel' with nothing active is NOT a workflow turn
reset()
check("bare cancel (no active wf) -> None", run("s2", "cancel") is None)

# --- conversations are isolated ---
reset()
run("a", "book advising")
run("b", "schedule an advising consultation")
run("a", "111222333")
check("session A at step 2", state_manager.get_state("a").step == 2)
check("session B still at step 1", state_manager.get_state("b").step == 1)
check("A and B hold different ids", state_manager.get_state("a").collected.get("student_id") == "111222333")

# --- TTL: an abandoned workflow expires ---
reset()
run("s3", "book advising")
st = state_manager._active["s3"]
st.updated_at -= (state_manager._TTL_SECONDS + 1)   # age it past the window
check("expired state reads as None", state_manager.get_state("s3") is None)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
