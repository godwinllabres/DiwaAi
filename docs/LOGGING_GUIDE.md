# CvSU Chatbot - Logging & Analytics Guide

## Overview

The chatbot now has comprehensive logging that tracks:
- ✅ All chat messages (user input + bot response)
- ✅ Intent classification & confidence scores
- ✅ User conversations & session history
- ✅ Intent usage statistics
- ✅ Response times & performance metrics
- ✅ Real-time analytics dashboard

---

## Features

### 1. **Automatic Chat Logging**
Every chat message is automatically logged to:
- **SQLite Database** — `logs/chat_history.db`
- **Daily JSON Files** — `logs/chat_YYYY-MM-DD.log`

### 2. **Session Tracking**
- Per-user conversation tracking
- Per-session grouping
- Message count & timestamps
- Average confidence per session

### 3. **Intent Statistics**
- Usage count per intent
- Average confidence per intent
- Last used timestamp
- Popularity ranking

### 4. **Performance Monitoring**
- Response time tracking (milliseconds)
- Confidence score analysis
- Intent classification metrics

### 5. **Analytics Dashboard**
- Real-time statistics
- Message search & filtering
- Session overview
- Intent popularity charts

---

## Logging Architecture

```
┌─────────────────────────────────┐
│   Chat Message Received         │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│   API Endpoint (/chat)          │
│   - Process message             │
│   - Classify intent             │
│   - Generate response           │
│   - Measure response time       │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│   ChatLogger.log_chat()         │
└────┬──────────────┬─────────┬──────┐
     │              │         │      │
     ▼              ▼         ▼      ▼
┌─────────┐  ┌──────────┐  ┌───────┐  ┌──────────┐
│ Database│  │JSON File │  │Intent │  │ Session  │
│ Insert  │  │  Write   │  │ Stats │  │ Tracking │
└─────────┘  └──────────┘  └───────┘  └──────────┘
```

---

## Database Schema

### chat_messages table
```sql
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,              -- ISO format timestamp
    user_id TEXT,               -- User identifier
    session_id TEXT,            -- Session identifier
    user_message TEXT,          -- User's input (PII-masked at write time)
    bot_response TEXT,          -- Bot's response
    intent TEXT,                -- Classified intent
    confidence REAL,            -- Confidence score (0-1)
    response_time_ms REAL,      -- Response time in milliseconds
    model_used TEXT,            -- Tier that answered (see api/model_registry.py)
    model_version_id INTEGER,   -- -> model_version.id
    device_id TEXT,             -- Opaque per-browser id; counts distinct devices
    device_class TEXT           -- "<phone|tablet|desktop>/<portrait|landscape>"
)
```

`device_id` / `device_class` are optional and validated at the API door
(`ChatRequest` in `api/app.py`): an id that does not match `[A-Za-z0-9_-]{8,64}`
and a class outside the seven allowlisted slugs are both dropped to NULL rather
than rejected, so a broken client loses a metric, not its answer. Roll them up
with `ChatLogger.get_device_stats(days)` or `GET /logs/devices` (admin).

### sessions table
```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT UNIQUE,
    user_id TEXT,
    start_time TEXT,
    end_time TEXT,
    message_count INTEGER,
    average_confidence REAL
)
```

### intent_stats table
```sql
CREATE TABLE intent_stats (
    id INTEGER PRIMARY KEY,
    intent TEXT UNIQUE,
    count INTEGER,
    avg_confidence REAL,
    last_used TEXT
)
```

---

## API Endpoints for Logging

### Get User Chat History

**GET** `/logs/user/{user_id}?limit=50`

```bash
curl "http://localhost:8000/logs/user/user_123?limit=50"
```

Response:
```json
{
  "user_id": "user_123",
  "message_count": 25,
  "messages": [
    {
      "timestamp": "2026-05-05T10:30:45.123456",
      "user_id": "user_123",
      "user_message": "What are admission requirements?",
      "bot_response": "For freshman admission...",
      "intent": "admissions_requirements",
      "confidence": 0.758,
      "response_time_ms": 45.2
    },
    ...
  ]
}
```

### Get Session Chat History

**GET** `/logs/session/{session_id}`

```bash
curl "http://localhost:8000/logs/session/session_abc123"
```

### Get Intent Statistics

**GET** `/logs/intents`

```bash
curl "http://localhost:8000/logs/intents"
```

Response:
```json
{
  "total_intents": 23,
  "intents": [
    {
      "intent": "admissions_requirements",
      "count": 125,
      "avg_confidence": 0.815,
      "last_used": "2026-05-05T14:20:30"
    },
    ...
  ]
}
```

### Get Sessions List

**GET** `/logs/sessions?user_id=user_123&limit=20`

```bash
curl "http://localhost:8000/logs/sessions?user_id=user_123&limit=20"
```

### Get Today's Statistics

**GET** `/logs/today`

```bash
curl "http://localhost:8000/logs/today"
```

Response:
```json
{
  "date": "2026-05-05",
  "total_messages": 342,
  "unique_users": 87,
  "average_confidence": 0.7645,
  "top_intent": {
    "intent": "admissions_requirements",
    "count": 45
  }
}
```

### Search Logs

**GET** `/logs/search?query=tuition&limit=20`

```bash
curl "http://localhost:8000/logs/search?query=tuition&limit=20"
```

### Export User Data

**POST** `/logs/export/{user_id}`

```bash
curl -X POST "http://localhost:8000/logs/export/user_123"
```

Response:
```json
{
  "status": "success",
  "user_id": "user_123",
  "filepath": "logs/export_user_123_20260505_143020.json",
  "message": "User data exported to logs/export_user_123_20260505_143020.json"
}
```

### Delete Old Logs

**DELETE** `/logs/cleanup?days=30`

```bash
curl -X DELETE "http://localhost:8000/logs/cleanup?days=30"
```

Deletes all logs older than 30 days.

---

## Dashboard Access

Open the **Logs Dashboard** in your browser:

```
file:///C:/Users/user/Documents/POC/SeviAI/logs_dashboard.html
```

Or serve it with:
```bash
python -m http.server 8080
# Then visit: http://localhost:8080/logs_dashboard.html
```

### Dashboard Features

1. **Overview Tab**
   - Today's statistics
   - Total messages, unique users
   - Average confidence
   - Top intents chart

2. **Messages Tab**
   - Recent chat messages
   - Search functionality
   - User ID, timestamp, intent filtering

3. **Intents Tab**
   - Intent usage statistics
   - Confidence analysis
   - Last used timestamps
   - Popularity ranking

4. **Sessions Tab**
   - Active sessions list
   - Session duration
   - Message count per session
   - Average confidence per session

5. **Users Tab**
   - User lookup by ID
   - User conversation history
   - User statistics

---

## File Structure

```
SeviAI/
├── app.py                    ← Updated with logging
├── logger.py                 ← Chat logging system
├── logs/                     ← Logs directory
│   ├── chat_history.db       ← SQLite database
│   ├── chat_2026-05-05.log   ← Daily JSON logs
│   ├── chat_2026-05-04.log
│   └── export_user_*.json    ← Exported user data
├── logs_dashboard.html       ← Analytics dashboard
└── LOGGING_GUIDE.md          ← This file
```

---

## Usage Examples

### Python - Retrieve User History

```python
import requests

response = requests.get(
    "http://localhost:8000/logs/user/user_123",
    params={"limit": 50}
)

data = response.json()
for msg in data["messages"]:
    print(f"{msg['timestamp']}: {msg['user_message']}")
    print(f"  → {msg['bot_response']}")
    print(f"  Intent: {msg['intent']} ({msg['confidence']:.1%})\n")
```

### JavaScript - Get Today's Stats

```javascript
const response = await fetch("http://localhost:8000/logs/today");
const stats = await response.json();

console.log(`Messages Today: ${stats.total_messages}`);
console.log(`Unique Users: ${stats.unique_users}`);
console.log(`Avg Confidence: ${(stats.average_confidence * 100).toFixed(1)}%`);
console.log(`Top Intent: ${stats.top_intent.intent}`);
```

### cURL - Search Logs

```bash
curl "http://localhost:8000/logs/search?query=scholarship&limit=10"
```

### Export User Data

```bash
curl -X POST "http://localhost:8000/logs/export/user_123" | jq .
```

---

## Configuration

### Log Directory

By default, logs are saved to `./logs/`. To change:

```python
# In app.py
chat_logger = ChatLogger(
    log_dir="custom_logs_path",
    db_path="custom_logs_path/chat_history.db"
)
```

### Database Location

Change SQLite database path:

```python
chat_logger = ChatLogger(
    log_dir="logs",
    db_path="/path/to/custom/chat_history.db"
)
```

### Log Rotation

Clean old logs automatically:

```python
# Delete logs older than 30 days
chat_logger.cleanup_old_logs(days=30)
```

Or via API:
```bash
curl -X DELETE "http://localhost:8000/logs/cleanup?days=30"
```

---

## Performance Considerations

### Database Optimization

The SQLite database automatically:
- Indexes user_id and timestamp columns
- Performs INSERT in transactions
- Uses UNIQUE constraint for intents

### Concurrent Access

The logger uses `threading.Lock()` for thread-safe writes.

### Storage Optimization

- **Daily JSON files**: One file per day (~500KB/day depending on traffic)
- **SQLite database**: Compact, ~1MB per 1000 messages
- **JSON exports**: Generated on-demand

---

## Analytics & Reports

### User Behavior Analysis

```python
# Get all messages from a user
user_logs = requests.get(f"http://localhost:8000/logs/user/{user_id}").json()

# Analyze conversation patterns
total_messages = user_logs["message_count"]
avg_confidence = sum(m["confidence"] for m in user_logs["messages"]) / total_messages

# Find most asked intents
from collections import Counter
intents = Counter(m["intent"] for m in user_logs["messages"])
print(f"Top 5 intents: {intents.most_common(5)}")
```

### Intent Performance

```python
# Get intent statistics
intent_stats = requests.get("http://localhost:8000/logs/intents").json()

for intent in intent_stats["intents"]:
    print(f"{intent['intent']}: {intent['count']} uses, {intent['avg_confidence']:.1%} confidence")
```

### Daily Engagement

```python
# Get today's statistics
today = requests.get("http://localhost:8000/logs/today").json()

print(f"Total Messages: {today['total_messages']}")
print(f"Unique Users: {today['unique_users']}")
print(f"Messages/User: {today['total_messages'] / today['unique_users']:.1f}")
```

---

## Data Privacy & GDPR

### User Data Export

Export all data for a user (GDPR compliance):

```bash
curl -X POST "http://localhost:8000/logs/export/user_123"
```

### User Data Deletion

To delete all logs for a user:

```python
import sqlite3

conn = sqlite3.connect("logs/chat_history.db")
cursor = conn.cursor()

cursor.execute("DELETE FROM chat_messages WHERE user_id = ?", ("user_123",))
cursor.execute("DELETE FROM sessions WHERE user_id = ?", ("user_123",))

conn.commit()
conn.close()
```

---

## Troubleshooting

### Database Locked Error

If you see "database is locked":
```python
# Increase timeout
conn = sqlite3.connect("logs/chat_history.db", timeout=10)
```

### Logs Directory Not Found

Create it manually:
```bash
mkdir logs
```

### High Disk Usage

Clean old logs:
```bash
curl -X DELETE "http://localhost:8000/logs/cleanup?days=7"
```

### Dashboard Shows No Data

1. Check API is running: `http://localhost:8000/health`
2. Check logs directory exists: `ls -la logs/`
3. Refresh dashboard: Press F5

---

## Summary

| Feature | Details |
|---------|---------|
| **Logging** | Automatic to DB + JSON files |
| **Storage** | SQLite + Daily JSON backups |
| **API Endpoints** | 7 logging endpoints |
| **Dashboard** | Real-time analytics UI |
| **Performance** | <10ms overhead per message |
| **Retention** | Configurable (default: unlimited) |
| **Privacy** | User data export & deletion support |

---

**Iskolar para sa Bayan!** 🎓

