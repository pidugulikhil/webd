curl -s -X POST http://localhost:11434/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Hello\",\"stream\":true}"
curl -s -X POST http://localhost:11434/api/generate -H "Content-Type: application/json" -d "{\"model\":\"chatgpt\",\"prompt\":\"Hello\",\"stream\":false}"
