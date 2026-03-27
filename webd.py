"""
AI Automator v9 - CYBREIGN
===========================
Key fixes:
- Completion detection by polling response text growth (no button watching)
- Reuses same chat tab — no new chat per request
- Only opens new chat when needed (first run or chat too long)

Install:
  pip install flask playwright playwright-stealth pystray pillow
  playwright install

First time:
  python ai_automator.py --setup

Every time:
  python ai_automator.py
"""

import os
import sys
import socket
import queue
import threading
import subprocess
from datetime import datetime

from flask import Flask, request, jsonify

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("[!] pip install playwright && playwright install")
    sys.exit(1)

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ─────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────
PROFILE_DIR      = os.path.join(os.path.expanduser("~"), "cybreign_browser_profile")
OUTPUT_FOLDER    = os.path.join(os.path.expanduser("~"), "Desktop", "AI_Responses")
NOTEPAD_PATH     = "notepad.exe"
MAX_WAIT_SEC     = 120    # max seconds to wait for response
STABLE_SEC       = 3.0    # seconds of no text growth = response done
POLL_MS          = 400    # how often to check (ms)
MAX_MSG_PER_CHAT = 20     # open new chat after this many messages

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(PROFILE_DIR,   exist_ok=True)

app           = Flask(__name__)
_job_queue    = queue.Queue()
_worker_ready = threading.Event()


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
        args                = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ],
        ignore_https_errors = True,
    )
    pages = browser.pages
    page  = pages[0] if pages else browser.new_page()

    if HAS_STEALTH:
        stealth_sync(page)
    page.set_extra_http_headers({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })

    print("[+] Browser ready")
    _worker_ready.set()

    while True:
        job = _job_queue.get()
        if job is None:
            break
        fn, args, box = job
        try:
            box["result"] = fn(page, *args)
        except Exception as e:
            box["error"] = str(e)
            print(f"[ERROR] {e}")
        finally:
            box["done"].set()

    browser.close()
    pw.stop()


def run_in_browser(fn, *args):
    box = {"done": threading.Event()}
    _job_queue.put((fn, args, box))
    box["done"].wait(timeout=180)
    if "error" in box:
        raise Exception(box["error"])
    return box.get("result", "")


# ─────────────────────────────────────────────────────
# CLOUDFLARE BYPASS + NAVIGATION
# ─────────────────────────────────────────────────────
def navigate(page, url: str, ready_selector: str):
    print(f"[*] Opening {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    for _ in range(20):
        html = page.content().lower()
        if any(x in html for x in ["security verification", "cf-challenge", "checking your browser", "just a moment"]):
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
        raise Exception(f"Page did not load. Screenshot: {shot}")


# ─────────────────────────────────────────────────────
# ENSURE CLAUDE IS OPEN (reuse tab, only navigate when needed)
# ─────────────────────────────────────────────────────
_claude_loaded   = False   # True after first navigation
_claude_msg_count = 0      # track messages sent in current chat

def ensure_claude_ready(page):
    global _claude_loaded, _claude_msg_count

    # If never opened, or chat is getting too long — open fresh chat
    if not _claude_loaded or _claude_msg_count >= MAX_MSG_PER_CHAT:
        navigate(page, "https://claude.ai/new", 'div[contenteditable="true"]')
        _claude_loaded    = True
        _claude_msg_count = 0
        return

    # Otherwise just make sure the input box is still there
    try:
        page.wait_for_selector('div[contenteditable="true"]', state="visible", timeout=5_000)
        print("[+] Reusing existing Claude chat")
    except PWTimeout:
        print("[!] Input not found — reopening Claude")
        navigate(page, "https://claude.ai/new", 'div[contenteditable="true"]')
        _claude_msg_count = 0


# ─────────────────────────────────────────────────────
# WAIT FOR RESPONSE TO FINISH
#
# Strategy: count the number of response blocks before submitting.
# After submitting, watch for a NEW block to appear, then wait
# until its text stops growing for STABLE_SEC seconds.
# This is reliable regardless of what buttons Claude shows.
# ─────────────────────────────────────────────────────
def get_last_response_text(page) -> str:
    """JS to get innerText of last data-is-streaming block."""
    return page.evaluate("""() => {
        const blocks = document.querySelectorAll('div[data-is-streaming]');
        if (!blocks.length) return '';
        return blocks[blocks.length - 1].innerText || '';
    }""")


def wait_for_new_response(page, prev_count: int):
    """Wait until a new response block appears (Claude started answering)."""
    print("[*] Waiting for Claude to start...")
    import time
    deadline = time.time() + 15
    while time.time() < deadline:
        page.wait_for_timeout(POLL_MS)
        count = page.evaluate("() => document.querySelectorAll('div[data-is-streaming]').length")
        if count > prev_count:
            print(f"[+] New response block appeared ({count} total)")
            return True
    print("[!] No new block appeared — checking if response already present")
    return False


def wait_until_stable(page):
    """Poll response text until it stops growing for STABLE_SEC seconds."""
    import time
    print(f"[*] Watching response grow (stable for {STABLE_SEC}s = done)...")

    last_text    = ""
    last_changed = time.time()
    deadline     = time.time() + MAX_WAIT_SEC

    while time.time() < deadline:
        page.wait_for_timeout(POLL_MS)
        try:
            cur_text = get_last_response_text(page)
        except Exception:
            continue

        if cur_text != last_text:
            # Text changed — reset stable timer
            last_text    = cur_text
            last_changed = time.time()
            elapsed      = time.time() - last_changed
            # Print progress every ~3 seconds
        else:
            stable_for = time.time() - last_changed
            if len(cur_text) > 10:
                print(f"[*] {len(cur_text)} chars, stable for {stable_for:.1f}s...")
            if stable_for >= STABLE_SEC:
                print(f"[+] Response stable — done! ({len(cur_text)} chars)")
                return cur_text

    print(f"[!] Hit {MAX_WAIT_SEC}s limit — grabbing whatever is there")
    return last_text


# ─────────────────────────────────────────────────────
# EXTRACT FULL RESPONSE (clean text only)
# ─────────────────────────────────────────────────────
def extract_last_response(page) -> str:
    # Primary: last data-is-streaming block via JS
    text = page.evaluate("""() => {
        const blocks = document.querySelectorAll('div[data-is-streaming]');
        if (!blocks.length) return '';
        return blocks[blocks.length - 1].innerText || '';
    }""").strip()

    if len(text) > 30:
        return text

    # Fallback: join all font-claude-response paragraphs
    try:
        all_blocks = page.locator("div.font-claude-response").all()
        if all_blocks:
            combined = "\n\n".join(b.inner_text().strip() for b in all_blocks if b.inner_text().strip())
            if len(combined) > 30:
                return combined
    except Exception:
        pass

    shot = os.path.join(OUTPUT_FOLDER, "debug.png")
    page.screenshot(path=shot)
    return f"[ERROR] Could not extract response. Debug screenshot saved: {shot}"


# ─────────────────────────────────────────────────────
# CLAUDE JOB
# ─────────────────────────────────────────────────────
def _claude_job(page, prompt: str) -> str:
    global _claude_msg_count

    ensure_claude_ready(page)

    # Count existing response blocks before submitting
    prev_count = page.evaluate("() => document.querySelectorAll('div[data-is-streaming]').length")
    print(f"[*] Current response blocks: {prev_count}")

    # Type and submit
    box = page.locator('div[contenteditable="true"]').first
    box.click()
    page.wait_for_timeout(300)
    box.press("Control+a")
    box.press("Delete")
    page.wait_for_timeout(200)
    box.type(prompt, delay=35)
    page.wait_for_timeout(400)

    typed = box.inner_text().strip()
    print(f"[*] Typed: '{typed[:60]}'")

    box.press("Enter")
    _claude_msg_count += 1
    print(f"[+] Submitted (message #{_claude_msg_count} in this chat)")

    # Wait for new response block to appear
    wait_for_new_response(page, prev_count)

    # Watch until text stops growing
    wait_until_stable(page)

    # Extract final clean text
    response = extract_last_response(page)
    print(f"[+] Final: {len(response)} chars")
    return response


# ─────────────────────────────────────────────────────
# CHATGPT JOB (same stable-poll strategy)
# ─────────────────────────────────────────────────────
def _chatgpt_job(page, prompt: str) -> str:
    navigate(page, "https://chatgpt.com/", "div#prompt-textarea")

    box = page.locator("div#prompt-textarea").first
    box.click()
    page.wait_for_timeout(300)
    box.press("Control+a")
    box.press("Delete")
    page.wait_for_timeout(200)
    box.type(prompt, delay=35)
    page.wait_for_timeout(400)
    box.press("Enter")
    print("[+] Submitted to ChatGPT")

    import time
    last_text    = ""
    last_changed = time.time()
    deadline     = time.time() + MAX_WAIT_SEC

    while time.time() < deadline:
        page.wait_for_timeout(POLL_MS)
        try:
            blocks = page.locator("div.markdown.prose").all()
            cur = blocks[-1].inner_text().strip() if blocks else ""
        except Exception:
            continue
        if cur != last_text:
            last_text    = cur
            last_changed = time.time()
        else:
            if time.time() - last_changed >= STABLE_SEC and len(cur) > 10:
                print(f"[+] ChatGPT done ({len(cur)} chars)")
                return cur

    return last_text or "[ERROR] No response found"


# ─────────────────────────────────────────────────────
# SAVE + NOTEPAD
# ─────────────────────────────────────────────────────
def save_to_notepad(prompt: str, response: str, target: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(OUTPUT_FOLDER, f"response_{ts}.txt")
    content  = (
        "AI AUTOMATOR - CYBREIGN\n"
        "========================\n"
        f"Target : {target}\n"
        f"Date   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "========================\n\n"
        f"PROMPT:\n{prompt}\n\n"
        f"RESPONSE:\n{response}\n"
    )
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.Popen([NOTEPAD_PATH, filename])
    print(f"[+] Saved: {filename}")
    return filename


# ─────────────────────────────────────────────────────
# CORE
# ─────────────────────────────────────────────────────
def run_automation(prompt: str, target: str) -> dict:
    try:
        print(f"\n{'='*50}")
        print(f"[START] {target} | {prompt[:70]}")
        print(f"{'='*50}")
        _worker_ready.wait(timeout=30)

        if   target == "claude_web":  response = run_in_browser(_claude_job,  prompt)
        elif target == "chatgpt_web": response = run_in_browser(_chatgpt_job, prompt)
        else: return {"status": "error", "message": f"Unknown target '{target}'"}

        saved = save_to_notepad(prompt, response, target)
        print("[DONE]\n")
        return {"status": "success", "saved_to": saved, "response": response}
    except Exception as e:
        print(f"[ERROR] {e}")
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────
# FLASK
# ─────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "AI Automator v9 - CYBREIGN",
        "targets": ["claude_web", "chatgpt_web"],
        "chat_messages": _claude_msg_count,
        "max_per_chat": MAX_MSG_PER_CHAT
    })

@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "ok", "browser_ready": _worker_ready.is_set()})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    prompt = data.get("prompt", "").strip()
    target = data.get("target", "claude_web").strip()
    if not prompt:
        return jsonify({"error": "'prompt' required"}), 400
    return jsonify(run_automation(prompt, target))


# ─────────────────────────────────────────────────────
# TRAY
# ─────────────────────────────────────────────────────
def start_tray():
    if not HAS_TRAY:
        return
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 62, 62], fill=(34, 197, 94))
    draw.ellipse([18, 18, 46, 46], fill=(255, 255, 255))
    draw.ellipse([24, 24, 40, 40], fill=(34, 197, 94))
    def open_folder(icon, item): subprocess.Popen(f'explorer "{OUTPUT_FOLDER}"')
    def quit_app(icon, item):    icon.stop(); os._exit(0)
    icon = pystray.Icon("CYBREIGN", img, "CYBREIGN AI Automator",
        menu=pystray.Menu(
            pystray.MenuItem("Open Output Folder", open_folder),
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
    pw      = sync_playwright().start()
    browser = pw.chromium.launch_persistent_context(
        user_data_dir = PROFILE_DIR, channel = "chrome",
        headless = False, no_viewport = True,
        args = ["--start-maximized", "--disable-blink-features=AutomationControlled"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    if HAS_STEALTH:
        stealth_sync(page)
    page.goto("https://claude.ai/", wait_until="domcontentloaded")
    input("\n>>> Log into Claude fully, then press ENTER...")
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
    input(">>> Log into ChatGPT fully, then press ENTER...")
    browser.close()
    pw.stop()
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
            print("=" * 55)
            print("  AI Automator v9 - CYBREIGN")
            print(f"  API : http://localhost:{port}")
            print("  Tray: green icon near clock")
            print("  Setup: python ai_automator.py --setup")
            print("=" * 55)
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
            break
        except OSError:
            print(f"[!] Port {port} busy, trying next...")