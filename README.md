<p align="center">
    <img src="https://capsule-render.vercel.app/api?type=blur&height=350&color=gradient&text=WEBD&section=header&reversal=true&textBg=false&fontColor=ffffff&fontSize=80&animation=twinkling&stroke=000000&strokeWidth=1&desc=-nl-Web%20Daemon-nl-LLM's%20API%20GATEWAY" alt="WEBD" />
</p>

<p align="center">
    <img src="https://img.shields.io/badge/WEBD-v12-00B894?style=for-the-badge&logo=github&logoColor=white" alt="WEBD v12" />
    <img src="https://img.shields.io/badge/ChatGPT-Claude_Bridge-10a37f?style=for-the-badge&logo=openai&logoColor=white" alt="ChatGPT Bridge" />
    <img src="https://img.shields.io/badge/Ollama-ChatGPT_API-000000?style=for-the-badge&logo=ollama&logoColor=white" alt="Ollama API" />
    <img src="https://img.shields.io/badge/OpenAI-Compatible-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI Compatible" />
</p>

<p align="center">
    <img src="https://readme-typing-svg.herokuapp.com?font=Fira+Code&weight=700&size=30&duration=2500&pause=700&color=00B894&center=true&vCenter=true&width=980&lines=Local+AI+API+Gateway;Flowing+ChatGPT+%2B+Claude+Bridge;Stable+Session+Memory+Across+Switches;Auto-Detects+Closed+Tabs;Just+Works+Out+of+the+Box" alt="Animated WEBD Banner" />
</p>

<p align="center">
    <img src="https://img.shields.io/badge/Platform-Windows-Linux-0078D6?style=flat-square&logo=windows&logoColor=white" />
    <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" />
    <img src="https://img.shields.io/badge/License-GPL_3.0-blue?style=flat-square&logo=gnu&logoColor=white" />
    <img src="https://img.shields.io/badge/Devs-Welcome-brightgreen?style=flat-square&logo=github&logoColor=white" />
</p>

---

## 🎯 **What is WEBD in ONE sentence?**

> **WEBD lets you talk to ChatGPT and Claude through ANY app that supports Ollama or OpenAI type APIs — without API keys, without monthly fees, just your browser logged in once.**

---

## ⚡ **15-Second Pitch**

OpenClaw, Continue, Cline, or any Ollama/OpenAI client connects to `http://localhost:50000`. WEBD opens Chrome, navigates to ChatGPT or Claude, types your prompt, captures the response, and sends it back — **all automatically**. It remembers your chats, reuses tabs, auto-recovers when you close a tab, and falls back to ChatGPT if Claude hits daily limits.

**No API keys. No cloud costs. Just local AI.**

---

## 🧠 **What WEBD Does (Simple Version)**

| # | What |
|---|------|
| 1 | Starts two API servers on your computer |
| 2 | Ollama API → port `50000` (for OpenClaw, Continue, Ollama clients) |
| 3 | OpenAI API → port `50001` (for OpenAI-compatible clients) |
| 4 | Opens real Chrome browser (you see it typing!) |
| 5 | Routes `chatgpt` → ChatGPT.com, `claude` → Claude.ai |
| 6 | Remembers your conversation (reuses same chat tab) |
| 7 | Auto-detects closed tabs and opens fresh ones |
| 8 | Falls back to ChatGPT if Claude says "daily limit reached" |

---

## 🧪 **See It in Action**

```bash
# One command to test ChatGPT
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Say hello in 3 words\",\"stream\":false}"

# One command to test Claude
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"claude\",\"prompt\":\"What is 2+2?\",\"stream\":false}"
```

**Expected output:** Your browser opens, types the prompt, and returns the AI's response in your terminal.

---

## 🚀 **Setup (Takes 2 Minutes)**

### Step 1: Install

```bash
pip install flask playwright playwright-stealth pystray pillow
playwright install
```

### Step 2: Login Once (Saves Forever)

```bash
python webd.py --setup
```
- Chrome opens to Claude.ai → login
- Then ChatGPT.com → login
- Press ENTER after each login

### Step 3: Run

```bash
python webd.py
```

**Done!** Your API gateway is running at:
- Ollama API: `http://localhost:50000`
- OpenAI API: `http://localhost:50001`

---

## 🔌 **OpenClaw Setup (Screenshot-Ready)**

| Setting | Value |
|---------|-------|
| Provider | Ollama |
| Base URL | `http://localhost:50000` |
| Model | `chatgpt` or `claude` |
| Streaming | ON (recommended) |

**That's it.** OpenClaw now talks to real ChatGPT/Claude through WEBD.

---

## 🧩 **Model Routing (Simple Table)**

| You type in model field | WEBD sends to |
|------------------------|---------------|
| `chatgpt`, `gpt-4`, `gpt-3.5` | ChatGPT.com |
| `claude`, `llama3`, `mistral`, `deepseek`, `qwen` | Claude.ai |
| anything else | ChatGPT.com (default) |

---

## 🩺 **Health Check (10 Seconds)**

```bash
curl http://localhost:50000/api/version
curl http://localhost:50000/api/tags
curl http://localhost:50001/v1/models
curl http://localhost:50000/sessions
```

---

## 📊 **Stability Score (What WEBD Focuses On)**

```mermaid
pie title WEBD v12 Engineering Focus
    "Session Reuse & Recovery" : 38
    "API Compatibility" : 28
    "Streaming Integrity" : 22
    "Prompt Sanitization" : 12
```

---

## 🎮 **All Available Endpoints (Quick Map)**

| Endpoint | Port | What it does |
|----------|------|---------------|
| `/api/generate` | 50000 | Single prompt → response |
| `/api/chat` | 50000 | Multi-turn conversation |
| `/api/tags` | 50000 | List available models |
| `/api/version` | 50000 | Version check |
| `/api/show` | 50000 | Model info |
| `/api/embeddings` | 50000 | Stub (returns zeros) |
| `/v1/chat/completions` | 50001 | OpenAI-compatible |
| `/v1/models` | 50001 | List OpenAI models |
| `/ask` | 50001 | Simple prompt endpoint |
| `/sessions` | Both | View active sessions |
| `/sessions/reset` | Both | Clear all chat history |

---

## 🧪 **Full API Examples**

### Ollama `/api/generate` (non-streaming)

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Explain APIs like I'm 10\",\"stream\":false}"
```

### Ollama `/api/generate` (streaming)

```bash
curl -N -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Count to 10\",\"stream\":true}"
```

### Ollama `/api/chat` (conversation)

```bash
curl -X POST http://localhost:50000/api/chat -H "Content-Type: application/json" -d "{\"model\":\"claude\",\"messages\":[{\"role\":\"user\",\"content\":\"My name is Alex\"},{\"role\":\"assistant\",\"content\":\"Hi Alex!\"},{\"role\":\"user\",\"content\":\"What's my name?\"}],\"stream\":false}"
```

### OpenAI `/v1/chat/completions`

```bash
curl -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi there\"}],\"stream\":false}"
```

---

## 🪟 **PowerShell Commands (Windows)**

```powershell
$body = @{ model='chatgpt'; prompt='Hello from PowerShell'; stream=$false } | ConvertTo-Json; Invoke-RestMethod -Uri 'http://localhost:50000/api/generate' -Method Post -ContentType 'application/json' -Body $body
```

---

## 🛠️ **Environment Variables (Tweak Behavior)**

| Variable | Default | What it does |
|----------|---------|---------------|
| `WEBD_OLLAMA_PORT` | 50000 | Change Ollama API port |
| `WEBD_OPENAI_PORT` | 50001 | Change OpenAI API port |
| `WEBD_PROVIDER_POLICY` | chatgpt_default | `chatgpt_only` = force all to ChatGPT |
| `WEBD_GENERATE_STATELESS` | 0 | `1` = new chat for every request |
| `WEBD_CHAT_PROMPT_MODE` | latest_user | `full` = send entire history |
| `WEBD_ENABLE_LOCAL_ACTIONS` | 0 | `1` = enable calculator/browser actions |

**Usage:** `set WEBD_OLLAMA_PORT=50050` (Windows) or `export WEBD_OLLAMA_PORT=50050` (Linux/Mac)

---

## 🐛 **Common Problems & Fixes**

| Problem | Fix |
|---------|-----|
| OpenClaw waits forever | Turn ON streaming in OpenClaw settings |
| "No user message found" | Check your prompt has text, not just images |
| Claude says "daily limit" | WEBD auto-falls back to ChatGPT + shows notice |
| Browser tab closed by accident | WEBD auto-detects and opens fresh tab |
| Port 50000 already in use | WEBD auto-increments to 50001, 50002... |
| Message didn't send | WEBD returns `[WEBD_SUBMIT_FAILED]` — retry once |

---

## 📁 **Files WEBD Creates**

| Path | What |
|------|------|
| `~/cybreign_browser_profile/` | Your Chrome profile (stays logged in) |
| `~/Desktop/AI_Responses/` | Screenshots and debug output |
| `~/cybreign_sessions.json` | Active session tracking |

---

## 🧑‍💻 **For Developers**

```python
# Python example using requests
import requests

response = requests.post(
    "http://localhost:50000/api/generate",
    json={"model": "chatgpt", "prompt": "Hello", "stream": False}
)
print(response.json()["response"])
```

```javascript
// JavaScript example
fetch("http://localhost:50000/api/generate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({model: "chatgpt", prompt: "Hello", stream: false})
})
.then(res => res.json())
.then(data => console.log(data.response));
```

---

## 🎨 **Architecture (How It Works)**

```mermaid
flowchart TB
    Client[OpenClaw / Continue / curl] -->|Ollama API :50000| WEBD
    Client -->|OpenAI API :50001| WEBD
    
    WEBD --> Router{Model Router}
    Router -->|chatgpt, gpt-*| ChatGPT[ChatGPT.com]
    Router -->|claude, llama*, mistral| Claude[Claude.ai]
    
    ChatGPT --> Browser[Playwright Chrome]
    Claude --> Browser
    
    Browser --> Session[Session Manager]
    Session -->|Reuses same tab| Client
```

---

## 📦 **Requirements**

| Requirement | Minimum |
|-------------|---------|
| OS | Windows 10/11 |
| Python | 3.10+ |
| Browser | Chrome (installed) |
| RAM | 512MB free |
| Internet | Yes (to reach ChatGPT/Claude) |

---

## 🤝 **Contributing**

PRs welcome! Areas to help:
- Add Gemini, Perplexity, or other providers
- Linux/macOS support
- Docker container
- Binary executable builds

---

## 📄 **License**

**GPL-3.0** — Free to use, modify, and distribute.

---

## 🌟 **Star History**

[![Star History Chart](https://api.star-history.com/svg?repos=cybreign/WEBD&type=Date)](https://star-history.com/#cybreign/WEBD&Date)

---

<p align="center">
    <img src="https://capsule-render.vercel.app/api?type=waving&height=150&color=gradient&section=footer&text=Made%20with%20%E2%9D%A4%20by%20CYBREIGN&fontSize=20&fontColor=ffffff" />
</p>

<p align="center">
    <b>WEBD v12 — The simplest way to add ChatGPT + Claude to any local AI app</b><br>
    <sub>No API keys. No subscriptions. Just your browser.</sub>
</p>
