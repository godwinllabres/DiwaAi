# Sevi — CvSU Virtual Assistant

Sevi is the intelligent assistant for **Cavite State University (CvSU)**. It answers
student and visitor questions about admissions, programs, tuition, scholarships,
campus navigation, and university services through a layered "hybrid brain" that
favours fast, curated, source-grounded answers and only escalates to a language
model when the cheaper tiers cannot answer confidently.

This repository is the **backend** (Python / FastAPI + ML pipeline). The web
frontend lives in the separate `SeviWeb` project, and the full containerised stack
is orchestrated from `sevi-deploy`.

> **New here?** Start with **[docs/KT_DOCS.md](docs/KT_DOCS.md)** — the
> knowledge-transfer guide is the authoritative description of the current
> architecture. This README is the operational overview.

---

## How Sevi answers a question

Every `/chat` request flows through an ordered pipeline. The first tier that can
answer confidently wins, so most replies are served in tens of milliseconds with
no LLM cost:

```
User message
   │
   ▼
1. Safety screen        api/safety.py        self-harm / threat / abuse / profanity
   │                                         → graded, supportive or boundary reply
   ▼
2. Naive Bayes          hybrid_chatbot.py    fast, confident intent classification
   ▼
3. Neural Network       models/nn_model.h5   fallback for ambiguous phrasings
   ▼
4. Intent retrieval     api/intent_retrieval TF-IDF nearest-pattern match over intents
   ▼
5. Charter / Site RAG   api/charter_rag.py   grounded retrieval over the Citizens'
   │                    api/site_rag.py       Charter + official CvSU website
   ▼
6. LLM fallback         (optional)           augmented with retrieved passages,
                                             cited; disabled → verbatim RAG or
                                             graceful fallback
```

Answers from the curated tiers are **source-grounded**: `api/intent_grounding.py`
binds each intent to its official document, so replies can cite the Citizens'
Charter page or official-site URL that backs them (`ChatResponse.sources`).

---

## Quick start

### Requirements

- **Python 3.11** (the training/serving environment is pinned to 3.11.9)
- NLTK data (`punkt`, `punkt_tab`, `wordnet`)

### Local setup

```powershell
# 1. Create and activate a virtual environment
py -3.11 -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -r deployment/requirements_local.txt

# 3. Download NLTK data (first run only)
python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab'); nltk.download('wordnet')"

# 4. Seed the database (campus places, waypoints, seasons, etc.)
python scripts/seed_db.py

# 5. Start the API — trained models ship in the repo, no retraining needed
uvicorn api.app:app --host 0.0.0.0 --port 8009
```

- API docs (Swagger UI): http://localhost:8009/docs
- Health check: http://localhost:8009/health

On Windows, `run_server.bat` is a one-click launcher for the same server.

### Docker

Single API container:

```bash
docker-compose -f deployment/docker-compose.yml up -d
```

The full stack (API + web + reverse proxy, port **8090**) is built and run from the
`sevi-deploy` repository — see its README.

---

## Configuration

Runtime is configured via environment variables (see `.env.example`). The most
important:

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` | LLM backend for tier 6 (`ollama`, `anthropic`, `none`). `none` disables the LLM tier — the curated + RAG tiers still answer. |
| `ANTHROPIC_API_KEY` | API key when `LLM_PROVIDER=anthropic`. |
| `OLLAMA_BASE_URL` | Ollama endpoint when `LLM_PROVIDER=ollama`. |
| `DASHBOARD_PIN` / admin PIN | Guards the `/admin/*` and logging endpoints. |
| Chat-history backend | SQLite by default; a Postgres backend is supported (dual-backend logger). See [docs/POSTGRES_MIGRATION.md](docs/POSTGRES_MIGRATION.md). |

---

## Project structure

```
SeviAI/
├── app.py                 # Root FastAPI entrypoint (delegates to api/app.py)
├── run_server.bat         # Windows one-click launcher
├── hybrid_chatbot.py      # Hybrid NB + NN classifier (compat shim)
├── intents_db.py          # JSON ↔ SQLite intents loader / auto-sync
├── train_naive_bayes.py   # Retrain NB → models/CvSU_classifier.pkl
├── train_hybrid.py        # Retrain NN → models/nn_model.h5 + thresholds
├── render.yaml            # Render.com deployment config
│
├── api/                   # FastAPI app and the hybrid-brain tiers
│   ├── app.py             #   HTTP server, all endpoints
│   ├── hybrid_chatbot.py  #   NB + NN classifier
│   ├── safety.py          #   front-door safety screen
│   ├── intent_retrieval.py#   TF-IDF nearest-pattern tier
│   ├── intent_grounding.py#   per-intent source citations
│   ├── charter_rag.py     #   Citizens' Charter retrieval tier
│   ├── site_rag.py        #   official-website retrieval tier
│   ├── model_registry.py  #   model version tracking for chat logs
│   ├── nlu_engine.py      #   entity extraction & context tracking
│   ├── logger.py          #   async chat logging (SQLite / Postgres)
│   ├── campus_places.py   #   campus map metadata & directory
│   └── ais_mcp.py, connectors_mcp.py, auth_ais.py  # AIS / MCP bridges
│
├── data/                  # cavsu_intents.json (⭐ source of truth), SQLite cache,
│                          #   intent_sources.json (grounding), map overrides, fixtures
├── models/                # Trained artifacts: NB .pkl, NN .h5, tokenizer/encoder,
│                          #   nn_thresholds.json, responses_map.json
├── training/              # Training, evaluation & test scripts
├── scripts/               # Utilities (seed_db.py, migrations, intent binding, …)
├── web/                   # Plain-HTML chat UI + logs dashboard
├── deployment/            # Dockerfiles, docker-compose, requirements variants
├── docs/                  # Guides (start with KT_DOCS.md ⭐)
├── archive/               # Historical snapshots & model backups — ignore day-to-day
└── logs/                  # Runtime chat logs (gitignored)
```

---

## Editing what Sevi knows

The intent definitions in **`data/cavsu_intents.json`** are the source of truth.

```
data/cavsu_intents.json   ← edit here
        ▼
intents_db.py             ← auto-syncs SQLite when the JSON changes
        ▼
data/cavsu_intents.db     ← runtime cache
        ▼
train_naive_bayes.py      ← retrains NB, NN, responses_map, and rebuilds the
train_hybrid.py              intent-retrieval index from the same corpus
        ▼
api/app.py                ← loads artifacts on startup (or POST /model/reload)
```

Intent JSON format:

```json
{
  "intents": [
    {
      "tag": "admissions_requirements",
      "patterns": ["What are admission requirements?"],
      "responses": ["For freshman admission, you need ..."]
    }
  ]
}
```

To change a response and keep every tier aligned: edit the JSON, retrain, and
reload. Source citations for an intent are bound in `data/intent_sources.json`
(`scripts/bind_intent_sources.py`).

---

## API surface

The server exposes a broad REST API (full reference at `/docs`). Grouped by tag:

| Area | Representative endpoints |
|------|--------------------------|
| **Chat** | `POST /chat`, `POST /batch` |
| **Health** | `GET /`, `GET /health` |
| **Intents** | `GET /intents`, `GET /intents/{tag}` |
| **Model** | `GET /model/info`, `POST /model/reload` |
| **Map / directory** | `GET /map`, `GET/PUT/DELETE /map/coords`, `/map/waypoints`, `/map/custom_markers` |
| **Conversation** | `GET/DELETE /conversation/{user_id}` |
| **Logging** *(admin)* | `/logs/recent`, `/logs/today`, `/logs/search`, `/logs/export/{user_id}`, … |
| **Feedback** *(admin)* | `/feedback`, `/feedback/stats`, `/feedback/analyze` |
| **Topics** | `GET /topics/recommended` |
| **Admin** | `/admin/status`, `/admin/llm`, `/admin/moderation`, `/admin/intents` |
| **AIS** | `/auth/login`, `/auth/whoami`, `/ais/write` |

Admin, logging, and feedback endpoints require the admin PIN.

Example:

```bash
curl -X POST "http://localhost:8009/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the admission requirements?", "user_id": "user_1"}'
```

---

## Training & evaluation

| Script | Purpose |
|--------|---------|
| `python train_naive_bayes.py` | Retrain the Naive Bayes intent classifier |
| `python train_hybrid.py` | Retrain the neural-network tier + thresholds |
| `python scripts/continuous_training.py` | Continuous-improvement pipeline |
| `python training/test_intents.py <port> <n>` | Evaluate the live model, surface weak intents |

Test suites (`test_*.py` at the repo root and under `training/`) cover the safety
gate, intent grounding, retrieval tiers, and campus context. Run them after any
change to the pipeline or intents.

---

## Chat logging & observability

Every message is logged asynchronously (`api/logger.py`) with the model version
that produced the answer, via the model registry (`api/model_registry.py`) — a
retrain or LLM swap is fully traceable in the history. Logs are written to SQLite
by default, with a Postgres backend available (see
[docs/POSTGRES_MIGRATION.md](docs/POSTGRES_MIGRATION.md)). The
`web/logs_dashboard.html` dashboard and the `/logs/*` and `/feedback/*` endpoints
surface analytics and moderation review.

---

## Documentation

| Document | Purpose |
|----------|---------|
| **[docs/KT_DOCS.md](docs/KT_DOCS.md)** | ⭐ Authoritative knowledge-transfer guide |
| [docs/POSTGRES_MIGRATION.md](docs/POSTGRES_MIGRATION.md) | Chat-history Postgres backend & migration |
| [api/README.md](api/README.md) | API module notes |

---

## License

Built for **Cavite State University**.

*"Iskolar para sa Bayan!"* 🎓
