"""Loopback-only inspector for the Jev browser agent."""

import atexit
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import Agent
from .browser import LostSession, StalePage
from .model import NoTextValue
from .questions import MAX_STEPS

ROOT = Path(__file__).parent
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENT = None
RUN = {}
NOTICE = {}


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def response_state():
    state = AGENT.snapshot() if AGENT else {"page": None, "status": "idle", "history": [], "decision": None}
    notice = NOTICE.pop("text", None)
    return {
        **state,
        "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "max_steps": MAX_STEPS,
        **({"notice": notice} if notice else {}),
    }


def close_browser():
    global AGENT
    if AGENT:
        try:
            AGENT.close()
        except Exception:
            pass  # the tab can already be gone after a dropped browser connection
        AGENT = None


def open_run(url, goal, *, reuse, scenario):
    """Start a run, reusing the tab already showing this page when asked to."""
    global AGENT
    close_browser()
    AGENT = Agent(url, goal, screenshots=True, reuse=reuse, record_dir=RUN.get("record_dir"))
    AGENT.state["scenario"] = scenario
    RUN.update(url=url, goal=goal, reuse=reuse, scenario=scenario)


def reopen():
    """Rebuild a run whose tab or CDP session died, so one dropped connection is not fatal."""
    close_browser()
    open_run(RUN["url"], RUN["goal"], reuse=RUN.get("reuse", False), scenario=RUN.get("scenario"))
    NOTICE["text"] = "The browser session was lost and the page was reopened. Choose again to continue."


def target_url(scenario, body):
    """The page this run opens: a fixture, Google Flights, or the requested http(s) page."""
    if scenario == "flights":
        return "https://www.google.com/travel/flights?hl=en"
    if scenario == "custom":
        requested = body.get("url", "").strip()
        parsed = urlparse(requested)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Enter a full http or https page address, for example https://example.com")
        return requested
    return f"{ORIGIN}/fixture.html?scenario={scenario}"


def command(name, body):
    global AGENT
    if name == "reset":
        scenario = body.get("scenario", "flights")
        if scenario not in {"travel", "research", "flights", "custom"}:
            raise ValueError("Unknown demo scenario")
        goal = body.get("goal", "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        url = target_url(scenario, body)
        RUN["record_dir"] = Path.cwd() / "artifacts" / "frames" if body.get("record") else None
        open_run(url, goal, reuse=bool(body.get("reuse")), scenario=scenario)
    else:
        if AGENT is None:
            raise ValueError("Start a demo first")
        try:
            AGENT.command(name, body)
        except LostSession:
            # The tab or session vanished mid-run; reopen the same page and let the user continue.
            reopen()
        except StalePage:
            # The page moved between the decision and the input. Nothing ran: re-observe instead of
            # reporting a failure the user did not cause.
            AGENT.refresh()
            NOTICE["text"] = (
                "The page moved before the action, so nothing was executed. "
                "It has been re-observed; choose again."
            )
        except NoTextValue:
            # The field needed words the helper would not supply. Nothing was typed, so the run is
            # still fresh; let the next choice pick another action.
            AGENT.refresh()
            NOTICE["text"] = (
                "The text helper returned no usable value, so nothing was typed. "
                "It has been re-observed; choose again."
            )
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime="application/json"):
        content = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state()))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        content = (ROOT / "static" / name).read_text().replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, ORIGIN)
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main():
    load_environment()
    atexit.register(close_browser)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jev Ultrafast: {ORIGIN}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
