# WEBD Release Notes

## Current Stable Direction

WEBD is now organized for beginner usage + developer reliability:

- Default professional ports: 50000 and 50001
- Auto-increment to next free port if busy
- Claude-first when Claude model is selected
- Automatic fallback to ChatGPT only when Claude limit is actually detected

## Latest Reliability Fixes

### 1) ChatGPT send-vanish fix

- Added submit confirmation checks for ChatGPT
- Added alternate send retry path
- If send still fails, WEBD returns explicit send-failed message

### 2) Stale/old response confusion reduction

- Better stream delta handling when UI rewrites partial text
- Safer behavior to avoid reusing previous response text patterns

### 3) OpenClaw multiline payload stability

- Improved prompt sanitation to keep meaningful multiline user content
- Removed accidental fence/backtick artifacts that could distort prompts

### 4) Claude routing correctness

- Explicit Claude model now routes to Claude first
- Claude limit fallback now includes a visible WEBD NOTICE
- Stale limit state auto-clears when Claude answers normally again

## Docs And Project Structure Refresh

- README redesigned for one-look beginner understanding
- Command guide rewritten with stream/non-stream examples
- Assets folder added for screenshots/videos/logo placeholders

## Standard Runtime Defaults

- Ollama-compatible default: 50000
- OpenAI-compatible default: 50001
- WEBD_PROVIDER_POLICY=chatgpt_default
- WEBD_GENERATE_STATELESS=0
- WEBD_ENABLE_LOCAL_ACTIONS=0

## Known Limits

- Full binary image-upload passthrough to browser chat UI is still pending
- Provider UI selector updates may require maintenance over time

## Next Recommended Milestones

1. Add automated end-to-end smoke tests for ChatGPT and Claude model switching.
2. Add optional provider_used field in API responses for easier debugging.
3. Add packaged release binary workflow in GitHub Actions.