# Quick Start Guide

Get the CvSU Virtual Assistant running in minutes.

## 🚀 Fastest Start (2 minutes)

### Option A: Docker (Recommended)
The full stack is built and run from the `sevi-deploy` repo:
```bash
cd ../sevi-deploy && docker compose up -d --wait
```

Then open:
- **Chat**: http://localhost:8090
- **API Docs**: http://localhost:8090/api/docs
- **Dashboard**: Open `web/logs_dashboard.html` in browser

### Option B: Local Python
```bash
# 1. Install dependencies
pip install -r deployment/requirements_minimal.txt

# 2. Start API
python -m uvicorn api.app:app --host 0.0.0.0 --port 8000

# 3. Open browser
# Chat UI: Open web/web_interface.html
# API Docs: http://localhost:8000/docs
```

---

## 📂 Project Organization

```
SeviAI/
├── api/              ← REST API & ML models
├── training/         ← Test & improve model
├── data/             ← Training patterns
├── models/           ← Trained models
├── web/              ← Chat UI & Dashboard
├── deployment/       ← Docker setup
├── docs/             ← Documentation
└── logs/             ← Chat database
```

---

## 💻 Common Commands

### Start API
```bash
# Docker (full stack, from the sevi-deploy repo)
cd ../sevi-deploy && docker compose up -d --wait

# Local Python
python -m uvicorn api.app:app --port 8000
```

### Test Model
```bash
python training/test_intents.py 8000 5
```

### Improve Model
```bash
python training/expand_intents.py
mv data/CvSU_intents_expanded.json data/CvSU_intents.json
python training/train_naive_bayes.py
```

### View Logs
```bash
docker-compose logs -f CvSU-chatbot
```

### Stop API
```bash
docker-compose down
```

---

## 🔗 API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /chat` | Send message |
| `GET /health` | Health check |
| `GET /docs` | API documentation |
| `GET /logs/today` | Today's stats |

### Example Request
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "What are admission requirements?"}'
```

---

## 📊 Model Performance

| Metric | Value |
|--------|-------|
| Accuracy | 95.59% |
| Response Time | <75ms |
| Training Patterns | 227 |
| Intent Categories | 23 |

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| `docs/SETUP_GUIDE.md` | Initial setup |
| `docs/TRAINING_GUIDE.md` | Training scripts |
| `docs/API_README.md` | API reference |
| `docs/DOCKER_DEPLOYMENT.md` | Docker guide |
| `docs/PROJECT_STATUS.md` | Full status |

---

## 🛠️ Directory Guides

- **`api/README.md`** - API server details
- **`training/README.md`** - Training scripts guide
- **`deployment/README.md`** - Docker deployment
- **`web/README.md`** - Web interfaces

---

## ✅ Next Steps

1. **Choose your option**: Docker or Local Python
2. **Start the API** using commands above
3. **Test the API**: Open `http://localhost:8000/docs`
4. **Send a message**: Use chat UI or curl
5. **Monitor performance**: Check logs dashboard
6. **Improve model** (optional): Run training scripts

---

## 🐛 Troubleshooting

### "Port already in use"
```bash
# Use different port
python -m uvicorn api.app:app --port 8001
```

### "API won't start"
```bash
# Check if dependencies installed
pip install -r deployment/requirements_minimal.txt

# Check Python version
python --version  # Should be 3.11+ for Docker, 3.14 for local
```

### "Can't connect to API"
```bash
# Verify API is running
curl http://localhost:8000/health

# Check logs
docker-compose logs CvSU-chatbot
```

---

## 📞 Support

- **Setup issues**: See `docs/SETUP_GUIDE.md`
- **API problems**: See `api/README.md` or `docs/API_README.md`
- **Training questions**: See `training/README.md` or `docs/TRAINING_GUIDE.md`
- **Deployment help**: See `deployment/README.md` or `docs/DOCKER_DEPLOYMENT.md`

---

## 🎯 Quick Links

| Location | Purpose |
|----------|---------|
| `web/web_interface.html` | Open in browser to chat |
| `web/logs_dashboard.html` | Open in browser for analytics |
| `http://localhost:8000/docs` | API documentation |
| `training/test_intents.py` | Quick model test |
| `data/CvSU_intents.json` | Training patterns |

---

## ✨ Status

- ✅ Model: Production-ready (95.59% accuracy)
- ✅ API: 15+ endpoints, fully functional
- ✅ Logging: SQLite + JSON backups
- ✅ Docker: Ready for deployment
- ✅ Web UI: Beautiful, responsive interfaces
- ✅ Documentation: Comprehensive guides

**Ready to deploy!** 🚀

---

## 📖 Full Documentation

For comprehensive details, see:
- **Main README**: `README.md`
- **API Details**: `api/README.md`
- **Training**: `training/README.md`
- **Deployment**: `deployment/README.md`
- **All Docs**: `docs/` folder

