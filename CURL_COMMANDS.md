# WEBD API Commands

Default ports:

- Ollama API: http://localhost:50000
- OpenAI API: http://localhost:50001

If busy, WEBD auto-increments to next free port.

## A) 10-Second Health Check

```bash
curl http://localhost:50000/api/version
curl http://localhost:50000/api/tags
curl http://localhost:50001/v1/models
```

## B) Model Routing Quick Test

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"say CHATGPT_OK only\",\"stream\":false}"
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"claude\",\"prompt\":\"say CLAUDE_OK only\",\"stream\":false}"
```

## C) Streaming Vs Non-Streaming

Non-stream:

```bash
curl -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"hello\",\"stream\":false}"
```

Stream:

```bash
curl -N -X POST http://localhost:50000/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"hello\",\"stream\":true}"
```

## D) OpenAI-Style Endpoint

Stream false:

```bash
curl -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}],\"stream\":false}"
```

Stream true:

```bash
curl -N -X POST http://localhost:50001/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer any-key" -d "{\"model\":\"chatgpt\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}],\"stream\":true}"
```

## E) PowerShell Safe Commands

```powershell
$body = @{ model='chatgpt'; prompt='Hello'; stream=$true } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://localhost:50000/api/generate' -Method Post -ContentType 'application/json' -Body $body

$body2 = @{
  model='chatgpt'
  messages=@(@{ role='user'; content='hello from powershell' })
  stream=$true
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri 'http://localhost:50001/v1/chat/completions' -Method Post -ContentType 'application/json' -Headers @{ Authorization='Bearer any-key' } -Body $body2
```

## F) Session Management

```bash
curl http://localhost:50000/sessions
curl -X POST http://localhost:50000/sessions/reset
curl -X POST http://localhost:50000/sessions/claude-limit -H "Content-Type: application/json" -d "{\"hit\":true}"
curl -X POST http://localhost:50000/sessions/claude-limit -H "Content-Type: application/json" -d "{\"hit\":false}"
```

## G) Common Fixes

1. If OpenClaw waits for full response:
  turn ON streaming in OpenClaw settings.
2. If JSON body errors occur:
  re-check quote escaping.
3. If ports changed:
  use the ports printed by WEBD at startup.
4. If message vanished and no send happened:
  WEBD now returns explicit send-failed error. Retry same prompt once.