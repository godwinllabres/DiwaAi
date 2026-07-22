# Agentic workflows — Tier 5.5 (POC)

Sevi's pipeline is otherwise stateless: each `/chat` turn is classified and
answered independently. A **workflow** is a stateful, multi-step conversation
that ends by *executing an action* through a tool — the first step toward an
agentic Sevi. The POC ships one workflow: **book an advising appointment**.

> **Status: proof of concept, OFF by default.** The tool is a MOCK (no real
> write). The whole tier is gated by `AGENTIC_WORKFLOWS_ENABLED` (unset = the
> pipeline behaves exactly as before). Do **not** enable in production until the
> security requirements below are met.

## Where it sits in the pipeline

```
/chat → Tier 1 Safety → [Tier 5.5 Workflows] → Campus → AIS/Connectors → NB/NN → RAG → LLM
```

It runs **after** safety (an abusive or self-harm message mid-workflow is still
caught first) and **before** everything else: an active workflow owns the turn,
and a trigger phrase starts one. Integration is a single bridge call in
`api/app.py` `chat_endpoint`:

```python
wf_key = request.session_id or request.user_id
wf_turn = await _workflows.dispatch(wf_key, request.message)
if wf_turn is not None:
    return _short_circuit_response(
        request, original_message, start_time,
        text=wf_turn.text, intent="action_book_advising",
        source=ResponseSource.WORKFLOW, model_used="Workflow (Tier 5.5)",
        suggestions=wf_turn.suggestions,
    )
```

Replies flow through the existing `_short_circuit_response`, so every workflow
turn is logged like any other — and the log copy is **PII-masked** by
`api/pii.py`, so a typed student number never lands in `chat_history.db`.

## Package layout (`api/workflows/`)

| File | Role |
|---|---|
| `state_manager.py` | per-conversation state, keyed by `session_id` (→ `user_id`); 10-min TTL; in-memory (POC) |
| `tools.py` | the actuators — **mock** `book_advising_appointment`; the security seam lives here |
| `base.py` | `Workflow` base, `Turn`, the registry, and `dispatch()` (cancel + kill-switch) |
| `advising_workflow.py` | the concrete 2-step booking workflow |
| `__init__.py` | registers workflows; exposes `dispatch`, `ENABLED`, `active_count` |

## Adding a workflow (e.g. `drop_subject`)

~30 lines, no pipeline change:

```python
# api/workflows/drop_subject_workflow.py
from .base import Turn, Workflow
class DropSubjectWorkflow(Workflow):
    name = "drop_subject"
    START_RE = re.compile(r"\b(drop|withdraw)\b[\w\s]{0,20}\b(subject|course|class)\b", re.I)
    def start(self): return Turn("Which subject code do you want to drop?", ["Cancel"])
    async def advance(self, state, message): ...   # collect, confirm, call the tool
```
then `register(DropSubjectWorkflow())` in `__init__.py`.

## ⚠️ Security requirements before any tool becomes real

The POC trusts a **typed** 9-digit student number. That is safe only because
the tool is a mock. A real, writing tool MUST:

1. **Authenticate the student and derive identity from the session, never the
   message.** Trusting a typed id lets anyone act as any student
   (impersonation / IDOR). Reuse the per-user OAuth pattern in
   `api/auth_ais.py` (the AIS write path already works this way); pass the
   authenticated id via `book_advising_appointment(..., authenticated_student_id=…)`.
2. **Confirm before executing** ("Book advising for {name} on {date}? yes/no").
3. **Stay behind the kill-switch** (`AGENTIC_WORKFLOWS_ENABLED`) and log every call.
4. **Get DPO sign-off + update the consent copy.** Collecting a student number
   in chat is new PII processing the current notice doesn't cover
   (see `docs/governance_signoff.md`).

## Registering the intent (optional, for graceful degradation)

Routing works from the bridge regex alone. Adding the intent to
`data/cavsu_intents.json` gives a sensible **fallback when the tier is OFF**
(the FAQ tier answers "here's how to book manually") and lets a future retrain
teach NB/NN to recognize it. Add this object to the `intents` array, then
re-import (`python intents_db.py`) and retrain:

```json
{
  "tag": "action_book_advising",
  "patterns": [
    "book an advising appointment",
    "schedule advising",
    "set up an advising consultation",
    "magpaschedule ng advising",
    "i want to meet my adviser"
  ],
  "responses": [
    "To book an advising appointment, contact your department's advising coordinator or visit the Registrar. (An in-chat booking assistant is coming soon.)"
  ]
}
```

## Test / run

```bash
python test_agentic_workflow.py          # 23 offline assertions (no app/models)
AGENTIC_WORKFLOWS_ENABLED=1 uvicorn api.app:app --port 8009   # then say "book advising"
```
