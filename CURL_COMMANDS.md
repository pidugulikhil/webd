# WEBD API Commands

**WEBD v1.12 — Complete API Reference**

Default ports: Ollama type parameters `50000` | OpenAI type parameters `50001` (auto-increments if busy)

---

## 🔥 Quick Health Check

```bash
curl http://localhost:50000/api/version
curl http://localhost:50000/api/tags
curl http://localhost:50001/v1/models
curl http://localhost:50000/sessions
```

---

## 🤖 Ollama `/api/generate` — Single Prompt

### Non-streaming (wait for full response)

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Say hello in 5 words\",\"stream\":false}"
```

### Streaming (real-time tokens)

```bash
curl -N -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Count to 5 slowly\",\"stream\":true}"
```

### With system prompt

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"claude\",\"prompt\":\"What is 2+2?\",\"system\":\"You are a math tutor. Only give the answer.\",\"stream\":false}"
```

### Stateless mode (force new chat, no session reuse)

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Remember my name: John\",\"stateless\":true,\"stream\":false}"
```

---

## 💬 Ollama `/api/chat` — Multi-turn Messages

### Simple user message

```bash
curl -X POST http://localhost:50000/api/chat -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":\"Tell me a short joke\"}],\"stream\":false}"
```

### Full conversation (system + user + assistant history)

```bash
curl -X POST http://localhost:50000/api/chat -H "Content-Type: application/json" -d "{\"model\":\"claude\",\"messages\":[{\"role\":\"system\",\"content\":\"You are a pirate\"},{\"role\":\"user\",\"content\":\"Say hello\"},{\"role\":\"assistant\",\"content\":\"Arr, ahoy matey!\"},{\"role\":\"user\",\"content\":\"Now say goodbye\"}],\"stream\":false}"
```

### Streaming chat

```bash
curl -N -X POST http://localhost:50000/api/chat -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":\"Write a haiku about coding\"}],\"stream\":true}"
```

---

## 🧠 Model Show / Info

```bash
curl -X POST http://localhost:50000/api/show -H "Content-Type: application/json" -d "{\"name\":\"chatgpt\"}"
curl -X POST http://localhost:50000/api/show -H "Content-Type: application/json" -d "{\"name\":\"claude\"}"
```

---

## 📥 Pull / Delete / Copy (stubs — always succeed)

```bash
curl -X POST http://localhost:50000/api/pull -H "Content-Type: application/json" -d "{\"name\":\"chatgpt:latest\"}"
curl -X DELETE http://localhost:50000/api/delete -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\"}"
curl -X POST http://localhost:50000/api/copy -H "Content-Type: application/json" -d "{\"source\":\"chatgpt\",\"destination\":\"mygpt\"}"
```

---

## 🔢 Embeddings (zero-vector stub)

```bash
curl -X POST http://localhost:50000/api/embeddings -H "Content-Type: application/json" -d "{\"model\":\"nomic-embed-text\",\"prompt\":\"test sentence\"}"
```

---

## 🚀 OpenAI-Compatible `/v1/chat/completions` (port 50001)

### Non-streaming

```bash
curl -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":\"What is Python?\"}],\"stream\":false}"
```

### Streaming

```bash
curl -N -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"claude\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain APIs in one sentence\"}],\"stream\":true}"
```

### Multi-turn conversation

```bash
curl -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"system\",\"content\":\"You are helpful\"},{\"role\":\"user\",\"content\":\"Hi\"},{\"role\":\"assistant\",\"content\":\"Hello!\"},{\"role\":\"user\",\"content\":\"How are you?\"}],\"stream\":false}"
```

### With image URL hint (text + image reference)

```bash
curl -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Describe this image\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"https://example.com/cat.jpg\"}}]}],\"stream\":false}"
```

---

## 🎯 Direct `/ask` endpoint (port 50001)

```bash
curl -X POST http://localhost:50001/ask -H "Content-Type: application/json" -d "{\"prompt\":\"What is 42 times 7?\",\"target\":\"chatgpt\"}"
curl -X POST http://localhost:50001/ask -H "Content-Type: application/json" -d "{\"prompt\":\"Explain quantum computing\",\"target\":\"claude\"}"
```

---

## 📋 Session Management

### View all sessions

```bash
curl http://localhost:50000/sessions
curl http://localhost:50001/sessions
```

### Reset all sessions (clears chat history)

```bash
curl -X POST http://localhost:50000/sessions/reset
curl -X POST http://localhost:50001/sessions/reset
```

### Manually set Claude daily limit (simulate rate limit)

```bash
curl -X POST http://localhost:50000/sessions/claude-limit -H "Content-Type: application/json" -d "{\"hit\":true}"
curl -X POST http://localhost:50000/sessions/claude-limit -H "Content-Type: application/json" -d "{\"hit\":false}"
```

---

## 🪟 PowerShell Commands (Windows)

```powershell
$body = @{ model='chatgpt'; prompt='Hello from PowerShell'; stream=$false } | ConvertTo-Json; Invoke-RestMethod -Uri 'http://localhost:50000/api/generate' -Method Post -ContentType 'application/json' -Body $body
```

```powershell
$body = @{ model='claude'; messages=@(@{ role='user'; content='Say hi' }); stream=$false } | ConvertTo-Json -Depth 8; Invoke-RestMethod -Uri 'http://localhost:50000/api/chat' -Method Post -ContentType 'application/json' -Body $body
```

```powershell
$body = @{ model='chatgpt'; messages=@(@{ role='user'; content='Hello OpenAI style' }); stream=$false } | ConvertTo-Json -Depth 8; Invoke-RestMethod -Uri 'http://localhost:50001/v1/chat/completions' -Method Post -ContentType 'application/json' -Headers @{ Authorization='Bearer any-key' } -Body $body
```

```powershell
Invoke-RestMethod -Uri 'http://localhost:50000/sessions' -Method Get
```

---

## 🧪 Model Routing Test (all providers)

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Who made you?\",\"stream\":false}"
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"gpt-4\",\"prompt\":\"Who made you?\",\"stream\":false}"
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"claude\",\"prompt\":\"Who made you?\",\"stream\":false}"
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"llama3\",\"prompt\":\"Who made you?\",\"stream\":false}"
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"deepseek\",\"prompt\":\"Who made you?\",\"stream\":false}"
```

---

## 🛠️ Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBD_OLLAMA_PORT` | 50000 | Ollama API port |
| `WEBD_OPENAI_PORT` | 50001 | OpenAI API port |
| `WEBD_BASE_PORT` | (none) | Set both ports sequentially (base, base+1) |
| `WEBD_PROVIDER_POLICY` | chatgpt_default | `chatgpt_only` / `chatgpt_default` |
| `WEBD_GENERATE_STATELESS` | 0 | `1` = new chat per generate |
| `WEBD_CHAT_PROMPT_MODE` | latest_user | `full` = send all history |
| `WEBD_ENABLE_LOCAL_ACTIONS` | 0 | `1` = enable calc/search/browser actions |
| `WEBD_USE_WAITRESS` | 1 | `0` = Flask dev server |
| `WEBD_WAITRESS_THREADS` | 32 | Threads for Waitress |

---

## ✅ Common Fixes

1. **OpenClaw waits for full response** → Enable streaming in OpenClaw settings
2. **JSON body errors** → Check quote escaping; use double quotes inside
3. **Ports changed** → Check WEBD startup output for actual ports
4. **Message didn't send** → WEBD returns explicit `[WEBD_SUBMIT_FAILED]`; retry once
5. **Claude daily limit hit** → WEBD auto-falls back to ChatGPT with notice
6. **Browser tab closed** → WEBD auto-detects and opens fresh chat
7. **Wrong model routing** → Use `claude` or `chatgpt` explicitly in model field
