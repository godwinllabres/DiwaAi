# Deployment Directory - Docker & Production

## Purpose
Production-ready Docker configuration and deployment files.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile.local` | Image used by the sevi-deploy compose stack and CI (`uvicorn api.app:app`) |
| `Dockerfile.render` | Slim image for Render (no TensorFlow, 512MB tier) |
| `requirements.txt` | Full Python dependencies (with TensorFlow) |
| `requirements_minimal.txt` | Minimal dependencies (NB only) |

## Quick Deploy

```bash
docker-compose up -d
```

Then access:
- API: http://localhost:8000
- Docs: http://localhost:8000/docs

## Docker Architecture

### Dockerfile.local / Dockerfile.render
- **Base**: Python 3.11-slim (lightweight)
- **Installs**: Build tools, curl, Python deps
- **Downloads**: NLTK resources
- **Runs**: Uvicorn with 4 workers
- **Port**: 8000
- **Health Check**: Every 30s

### Removed: docker-compose.yml
- **Service**: CvSU-chatbot
- **Image**: Built from Dockerfile
- **Ports**: 8000:8000
- **Volumes**:
  - `./models:/app/models` - Trained models
  - `./data:/app/data` - Training data
  - `./logs:/app/logs` - Chat logs
- **Network**: CvSU-network
- **Restart**: unless-stopped

## Commands

### Start
```bash
docker-compose up -d
```

### Stop
```bash
docker-compose down
```

### View Logs
```bash
docker-compose logs -f CvSU-chatbot
```

### Rebuild
```bash
docker-compose up --build -d
```

### Execute Command
```bash
docker-compose exec CvSU-chatbot python training/test_intents.py 8000 5
```

### Remove Everything
```bash
docker-compose down -v  # Removes volumes too
```

## Production Considerations

### 1. Environment Variables
Create `.env` file:
```
WORKERS=4
LOG_LEVEL=INFO
```

### 2. Reverse Proxy (nginx)
```nginx
upstream CvSU {
    server CvSU-chatbot:8000;
}

server {
    listen 80;
    server_name api.cvsu.edu.ph;
    
    location / {
        proxy_pass http://CvSU;
        proxy_set_header Host $host;
    }
}
```

### 3. SSL/TLS
```bash
# Let's Encrypt
certbot certonly --standalone -d api.cvsu.edu.ph
```

### 4. Database Persistence
```bash
docker volume create CvSU-logs
```

Update docker-compose.yml:
```yaml
volumes:
  - CvSU-logs:/app/logs
```

### 5. Monitoring
Add to docker-compose.yml:
```yaml
environment:
  - LOG_LEVEL=DEBUG
```

## Requirements Files

### requirements.txt
Full dependencies including TensorFlow for Python 3.11:
```
fastapi==0.104.1
uvicorn==0.24.0
scikit-learn==1.3.2
tensorflow==2.14.0
keras==2.14.0
...
```

Used when Neural Network training is needed.

### requirements_minimal.txt
Essential dependencies only:
```
fastapi==0.104.1
uvicorn==0.24.0
scikit-learn==1.3.2
nltk==3.8.1
...
```

Used for lightweight deployment (Naive Bayes only).

## Scaling

### Single Container
```bash
docker-compose up -d
```

### Multiple Containers
```bash
docker-compose up -d --scale CvSU-chatbot=3
```

Then use load balancer (nginx, HAProxy, etc.).

## Health Checks

### Docker Health
```bash
docker ps --format "{{.Names}}\t{{.Status}}"
```

### API Health
```bash
curl http://localhost:8000/health
```

### View Logs
```bash
docker-compose logs CvSU-chatbot
```

## Environment Variables

Create `.env`:
```bash
# Service
WORKERS=4
LOG_LEVEL=INFO

# Database
DB_PATH=/app/logs/chat_history.db

# API
HOST=0.0.0.0
PORT=8000
```

## Cleanup

### Remove Stopped Containers
```bash
docker-compose down
docker container prune
```

### Remove Unused Images
```bash
docker image prune
```

### Remove All Unused Resources
```bash
docker system prune -a
```

## Troubleshooting

### Container Won't Start
```bash
docker-compose logs CvSU-chatbot
```

### Port Already in Use
```bash
# Change port in docker-compose.yml
ports:
  - "9000:8000"  # Use 9000 instead of 8000
```

### Out of Memory
```bash
# In docker-compose.yml
deploy:
  resources:
    limits:
      memory: 2G
```

### Model Files Missing
```bash
# Check volumes
docker inspect CvSU-chatbot | grep Mounts

# Copy files into container
docker cp models/. CvSU-chatbot:/app/models/
```

## Kubernetes (Future)

Convert to Kubernetes:
```bash
pip install kompose
kompose convert -f docker-compose.yml
```

## Related Documentation

- **Full Deployment Guide**: `docs/DOCKER_DEPLOYMENT.md`
- **Setup**: `docs/SETUP_GUIDE.md`
- **API**: `docs/API_README.md`



> **Note — removed files.** `Dockerfile` and `docker-compose.yml` were deleted
> along with the legacy root `app.py` they started. That entrypoint exposed the
> chat-log, conversation and feedback-read routes with no authentication, and
> published them on host port 8000. The supported paths are
> `Dockerfile.local` (the sevi-deploy compose stack, port 8090) and
> `Dockerfile.render`, both running `uvicorn api.app:app`.
