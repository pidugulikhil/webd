# AI Automator v10 - CYBREIGN
### Free OpenAI-Compatible API powered by Claude.ai + ChatGPT

---

## Install

```bash
pip install flask playwright playwright-stealth pystray pillow
playwright install
```

---

## First Time Setup (login once)

```bash
python ai_automator.py --setup
```

Log into Claude → press Enter → log into ChatGPT → press Enter. Done forever.

---

## Run

```bash
python ai_automator.py
```

---

## OpenClaw Config

| Field    | Value                        |
|----------|------------------------------|
| Base URL | `http://localhost:8080/v1`   |
| API Key  | `cybreign` (any string)      |
| Model    | `claude` or `chatgpt`        |

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | OpenAI-compatible (use with OpenClaw) |
| GET  | `/v1/models` | List available models |
| POST | `/ask` | Simple direct ask |
| GET  | `/sessions` | View all session state |
| POST | `/sessions/reset` | Reset all sessions |
| POST | `/sessions/claude-limit` | Manually mark Claude limit |

---

## Test — Direct ask

```bash
curl -X POST http://localhost:8080/ask -H "Content-Type: application/json" -d "{\"prompt\": \"What is ethical hacking?\", \"target\": \"claude_web\"}"
```

## Test — OpenAI format (non-streaming)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -d "{\"model\": \"claude\", \"messages\": [{\"role\": \"user\", \"content\": \"What is ethical hacking?\"}]}"
```

## Test — OpenAI format (streaming)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -d "{\"model\": \"claude\", \"messages\": [{\"role\": \"user\", \"content\": \"What is SQL injection?\"}], \"stream\": true}"
```

---

## Session File

Stored at: `~/cybreign_sessions.json`

```json
{
  "sessions": [
    {"index": 0, "provider": "claude",  "url": "https://claude.ai/chat/xxx", "status": "active", "msg_count": 5},
    {"index": 1, "provider": "chatgpt", "url": "https://chatgpt.com/c/xxx",  "status": "full",   "msg_count": 20}
  ],
  "claude_daily_limit": false,
  "active_claude_index": 0,
  "active_chatgpt_index": null
}
```

- `active` = currently in use
- `full` = hit message limit, archived
- On restart, always resumes from the `active` session URL
- After `MAX_MSG_PER_CHAT` (default 20) messages, opens new chat and saves new URL
- If Claude hits daily limit, automatically falls back to ChatGPT

---

## Tray Icon

Green circle in Windows system tray (bottom-right). Right-click for:
- Open Output Folder
- View Sessions JSON
- Quit

---

## Changelog

### v10 — OpenAI-Compatible API + Session Persistence (current)
- **Session persistence**: Chat URLs saved to `~/cybreign_sessions.json`. On restart, resumes from the same chat — no memory loss, no history gap
- **Smart session management**: tracks message count per chat. After `MAX_MSG_PER_CHAT` messages, marks session as `full` and opens a new chat, saving its URL
- **Auto fallback**: if Claude daily limit is detected (via page content check), automatically switches to ChatGPT. If Claude limit clears next day, resumes Claude
- **OpenAI-compatible API**: `POST /v1/chat/completions` accepts exact same format as OpenAI — works directly with OpenClaw, any OpenAI SDK, or curl
- **Streaming (SSE)**: when `stream: true`, pushes response text word-by-word as Server-Sent Events in OpenAI delta format. OpenClaw receives tokens as they appear
- **`/v1/models` endpoint**: returns model list so OpenClaw can discover available models
- **No Notepad saving** — responses returned directly via API (removed file saving from main flow)
- **Tray: View Sessions JSON** — new menu item to inspect session state

### v9 — Text-Growth Polling + Chat Reuse
- Text-growth stability detection (3s stable = done)
- Chat tab reused across requests
- Pre-submit block count to isolate new response

### v8 — Disabled/Enabled Button Detection
- JS-based button disabled polling
- 3-strategy response extraction

### v7 — Robust Response Detection
- 4-stage pipeline: stop appear → stop gone → send reappear → DOM stable

### v6 — Thread-Safe + Tray Icon
- Playwright worker thread with job queue
- System tray icon

### v5 — Stealth Edition
- playwright-stealth to bypass Cloudflare bot detection
- Real Chrome channel

### v4 — Playwright Fixed
- Dedicated profile, no Chrome conflict
- --setup login mode

### v3 — Playwright Edition
- Replaced Selenium with Playwright

### v2 — Selenium Edition
- Replaced pyautogui with Selenium

### v1 — pyautogui Edition
- Screen coordinate mouse control
- Ctrl+A clipboard copy (grabbed full page junk)