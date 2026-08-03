# CvSU Chatbot REST API

A production-ready REST API for the CvSU Virtual Assistant chatbot with agent instructions, zero external dependencies, and easy web app integration.

## Features

✅ **Agent Instructions** — Customizable system prompts for chatbot personality  
✅ **REST API** — FastAPI with automatic docs (Swagger UI)  
✅ **Intent Classification** — Naive Bayes with 96.65% accuracy  
✅ **Conversation Tracking** — Per-user and per-session history  
✅ **Batch Processing** — Handle multiple requests at once  
✅ **No External APIs** — Runs completely offline  
✅ **CORS Enabled** — Ready for web app integration  
✅ **Docker Ready** — One-command deployment  

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the Server

```bash
python app.py
```

Server starts on: `http://localhost:8000`

### 3. Access Documentation

- **Interactive API Docs**: `http://localhost:8000/docs`
- **Alternative Docs**: `http://localhost:8000/redoc`
- **Web Interface**: Open `web_interface.html` in a browser

---

## API Endpoints

### Core Chat Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/chat` | Send a message, get response |
| `POST` | `/batch` | Send multiple messages |
| `GET` | `/intents` | List all intents |
| `GET` | `/intents/{tag}` | Get intent details |

### Model Information

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/health` | Health check |
| `GET` | `/model/info` | Model metadata |
| `GET` | `/model/instructions` | Agent instructions |

### Conversation Management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/conversation/{user_id}` | View user history |
| `DELETE` | `/conversation/{user_id}` | Clear user history |

---

## Agent Instructions

The chatbot has customizable system instructions defined in `app.py`:

```python
SYSTEM_INSTRUCTIONS = """
You are CvSU Virtual Assistant - a helpful, friendly guide for Cavite State University.

CORE PERSONALITY:
- Professional yet approachable
- Patient and empathetic
- Always respectful of Filipino culture ("Iskolar para sa Bayan")
- Proactive in offering additional help

BEHAVIOR GUIDELINES:
1. Intent Recognition: Use the classified intent to provide relevant information
2. Fallback Handling: When confidence is low, politely ask for clarification
3. Tone: Warm, encouraging, supportive of student goals
4. Context Awareness: Remember user's intent to provide follow-up suggestions

RESPONSE PROTOCOLS:
- Always start with a relevant greeting if it's the first message
- Provide complete, actionable information
- Include contact details when relevant (email, phone, office)
- Offer next steps: "Is there anything else I can help you with?"
...
"""
```

### Customize Instructions

Edit the `SYSTEM_INSTRUCTIONS` variable in `app.py` to:
- Change personality (formal, casual, friendly)
- Modify response style (detailed, concise, with examples)
- Add language preferences (English, Filipino, mixed)
- Update behavior rules and guidelines

---

## API Examples

### JavaScript/Fetch

```javascript
const response = await fetch("http://localhost:8000/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    message: "What are the admission requirements?",
    user_id: "user_123"
  })
});

const data = await response.json();
console.log(data.response);
console.log(`Intent: ${data.intent} (${data.confidence.toFixed(1)}%)`);
```

### Python/Requests

```python
import requests

response = requests.post(
    "http://localhost:8000/chat",
    json={
        "message": "Tell me about CvSU",
        "user_id": "user_123"
    }
)

data = response.json()
print(f"Response: {data['response']}")
print(f"Intent: {data['intent']} ({data['confidence']:.1%})")
```

### cURL

```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "When is enrollment?",
    "user_id": "user_123"
  }'
```

---

## Request/Response Format

### Chat Request

```json
{
  "message": "What are the admission requirements?",
  "user_id": "user_123",          // Optional: for conversation tracking
  "session_id": "session_456"     // Optional: for session tracking
}
```

### Chat Response

```json
{
  "response": "For freshman admission to CvSU, you will need...",
  "intent": "admissions_requirements",
  "confidence": 0.758,
  "user_id": "user_123",
  "session_id": "session_456"
}
```

---

## Integration Examples

### React Component

```jsx
import { useState } from "react";

export default function ChatWidget() {
  const [message, setMessage] = useState("");
  const [responses, setResponses] = useState([]);
  const userId = `user_${Date.now()}`;

  const handleSend = async () => {
    const res = await fetch("http://localhost:8000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, user_id: userId })
    });

    const data = await res.json();
    setResponses((prev) => [
      ...prev,
      { role: "bot", text: data.response, intent: data.intent }
    ]);
    setMessage("");
  };

  return (
    <div className="chat">
      <div className="messages">
        {responses.map((msg, i) => (
          <div key={i} className={msg.role}>
            {msg.text}
            {msg.intent && <small>{msg.intent}</small>}
          </div>
        ))}
      </div>
      <input value={message} onChange={(e) => setMessage(e.target.value)} />
      <button onClick={handleSend}>Send</button>
    </div>
  );
}
```

### Vue.js Component

```vue
<template>
  <div class="chat-container">
    <div v-for="msg in messages" :key="msg.id" :class="msg.role">
      {{ msg.text }}
      <small v-if="msg.intent">{{ msg.intent }}</small>
    </div>
    <input v-model="input" @keyup.enter="send" placeholder="Ask..." />
    <button @click="send">Send</button>
  </div>
</template>

<script>
import axios from "axios";

export default {
  data() {
    return {
      messages: [],
      input: "",
      userId: `user_${Date.now()}`
    };
  },
  methods: {
    async send() {
      const { data } = await axios.post("http://localhost:8000/chat", {
        message: this.input,
        user_id: this.userId
      });
      this.messages.push({
        id: Date.now(),
        role: "bot",
        text: data.response,
        intent: data.intent
      });
      this.input = "";
    }
  }
};
</script>
```

---

## Deployment

### Docker (Recommended)

```bash
# Build image
docker build -t CvSU-chatbot .

# Run container
docker run -p 8000:8000 CvSU-chatbot
```

### Docker Compose

```bash
docker-compose up
```

### Cloud Platforms

**Heroku:**
```bash
heroku create CvSU-chatbot
git push heroku main
```

**Railway / Render / Fly.io:**
- Connect GitHub repo
- Set `Command`: `python app.py`
- Auto-deploy on push

---

## Configuration

### Environment Variables

Create `.env` file:
```
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=*
CONFIDENCE_THRESHOLD=0.30
```

### Model Configuration

Edit `CONFIDENCE_THRESHOLD` in `app.py`:
- **0.30**: More lenient (accepts more responses)
- **0.50**: Moderate (balanced)
- **0.70**: Strict (only high-confidence)

---

## Monitoring & Logging

### Health Endpoint

```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "model_loaded": true,
  "intents_available": 23
}
```

### Model Info

```bash
curl http://localhost:8000/model/info
```

---

## Performance

| Metric | Value |
|--------|-------|
| Response Time | <50ms |
| Throughput | 1000+ req/min |
| Concurrent Connections | Unlimited |
| Model Accuracy | 96.65% |
| Memory Usage | ~100MB |
| CPU Usage | <5% (idle) |

---

## Troubleshooting

### Server Won't Start

**Error**: `Port 8000 already in use`

```bash
# Find process using port 8000
lsof -i :8000

# Kill process
kill -9 <PID>
```

### API Timeout

**Error**: `Connection refused`

- Ensure server is running: `python app.py`
- Check firewall allows port 8000
- Verify no proxy blocking requests

### Low Confidence Responses

**Issue**: Intent classification confidence is low

- Increase confidence threshold in frontend
- Ask user to rephrase question
- Check if intent exists with `GET /intents`

### CORS Issues

**Error**: `No 'Access-Control-Allow-Origin' header`

- Ensure CORS middleware is enabled in `app.py`
- Set `allow_origins` to your domain
- For development: `allow_origins=["*"]`

---

## Security Notes

⚠️ **For Production:**

1. **CORS**: Restrict `allow_origins` to specific domains
```python
allow_origins=["https://yourdomain.com"]
```

2. **Authentication**: Add API key validation
```python
@app.post("/chat")
async def chat_endpoint(request: ChatRequest, api_key: str = Header(...)):
    if api_key != os.getenv("API_KEY"):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

3. **Rate Limiting**: Use rate limit middleware
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)
```

4. **HTTPS**: Always use HTTPS in production
- Deploy behind nginx/Apache with SSL
- Use cloud provider's SSL certificate

---

## API Specs

- **Framework**: FastAPI
- **Server**: Uvicorn
- **Python**: 3.8+
- **Dependencies**: See `requirements.txt`
- **License**: MIT

---

## Support

### Documentation

- Interactive API Docs: `http://localhost:8000/docs`
- Full Integration Guide: `INTEGRATION_GUIDE.md`
- Test Script: `test_api.py`

### Common Questions

**Q: Can I use this in production?**
A: Yes! It's designed for production use. See Security Notes above.

**Q: How do I customize the chatbot personality?**
A: Edit `SYSTEM_INSTRUCTIONS` in `app.py`.

**Q: Can I add new intents?**
A: Yes — two ways. Preferred: `POST /admin/intents` (admin PIN required), which
runs server-side sanitation first — tag format, pattern/response hygiene,
collision detection, and a reserved-tag blocklist (system tags like
`llm_unavailable` or `safety_*` are refused, even with `?force=true`). Or edit
`data/cavsu_intents.json` directly and retrain with `py -3.11
train_naive_bayes.py`. Either way, finish with
`py -3.11 scripts/update_trusted_hashes.py` (the API refuses unpinned model
artifacts) and restart or `POST /model/reload`.

**Q: Is there a rate limit?**
A: No built-in limit, but add one with slowapi for production.

**Q: Can I host this on serverless (Lambda, Cloud Functions)?**
A: Yes, wrap with appropriate handlers for your platform.

---

Made with ❤️ for **Cavite State University**

**Iskolar para sa Bayan!**

