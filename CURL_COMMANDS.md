# WEBD API — All curl Commands
### github.com/pidugulikhil/webd

Base URL: `http://localhost:8080`

---

## 1. Health Check

```bash
curl http://localhost:8080/
```

---

## 2. List Models

```bash
curl http://localhost:8080/v1/models
```

---

## 3. Simple Ask (no stream)

### Claude
```bash
curl -X POST http://localhost:8080/ask -H "Content-Type: application/json" -d "{\"prompt\": \"What is ethical hacking?\", \"target\": \"claude_web\"}"
```

### ChatGPT
```bash
curl -X POST http://localhost:8080/ask -H "Content-Type: application/json" -d "{\"prompt\": \"What is ethical hacking?\", \"target\": \"chatgpt_web\"}"
```

---

## 4. OpenAI Format — No Stream (Claude)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -d "{\"model\": \"claude\", \"messages\": [{\"role\": \"user\", \"content\": \"What is ethical hacking?\"}], \"stream\": false}"
```

---

## 5. OpenAI Format — No Stream (ChatGPT)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -d "{\"model\": \"chatgpt\", \"messages\": [{\"role\": \"user\", \"content\": \"What is ethical hacking?\"}], \"stream\": false}"
```

---

## 6. OpenAI Format — WITH Stream (Claude)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -N -d "{\"model\": \"claude\", \"messages\": [{\"role\": \"user\", \"content\": \"What is ethical hacking?\"}], \"stream\": true}"
```

---

## 7. OpenAI Format — WITH Stream (ChatGPT)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -N -d "{\"model\": \"chatgpt\", \"messages\": [{\"role\": \"user\", \"content\": \"What is ethical hacking?\"}], \"stream\": true}"
```

---

## 8. Multi-turn Conversation (with history)

```bash
curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer cybreign" -d "{\"model\": \"claude\", \"messages\": [{\"role\": \"user\", \"content\": \"My name is Likhil\"}, {\"role\": \"assistant\", \"content\": \"Nice to meet you Likhil!\"}, {\"role\": \"user\", \"content\": \"What is my name?\"}]}"
```

---

## 9. View Sessions

```bash
curl http://localhost:8080/sessions
```

---

## 10. Reset All Sessions

```bash
curl -X POST http://localhost:8080/sessions/reset
```

---

## 11. Mark Claude Daily Limit (manual override)

```bash
curl -X POST http://localhost:8080/sessions/claude-limit -H "Content-Type: application/json" -d "{\"hit\": true}"
```

## 12. Clear Claude Daily Limit

```bash
curl -X POST http://localhost:8080/sessions/claude-limit -H "Content-Type: application/json" -d "{\"hit\": false}"
```

---

## 13. Check Server Status

```bash
curl http://localhost:8080/status
```

---

## Notes

- `-N` flag in streaming curl = no buffering (required to see tokens live)
- `Authorization: Bearer cybreign` — any string works, not validated
- Model names: `claude`, `claude_web`, `chatgpt`, `chatgpt_web` all accepted
- Stream response format is OpenAI SSE: `data: {...}\n\n` then `data: [DONE]\n\n`
