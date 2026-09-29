"""Loopback-only inspector for the Jev browser agent."""

import atexit
import base64
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
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
RUNS = Path.cwd() / "artifacts" / "runs"
SNAPSHOT_FIELDS = (
    "id",
    "url",
    "start_url",
    "plan",
    "title",
    "goal",
    "status",
    "started_at",
    "finished_at",
    "elapsed_ms",
    "steps",
    "has_shot",
)


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def response_state():
    state = AGENT.snapshot() if AGENT else {"page": None, "status": "idle", "history": [], "decision": None}
    notice = NOTICE.pop("text", None)
    return {
        **state,
        "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "max_steps": MAX_STEPS,
        "run_id": (RUN.get("record") or {}).get("id"),
        "run_url": RUN.get("url"),
        "instructions": [item["text"] for item in (RUN.get("record") or {}).get("instructions") or []],
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


def save_run(force=False):
    """Write the current run to disk so this and later sessions can review it.

    Every command would rewrite several megabytes of model payload, so writes are rate limited
    except at the end of a run, where the final state is always stored.
    """
    record = RUN.get("record")
    if not record:
        return
    # The run's own status decides, not the stored one: the command that finishes a run must write it.
    status = AGENT.state.get("status") if AGENT else record.get("status")
    finished = record.get("status") == "error" or status in {"done", "blocked", "error"}
    if not force and not finished and time.monotonic() - RUN.get("saved", 0.0) < 2:
        return
    if AGENT:
        state = AGENT.snapshot()
        page = state.get("page") or {}
        record.update(
            status=state.get("status", record.get("status", "running")),
            elapsed_ms=state.get("elapsed_ms", record.get("elapsed_ms", 0)),
            steps=len(state.get("history") or []),
            url=page.get("url") or record.get("url", ""),
            title=page.get("title") or record.get("title", ""),
            history=state.get("history") or [],
            decisions=state.get("decisions") or [],
            text_calls=state.get("text_calls") or [],
            page_text=(page.get("text") or "")[:4000],
        )
        shot = page.get("screenshot")
        if shot and (finished or not record.get("has_shot")):
            RUNS.mkdir(parents=True, exist_ok=True)
            (RUNS / f"{record['id']}.jpg").write_bytes(base64.b64decode(shot))
            record["has_shot"] = True
    instructions = record.get("instructions") or []
    if instructions:
        last = instructions[-1]
        last["status"] = {"done": "done", "blocked": "blocked", "error": "error"}.get(
            record.get("status"), last.get("status", "running")
        )
        last["to_step"] = record.get("steps", 0)
    if record.get("status") in {"done", "blocked", "error"} and not record.get("finished_at"):
        record["finished_at"] = timestamp()
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{record['id']}.json").write_text(json.dumps(record, ensure_ascii=False))
    RUN["saved"] = time.monotonic()


def note_error(message, fatal=False):
    """Keep failures with the run that produced them instead of only in the UI.

    A rejected command (wrong order, missing page) is the user's input problem, not a failed run;
    only an execution failure marks the run itself as broken.
    """
    record = RUN.get("record")
    if record is None:
        return
    record.setdefault("errors", []).append({"at": timestamp(), "message": message})
    if fatal and record.get("status") in {"running", "ready", "predicted"}:
        record["status"] = "error"
    save_run(force=True)


def list_runs():
    """Newest first, summaries only: a run record carries the whole model payload."""
    runs = []
    for path in sorted(RUNS.glob("*.json"), reverse=True)[:200]:
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        record["start_url"] = start_url(record)
        runs.append({key: record.get(key) for key in SNAPSHOT_FIELDS})
    runs.sort(key=lambda run: run.get("started_at") or "", reverse=True)
    return runs


def load_run(name):
    if not name.replace("-", "").isalnum():
        raise ValueError("Unknown run")
    path = RUNS / f"{name}.json"
    if not path.exists():
        raise ValueError("Unknown run")
    record = json.loads(path.read_text())
    record["errors"] = record.get("errors") or []
    record["start_url"] = start_url(record)
    return record


def start_url(record):
    """Where a replay begins. Records from before ``start_url`` fall back to the first acted page."""
    first = (record.get("history") or [{}])[0]
    return record.get("start_url") or first.get("url") or record.get("url", "")


def delete_run(name):
    """Remove a stored run; the current run stops being recorded if it is the one removed."""
    load_run(name)
    for suffix in (".json", ".jpg"):
        (RUNS / f"{name}{suffix}").unlink(missing_ok=True)
    if (RUN.get("record") or {}).get("id") == name:
        RUN["record"] = None
    return {"deleted": name}


def open_run(url, instruction, *, reuse, record_dir=None, previous=None):
    """Start a run, reusing the tab already showing this page when asked to.

    ``previous`` continues an attempt whose tab was lost: the run keeps its identity and its trail.
    """
    global AGENT
    close_browser()
    AGENT = Agent(url, instruction, screenshots=True, reuse=reuse, record_dir=record_dir)
    plan = list((previous or {}).get("plan") or [instruction])
    if not previous:
        plan = [instruction]
    index = len(plan) - 1 if previous else 0
    run_id = (previous or {}).get("id") or (
        time.strftime("%Y%m%dT%H%M%S", time.localtime()) + "-" + secrets.token_hex(2)
    )
    RUN.clear()
    RUN.update(url=url, goal=instruction, reuse=reuse, saved=0.0, record_dir=record_dir)
    RUN["record"] = {
        "id": run_id,
        "url": url,
        "start_url": (previous or {}).get("start_url") or url,
        "goal": instruction,
        "plan": plan,
        "instructions": list((previous or {}).get("instructions") or [])
        + [{"text": instruction, "index": index, "status": "running", "from_step": 1, "at": timestamp()}],
        "reuse": reuse,
        "status": "running",
        "started_at": (previous or {}).get("started_at") or timestamp(),
        "finished_at": None,
        "elapsed_ms": 0,
        "steps": len((previous or {}).get("history") or []),
        "title": "",
        "history": list((previous or {}).get("history") or []),
        "decisions": list((previous or {}).get("decisions") or []),
        "text_calls": [],
        "errors": [],
        "has_shot": False,
    }
    save_run(force=True)



def reopen():
    """Rebuild a run whose tab or CDP session died, so one dropped connection is not fatal."""
    record = RUN.get("record") or {}
    open_run(
        RUN["url"],
        RUN["goal"],
        reuse=RUN.get("reuse", False),
        record_dir=RUN.get("record_dir"),
        previous=record,  # the same attempt, with the same identity and trail
    )
    NOTICE["text"] = "The browser session was lost and the page was reopened. Choose again to continue."


def target_url(body):
    """The page this run opens. http and https only: the inspector never loads anything else."""
    requested = body.get("url", "").strip()
    parsed = urlparse(requested)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a full http or https page address, for example https://example.com")
    return requested


def instruction_text(body, key="instruction"):
    text = (body.get(key) or body.get("goal") or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("Enter 1–2,000 characters")
    return text


def command(name, body):
    global AGENT
    if name.startswith("runs/") and name.endswith("/delete"):
        return delete_run(name.removeprefix("runs/").removesuffix("/delete"))
    if name == "clear":
        # A finished run belongs to the history, not to the console: opening the page again starts
        # clean. A run still in progress keeps its tab, its page, and its trail.
        close_browser()
        RUN.clear()
        NOTICE.pop("text", None)
        return response_state()
    if name in {"open", "reset"}:
        record_dir = Path.cwd() / "artifacts" / "frames" if body.get("record") else None
        RUN["record_dir"] = record_dir
        open_run(
            target_url(body),
            instruction_text(body),
            reuse=bool(body.get("reuse")),
            record_dir=record_dir,
        )
    elif name == "instruct":
        if AGENT is None:
            raise ValueError("Start a run first")
        text = instruction_text(body)
        record = RUN.get("record") or {}
        instructions = record.setdefault("instructions", [])
        steps = len(AGENT.state["history"])
        if instructions:
            instructions[-1].update(status=instructions[-1].get("status", "done"), to_step=steps)
        instructions.append(
            {
                "text": text,
                "index": len(instructions),
                "status": "running",
                "from_step": steps + 1,
                "at": timestamp(),
            }
        )
        plan = [item["text"] for item in instructions]
        AGENT.instruct(text, index=len(plan) - 1, plan=plan)
        record["goal"] = text
        record["plan"] = plan
        save_run(force=True)
    else:
        if AGENT is None:
            raise ValueError("Start a run first")
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
        save_run()
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
        if path == "/api/runs":
            return self.send(200, json.dumps({"runs": list_runs()}))
        if path.startswith("/api/runs/"):
            parts = path.removeprefix("/api/runs/").split("/")
            try:
                if len(parts) == 1:
                    return self.send(200, json.dumps(load_run(parts[0])))
                if len(parts) == 2 and parts[1] == "shot":
                    shot = RUNS / f"{parts[0]}.jpg"
                    if not shot.exists():
                        return self.send(404, "No screenshot", "text/plain")
                    return self.send(200, shot.read_bytes(), "image/jpeg")
            except (OSError, ValueError) as error:
                return self.send(404, json.dumps({"error": str(error)}))
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
            note_error(str(error), fatal=not isinstance(error, ValueError))
            self.send(400, json.dumps({"error": str(error)}))
        except Exception as error:
            note_error(f"{type(error).__name__}: {error}")
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main():
    load_environment()
    atexit.register(close_browser)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Browser Ultrafast: {ORIGIN}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
