"""
AI Automator v10 - CYBREIGN
============================
Acts as a real streaming LLM API (OpenAI-compatible format).
Compatible with OpenClaw and any OpenAI SDK client.

Features:
- Persistent chat session management (JSON-based, survives restarts)
- Auto-switches chat when limit reached
- Falls back Claude -> ChatGPT if Claude daily limit hit
- Streaming responses (SSE) like a real API
- OpenAI-compatible endpoints (/v1/chat/completions)

Install:
  pip install flask playwright playwright-stealth pystray pillow
  playwright install

First time:
  python ai_automator.py --setup

Run:
  python ai_automator.py

OpenClaw config:
  Base URL : http://localhost:8080/v1
  API Key  : cybreign (any string, not validated)
  Model    : claude or chatgpt
"""

import os, sys, json, time, uuid, socket, queue, threading, subprocess
from datetime import datetime
from flask import Flask, request, jsonify, Response, stream_with_context

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("[!] pip install playwright && playwright install"); sys.exit(1)

try:
    from playwright_stealth import stealth_sync; HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

try:
    import pystray; from PIL import Image, ImageDraw; HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ─────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────
PROFILE_DIR      = os.path.join(os.path.expanduser("~"), "cybreign_browser_profile")
OUTPUT_FOLDER    = os.path.join(os.path.expanduser("~"), "Desktop", "AI_Responses")
SESSION_FILE     = os.path.join(os.path.expanduser("~"), "cybreign_sessions.json")
MAX_MSG_PER_CHAT = 20      # messages before opening new chat
STABLE_SEC       = 3.0     # seconds of no text growth = done
POLL_MS          = 350     # polling interval ms
MAX_WAIT_SEC     = 120     # max wait for response

for d in [OUTPUT_FOLDER, PROFILE_DIR]: os.makedirs(d, exist_ok=True)

app        = Flask(__name__)
_job_queue = queue.Queue()
_ready     = threading.Event()

# ─────────────────────────────────────────────────────
# SESSION MANAGER
# Persists chat URLs to JSON so restarts reuse same chats
#
# Structure:
# {
#   "sessions": [
#     {"index": 0, "provider": "claude",  "url": "https://claude.ai/chat/xxx", "status": "active", "msg_count": 5},
#     {"index": 1, "provider": "chatgpt", "url": "https://chatgpt.com/c/xxx",  "status": "full",   "msg_count": 20},
#     ...
#   ],
#   "claude_daily_limit": false,
#   "active_claude_index": 0,
#   "active_chatgpt_index": null
# }
# ─────────────────────────────────────────────────────
def load_sessions() -> dict:
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "sessions": [],
        "claude_daily_limit": False,
        "active_claude_index": None,
        "active_chatgpt_index": None
    }


def save_sessions(data: dict):
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[+] Sessions saved to {SESSION_FILE}")


def get_active_session(provider: str) -> dict | None:
    data = load_sessions()
    key  = f"active_{provider}_index"
    idx  = data.get(key)
    if idx is None:
        return None
    sessions = data["sessions"]
    matches  = [s for s in sessions if s["index"] == idx and s["provider"] == provider]
    return matches[0] if matches else None


def mark_session_full(provider: str):
    data = load_sessions()
    key  = f"active_{provider}_index"
    idx  = data.get(key)
    if idx is not None:
        for s in data["sessions"]:
            if s["index"] == idx and s["provider"] == provider:
                s["status"] = "full"
                print(f"[*] Marked session #{idx} ({provider}) as full")
    save_sessions(data)


def register_new_session(provider: str, url: str) -> dict:
    data     = load_sessions()
    new_idx  = max((s["index"] for s in data["sessions"]), default=-1) + 1
    session  = {
        "index":     new_idx,
        "provider":  provider,
        "url":       url,
        "status":    "active",
        "msg_count": 0,
        "created":   datetime.now().isoformat()
    }
    data["sessions"].append(session)
    data[f"active_{provider}_index"] = new_idx
    save_sessions(data)
    print(f"[+] Registered new {provider} session #{new_idx}: {url}")
    return session


def increment_msg_count(provider: str) -> int:
    data = load_sessions()
    key  = f"active_{provider}_index"
    idx  = data.get(key)
    count = 0
    if idx is not None:
        for s in data["sessions"]:
            if s["index"] == idx and s["provider"] == provider:
                s["msg_count"] = s.get("msg_count", 0) + 1
                count = s["msg_count"]
    save_sessions(data)
    return count


def set_claude_daily_limit(hit: bool):
    data = load_sessions()
    data["claude_daily_limit"] = hit
    save_sessions(data)


def claude_daily_limit_hit() -> bool:
    return load_sessions().get("claude_daily_limit", False)


# ─────────────────────────────────────────────────────
# PLAYWRIGHT WORKER
# ─────────────────────────────────────────────────────
def playwright_worker():
    pw      = sync_playwright().start()
    browser = pw.chromium.launch_persistent_context(
        user_data_dir       = PROFILE_DIR,
        channel             = "chrome",
        headless            = False,
        no_viewport         = True,
        args                = ["--start-maximized", "--disable-blink-features=AutomationControlled", "--disable-infobars"],
        ignore_https_errors = True,
    )
    pages = browser.pages
    page  = pages[0] if pages else browser.new_page()
    if HAS_STEALTH: stealth_sync(page)
    page.set_extra_http_headers({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    print("[+] Browser ready")
    _ready.set()

    while True:
        job = _job_queue.get()
        if job is None: break
        fn, args, box = job
        try:    box["result"] = fn(page, *args)
        except Exception as e: box["error"] = str(e); print(f"[ERROR] {e}")
        finally: box["done"].set()

    browser.close(); pw.stop()


def run_in_browser(fn, *args):
    box = {"done": threading.Event()}
    _job_queue.put((fn, args, box))
    box["done"].wait(timeout=180)
    if "error" in box: raise Exception(box["error"])
    return box.get("result", "")


# ─────────────────────────────────────────────────────
# NAVIGATION + CLOUDFLARE
# ─────────────────────────────────────────────────────
def navigate(page, url: str, ready_selector: str):
    print(f"[*] Navigating to {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    for _ in range(20):
        html = page.content().lower()
        if any(x in html for x in ["security verification", "cf-challenge", "checking your browser", "just a moment"]):
            print("[*] Cloudflare check, waiting...")
            page.wait_for_timeout(2000)
        else: break
    try:
        page.wait_for_selector(ready_selector, state="visible", timeout=20_000)
        print("[+] Page ready")
    except PWTimeout:
        shot = os.path.join(OUTPUT_FOLDER, "debug.png")
        page.screenshot(path=shot)
        raise Exception(f"Page did not load. Screenshot: {shot}")


# ─────────────────────────────────────────────────────
# GET CURRENT PAGE URL (to register new chat sessions)
# ─────────────────────────────────────────────────────
def get_current_url(page) -> str:
    return page.url


# ─────────────────────────────────────────────────────
# RESPONSE POLLING — CLAUDE
# Uses div[data-is-streaming] (Claude-specific selector)
# ─────────────────────────────────────────────────────
def get_last_claude_response_text(page) -> str:
    return page.evaluate("""() => {
        const blocks = document.querySelectorAll('div[data-is-streaming]');
        if (!blocks.length) return '';
        return blocks[blocks.length - 1].innerText || '';
    }""")


def wait_for_new_claude_block(page, prev_count: int) -> bool:
    deadline = time.time() + 15
    while time.time() < deadline:
        page.wait_for_timeout(POLL_MS)
        count = page.evaluate("() => document.querySelectorAll('div[data-is-streaming]').length")
        if count > prev_count:
            print(f"[+] New Claude response block appeared")
            return True
    return False


def poll_claude_until_stable(page, stream_queue=None) -> str:
    """Poll Claude response using data-is-streaming blocks."""
    print(f"[*] Polling Claude response (stable for {STABLE_SEC}s = done)...")
    last_text    = ""
    last_changed = time.time()
    deadline     = time.time() + MAX_WAIT_SEC

    while time.time() < deadline:
        page.wait_for_timeout(POLL_MS)
        try:
            cur_text = get_last_claude_response_text(page)
        except Exception:
            continue

        if cur_text != last_text:
            if stream_queue and len(cur_text) > len(last_text):
                delta = cur_text[len(last_text):]
                stream_queue.put(delta)
            last_text    = cur_text
            last_changed = time.time()
        else:
            stable_for = time.time() - last_changed
            if stable_for >= STABLE_SEC and len(cur_text) > 10:
                print(f"[+] Claude stable — done ({len(cur_text)} chars)")
                if stream_queue:
                    stream_queue.put(None)
                return cur_text

    print(f"[!] Claude hit {MAX_WAIT_SEC}s limit")
    if stream_queue: stream_queue.put(None)
    return last_text


# ─────────────────────────────────────────────────────
# RESPONSE POLLING — CHATGPT
# Strategy:
#   1. Wait for stop button (#composer-submit-button[data-testid="stop-button"])
#      to appear  → generation started
#   2. Wait for send button (#composer-submit-button[data-testid="send-button"])
#      to reappear → generation finished
#   3. Extract the last assistant message text
# ─────────────────────────────────────────────────────
# ── JS helpers (avoid Playwright selector-quoting bugs) ──────────────────────

def _chatgpt_is_stop_visible(page) -> bool:
    """True if the stop-streaming button is in the DOM and visible."""
    return page.evaluate("""() => {
        const btn = document.querySelector('#composer-submit-button[data-testid="stop-button"]');
        if (!btn) return false;
        const r = btn.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }""")


def _chatgpt_is_done(page) -> bool:
    """
    True when generation has finished.
    Detects: send-button back  OR  voice-button back  OR  stop-button gone.
    Any one of these means ChatGPT finished.
    """
    return page.evaluate("""() => {
        // send button reappeared
        const send = document.querySelector('#composer-submit-button[data-testid="send-button"]');
        if (send) { const r = send.getBoundingClientRect(); if (r.width > 0) return true; }
        // voice / mic button reappeared (shown after response, before next send)
        const voice = document.querySelector('button[aria-label="Start Voice"]');
        if (voice) { const r = voice.getBoundingClientRect(); if (r.width > 0) return true; }
        // stop button completely gone from DOM
        const stop = document.querySelector('#composer-submit-button[data-testid="stop-button"]');
        if (!stop) return true;
        return false;
    }""")


def get_last_chatgpt_response_text(page) -> str:
    return page.evaluate("""() => {
        const selectors = [
            "div[data-message-author-role='assistant'] .markdown",
            "div[data-message-author-role='assistant']",
            "article[data-testid] .markdown",
            "article[data-testid]"
        ];
        for (const sel of selectors) {
            const blocks = document.querySelectorAll(sel);
            if (blocks.length) {
                return blocks[blocks.length - 1].innerText || '';
            }
        }
        return '';
    }""")


def poll_chatgpt_response(page, stream_queue=None) -> str:
    """
    Wait for ChatGPT to start then finish generating.
    Streams deltas into stream_queue if provided.
    Always puts None into stream_queue when done (signals [DONE]).
    """
    try:
        # ── Step 1: wait for stop button (generation started) ────────────────
        print("[*] Waiting for ChatGPT to start (stop button)...")
        deadline = time.time() + 20
        started  = False
        while time.time() < deadline:
            page.wait_for_timeout(POLL_MS)
            try:
                if _chatgpt_is_stop_visible(page):
                    print("[+] ChatGPT generation started")
                    started = True
                    break
            except Exception:
                pass

        if not started:
            # Already done before we caught it? Try to grab whatever text is there.
            print("[!] Stop button never caught — grabbing text anyway")
            try:
                text = get_last_chatgpt_response_text(page)
            except Exception:
                text = ""
            if stream_queue:
                if text: stream_queue.put(text)
                stream_queue.put(None)
            return text

        # ── Step 2: poll text while generating, detect done ──────────────────
        last_text = ""
        deadline  = time.time() + MAX_WAIT_SEC
        print("[*] Polling ChatGPT response...")

        while time.time() < deadline:
            page.wait_for_timeout(POLL_MS)
            try:
                done = _chatgpt_is_done(page)
            except Exception:
                done = False

            try:
                cur_text = get_last_chatgpt_response_text(page)
            except Exception:
                cur_text = last_text

            # push delta
            if stream_queue and len(cur_text) > len(last_text):
                stream_queue.put(cur_text[len(last_text):])
            last_text = cur_text

            if done:
                print(f"[+] ChatGPT done — {len(last_text)} chars")
                break
        else:
            print(f"[!] ChatGPT hit {MAX_WAIT_SEC}s timeout")

        # ── Step 3: one final grab after done signal ──────────────────────────
        page.wait_for_timeout(300)
        try:
            final_text = get_last_chatgpt_response_text(page)
        except Exception:
            final_text = last_text

        if stream_queue:
            if len(final_text) > len(last_text):
                stream_queue.put(final_text[len(last_text):])
            stream_queue.put(None)   # ← always signals [DONE]

        return final_text

    except Exception as e:
        print(f"[ERROR] poll_chatgpt_response: {e}")
        if stream_queue:
            stream_queue.put(None)   # ← safety: never leave caller hanging
        return ""


# ─────────────────────────────────────────────────────
# DETECT CLAUDE DAILY LIMIT
# ─────────────────────────────────────────────────────
def check_claude_limit(page) -> bool:
    try:
        content = page.content().lower()
        if any(x in content for x in ["you've reached your limit", "usage limit", "upgrade your plan", "claude is at capacity"]):
            print("[!] Claude daily limit detected")
            set_claude_daily_limit(True)
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────
# CLAUDE NAVIGATION WITH SESSION PERSISTENCE
# ─────────────────────────────────────────────────────
def ensure_claude(page):
    session = get_active_session("claude")

    if session and session["status"] == "active":
        msg_count = session.get("msg_count", 0)
        if msg_count < MAX_MSG_PER_CHAT:
            # Reuse existing chat URL
            current = get_current_url(page)
            if session["url"] in current:
                print(f"[+] Reusing Claude session #{session['index']} ({msg_count} msgs)")
                try:
                    page.wait_for_selector('div[contenteditable="true"]', state="visible", timeout=3_000)
                    return  # already on correct page
                except PWTimeout:
                    pass
            # Navigate to saved URL
            print(f"[*] Returning to Claude session #{session['index']}")
            navigate(page, session["url"], 'div[contenteditable="true"]')
            return

        # Chat is full — mark it and open new one
        print(f"[*] Session #{session['index']} reached {msg_count} msgs — opening new chat")
        mark_session_full("claude")

    # Open new Claude chat
    print("[*] Opening new Claude chat...")
    navigate(page, "https://claude.ai/new", 'div[contenteditable="true"]')

    # Send a dummy message to get the chat URL assigned
    # (Claude only assigns a URL after the first message)
    # We'll capture it after the first real message instead


def capture_claude_url_if_new(page):
    """Called after first message in a new chat to capture and save the URL."""
    session = get_active_session("claude")
    current_url = get_current_url(page)

    # If URL changed from /new to /chat/xxx — register it
    if "/chat/" in current_url:
        if session is None or session["url"] != current_url:
            register_new_session("claude", current_url)


# ─────────────────────────────────────────────────────
# CHATGPT NAVIGATION WITH SESSION PERSISTENCE
# ─────────────────────────────────────────────────────
def ensure_chatgpt(page):
    session = get_active_session("chatgpt")

    if session and session["status"] == "active":
        msg_count = session.get("msg_count", 0)
        if msg_count < MAX_MSG_PER_CHAT:
            current = get_current_url(page)
            if session["url"] in current:
                print(f"[+] Reusing ChatGPT session #{session['index']} ({msg_count} msgs)")
                try:
                    page.wait_for_selector("div#prompt-textarea", state="visible", timeout=3_000)
                    return
                except PWTimeout:
                    pass
            navigate(page, session["url"], "div#prompt-textarea")
            return
        mark_session_full("chatgpt")

    # Open new ChatGPT chat
    print("[*] Opening new ChatGPT chat...")
    navigate(page, "https://chatgpt.com/", "div#prompt-textarea")


def capture_chatgpt_url_if_new(page):
    session = get_active_session("chatgpt")
    current_url = get_current_url(page)
    if "/c/" in current_url:
        if session is None or session["url"] != current_url:
            register_new_session("chatgpt", current_url)


# ─────────────────────────────────────────────────────
# SUBMIT PROMPT + STREAM RESPONSE
# ─────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────
# SUBMIT PROMPT + STREAM RESPONSE
# ─────────────────────────────────────────────────────
def submit_and_stream(page, prompt: str, provider: str, stream_queue=None) -> str:
    if provider == "claude":
        input_sel = 'div[contenteditable="true"]'
    else:
        input_sel = "div#prompt-textarea"

    box = page.locator(input_sel).first
    box.click()
    page.wait_for_timeout(300)
    box.press("Control+a")
    box.press("Delete")
    page.wait_for_timeout(200)
    box.type(prompt, delay=35)
    page.wait_for_timeout(400)

    typed = box.inner_text().strip()
    print(f"[*] Typed: '{typed[:60]}'")

    if provider == "claude":
        # Claude: count existing blocks, press Enter, wait for new block
        prev_count = page.evaluate("() => document.querySelectorAll('div[data-is-streaming]').length")
        box.press("Enter")
        print(f"[+] Submitted to Claude")
        wait_for_new_claude_block(page, prev_count)

        # Check for Claude daily limit
        page.wait_for_timeout(1000)
        if check_claude_limit(page):
            if stream_queue: stream_queue.put(None)
            return "[CLAUDE_LIMIT_HIT]"

        # Capture URL for new sessions
        page.wait_for_timeout(500)
        capture_claude_url_if_new(page)

        return poll_claude_until_stable(page, stream_queue)

    else:
        # ChatGPT: click send button via JS (avoids selector-quoting issues),
        # fall back to Enter key if button not found
        clicked = page.evaluate("""() => {
            const btn = document.querySelector('#composer-submit-button[data-testid="send-button"]');
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        if clicked:
            print(f"[+] Submitted to ChatGPT via JS send button click")
        else:
            box.press("Enter")
            print(f"[+] Submitted to ChatGPT via Enter (send button not found)")

        # Capture URL for new sessions
        page.wait_for_timeout(800)
        capture_chatgpt_url_if_new(page)

        return poll_chatgpt_response(page, stream_queue)


# ─────────────────────────────────────────────────────
# MAIN AUTOMATION JOB
# ─────────────────────────────────────────────────────
def _automation_job(page, prompt: str, provider: str, stream_queue=None) -> str:
    # Choose provider — fall back to chatgpt if claude daily limit hit
    if provider == "claude" and claude_daily_limit_hit():
        print("[!] Claude daily limit active — falling back to ChatGPT")
        provider = "chatgpt"

    if provider == "claude":
        ensure_claude(page)
    else:
        ensure_chatgpt(page)

    response = submit_and_stream(page, prompt, provider, stream_queue)

    if response == "[CLAUDE_LIMIT_HIT]":
        print("[!] Claude limit hit mid-session — switching to ChatGPT")
        ensure_chatgpt(page)
        response = submit_and_stream(page, prompt, "chatgpt", stream_queue)
        increment_msg_count("chatgpt")
    else:
        count = increment_msg_count(provider)
        print(f"[*] Message #{count} in this {provider} chat")

    return response


# ─────────────────────────────────────────────────────
# FLASK — OpenAI-compatible API
# ─────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    data = load_sessions()
    return jsonify({
        "service":  "CYBREIGN AI Automator v10",
        "version":  "1.0.0",
        "sessions": data["sessions"],
        "claude_daily_limit": data["claude_daily_limit"],
        "endpoints": {
            "POST /v1/chat/completions": "OpenAI-compatible chat (use with OpenClaw)",
            "POST /ask":                 "Simple ask endpoint",
            "GET  /sessions":            "View all sessions",
            "POST /sessions/reset":      "Reset session state",
        }
    })


@app.route("/v1/models", methods=["GET"])
def list_models():
    """OpenAI-compatible models list — required by OpenClaw."""
    return jsonify({
        "object": "list",
        "data": [
            {"id": "claude",  "object": "model", "created": 1700000000, "owned_by": "cybreign"},
            {"id": "chatgpt", "object": "model", "created": 1700000000, "owned_by": "cybreign"},
        ]
    })


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """
    OpenAI-compatible endpoint.
    OpenClaw sends:
      { "model": "claude", "messages": [...], "stream": true/false }
    We take the last user message as the prompt.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    messages = data.get("messages", [])
    stream   = data.get("stream", False)
    model    = data.get("model", "claude").lower()
    provider = "claude" if "claude" in model else "chatgpt"

    # Extract the last user message as the prompt
    prompt = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            prompt  = content if isinstance(content, str) else str(content)
            break

    if not prompt:
        return jsonify({"error": "No user message found"}), 400

    _ready.wait(timeout=30)
    req_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    if stream:
        # SSE streaming response
        stream_queue = queue.Queue()

        def run_job():
            try:
                run_in_browser(_automation_job, prompt, provider, stream_queue)
            except Exception as e:
                stream_queue.put(None)

        threading.Thread(target=run_job, daemon=True).start()

        def generate():
            while True:
                chunk = stream_queue.get(timeout=MAX_WAIT_SEC + 10)
                if chunk is None:
                    # End of stream
                    end = {
                        "id": req_id, "object": "chat.completion.chunk",
                        "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n"
                    return
                # Send delta chunk
                payload = {
                    "id": req_id, "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": chunk}, "index": 0, "finish_reason": None}]
                }
                yield f"data: {json.dumps(payload)}\n\n"

        return Response(stream_with_context(generate()), content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    else:
        # Regular blocking response
        try:
            response = run_in_browser(_automation_job, prompt, provider, None)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        return jsonify({
            "id":      req_id,
            "object":  "chat.completion",
            "model":   provider,
            "choices": [{
                "index":         0,
                "message":       {"role": "assistant", "content": response},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens":     len(prompt.split()),
                "completion_tokens": len(response.split()),
                "total_tokens":      len(prompt.split()) + len(response.split())
            }
        })


@app.route("/ask", methods=["POST"])
def ask():
    """Simple non-OpenAI endpoint for direct use."""
    data = request.get_json()
    if not data: return jsonify({"error": "No JSON body"}), 400
    prompt   = data.get("prompt", "").strip()
    target   = data.get("target", "claude_web").strip()
    provider = "claude" if "claude" in target else "chatgpt"
    if not prompt: return jsonify({"error": "'prompt' required"}), 400
    _ready.wait(timeout=30)
    try:
        response = run_in_browser(_automation_job, prompt, provider, None)
        return jsonify({"status": "success", "response": response, "provider": provider})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/sessions", methods=["GET"])
def sessions():
    return jsonify(load_sessions())


@app.route("/sessions/reset", methods=["POST"])
def reset_sessions():
    data = {
        "sessions": [],
        "claude_daily_limit": False,
        "active_claude_index": None,
        "active_chatgpt_index": None
    }
    save_sessions(data)
    return jsonify({"status": "sessions reset"})


@app.route("/sessions/claude-limit", methods=["POST"])
def toggle_claude_limit():
    """Manually mark/unmark Claude daily limit."""
    body = request.get_json() or {}
    hit  = body.get("hit", True)
    set_claude_daily_limit(hit)
    return jsonify({"claude_daily_limit": hit})


# ─────────────────────────────────────────────────────
# TRAY ICON
# ─────────────────────────────────────────────────────
def start_tray():
    if not HAS_TRAY: return
    img = Image.new("RGBA", (64, 64), (0,0,0,0))
    d   = ImageDraw.Draw(img)
    d.ellipse([2,2,62,62],   fill=(34,197,94))
    d.ellipse([18,18,46,46], fill=(255,255,255))
    d.ellipse([24,24,40,40], fill=(34,197,94))
    def open_folder(i,_): subprocess.Popen(f'explorer "{OUTPUT_FOLDER}"')
    def view_sessions(i,_): subprocess.Popen(f'notepad "{SESSION_FILE}"')
    def quit_app(i,_):    i.stop(); os._exit(0)
    icon = pystray.Icon("CYBREIGN", img, "CYBREIGN AI Automator v10",
        menu=pystray.Menu(
            pystray.MenuItem("Open Output Folder", open_folder),
            pystray.MenuItem("View Sessions JSON",  view_sessions),
            pystray.MenuItem("Quit", quit_app),
        ))
    threading.Thread(target=icon.run, daemon=True).start()
    print("[+] Tray icon active")


# ─────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────
def setup_mode():
    print("\n" + "="*55)
    print("  SETUP — Log in once, session saved forever")
    print("="*55)
    pw = sync_playwright().start()
    browser = pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR, channel="chrome", headless=False, no_viewport=True,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    if HAS_STEALTH: stealth_sync(page)
    page.goto("https://claude.ai/", wait_until="domcontentloaded")
    input("\n>>> Log into Claude fully, then press ENTER...")
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
    input(">>> Log into ChatGPT fully, then press ENTER...")
    browser.close(); pw.stop()
    print("\n[+] Done! Run: python ai_automator.py\n")
    sys.exit(0)


# ─────────────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup_mode()

    threading.Thread(target=playwright_worker, daemon=True).start()
    start_tray()

    ports = [8080, 8000, 9000, 7000, 3000]
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", port))
            s.close()
            print("=" * 60)
            print("  AI Automator v10 - CYBREIGN  [OpenAI-Compatible API]")
            print(f"  Base URL : http://localhost:{port}/v1")
            print(f"  Models   : claude, chatgpt")
            print(f"  Sessions : {SESSION_FILE}")
            print(f"  Tray     : green icon near clock")
            print(f"  Setup    : python ai_automator.py --setup")
            print("=" * 60)
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
            break
        except OSError:
            print(f"[!] Port {port} busy, trying next...")