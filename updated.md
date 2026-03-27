# AI Automator - CYBREIGN

Sends a prompt to Claude/ChatGPT via API, waits for the response, and saves it clean to a Notepad file automatically.

---

## Setup

```bash
pip install flask playwright
playwright install chromium
```

---

## Run

```bash
python ai_automator.py
```

---

## Test

```bash
curl -X POST http://localhost:8080/ask -H "Content-Type: application/json" -d "{\"prompt\": \"What is ethical hacking?\", \"target\": \"claude_web\"}"
```

---

## Targets

| Value | Opens |
|-------|-------|
| `claude_web` | claude.ai in Chromium |
| `chatgpt_web` | chatgpt.com in Chromium |

---

## Output

Responses saved to: `Desktop\AI_Responses\response_YYYYMMDD_HHMMSS.txt`  
File auto-opens in Notepad.

---

## Changelog

### v3 — Playwright Edition (current)
- **Replaced Selenium with Playwright** — Playwright owns the browser via Chrome DevTools Protocol (CDP), no profile conflicts, no shared-instance crashes
- **Replaced Ctrl+A clipboard copy with `inner_text()`** — extracts only the clean response text from the exact DOM element, no HTML tags, no timestamps, no button labels
- **Browser launches in ~2s** instead of minutes — no remote debug port setup needed
- **Uses your real Chrome profile** (`AppData/Local/Google/Chrome/User Data/Default`) so you're already logged into Claude/ChatGPT
- **Streaming detection** — watches for the Stop button to appear then disappear, so it grabs the response only after it's fully done
- Removed pyautogui and pyperclip entirely

### v2 — Selenium Edition
- Replaced pyautogui mouse control with Selenium WebDriver
- Used `--remote-debugging-port=9222` to attach to existing Chrome
- Grabbed response via CSS selector `div.font-claude-response`
- Problem: Chrome profile conflict caused "session not created" crash
- Problem: Slow startup (minutes) due to Chrome profile locking
- Problem: `Ctrl+A` + clipboard still used for some fallback paths

### v1 — pyautogui Edition
- Used pyautogui to move mouse and type into screen coordinates
- Pressed `/` to focus Claude input box, then Ctrl+A + Delete to clear it
- Used `Ctrl+A` on entire page to copy — grabbed everything including HTML, timestamps, nav buttons
- Problem: Clicked URL bar instead of chat input (wrong coordinates)
- Problem: Ctrl+A copied full page HTML junk into the output file
- Problem: Port 5000 blocked by Windows — added auto port fallback


### v4 — Playwright Fixed (current)
- **Root cause of "about:about"**: `launch_persistent_context` with your real Chrome profile was locking up and blocking `goto()` — browser opened but navigation never fired
- **Fix: dedicated automation profile** — created a separate folder `~/cybreign_browser_profile` just for this tool, completely isolated from your real Chrome, zero conflicts
- **Added `--setup` mode** — run once with `python ai_automator.py --setup` to log into Claude and ChatGPT manually; session cookies are saved to the profile and reused forever after
- **Added `delay=30` to `box.type()`** — types each character with a small delay so Claude's input doesn't drop characters
- **Added explicit waits** before every interaction — `wait_for_selector(..., state="visible")` before clicking, so the script never tries to type into an element that isn't ready yet
- **Improved streaming detection** — cleaner start/stop button watching with proper fallback if stop button never appears


### v5 — Stealth Edition (current)
- **Root cause of Cloudflare block**: Playwright's default Chromium exposes `navigator.webdriver = true` and dozens of other automation fingerprints — Cloudflare detects these instantly and shows the bot verification screen
- **Fix 1: `playwright-stealth`** — patches all JS automation signals (`navigator.webdriver`, `chrome.runtime`, `permissions`, `plugins`, `languages`, etc.) so the browser looks like a normal human Chrome session
- **Fix 2: Real Google Chrome** (`channel="chrome"`) instead of Playwright's bundled Chromium — real Chrome has a proper fingerprint that matches what Cloudflare expects
- **Fix 3: Spoofed User-Agent and Accept-Language headers** — matches what a real Windows Chrome 122 user sends
- **Added `wait_past_cloudflare()`** — actively polls page content for CF challenge strings and waits them out, then takes a debug screenshot if the real page never loads
- **Debug screenshot on failure** — if the expected element is never found, saves a PNG to `Desktop/AI_Responses/debug_screenshot.png` so you can see exactly what the browser is showing
- New dependency: `playwright-stealth` (`pip install playwright-stealth`)


### v5.1 — Response Completeness Fix (current)
- **Root cause of cutoff**: `div.font-claude-response` only contains the first paragraph block. Claude renders each paragraph into separate sibling divs, so grabbing just one div gave partial text
- **Fix 1: switched primary selector to `div[data-is-streaming="false"]`** — this is Claude's outermost message container that wraps the entire response, giving the full text in one `inner_text()` call
- **Fix 2: stable-text render wait** — instead of a fixed 1.2s buffer, now polls `body.inner_text()` length every 500ms until it stops changing, confirming the DOM is fully rendered before grabbing
- **Fix 3: short-text fallback** — if the grabbed text is under 100 chars (suspiciously short), walks up to the parent container and uses that instead
- **Behavior clarification**: the tool only copies the **last/newest response** in the conversation — it does NOT copy past conversation history, only the answer to the current prompt


### v6 — Thread-Safe + Tray Icon (current)
- **Root cause of "cannot switch to a different thread"**: Playwright's sync API is not thread-safe — Flask runs each request on a different thread, but Playwright requires all calls to happen on the same thread that created it. Calling browser methods from a Flask thread caused the crash
- **Fix: dedicated Playwright worker thread with a job queue** — Playwright now runs exclusively on one background thread. Flask threads send jobs via `queue.Queue()` and block-wait for the result. Zero thread conflicts
- **Browser stays alive between requests** — no more restarting or reconnecting between prompts, the same browser page is reused for every job
- **System tray icon** — green circle icon appears in Windows system tray showing the tool is running. Right-click menu has "Open Output Folder" and "Quit". Requires `pip install pystray pillow`
- **`_worker_ready` event** — Flask waits for the browser to be fully initialized before accepting the first request, preventing race conditions on startup
- New dependency: `pystray`, `pillow` (`pip install pystray pillow`)


### v7 — Robust Response Detection (current)
- **Root cause of early Notepad**: render stabilization was firing after Stop button disappeared but before Claude finished painting the last few DOM nodes — grabbed partial text
- **Fix: 4-stage completion pipeline**
  1. Wait for Stop button to appear (generation started)
  2. Wait for Stop button to disappear (generation finished)
  3. Wait for **Send button to reappear** (`button[aria-label="Send message"]`) — this is the definitive signal Claude is fully done and the UI is back to idle state, exactly as you identified from the page HTML
  4. Poll `body.inner_text()` length every 400ms until stable for 2 consecutive checks — confirms DOM fully rendered
- **Added typed text verification** — logs what was actually typed into the box before submitting, so you can confirm the prompt went in correctly
- **Three-layer response extraction fallback** — tries `data-is-streaming=false` container first, then walks up to parent div, then joins all `font-claude-response` divs as last resort
- **No user interaction required** — browser is fully controlled by Python; moving or resizing the browser window has no effect on the automation


### v8 — Disabled/Enabled Button Detection (current)
- **Root cause**: Previous code watched for Send button visibility (hidden/visible) but the Send button is ALWAYS present in Claude's DOM — it never hides. It only changes between `disabled=true` and `disabled=false`
- **Fix: `page.wait_for_function()` with JS** — polls `button.disabled` property directly in the browser's JS engine. Waits for `disabled=true` (generation started) then `disabled=false` (generation done). This is the most reliable signal possible
- **Artifact/Widget responses**: When Claude renders a visualize widget, `inner_text()` skips the iframe content but correctly captures all surrounding text. The "V visualize V visualize show_widget" text in previous output was the widget placeholder text leaking — now filtered by the 3-strategy extraction pipeline
- **3-strategy extraction**: `data-is-streaming=false` container → joined `font-claude-response` divs → JS `evaluate()` direct DOM read. Falls back gracefully and saves a debug screenshot if all fail
- **Stable render detection improved**: Now requires 3 consecutive identical length readings (up from 2) before confirming DOM is done


### v8.1 — Fast Polling Fix (current)
- **Root cause of 120s wait**: `page.wait_for_function()` uses browser-side JS polling which was not reliably detecting the `disabled` attribute change on the Send button — it kept timing out even after Claude finished
- **Fix: replaced with Python-side polling** — every 500ms, Python calls `btn.is_disabled()` directly via Playwright's element API. This is simpler, faster, and more reliable than injecting JS into the page
- **Result**: detects completion within ~1s of Claude finishing, no more 120s timeout waits


### v9 — Text-Growth Polling + Chat Reuse (current)
- **Root cause of slow detection**: `wait_for_function` polling `button.disabled` in React is unreliable — React batches state updates and the disabled property doesn't fire predictably on Playwright's JS polling interval
- **Fix: response text growth polling** — every 400ms, reads `innerText` of the last `div[data-is-streaming]` block via JS. When text hasn't changed for 3 full seconds, response is confirmed complete. Works for any response length, any prompt type, regardless of what buttons Claude shows
- **Chat reuse** — tracks `_claude_msg_count`. Same chat tab is reused for every request. Only opens a new chat after `MAX_MSG_PER_CHAT` (default 20) messages, or on first run. No more `claude.ai/new` every time
- **Pre-submit block count** — counts existing `data-is-streaming` divs before submitting, then watches for a NEW one to appear. This separates "Claude started responding to THIS prompt" from previous messages in the chat history
- **Stable timer reset** — every time text changes, the 3-second stability timer resets from zero. Claude finishing a sentence but pausing briefly won't trigger a false early grab