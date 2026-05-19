# ARIA — Restaurant Inventory Assistant — Setup Guide

## Prerequisites
- Python 3.10+
- Node.js 18+
- OpenAI API key

---

## 1. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env
# Edit .env and set your OPENAI_API_KEY

# Start backend
uvicorn main:app --reload --port 8000
```

Backend runs at: http://localhost:8000
API docs at: http://localhost:8000/docs

---

## 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

Frontend runs at: http://localhost:5173

---

## 3. Quick Start

1. Start backend → `cd backend && uvicorn main:app --reload`
2. Start frontend → `cd frontend && npm run dev`
3. Open http://localhost:5173
4. The system loads with sample inventory data automatically
5. Click the mic button or type in the chat box

---

## Conversation Examples

### Add inventory
> "Shelf A has 20 tomato boxes"
> "Add 50 kg of rice to the warehouse"
> "Put 15 liters of olive oil on Shelf B"

### Remove inventory
> "Remove 5 kg of chicken from the freezer"
> "Take out 10 milk cartons from cold storage"

### Query
> "What's in the freezer?"
> "Which items are running low?"
> "What expires soon?"

### Context-aware
> "Add 20 tomatoes" → AI asks for unit/location
> "Add 10 more" → AI remembers it's tomatoes

### Confirmation flow
> User: "Set flour to 100 kg in warehouse"
> ARIA: "I'll update flour to 100 kg at Warehouse. Confirm?"
> User: "Yes"
> ARIA: "Done! Flour updated."

---

## Architecture

```
voice-assistant/
├── backend/                  # FastAPI Python backend
│   ├── main.py               # App entry point + WebSocket
│   ├── database.py           # SQLAlchemy + SQLite setup
│   ├── models.py             # Database models
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── routers/
│   │   ├── voice.py          # Voice processing endpoints
│   │   ├── inventory.py      # CRUD inventory endpoints
│   │   ├── analytics.py      # Analytics endpoints
│   │   └── conversations.py  # Conversation history endpoints
│   ├── services/
│   │   ├── ai_service.py     # GPT-4 conversation + inventory reasoning
│   │   ├── speech_service.py # Whisper STT + OpenAI TTS
│   │   └── inventory_service.py # Business logic + seeding
│   └── utils/
│       ├── unit_converter.py # Unit normalization + conversion
│       └── categorizer.py    # Auto product categorization
│
└── frontend/                 # React + Vite + Tailwind
    └── src/
        ├── App.jsx            # Main layout + tabs
        ├── components/
        │   ├── VoiceAssistant.jsx   # Main chat + mic interface
        │   ├── InventoryTable.jsx   # Live filterable table
        │   ├── Analytics.jsx        # Stats + charts dashboard
        │   ├── ActivityFeed.jsx     # Real-time action log
        │   ├── ConversationHistory.jsx
        │   ├── WaveformAnimation.jsx
        │   └── Header.jsx
        ├── hooks/
        │   ├── useSpeechRecognition.js  # Web Speech API wrapper
        │   └── useWebSocket.js          # WebSocket client
        └── services/
            └── api.js          # Axios API client
```

---

## AI Features

| Feature | Implementation |
|---|---|
| Conversational memory | Last 12 messages sent with every GPT-4 call |
| Confirmation before save | `action: confirm` → user says yes → `action: update` |
| Unit validation | Checked against existing item's unit before confirm |
| Suspicious quantities | >10x existing value triggers flag |
| Auto-categorization | Keyword matching (200+ items) + GPT-4 fallback |
| Context awareness | "Add 10 more" uses previous item from history |
| Expiry tracking | Date stored per item, alert within 3 days |
| Multi-worker conflict | Real-time WebSocket broadcast to all sessions |

---

## Switching to PostgreSQL

In `.env`:
```
DATABASE_URL=postgresql://user:password@localhost:5432/inventory
```

Remove `connect_args` from `database.py` (already handled conditionally).

---

## Voice Input Notes

- **Browser STT**: Works in Chrome/Edge (Web Speech API, free)
- **Whisper STT**: Use `/api/voice/transcribe` endpoint with audio blob
- **TTS**: Browser `speechSynthesis` used by default (free, no API needed)
- **OpenAI TTS**: `/api/voice/synthesize` returns MP3 audio for higher quality
