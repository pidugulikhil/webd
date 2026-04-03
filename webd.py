"""
WEBD v12 - Dev by likhil (CYBREIGN)
=====================================
Dual-port server:
    Port 50001  → OpenAI-compatible API  (/v1/chat/completions, /v1/models)
    Port 50000  → Ollama-compatible API  (/api/generate, /api/chat, /api/tags, etc.)

Both ports run simultaneously — connect OpenClaw (or any Ollama client) to:
    http://localhost:50000

Model name routing:
  claude, llama3, llama2, mistral, gemma, phi, deepseek, qwen,
  nomic-embed-text  →  Claude browser
  chatgpt, gpt-4, gpt-4o, gpt-3.5   →  ChatGPT browser

Install:
  pip install flask playwright playwright-stealth pystray pillow
  playwright install

First time login:
  python webd.py --setup

Run normally:
  python webd.py

v12 fixes:
  - Page health check before every submit (detects closed tabs)
  - Forced fresh navigation when page is dead/blank
  - Clears stale session URL when tab is closed by user
  - Robust input-box validation before typing
"""

import os, sys, json, time, uuid, socket, queue, threading, subprocess, re
from urllib.parse import quote_plus
from datetime import datetime
from flask import Flask, request, jsonify, Response, stream_with_context

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("[!] pip install playwright && playwright install"); sys.exit(1)

try:
    from waitress import serve as waitress_serve
    HAS_WAITRESS = True
except ImportError:
    HAS_WAITRESS = False

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
MAX_MSG_PER_CHAT = 20
STABLE_SEC       = 3.0
POLL_MS          = 350
MAX_WAIT_SEC     = 120

# Model name → provider routing
CLAUDE_MODELS  = {"claude", "claude_web", "llama3", "llama2", "mistral", "gemma",
                  "phi", "deepseek", "qwen", "nomic-embed-text", "llama3.2",
                  "llama3.1", "codellama", "vicuna", "orca", "solar"}
CHATGPT_MODELS = {"chatgpt", "chatgpt_web", "gpt-4", "gpt-4o", "gpt-3.5",
                  "gpt-3.5-turbo", "gpt-4-turbo"}

def resolve_provider(model: str) -> str:
    m = model.lower().strip()
    provider_policy = os.getenv("WEBD_PROVIDER_POLICY", "chatgpt_default").strip().lower()

    # Always honor explicit Claude model selection.
    # This avoids accidental ChatGPT routing when the user clearly chose Claude.
    if m in CLAUDE_MODELS or "claude" in m:
        return "claude"

    # Optional strict mode: route all non-explicit-Claude models to ChatGPT.
    if provider_policy in {"chatgpt_only", "chatgpt", "default_chatgpt"}:
        return "chatgpt"

    # Default/recommended mode: explicit Claude works, unknown -> ChatGPT.
    if m in CHATGPT_MODELS or m.startswith("gpt"):
        return "chatgpt"
    if m in CLAUDE_MODELS or "claude" in m:
        return "claude"
    return "chatgpt"

for d in [OUTPUT_FOLDER, PROFILE_DIR]: os.makedirs(d, exist_ok=True)

openai_app = Flask("openai_app")
ollama_app = Flask("ollama_app")
_job_queue = queue.Queue()
_ready     = threading.Event()
_BROWSER_CONTEXT = None
ACTIVE_OLLAMA_PORT = 50000
ACTIVE_OPENAI_PORT = 50001

# Prompt routing mode for /api/chat requests.
# latest_user: sends only the latest user message (faster, avoids noisy history loops)
# full: sends full messages transcript
CHAT_PROMPT_MODE = os.getenv("WEBD_CHAT_PROMPT_MODE", "latest_user").strip().lower()

# /api/generate should reuse active session by default (memory-efficient, no new tab each call).
# Set WEBD_GENERATE_STATELESS=1 only when strict per-request isolation is needed.
GENERATE_STATELESS = os.getenv("WEBD_GENERATE_STATELESS", "0").strip().lower() not in {"0", "false", "no"}

# System-access local actions are intentionally disabled by default for now.
# Set WEBD_ENABLE_LOCAL_ACTIONS=1 later to re-enable during future development.
LOCAL_ACTIONS_ENABLED = os.getenv("WEBD_ENABLE_LOCAL_ACTIONS", "0").strip().lower() in {"1", "true", "yes", "on"}


# ─────────────────────────────────────────────────────
# SESSION MANAGER
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


def get_active_session(provider: str) -> dict | None:
    data = load_sessions()
    idx  = data.get(f"active_{provider}_index")
    if idx is None: return None
    matches = [s for s in data["sessions"] if s["index"] == idx and s["provider"] == provider]
    return matches[0] if matches else None



def get_latest_reusable_session(provider: str) -> dict | None:
    """
    Return the latest non-full, non-closed session for a provider.
    This recovers from cases where active_<provider>_index was cleared unexpectedly.
    """
    data = load_sessions()
    candidates = [
        s for s in data.get("sessions", [])
        if s.get("provider") == provider
        and s.get("status", "active") == "active"
        and int(s.get("msg_count", 0)) < MAX_MSG_PER_CHAT
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: int(s.get("index", -1)))


def ensure_active_session_index(provider: str) -> dict | None:
    """
    Ensure active_<provider>_index points to a reusable session when possible.
    """
    current = get_active_session(provider)
    if current and current.get("status", "active") == "active" and int(current.get("msg_count", 0)) < MAX_MSG_PER_CHAT:
        return current

    recovered = get_latest_reusable_session(provider)
    if not recovered:
        return None

    data = load_sessions()
    data[f"active_{provider}_index"] = recovered["index"]
    save_sessions(data)
    print(f"[+] Recovered active {provider} session #{recovered['index']}")
    return recovered


def mark_session_full(provider: str):
    data = load_sessions()
    idx  = data.get(f"active_{provider}_index")
    if idx is not None:
        for s in data["sessions"]:
            if s["index"] == idx and s["provider"] == provider:
                s["status"] = "full"
    save_sessions(data)


def invalidate_session(provider: str):
    """
    Called when the page is confirmed dead (tab closed by user).
    Clears the active index so the next request opens a fresh chat.
    """
    data = load_sessions()
    key  = f"active_{provider}_index"
    idx  = data.get(key)
    if idx is not None:
        for s in data["sessions"]:
            if s["index"] == idx and s["provider"] == provider:
                s["status"] = "closed"
                print(f"[*] Session #{idx} ({provider}) marked as closed (tab was shut)")
        data[key] = None
    save_sessions(data)


def register_new_session(provider: str, url: str) -> dict:
    data    = load_sessions()
    new_idx = max((s["index"] for s in data["sessions"]), default=-1) + 1
    session = {
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


def update_active_session_url(provider: str, url: str):
    """
    Update the URL of the CURRENT active session in-place.
    Called after the first message when the chat gets its permanent /c/ or /chat/ URL.
    Does NOT create a new session — just patches the existing one.
    """
    data = load_sessions()
    idx  = data.get(f"active_{provider}_index")
    if idx is not None:
        for s in data["sessions"]:
            if s["index"] == idx and s["provider"] == provider:
                old = s.get("url", "")
                s["url"] = url
                print(f"[+] Updated {provider} session #{idx} URL: {old} → {url}")
                break
    save_sessions(data)


def increment_msg_count(provider: str) -> int:
    data  = load_sessions()
    idx   = data.get(f"active_{provider}_index")
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
# PLAYWRIGHT WORKER (single browser thread)
# ─────────────────────────────────────────────────────
def playwright_worker():
    global _BROWSER_CONTEXT
    pw      = sync_playwright().start()
    def _create_browser_and_page():
        browser = pw.chromium.launch_persistent_context(
            user_data_dir       = PROFILE_DIR,
            channel             = "chrome",
            headless            = False,
            no_viewport         = True,
            args                = ["--start-maximized",
                                   "--disable-blink-features=AutomationControlled",
                                   "--disable-infobars"],
            ignore_https_errors = True,
        )
        pages = browser.pages
        page  = pages[0] if pages else browser.new_page()
        if HAS_STEALTH:
            stealth_sync(page)
        page.set_extra_http_headers({
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        return browser, page

    browser, page = _create_browser_and_page()
    _BROWSER_CONTEXT = browser
    print("[+] Browser ready")
    _ready.set()

    while True:
        job = _job_queue.get()
        if job is None: break
        fn, args, box = job
        try:
            box["result"] = fn(page, *args)
        except Exception as e:
            err = str(e)
            if "Target page, context or browser has been closed" in err:
                print("[!] Browser context closed — recreating browser and retrying job")
                try:
                    browser.close()
                except Exception:
                    pass
                try:
                    browser, page = _create_browser_and_page()
                    _BROWSER_CONTEXT = browser
                    box["result"] = fn(page, *args)
                except Exception as e2:
                    box["error"] = str(e2)
                    print(f"[ERROR] {e2}")
            else:
                box["error"] = err
                print(f"[ERROR] {e}")
        finally: box["done"].set()

    browser.close(); pw.stop()


def run_in_browser(fn, *args):
    box = {"done": threading.Event()}
    _job_queue.put((fn, args, box))
    box["done"].wait(timeout=180)
    if "error" in box: raise Exception(box["error"])
    return box.get("result", "")


def ensure_alive_page(page):
    """
    If the current Playwright page got closed, create a replacement page.
    This fixes the "tab closed" loop where old page handles cannot navigate.
    """
    global _BROWSER_CONTEXT
    try:
        if page and not page.is_closed():
            return page
    except Exception:
        pass

    if _BROWSER_CONTEXT is None:
        raise Exception("Browser context is not ready")

    new_page = _BROWSER_CONTEXT.new_page()
    if HAS_STEALTH:
        stealth_sync(new_page)
    new_page.set_extra_http_headers({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    print("[+] Recovered with a fresh browser tab")
    return new_page


# ─────────────────────────────────────────────────────
# PAGE HEALTH CHECK  ← KEY FIX FOR CLOSED TABS
# ─────────────────────────────────────────────────────
def is_page_healthy(page, provider: str) -> bool:
    """
    Returns True only if the page is on the correct site AND
    the input box is visible and ready to receive text.

    This catches the case where the user closed the tab — page.url
    becomes about:blank or chrome://newtab, input selectors fail,
    and we'd type into nothing and get an empty response.
    """
    try:
        url = page.url
        # Dead tab indicators
        if url in ("about:blank", "", "chrome://newtab/") or not url:
            print(f"[!] Page is dead (url='{url}')")
            return False

        if provider == "claude":
            if "claude.ai" not in url:
                print(f"[!] Not on Claude (url='{url}')")
                return False
            page.wait_for_selector('div[contenteditable="true"]',
                                   state="visible", timeout=2_500)
        else:
            if "chatgpt.com" not in url:
                print(f"[!] Not on ChatGPT (url='{url}')")
                return False
            page.wait_for_selector("div#prompt-textarea",
                                   state="visible", timeout=2_500)
        return True
    except Exception as e:
        print(f"[!] Health check failed: {e}")
        return False


def ensure_healthy_page(page, provider: str):
    """
    Call before every submit.
    If the page isn't healthy (tab closed, wrong site, etc.),
    clears the stale session and forces a fresh navigation.
    """
    page = ensure_alive_page(page)

    if provider == "claude":
        target_url = "https://claude.ai/new"
        expected_host = "claude.ai"
        ready_selector = 'div[contenteditable="true"]'
    else:
        target_url = "https://chatgpt.com/"
        expected_host = "chatgpt.com"
        ready_selector = "div#prompt-textarea"

    # Healthy page -> keep current session untouched.
    if is_page_healthy(page, provider):
        return page

    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    # Only invalidate when tab is truly dead/blank. Do not invalidate on provider switches.
    if current_url in ("about:blank", "", "chrome://newtab/"):
        print(f"[!] Dead tab for {provider} — invalidating session and opening fresh page")
        invalidate_session(provider)
        navigate(page, target_url, ready_selector)
        return page

    # If currently on another provider site, just navigate to the expected one.
    if expected_host not in current_url:
        print(f"[*] Switching provider view to {provider} without resetting session")
        navigate(page, target_url, ready_selector)
        return page

    # Same provider site but input not ready yet: allow slower loads before resetting session.
    try:
        page.wait_for_selector(ready_selector, state="visible", timeout=8_000)
        return page
    except Exception:
        print(f"[!] {provider} input not ready, trying one refresh before reset")

    try:
        page.reload(wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_selector(ready_selector, state="visible", timeout=10_000)
        return page
    except Exception:
        print(f"[!] {provider} page stayed unhealthy — invalidating session and reopening")
        invalidate_session(provider)
        navigate(page, target_url, ready_selector)
        return page


# ─────────────────────────────────────────────────────
# NAVIGATION + CLOUDFLARE BYPASS
# ─────────────────────────────────────────────────────
def navigate(page, url: str, ready_selector: str):
    print(f"[*] Navigating to {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    for _ in range(20):
        html = page.content().lower()
        if any(x in html for x in ["security verification", "cf-challenge",
                                    "checking your browser", "just a moment"]):
            print("[*] Cloudflare check, waiting...")
            page.wait_for_timeout(2000)
        else:
            break
    try:
        page.wait_for_selector(ready_selector, state="visible", timeout=20_000)
        print("[+] Page ready")
    except PWTimeout:
        shot = os.path.join(OUTPUT_FOLDER, "debug.png")
        page.screenshot(path=shot)
        raise Exception(f"Page did not load. Screenshot saved: {shot}")


def get_current_url(page) -> str:
    return page.url


# ─────────────────────────────────────────────────────
# CLAUDE POLLING
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
        count = page.evaluate(
            "() => document.querySelectorAll('div[data-is-streaming]').length")
        if count > prev_count:
            print("[+] New Claude response block appeared")
            return True
    return False


def _delta_from_growth(prev: str, cur: str) -> str:
    """Best-effort delta extraction for streamed UI text that may rewrite in-place."""
    if not cur:
        return ""
    if not prev:
        return cur
    if cur.startswith(prev):
        return cur[len(prev):]

    i = 0
    lim = min(len(prev), len(cur))
    while i < lim and prev[i] == cur[i]:
        i += 1
    return cur[i:]


def poll_claude_until_stable(page, stream_queue=None) -> str:
    print(f"[*] Polling Claude (stable {STABLE_SEC}s = done)...")
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
            if stream_queue:
                delta = _delta_from_growth(last_text, cur_text)
                if delta:
                    stream_queue.put(delta)
            last_text    = cur_text
            last_changed = time.time()
        else:
            if time.time() - last_changed >= STABLE_SEC and len(cur_text) > 10:
                print(f"[+] Claude stable — {len(cur_text)} chars")
                if stream_queue: stream_queue.put(None)
                return cur_text

    print(f"[!] Claude hit {MAX_WAIT_SEC}s limit")
    if stream_queue: stream_queue.put(None)
    return last_text


# ─────────────────────────────────────────────────────
# CHATGPT POLLING
# ─────────────────────────────────────────────────────
def _chatgpt_is_stop_visible(page) -> bool:
    return page.evaluate("""() => {
        const btn = document.querySelector(
            '#composer-submit-button[data-testid="stop-button"]');
        if (!btn) return false;
        const r = btn.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }""")


def _chatgpt_is_done(page) -> bool:
    return page.evaluate("""() => {
        const send = document.querySelector(
            '#composer-submit-button[data-testid="send-button"]');
        if (send) { const r = send.getBoundingClientRect(); if (r.width > 0) return true; }
        const voice = document.querySelector('button[aria-label="Start Voice"]');
        if (voice) { const r = voice.getBoundingClientRect(); if (r.width > 0) return true; }
        const stop = document.querySelector(
            '#composer-submit-button[data-testid="stop-button"]');
        if (!stop) return true;
        return false;
    }""")


def _chatgpt_composer_text(page) -> str:
    try:
        return page.evaluate("""() => {
            const candidates = [
              "div#prompt-textarea",
              "textarea#prompt-textarea",
              "textarea[data-testid='prompt-textarea']",
              "div[contenteditable='true'][id='prompt-textarea']",
              "div[contenteditable='true']"
            ];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (!el) continue;
                const val = (el.value || el.innerText || el.textContent || '').trim();
                if (val) return val;
            }
            return '';
        }""")
    except Exception:
        return ""


def _chatgpt_click_send(page) -> bool:
    try:
        return bool(page.evaluate("""() => {
            const selectors = [
              "#composer-submit-button[data-testid='send-button']",
              "button[data-testid='send-button']",
              "button[aria-label='Send prompt']",
              "button[aria-label='Send message']"
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (!btn) continue;
                const r = btn.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (btn.disabled) continue;
                btn.click();
                return true;
            }
            return false;
        }"""))
    except Exception:
        return False


def _chatgpt_submit_confirmed(page, baseline_count: int, typed_before_send: str) -> bool:
    deadline = time.time() + 6
    while time.time() < deadline:
        page.wait_for_timeout(200)
        try:
            if _chatgpt_is_stop_visible(page):
                return True
        except Exception:
            pass
        try:
            if get_chatgpt_assistant_count(page) > baseline_count:
                return True
        except Exception:
            pass
        cur = _chatgpt_composer_text(page)
        if typed_before_send and not cur:
            return True
    return False


def get_last_chatgpt_response_text(page) -> str:
    return page.evaluate("""() => {
        // Collect likely assistant containers and return the LAST non-empty one.
        // Returning the last element blindly can yield empty text on newer ChatGPT UIs.
        const candidates = Array.from(document.querySelectorAll(
            "div[data-message-author-role='assistant']"
        ));

        for (let i = candidates.length - 1; i >= 0; i--) {
            const t = (candidates[i].innerText || '').replace(/\u00a0/g, ' ').trim();
            if (t.length) return t;
        }

        // Fallback for occasional DOM variants where assistant role marker is absent.
        const fallback = Array.from(document.querySelectorAll(
            "article[data-testid^='conversation-turn-'], div[data-testid^='conversation-turn-']"
        ));
        for (let i = fallback.length - 1; i >= 0; i--) {
            const t = (fallback[i].innerText || '').replace(/\u00a0/g, ' ').trim();
            if (!t) continue;
            if (/^\s*(ChatGPT can make mistakes|By messaging ChatGPT|Tools available)/i.test(t)) continue;
            return t;
        }
        return '';
    }""")


def get_chatgpt_assistant_count(page) -> int:
    try:
        return int(page.evaluate("""() => {
            return document.querySelectorAll("div[data-message-author-role='assistant']").length;
        }"""))
    except Exception:
        return 0


def get_chatgpt_response_since_index(page, baseline_count: int) -> str:
    """
    Return only the assistant text generated after submit.
    baseline_count is captured before we click send.
    """
    return page.evaluate("""(baseline) => {
        const nodes = Array.from(document.querySelectorAll("div[data-message-author-role='assistant']"));
        if (!nodes.length) return '';

        let start = Math.max(0, Number(baseline) || 0);
        if (start >= nodes.length) return '';

        // During generation, ChatGPT typically updates the first new assistant node.
        for (let i = start; i < nodes.length; i++) {
            const t = (nodes[i].innerText || '').replace(/\u00a0/g, ' ').trim();
            if (t.length) return t;
        }
        return '';
    }""", baseline_count)


def poll_chatgpt_response(page, stream_queue=None, baseline_count: int = 0, baseline_text: str = "") -> str:
    try:
        print("[*] Waiting for ChatGPT to start (stop button)...")
        deadline = time.time() + 20
        started  = False
        while time.time() < deadline:
            page.wait_for_timeout(POLL_MS)
            try:
                if _chatgpt_is_stop_visible(page):
                    started = True; break
            except Exception:
                pass

        if not started:
            print("[!] Stop button never caught — grabbing text anyway")
            try:    text = get_chatgpt_response_since_index(page, baseline_count)
            except: text = ""

            # If nothing changed, do not return stale old response.
            if not text.strip() or text.strip() == (baseline_text or "").strip():
                msg = "[WEBD ERROR] ChatGPT did not start generating. Please retry."
                print("[!] " + msg)
                if stream_queue:
                    stream_queue.put(msg)
                    stream_queue.put(None)
                return "[WEBD_SUBMIT_FAILED]"

            if stream_queue:
                if text: stream_queue.put(text)
                stream_queue.put(None)
            return text

        last_text      = ""
        last_nonempty  = ""
        stable_polls   = 0
        saw_nonempty   = False
        deadline       = time.time() + MAX_WAIT_SEC
        print("[*] Polling ChatGPT response...")

        while time.time() < deadline:
            page.wait_for_timeout(POLL_MS)
            try:    done = _chatgpt_is_done(page)
            except: done = False
            try:    cur_text = get_chatgpt_response_since_index(page, baseline_count)
            except: cur_text = last_text

            if stream_queue:
                delta = _delta_from_growth(last_text, cur_text)
                if delta:
                    stream_queue.put(delta)

            if cur_text == last_text and cur_text.strip():
                stable_polls += 1
            else:
                stable_polls = 0

            last_text = cur_text
            if cur_text.strip():
                saw_nonempty = True
                last_nonempty = cur_text

            # Only finish once we actually saw assistant text and it stabilized.
            if done and saw_nonempty and stable_polls >= 2:
                print(f"[+] ChatGPT done — {len(last_text)} chars")
                break
        else:
            print(f"[!] ChatGPT hit {MAX_WAIT_SEC}s timeout")

        page.wait_for_timeout(300)
        try:    final_text = get_chatgpt_response_since_index(page, baseline_count)
        except: final_text = last_text

        if not final_text.strip() or (baseline_text and final_text.strip() == baseline_text.strip()):
            msg = "[WEBD ERROR] ChatGPT did not return a new response. Please retry."
            print("[!] " + msg)
            if stream_queue:
                stream_queue.put(msg)
                stream_queue.put(None)
            return "[WEBD_SUBMIT_FAILED]"

        if stream_queue:
            delta = _delta_from_growth(last_text, final_text)
            if delta:
                stream_queue.put(delta)
            stream_queue.put(None)

        final = final_text if final_text.strip() else last_nonempty
        return final

    except Exception as e:
        print(f"[ERROR] poll_chatgpt_response: {e}")
        if stream_queue: stream_queue.put(None)
        return ""


# ─────────────────────────────────────────────────────
# CLAUDE DAILY LIMIT DETECTION
# ─────────────────────────────────────────────────────
def check_claude_limit(page) -> bool:
    try:
        content = page.content().lower()
        if any(x in content for x in ["you've reached your limit", "usage limit",
                                       "upgrade your plan", "claude is at capacity"]):
            print("[!] Claude daily limit detected")
            set_claude_daily_limit(True)
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────
# SESSION-AWARE NAVIGATION
# ─────────────────────────────────────────────────────
def ensure_claude(page):
    page = ensure_alive_page(page)
    session = ensure_active_session_index("claude")
    if session and session["status"] == "active":
        if session.get("msg_count", 0) < MAX_MSG_PER_CHAT:
            saved_url = session["url"]
            current   = get_current_url(page)

            # Placeholder URL means first message not sent yet
            is_new_placeholder = saved_url.rstrip("/").endswith("/new") or \
                                  saved_url == "https://claude.ai/"

            if is_new_placeholder:
                if "claude.ai" in current:
                    try:
                        page.wait_for_selector('div[contenteditable="true"]',
                                               state="visible", timeout=3_000)
                        print(f"[+] Reusing Claude session #{session['index']} (new chat, no msg yet)")
                        return page
                    except PWTimeout:
                        pass
                navigate(page, "https://claude.ai/new", 'div[contenteditable="true"]')
                return page

            # Normal case: saved_url is a /chat/xxx URL
            if saved_url in current or current in saved_url:
                try:
                    page.wait_for_selector('div[contenteditable="true"]',
                                           state="visible", timeout=3_000)
                    print(f"[+] Reusing Claude session #{session['index']} (already on page)")
                    return page
                except PWTimeout:
                    print("[*] Claude input gone — reloading saved URL")

            print(f"[*] Navigating back to Claude session #{session['index']}: {saved_url}")
            navigate(page, saved_url, 'div[contenteditable="true"]')
            return page

        print(f"[*] Session #{session['index']} full — opening new chat")
        mark_session_full("claude")

    print("[*] Opening new Claude chat...")
    # Register placeholder so active index exists before first message
    data = load_sessions()
    placeholder_idx = max((s["index"] for s in data["sessions"]), default=-1) + 1
    placeholder = {
        "index":    placeholder_idx,
        "provider": "claude",
        "url":      "https://claude.ai/new",
        "status":   "active",
        "msg_count": 0,
        "created":  datetime.now().isoformat()
    }
    data["sessions"].append(placeholder)
    data["active_claude_index"] = placeholder_idx
    save_sessions(data)
    navigate(page, "https://claude.ai/new", 'div[contenteditable="true"]')
    return page


def capture_claude_url_if_new(page):
    """
    After submitting to Claude, the URL changes from /new → /chat/xxx.
    We must UPDATE the existing session's URL in-place (not register a new one),
    otherwise every request creates a new session → new chat tab.
    """
    current_url = get_current_url(page)
    if "/chat/" not in current_url:
        return  # URL not settled yet, skip

    session = get_active_session("claude")
    if session is None:
        # No session at all — register fresh
        register_new_session("claude", current_url)
    elif session["url"] != current_url:
        # URL changed (e.g. /new → /chat/abc123) — patch in-place
        update_active_session_url("claude", current_url)
    # else: URL already matches, nothing to do


def ensure_chatgpt(page):
    page = ensure_alive_page(page)
    session = ensure_active_session_index("chatgpt")
    if session and session["status"] == "active":
        if session.get("msg_count", 0) < MAX_MSG_PER_CHAT:
            saved_url = session["url"]
            current   = get_current_url(page)

            # A session whose URL is still the homepage placeholder means we
            # haven't sent a message yet — the real /c/ URL hasn't been captured.
            # In that case we must be ON chatgpt.com already (or navigate there).
            is_homepage = saved_url.rstrip("/") in (
                "https://chatgpt.com", "https://www.chatgpt.com"
            )

            if is_homepage:
                # Either already on chatgpt.com, or navigate there
                if "chatgpt.com" in current:
                    try:
                        page.wait_for_selector("div#prompt-textarea",
                                               state="visible", timeout=3_000)
                        print(f"[+] Reusing ChatGPT session #{session['index']} (on homepage, no msg yet)")
                        return page
                    except PWTimeout:
                        pass
                navigate(page, "https://chatgpt.com/", "div#prompt-textarea")
                return page

            # Normal case: saved_url is a specific /c/ chat URL
            # Check if we're already on it (current URL contains saved chat id)
            if saved_url in current or current in saved_url:
                try:
                    page.wait_for_selector("div#prompt-textarea",
                                           state="visible", timeout=3_000)
                    print(f"[+] Reusing ChatGPT session #{session['index']} (already on page)")
                    return page
                except PWTimeout:
                    print("[*] ChatGPT input gone — reloading saved URL")

            # Not on the right page — navigate back to the saved chat
            print(f"[*] Navigating back to ChatGPT session #{session['index']}: {saved_url}")
            navigate(page, saved_url, "div#prompt-textarea")
            return page

        mark_session_full("chatgpt")

    print("[*] Opening new ChatGPT chat...")
    # Register a placeholder session so we have an active index to update later
    data = load_sessions()
    placeholder_idx = max((s["index"] for s in data["sessions"]), default=-1) + 1
    placeholder = {
        "index":    placeholder_idx,
        "provider": "chatgpt",
        "url":      "https://chatgpt.com/",
        "status":   "active",
        "msg_count": 0,
        "created":  datetime.now().isoformat()
    }
    data["sessions"].append(placeholder)
    data["active_chatgpt_index"] = placeholder_idx
    save_sessions(data)
    navigate(page, "https://chatgpt.com/", "div#prompt-textarea")
    return page


def capture_chatgpt_url_if_new(page):
    """
    After submitting to ChatGPT, the URL changes from chatgpt.com/ → chatgpt.com/c/xxx.
    We must UPDATE the existing session's URL in-place (not register a new one),
    otherwise every request creates a new session → new chat tab.
    """
    current_url = get_current_url(page)
    if "/c/" not in current_url:
        return  # URL not settled yet (still on homepage), skip

    session = get_active_session("chatgpt")
    if session is None:
        # No session at all — register fresh
        register_new_session("chatgpt", current_url)
    elif session["url"] != current_url:
        # URL changed (homepage → /c/abc123) — patch in-place
        update_active_session_url("chatgpt", current_url)
    # else: URL already matches, nothing to do


def ensure_gemini(page):
    page = ensure_alive_page(page)
    session = get_active_session("gemini")
    if session and session["status"] == "active":
        if session.get("msg_count", 0) < MAX_MSG_PER_CHAT:
            saved_url = session["url"]
            current   = get_current_url(page)

            is_home = saved_url.rstrip("/") in (
                "https://gemini.google.com", "https://gemini.google.com/app"
            )
            if is_home:
                if "gemini.google.com" in current:
                    for sel in ["textarea", "div[contenteditable='true']", "rich-textarea div[contenteditable='true']"]:
                        try:
                            page.wait_for_selector(sel, state="visible", timeout=2_000)
                            print(f"[+] Reusing Gemini session #{session['index']} (homepage)")
                            return page
                        except Exception:
                            pass
                navigate(page, "https://gemini.google.com/app", "textarea")
                return page

            if saved_url in current or current in saved_url:
                for sel in ["textarea", "div[contenteditable='true']", "rich-textarea div[contenteditable='true']"]:
                    try:
                        page.wait_for_selector(sel, state="visible", timeout=2_000)
                        print(f"[+] Reusing Gemini session #{session['index']} (already on page)")
                        return page
                    except Exception:
                        pass

            print(f"[*] Navigating back to Gemini session #{session['index']}: {saved_url}")
            navigate(page, saved_url, "textarea")
            return page

        mark_session_full("gemini")

    print("[*] Opening new Gemini chat...")
    data = load_sessions()
    placeholder_idx = max((s["index"] for s in data["sessions"]), default=-1) + 1
    placeholder = {
        "index":    placeholder_idx,
        "provider": "gemini",
        "url":      "https://gemini.google.com/app",
        "status":   "active",
        "msg_count": 0,
        "created":  datetime.now().isoformat()
    }
    data["sessions"].append(placeholder)
    data["active_gemini_index"] = placeholder_idx
    save_sessions(data)
    navigate(page, "https://gemini.google.com/app", "textarea")
    return page


def capture_gemini_url_if_new(page):
    current_url = get_current_url(page)
    if "gemini.google.com" not in current_url:
        return

    session = get_active_session("gemini")
    if session is None:
        register_new_session("gemini", current_url)
    elif session["url"] != current_url:
        update_active_session_url("gemini", current_url)


def get_last_gemini_response_text(page) -> str:
    return page.evaluate("""() => {
        const selectors = [
            'model-response',
            'message-content',
            'div.response-content',
            'div.markdown',
            'div[data-test-id="response-content"]'
        ];
        const texts = [];
        for (const sel of selectors) {
            const nodes = Array.from(document.querySelectorAll(sel));
            for (const n of nodes) {
                const t = (n.innerText || '').replace(/\u00a0/g, ' ').trim();
                if (!t) continue;
                if (/Google Gemini can make mistakes/i.test(t)) continue;
                texts.push(t);
            }
            if (texts.length) break;
        }
        return texts.length ? texts[texts.length - 1] : '';
    }""")


def poll_gemini_until_stable(page, stream_queue=None) -> str:
    print(f"[*] Polling Gemini (stable {STABLE_SEC}s = done)...")
    last_text = ""
    last_changed = time.time()
    deadline = time.time() + MAX_WAIT_SEC

    while time.time() < deadline:
        page.wait_for_timeout(POLL_MS)
        try:
            cur_text = get_last_gemini_response_text(page)
        except Exception:
            continue

        if cur_text != last_text:
            if stream_queue and len(cur_text) > len(last_text):
                stream_queue.put(cur_text[len(last_text):])
            last_text = cur_text
            last_changed = time.time()
        elif time.time() - last_changed >= STABLE_SEC and len(cur_text) > 10:
            print(f"[+] Gemini stable — {len(cur_text)} chars")
            if stream_queue:
                stream_queue.put(None)
            return cur_text

    print(f"[!] Gemini hit {MAX_WAIT_SEC}s limit")
    if stream_queue:
        stream_queue.put(None)
    return last_text


# ─────────────────────────────────────────────────────
# SUBMIT PROMPT + STREAM RESPONSE
# ─────────────────────────────────────────────────────
def submit_and_stream(page, prompt: str, provider: str, stream_queue=None) -> str:
    page = ensure_alive_page(page)
    input_sel = 'div[contenteditable="true"]' if provider == "claude" \
                else "div#prompt-textarea"

    # ── Validate input box is actually present before typing ─────────────────
    try:
        page.wait_for_selector(input_sel, state="visible", timeout=5_000)
    except PWTimeout:
        print(f"[!] Input box not found on {page.url} — aborting submit")
        if stream_queue: stream_queue.put(None)
        return ""

    box = page.locator(input_sel).first
    box.click()
    page.wait_for_timeout(80)
    box.press("Control+a")
    box.press("Delete")
    page.wait_for_timeout(80)

    # Prefer fast paste-like insertion over slow per-character typing.
    try:
        box.fill(prompt)
    except Exception:
        page.keyboard.insert_text(prompt)
    page.wait_for_timeout(120)

    # Sanity check — make sure something was actually typed
    typed = box.inner_text().strip() if provider == "claude" else _chatgpt_composer_text(page)
    print(f"[*] Typed ({len(typed)} chars): '{typed[:80]}'")

    if len(typed) == 0:
        print(f"[!] Nothing typed — page may be broken. Aborting.")
        if stream_queue: stream_queue.put(None)
        return ""

    if provider == "claude":
        prev_count = page.evaluate(
            "() => document.querySelectorAll('div[data-is-streaming]').length")
        box.press("Enter")
        print("[+] Submitted to Claude")
        wait_for_new_claude_block(page, prev_count)
        page.wait_for_timeout(400)
        if check_claude_limit(page):
            return "[CLAUDE_LIMIT_HIT]"
        page.wait_for_timeout(150)
        capture_claude_url_if_new(page)
        return poll_claude_until_stable(page, stream_queue)
    else:
        baseline_count = get_chatgpt_assistant_count(page)
        baseline_text = get_last_chatgpt_response_text(page)
        clicked = _chatgpt_click_send(page)
        if clicked:
            print("[+] Submitted to ChatGPT via JS send button")
        else:
            box.press("Enter")
            print("[+] Submitted to ChatGPT via Enter fallback")

        if not _chatgpt_submit_confirmed(page, baseline_count, typed):
            print("[!] ChatGPT submit not confirmed, retrying with alternate send")
            if clicked:
                box.press("Enter")
            else:
                clicked2 = _chatgpt_click_send(page)
                if not clicked2:
                    box.press("Enter")

            if not _chatgpt_submit_confirmed(page, baseline_count, typed):
                msg = "[WEBD ERROR] Message was not submitted to ChatGPT. Please retry."
                print("[!] " + msg)
                if stream_queue:
                    stream_queue.put(msg)
                    stream_queue.put(None)
                return "[WEBD_SUBMIT_FAILED]"

        page.wait_for_timeout(250)
        capture_chatgpt_url_if_new(page)
        return poll_chatgpt_response(page, stream_queue, baseline_count, baseline_text)


# ─────────────────────────────────────────────────────
# CORE AUTOMATION JOB
# ─────────────────────────────────────────────────────
def _automation_job(page, prompt: str, provider: str, stream_queue=None) -> str:
    page = ensure_alive_page(page)

    # ── Step 1: navigate to the correct site ─────────────────────────────────
    if provider == "claude":
        page = ensure_claude(page)
    else:
        page = ensure_chatgpt(page)

    # ── Step 2: health check AFTER navigation ─────────────────────────────────
    #   If the user closed the tab while we were navigating (or the saved URL
    #   is stale), ensure_healthy_page will force a fresh navigation.
    page = ensure_healthy_page(page, provider)

    # ── Step 3: submit and collect response ───────────────────────────────────
    response = submit_and_stream(page, prompt, provider, stream_queue)

    if response == "[WEBD_SUBMIT_FAILED]":
        if not stream_queue:
            return "[WEBD ERROR] Message was not submitted to ChatGPT. Please retry."
        return response

    if response == "[CLAUDE_LIMIT_HIT]":
        print("[!] Claude limit hit mid-session — switching to ChatGPT")
        notice = "[WEBD NOTICE] Claude limit detected. Fallback response is from ChatGPT.\n\n"
        if stream_queue:
            stream_queue.put(notice)
        page = ensure_chatgpt(page)
        page = ensure_healthy_page(page, "chatgpt")
        response = submit_and_stream(page, prompt, "chatgpt", stream_queue)
        if not stream_queue:
            response = notice + response
        increment_msg_count("chatgpt")
    else:
        if provider == "claude":
            # Clear stale limit flag once Claude answers normally again.
            set_claude_daily_limit(False)
        count = increment_msg_count(provider)
        print(f"[*] Message #{count} in this {provider} chat")

    return response


# ─────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────
def messages_to_prompt(messages: list) -> str:
    """Convert OpenAI/Ollama messages array to a single prompt string."""
    parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            content, _ = extract_user_content_for_prompt(content)
        elif isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role == "system":
            parts.append(f"[System]: {content}")
        elif role == "assistant":
            parts.append(f"[Assistant]: {content}")
        else:
            parts.append(content)
    return "\n".join(parts)


def parse_message_content_parts(content) -> tuple[str, list[str], bool]:
    """
    Parse OpenAI-style content payload into text + image URLs.
    Returns: (text, image_urls, had_image_part)
    """
    if isinstance(content, str):
        return content.strip(), [], False

    if not isinstance(content, list):
        return str(content or "").strip(), [], False

    texts = []
    image_urls = []
    had_image_part = False

    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type", "")).strip().lower()

        if ptype in {"text", "input_text"}:
            t = str(part.get("text", "") or "").strip()
            if t:
                texts.append(t)
            continue

        if ptype in {"image_url", "input_image", "image"}:
            had_image_part = True
            url = ""
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                url = str(image_url.get("url", "") or "").strip()
            elif isinstance(image_url, str):
                url = image_url.strip()
            if not url:
                url = str(part.get("url", "") or "").strip()
            if url and not url.startswith("data:"):
                image_urls.append(url)

    return " ".join(texts).strip(), image_urls, had_image_part


def extract_user_content_for_prompt(content) -> tuple[str, bool]:
    """
    Build a user prompt string from text/image parts.
    Image URLs are forwarded as plain text hints; binary-only image parts are flagged.
    Returns: (prompt_text, had_image_part)
    """
    raw_text, image_urls, had_image_part = parse_message_content_parts(content)
    cleaned = sanitize_user_text(raw_text) if raw_text else ""

    out = []
    if cleaned:
        out.append(cleaned)

    if had_image_part:
        if image_urls:
            out.append("User attached image URL(s):\n" + "\n".join(image_urls))
        else:
            out.append("User attached image data, but this WEBD bridge cannot upload binary/data-image content to the web UI yet.")

    return "\n\n".join(out).strip(), had_image_part


def latest_user_message(messages: list) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        prompt, _ = extract_user_content_for_prompt(content)
        if prompt:
            return prompt
    return ""


def sanitize_user_text(text: str) -> str:
    """
    OpenClaw sometimes sends wrapped transcript blocks (metadata/tool preamble + timestamp).
    Extract the latest user intent so ChatGPT receives only the actionable prompt.
    """
    t = (text or "").replace("\r\n", "\n").strip()
    if not t:
        return ""

    # If message is timestamp-wrapped, remove only the [timestamp] markers,
    # but keep the full multiline user content.
    if re.search(r"\[[^\]]+\]\s*", t):
        t = re.sub(r"\[[^\]]+\]\s*", "", t).strip()

    # Drop noisy metadata wrappers if present.
    t = re.sub(r"Sender\s*\(untrusted metadata\):[\s\S]*?\}\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"```[\s\S]*?```", "", t)

    # Remove frequent OpenClaw HEARTBEAT wrapper/instruction lines.
    noise_patterns = [
        r"^read\s+heartbeat\.md\b.*$",
        r"^when\s+reading\s+heartbeat\.md\b.*$",
        r"^current\s+time:\b.*$",
        r"^i\s+don['’]t\s+have\s+access\s+to\s+your\s+local\s+filesystem\b.*$",
        r"^please\s+paste\s+the\s+contents\s+here\b.*$",
        r"^follow\s+it\s+strictly\b.*$",
        r"^do\s+not\s+infer\s+or\s+repeat\s+old\s+tasks\b.*$",
        r"^if\s+nothing\s+needs\s+attention\b.*$",
        r"^[a-z]:[/\\].*heartbeat\.md.*$",
    ]
    filtered_lines = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s in {"```", "`"}:
            continue
        if s.startswith("```"):
            s = s[3:].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
        if not s:
            continue
        low = s.lower()
        if any(re.match(p, low, flags=re.IGNORECASE) for p in noise_patterns):
            continue
        if "heartbeat.md" in low:
            continue
        filtered_lines.append(s)
    if filtered_lines:
        t = "\n".join(filtered_lines).strip()
    else:
        # HEARTBEAT-only payload should not be forwarded to web LLM providers.
        return ""

    # Keep all meaningful user lines in order (supports multiline prompts).
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    kept = []
    for ln in lines:
        ln = re.sub(r"^`+(?=\w)", "", ln)
        ln = re.sub(r"(?<=\w)`+$", "", ln)
        low = ln.lower()
        if low.startswith(("sender", "assistant", "tool", "tools available", "understood", "current time")):
            continue
        if "heartbeat.md" in low:
            continue
        if low.startswith(("read heartbeat", "follow it strictly", "do not infer", "when reading heartbeat")):
            continue
        kept.append(ln)

    if kept:
        return "\n".join(kept)
    return t


def _extract_addition_expression(text: str):
    """Parse simple addition asks like 'add 10 and 20' or '10 + 20'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"\badd\b[^\d-]*(\d+(?:\.\d+)?)[^\d-]+(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1)), float(m.group(2))

    # Handles phrases like:
    # "type 10 and 20 in calculator and perform addition"
    m = re.search(
        r"\b(type|enter|put)\b[^\d-]*(\d+(?:\.\d+)?)[^\d-]+(\d+(?:\.\d+)?)(?=[\s\S]*\b(add|sum|plus|perform addition)\b)",
        text,
    )
    if m:
        return float(m.group(2)), float(m.group(3))

    # Fallback: if sentence explicitly asks to perform addition, use first 2 numbers.
    if re.search(r"\b(add|sum|plus|perform addition)\b", text):
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if len(nums) >= 2:
            return float(nums[0]), float(nums[1])

    return None


def _extract_search_query(text: str) -> str:
    """Extract query after 'search' while trimming trailing chained actions."""
    m = re.search(r"\bsearch\b\s+(.+)", text, flags=re.IGNORECASE)
    if not m:
        return ""

    q = m.group(1).strip()

    # Remove explicit browser destination tail.
    q = re.sub(r"\b(in|on)\s+(the\s+)?(firefox|edge|chrome|browser|broser)\b", "", q, flags=re.IGNORECASE).strip()

    # Stop query at the start of another actionable clause.
    q = re.split(
        r"\b(?:and|then|after that|next)\b\s+(?:open|launch|start|run|type|enter|put|add|sum|plus|perform)\b",
        q,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()

    # Strip wrapping quotes if present.
    q = q.strip(" \t\n\r\"'")
    return q


def _open_browser_url_windows(browser: str | None, url: str) -> tuple[bool, str]:
    """Open URL on Windows with a preferred browser when possible."""
    if os.name != "nt":
        return False, "Browser quick action is currently configured for Windows only."

    b = (browser or "").lower().strip()
    candidates = []
    if b == "edge":
        candidates = [["msedge.exe", url], ["msedge", url]]
    elif b == "firefox":
        candidates = [["firefox.exe", url], ["firefox", url]]
    elif b == "chrome":
        candidates = [["chrome.exe", url], ["chrome", url]]
    else:
        # No preferred browser -> let Windows open default browser.
        try:
            os.startfile(url)
            return True, "Opened URL in your default browser."
        except Exception as e:
            return False, f"Failed to open URL in default browser: {e}"

    for cmd in candidates:
        try:
            subprocess.Popen(cmd)
            return True, f"Opened URL in {b.title()}."
        except Exception:
            continue

    # Final fallback through default handler.
    try:
        os.startfile(url)
        return True, f"{b.title()} not found, opened URL in default browser."
    except Exception as e:
        return False, f"Failed to open browser URL: {e}"


def _send_addition_to_calculator_windows(a: float, b: float) -> tuple[bool, str]:
    """
    Best-effort automation: focus Calculator and type '<a>+<b>=' using SendKeys.
    """
    if os.name != "nt":
        return False, "Calculator key automation is supported on Windows only."

    # Windows SendKeys syntax uses {+} for plus key.
    expr = f"{a:g}{{+}}{b:g}="
    ps_script = (
        "$wshell = New-Object -ComObject WScript.Shell; "
        "$ok = $false; "
        "for ($i=0; $i -lt 8; $i++) { "
        "  if ($wshell.AppActivate('Calculator')) { $ok = $true; break } "
        "  Start-Sleep -Milliseconds 250; "
        "}; "
        "if ($ok) { "
        "  Start-Sleep -Milliseconds 500; "
        f"  $wshell.SendKeys('{expr}'); "
        "} else { "
        "  exit 2 "
        "}"
    )
    try:
        p = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-Command", ps_script,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rc = p.wait(timeout=5)
        if rc != 0:
            return False, "Could not focus Calculator window for typing."
        return True, "Typed addition into Calculator."
    except Exception as e:
        return False, f"Could not type into Calculator automatically: {e}"


def try_handle_local_action(prompt: str) -> str | None:
    """Run selected device actions directly instead of sending them to web chat."""
    # Disabled by default. Keep implementation below as commented-in-place future work.
    if not LOCAL_ACTIONS_ENABLED:
        return None

    clean = sanitize_user_text(prompt)
    low = clean.lower()
    raw = (prompt or "").replace("\r\n", "\n").strip()
    low_raw = raw.lower()
    scan_low = f"{low}\n{low_raw}" if low_raw else low

    # Modes:
    #   simple: execute locally when supported intent is detected
    #   always: force local execution whenever any supported intent is detected
    #   off: never intercept locally
    mode = os.getenv("WEBD_LOCAL_ACTION_MODE", "always").strip().lower()
    if mode in {"off", "0", "false", "no"}:
        return None

    notes = []

    calc_requested = bool(re.search(r"\b(open|launch|start|run)\b.*\b(calculator|calc)\b", scan_low) or low in {"calc", "calculator"})
    browser_open_requested = bool(re.search(r"\b(open|launch|start|run)\b.*\b(firefox|edge|chrome|browser|broser)\b", scan_low))
    search_requested = "search" in scan_low
    add_terms = _extract_addition_expression(low) or _extract_addition_expression(low_raw)

    multi_step = bool(re.search(r"\b(and|then|after that|also|next)\b", scan_low)) and \
                 sum([1 if calc_requested else 0,
                      1 if add_terms is not None else 0,
                      1 if search_requested else 0]) >= 2

    # Smart gating: in simple mode, only intercept if we can satisfy the whole intent set.
    if mode == "simple":
        local_intents = 0
        if calc_requested:
            local_intents += 1
        if add_terms is not None:
            local_intents += 1
        if search_requested:
            local_intents += 1
        if browser_open_requested and not search_requested:
            local_intents += 1

        if local_intents == 0:
            return None

    calc_result_text = None

    if calc_requested:
        if os.name != "nt":
            notes.append("Calculator quick action is currently configured for Windows only.")
        else:
            try:
                subprocess.Popen(["calc.exe"])
                notes.append("Opened Calculator on your Windows device.")
            except Exception as e:
                notes.append(f"Failed to open Calculator locally: {e}")

        if add_terms is not None:
            a, b = add_terms
            result = a + b
            if result.is_integer():
                result_text = str(int(result))
            else:
                result_text = str(result)
            calc_result_text = result_text
            notes.append(f"Calculated sum: {a:g} + {b:g} = {result_text}.")
            if os.name == "nt":
                ok, msg = _send_addition_to_calculator_windows(a, b)
                notes.append(msg)

    if browser_open_requested and not search_requested:
        browser = None
        if "firefox" in scan_low:
            browser = "firefox"
        elif "edge" in scan_low:
            browser = "edge"
        elif "chrome" in scan_low:
            browser = "chrome"

        ok, msg = _open_browser_url_windows(browser, "about:blank")
        notes.append(msg)

    if search_requested and any(k in scan_low for k in ("edge", "firefox", "chrome", "browser", "search")):
        query = _extract_search_query(clean)

        # Fallback: parse from raw prompt if sanitize collapsed user text.
        if (not query) and raw:
            m2 = re.search(
                r"\bsearch\b\s+['\"]?([^'\"\n]+?)['\"]?(?:\s+(?:in|on)\s+(?:the\s+)?(?:firefox|edge|chrome|browser|broser)\b|$)",
                raw,
                flags=re.IGNORECASE,
            )
            query = m2.group(1).strip() if m2 else ""

        query = re.sub(r"\b(in|on)\s+my\s+(laptop|pc|device)\b", "", query, flags=re.IGNORECASE).strip()
        query = re.sub(r"\b(then|and then)\b.*$", "", query, flags=re.IGNORECASE).strip()

        # Resolve references like "search that calculator result" after an addition.
        if calc_result_text and re.search(r"\b(that|the)\s+calculator\s+result\b", query, flags=re.IGNORECASE):
            query = re.sub(r"\b(that|the)\s+calculator\s+result\b", calc_result_text, query, flags=re.IGNORECASE)

        if query:
            browser = None
            if "firefox" in scan_low:
                browser = "firefox"
            elif "edge" in scan_low:
                browser = "edge"
            elif "chrome" in scan_low:
                browser = "chrome"

            url = f"https://www.bing.com/search?q={quote_plus(query)}"
            ok, msg = _open_browser_url_windows(browser, url)
            if ok:
                notes.append(f"{msg} Query: {query}")
            else:
                notes.append(msg)
        else:
            notes.append("I could not parse the search text after 'search'.")

    if notes:
        return " ".join(notes)

    return None


def build_ollama_generate_prompt(data: dict) -> str:
    prompt = str(data.get("prompt", "") or "").strip()
    if not prompt:
        return ""

    system = str(data.get("system", "") or "").strip()
    suffix = str(data.get("suffix", "") or "").strip()
    raw    = bool(data.get("raw", False))

    if raw:
        return prompt

    parts = []
    if system:
        parts.append(f"[System]: {system}")
    parts.append(prompt)
    if suffix:
        parts.append(f"[Suffix]: {suffix}")
    return "\n".join(parts).strip()


def build_ollama_chat_prompt(messages: list) -> str:
    if CHAT_PROMPT_MODE == "full":
        return messages_to_prompt(messages)

    # Default: latest_user (faster and avoids replaying noisy prior assistant/tool text)
    latest = latest_user_message(messages)
    return latest


def do_ask(prompt: str, provider: str, stream: bool):
    """
    Returns (response_text, stream_queue) pair.
    If stream=True, response_text is "" and caller reads from stream_queue.
    """
    local_action_result = try_handle_local_action(prompt)
    if local_action_result is not None:
        print(f"[LOCAL ACTION] Executed locally: {prompt[:120]!r}")
        if stream:
            sq = queue.Queue()
            sq.put(local_action_result)
            sq.put(None)
            return "", sq
        return local_action_result, None
    else:
        p = (prompt or "").lower()
        if any(k in p for k in ("open", "search", "calculator", "calc", "firefox", "edge", "chrome")):
            print(f"[LOCAL ACTION] Not matched, forwarding to {provider}: {prompt[:120]!r}")

    _ready.wait(timeout=30)
    if stream:
        sq = queue.Queue()
        def run():
            try:   run_in_browser(_automation_job, prompt, provider, sq)
            except Exception as e:
                print(f"[ERROR] do_ask stream: {e}")
                sq.put(None)
        threading.Thread(target=run, daemon=True).start()
        return "", sq
    else:
        result = run_in_browser(_automation_job, prompt, provider, None)
        return result, None


# ═════════════════════════════════════════════════════
# OPENAI-COMPATIBLE APP  (default port 50001)
# ═════════════════════════════════════════════════════

@openai_app.route("/", methods=["GET"])
def oa_home():
    data = load_sessions()
    return jsonify({
        "service":  "WEBD v12 - CYBREIGN",
        "version":  "12.0.0",
        "ollama_port": ACTIVE_OLLAMA_PORT,
        "openai_port": ACTIVE_OPENAI_PORT,
        "sessions": data["sessions"],
        "claude_daily_limit": data["claude_daily_limit"],
    })


@openai_app.route("/v1/models", methods=["GET"])
def oa_models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": "claude",  "object": "model", "created": 1700000000, "owned_by": "cybreign"},
            {"id": "chatgpt", "object": "model", "created": 1700000000, "owned_by": "cybreign"},
            {"id": "llama3",  "object": "model", "created": 1700000000, "owned_by": "cybreign"},
        ]
    })


@openai_app.route("/v1/chat/completions", methods=["POST"])
def oa_chat_completions():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    messages = data.get("messages", [])
    stream   = data.get("stream", False)
    model    = data.get("model", "claude")
    provider = resolve_provider(model)

    prompt = ""
    saw_user_image = False
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            built_prompt, had_image = extract_user_content_for_prompt(content)
            if had_image:
                saw_user_image = True
            if built_prompt:
                prompt = built_prompt
                break
    if not prompt:
        if saw_user_image:
            return jsonify({
                "error": "Image-only requests are not fully supported yet. Add text context or image_url links in the message."
            }), 400
        return jsonify({"error": "No user message found"}), 400

    req_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    response_text, sq = do_ask(prompt, provider, stream)

    if stream:
        def generate():
            while True:
                try:
                    chunk = sq.get(timeout=MAX_WAIT_SEC + 10)
                except queue.Empty:
                    chunk = None
                if chunk is None:
                    end = {"id": req_id, "object": "chat.completion.chunk",
                           "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(end, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
                    return
                payload = {"id": req_id, "object": "chat.completion.chunk",
                           "choices": [{"delta": {"content": chunk},
                                        "index": 0, "finish_reason": None}]}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return Response(stream_with_context(generate()),
                        content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    else:
        return jsonify({
            "id": req_id, "object": "chat.completion", "model": provider,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": response_text},
                         "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens":     len(prompt.split()),
                "completion_tokens": len(response_text.split()),
                "total_tokens":      len(prompt.split()) + len(response_text.split())
            }
        })


@openai_app.route("/ask", methods=["POST"])
def oa_ask():
    data = request.get_json(force=True, silent=True)
    if not data: return jsonify({"error": "No JSON body"}), 400
    prompt   = data.get("prompt", "").strip()
    target   = data.get("target", "claude_web").strip()
    provider = resolve_provider(target)
    if not prompt: return jsonify({"error": "'prompt' required"}), 400
    result, _ = do_ask(prompt, provider, False)
    return jsonify({"status": "success", "response": result, "provider": provider})


@openai_app.route("/sessions", methods=["GET"])
def oa_sessions():
    return jsonify(load_sessions())


@openai_app.route("/sessions/reset", methods=["POST"])
def oa_reset():
    data = {"sessions": [], "claude_daily_limit": False,
            "active_claude_index": None, "active_chatgpt_index": None}
    save_sessions(data)
    return jsonify({"status": "sessions reset"})


@openai_app.route("/sessions/claude-limit", methods=["POST"])
def oa_claude_limit():
    body = request.get_json(force=True, silent=True) or {}
    hit  = body.get("hit", True)
    set_claude_daily_limit(bool(hit))
    return jsonify({"claude_daily_limit": bool(hit)})


# ═════════════════════════════════════════════════════
# OLLAMA-COMPATIBLE APP  (default port 50000)
# ═════════════════════════════════════════════════════

OLLAMA_MODELS = [
    {"name": "claude:latest",  "model": "claude",  "modified_at": "2025-01-01T00:00:00Z",
     "size": 0, "digest": "webd-claude",
     "details": {"format": "gguf", "family": "claude",
                 "parameter_size": "unknown", "quantization_level": "web"}},
    {"name": "chatgpt:latest", "model": "chatgpt", "modified_at": "2025-01-01T00:00:00Z",
     "size": 0, "digest": "webd-chatgpt",
     "details": {"format": "gguf", "family": "gpt",
                 "parameter_size": "unknown", "quantization_level": "web"}},
    {"name": "llama3:latest",  "model": "llama3",  "modified_at": "2025-01-01T00:00:00Z",
     "size": 0, "digest": "webd-llama3",
     "details": {"format": "gguf", "family": "llama",
                 "parameter_size": "8B", "quantization_level": "web"}},
    {"name": "llama3.2:latest","model": "llama3.2","modified_at": "2025-01-01T00:00:00Z",
     "size": 0, "digest": "webd-llama3.2",
     "details": {"format": "gguf", "family": "llama",
                 "parameter_size": "3B", "quantization_level": "web"}},
    {"name": "mistral:latest", "model": "mistral", "modified_at": "2025-01-01T00:00:00Z",
     "size": 0, "digest": "webd-mistral",
     "details": {"format": "gguf", "family": "mistral",
                 "parameter_size": "7B", "quantization_level": "web"}},
]


@ollama_app.route("/", methods=["GET"])
def ol_home():
    return jsonify({"status": "WEBD v12 (Ollama mode) - CYBREIGN"})


@ollama_app.route("/api/version", methods=["GET"])
def ol_version():
    return jsonify({"version": "0.3.6"})


@ollama_app.route("/api/tags", methods=["GET"])
def ol_tags():
    return jsonify({"models": OLLAMA_MODELS})


@ollama_app.route("/api/show", methods=["POST"])
def ol_show():
    data  = request.get_json(force=True, silent=True) or {}
    name  = data.get("name", "claude")
    model = next((m for m in OLLAMA_MODELS if m["model"] == name.split(":")[0]), OLLAMA_MODELS[0])
    return jsonify({
        "modelfile":  f"# WEBD web-backed model: {name}",
        "parameters": "num_ctx 4096",
        "template":   "{{ .Prompt }}",
        "details":    model["details"],
        "model_info": {"general.architecture": "web"}
    })


@ollama_app.route("/api/pull", methods=["POST"])
def ol_pull():
    data   = request.get_json(force=True, silent=True) or {}
    name   = data.get("name", "llama3")
    stream = data.get("stream", True)

    def generate():
        msgs = [
            {"status": f"pulling manifest for {name}"},
            {"status": "pulling layer", "digest": "sha256:webd",
             "total": 1000, "completed": 1000},
            {"status": "verifying sha256 digest"},
            {"status": "writing manifest"},
            {"status": "removing any unused layers"},
            {"status": "success"},
        ]
        for m in msgs:
            yield json.dumps(m, ensure_ascii=False) + "\n"

    if stream:
        return Response(stream_with_context(generate()),
                        content_type="application/x-ndjson")
    return jsonify({"status": "success"})


@ollama_app.route("/api/generate", methods=["POST"])
def ol_generate():
    """
    Ollama /api/generate — single prompt, optional streaming.
    Used by OpenClaw and most basic Ollama clients.
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    model    = data.get("model", "claude")
    prompt   = build_ollama_generate_prompt(data)
    stream   = data.get("stream", True)
    provider = resolve_provider(model)

    # Keep /api/generate stateful unless request explicitly asks for stateless.
    # This prevents unwanted new chats when users switch models in clients like OpenClaw.
    request_stateless = bool(data.get("stateless", False))
    if GENERATE_STATELESS and request_stateless:
        invalidate_session(provider)

    if not prompt:
        return jsonify({"error": "'prompt' is required"}), 400

    created_at   = datetime.utcnow().isoformat() + "Z"
    response_txt, sq = do_ask(prompt, provider, stream)

    if stream:
        def generate():
            full = ""
            while True:
                try:
                    chunk = sq.get(timeout=MAX_WAIT_SEC + 10)
                except queue.Empty:
                    chunk = None
                if chunk is None:
                    done_obj = {
                        "model": model, "created_at": created_at,
                        "response": "", "done": True,
                        "done_reason": "stop",
                        "context": [],
                        "total_duration":    0,
                        "load_duration":     0,
                        "prompt_eval_count": len(prompt.split()),
                        "eval_count":        len(full.split()),
                        "eval_duration":     0
                    }
                    yield json.dumps(done_obj, ensure_ascii=False) + "\n"
                    return
                full += chunk
                chunk_obj = {
                    "model":      model,
                    "created_at": created_at,
                    "response":   chunk,
                    "done":       False
                }
                yield json.dumps(chunk_obj, ensure_ascii=False) + "\n"

        return Response(stream_with_context(generate()),
                        content_type="application/x-ndjson")
    else:
        return jsonify({
            "model":      model,
            "created_at": created_at,
            "response":   response_txt,
            "done":       True,
            "done_reason": "stop",
            "context": [],
            "total_duration":    0,
            "load_duration":     0,
            "prompt_eval_count": len(prompt.split()),
            "eval_count":        len(response_txt.split()),
            "eval_duration":     0
        })


@ollama_app.route("/api/chat", methods=["POST"])
def ol_chat():
    """
    Ollama /api/chat — messages array with roles, optional streaming.
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    model    = data.get("model", "claude")
    messages = data.get("messages", [])
    stream   = data.get("stream", True)
    provider = resolve_provider(model)

    prompt = build_ollama_chat_prompt(messages)
    if not prompt:
        return jsonify({"error": "No messages provided"}), 400

    created_at = datetime.utcnow().isoformat() + "Z"
    response_txt, sq = do_ask(prompt, provider, stream)

    if stream:
        def generate():
            full = ""
            while True:
                try:
                    chunk = sq.get(timeout=MAX_WAIT_SEC + 10)
                except queue.Empty:
                    chunk = None
                if chunk is None:
                    done_obj = {
                        "model":      model,
                        "created_at": created_at,
                        "message":    {"role": "assistant", "content": ""},
                        "done":       True,
                        "done_reason": "stop",
                        "total_duration":    0,
                        "load_duration":     0,
                        "prompt_eval_count": len(prompt.split()),
                        "eval_count":        len(full.split()),
                        "eval_duration":     0
                    }
                    yield json.dumps(done_obj, ensure_ascii=False) + "\n"
                    return
                full += chunk
                chunk_obj = {
                    "model":      model,
                    "created_at": created_at,
                    "message":    {"role": "assistant", "content": chunk},
                    "done":       False
                }
                yield json.dumps(chunk_obj, ensure_ascii=False) + "\n"

        return Response(stream_with_context(generate()),
                        content_type="application/x-ndjson")
    else:
        return jsonify({
            "model":      model,
            "created_at": created_at,
            "message":    {"role": "assistant", "content": response_txt},
            "done":       True,
            "done_reason": "stop",
            "total_duration": 0, "load_duration": 0,
            "prompt_eval_count": len(prompt.split()),
            "eval_count": len(response_txt.split()),
            "eval_duration": 0
        })


@ollama_app.route("/api/embeddings", methods=["POST"])
def ol_embeddings():
    """Fake embeddings endpoint — returns zero vector (compatibility stub)."""
    data  = request.get_json(force=True, silent=True) or {}
    model = data.get("model", "nomic-embed-text")
    return jsonify({
        "model":     model,
        "embedding": [0.0] * 384
    })


@ollama_app.route("/api/delete", methods=["DELETE"])
def ol_delete():
    return jsonify({"status": "deleted"})


@ollama_app.route("/api/copy", methods=["POST"])
def ol_copy():
    return jsonify({"status": "copied"})


# Mirror OpenAI endpoints on ollama port for wide compatibility
@ollama_app.route("/v1/models", methods=["GET"])
def ol_v1_models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": m["model"], "object": "model",
             "created": 1700000000, "owned_by": "cybreign"}
            for m in OLLAMA_MODELS
        ]
    })


def _openai_chat_handler():
    """Shared OpenAI chat handler callable from both apps."""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    messages = data.get("messages", [])
    stream   = data.get("stream", False)
    model    = data.get("model", "claude")
    provider = resolve_provider(model)

    prompt = ""
    saw_user_image = False
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            built_prompt, had_image = extract_user_content_for_prompt(content)
            if had_image:
                saw_user_image = True
            if built_prompt:
                prompt = built_prompt
                break
    if not prompt:
        if saw_user_image:
            return jsonify({
                "error": "Image-only requests are not fully supported yet. Add text context or image_url links in the message."
            }), 400
        return jsonify({"error": "No user message found"}), 400

    req_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    response_text, sq = do_ask(prompt, provider, stream)

    if stream:
        def generate():
            while True:
                try:    chunk = sq.get(timeout=MAX_WAIT_SEC + 10)
                except: chunk = None
                if chunk is None:
                    end = {"id": req_id, "object": "chat.completion.chunk",
                           "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(end, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
                    return
                payload = {"id": req_id, "object": "chat.completion.chunk",
                           "choices": [{"delta": {"content": chunk},
                                        "index": 0, "finish_reason": None}]}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        return Response(stream_with_context(generate()),
                        content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache"})
    else:
        return jsonify({
            "id": req_id, "object": "chat.completion", "model": provider,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": response_text},
                         "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens":     len(prompt.split()),
                "completion_tokens": len(response_text.split()),
                "total_tokens":      len(prompt.split()) + len(response_text.split())
            }
        })


@ollama_app.route("/v1/chat/completions", methods=["POST"])
def ol_v1_chat():
    return _openai_chat_handler()


# Session management on ollama port too
@ollama_app.route("/sessions", methods=["GET"])
def ol_sessions():
    return jsonify(load_sessions())

@ollama_app.route("/sessions/reset", methods=["POST"])
def ol_reset():
    data = {"sessions": [], "claude_daily_limit": False,
            "active_claude_index": None, "active_chatgpt_index": None}
    save_sessions(data)
    return jsonify({"status": "sessions reset"})

@ollama_app.route("/sessions/claude-limit", methods=["POST"])
def ol_claude_limit():
    body = request.get_json(force=True, silent=True) or {}
    hit  = body.get("hit", True)
    set_claude_daily_limit(bool(hit))
    return jsonify({"claude_daily_limit": bool(hit)})


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

    def open_folder(i, _): subprocess.Popen(f'explorer "{OUTPUT_FOLDER}"')
    def view_sessions(i, _): subprocess.Popen(f'notepad "{SESSION_FILE}"')
    def quit_app(i, _): i.stop(); os._exit(0)

    icon = pystray.Icon("WEBD", img, "WEBD v12 - CYBREIGN",
        menu=pystray.Menu(
            pystray.MenuItem("Open Output Folder", open_folder),
            pystray.MenuItem("View Sessions JSON",  view_sessions),
            pystray.MenuItem("Quit", quit_app),
        ))
    threading.Thread(target=icon.run, daemon=True).start()
    print("[+] Tray icon active")


# ─────────────────────────────────────────────────────
# SETUP MODE
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
    print("\n[+] Done! Run: python webd.py\n")
    sys.exit(0)


# ─────────────────────────────────────────────────────
# FIND FREE PORT
# ─────────────────────────────────────────────────────
def find_free_port_incremental(start_port: int, reserved: set[int] | None = None) -> int:
    reserved = reserved or set()
    start = max(1024, int(start_port))
    for port in range(start, 65531):
        if port in reserved:
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", port)); s.close()
            if port != start:
                print(f"[!] Port {start} busy, auto-incremented to {port}")
            return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports found from {start} to 65530")


def run_http_app(app, host: str, port: int, label: str):
    """
    Serve with Waitress by default (production-friendly),
    fallback to Flask dev server when Waitress is unavailable.
    """
    use_waitress = os.getenv("WEBD_USE_WAITRESS", "1").strip().lower() not in {"0", "false", "no"}
    if HAS_WAITRESS and use_waitress:
        threads = int(os.getenv("WEBD_WAITRESS_THREADS", "32"))
        print(f"[+] {label} started with Waitress on {host}:{port} (threads={threads})")
        waitress_serve(app, host=host, port=port, threads=threads)
        return

    print(f"[!] Waitress not active for {label}; using Flask development server")
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


def _safe_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        print(f"[!] Invalid {name}='{raw}', using {default}")
        return default


# ─────────────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup_mode()

    threading.Thread(target=playwright_worker, daemon=True).start()
    start_tray()

    base_port_raw = os.getenv("WEBD_BASE_PORT", "").strip()
    if base_port_raw:
        base_port = _safe_int_env("WEBD_BASE_PORT", 50000)
        preferred_ollama = base_port
        preferred_openai = base_port + 1
    else:
        preferred_ollama = _safe_int_env("WEBD_OLLAMA_PORT", 50000)
        preferred_openai = _safe_int_env("WEBD_OPENAI_PORT", 50001)

    ollama_port = find_free_port_incremental(preferred_ollama)
    openai_port = find_free_port_incremental(preferred_openai, reserved={ollama_port})
    ACTIVE_OLLAMA_PORT = ollama_port
    ACTIVE_OPENAI_PORT = openai_port

    print("=" * 65)
    print("  WEBD v12 — CYBREIGN  [Dual-Port API Server]")
    print("=" * 65)
    provider_policy = os.getenv("WEBD_PROVIDER_POLICY", "chatgpt_default").strip().lower()
    print(f"  Provider Policy : {provider_policy}")
    print(f"  Local Actions   : {'enabled' if LOCAL_ACTIONS_ENABLED else 'disabled'}")
    print(f"  Generate Mode   : {'stateless' if GENERATE_STATELESS else 'stateful'}")
    print(f"  Ollama API  → http://localhost:{ollama_port}")
    print(f"  OpenAI API  → http://localhost:{openai_port}/v1")
    print()
    print("  OpenClaw Setup:")
    print(f"    Provider : Ollama")
    print(f"    URL      : http://localhost:{ollama_port}")
    print(f"    Model    : chatgpt  (Claude works if explicitly requested)")
    print()
    print("  KEY FIX (v12): If you close the browser tab,")
    print("  WEBD auto-detects it and opens a fresh chat.")
    print()
    print(f"  Sessions : {SESSION_FILE}")
    print(f"  Setup    : python webd.py --setup")
    print("=" * 65)

    def run_ollama():
        run_http_app(ollama_app, host="0.0.0.0", port=ollama_port, label="Ollama API")

    threading.Thread(target=run_ollama, daemon=True).start()
    print(f"[+] Ollama API started on port {ollama_port}")

    run_http_app(openai_app, host="0.0.0.0", port=openai_port, label="OpenAI API")