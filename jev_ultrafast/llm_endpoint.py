"""A chat model behind the System One protocol: the agent's loop with Jev switched off.

The agent speaks one protocol and never learns which model answers it: it posts the observed page,
the indexed element table and typed questions to ``TYPESAFE_BASE_URL``, and reads one choice per
question head back. This module is that endpoint, backed by any OpenAI-compatible chat model.

    JEV_ENABLED=true   -> the hosted Jev (or any other System One endpoint) answers
    JEV_ENABLED=false  -> the inspector starts this endpoint in-process and the text model answers

Served on its own it is also usable from a terminal, which is what the mode below documents:

    uv run python -m jev_ultrafast.llm_endpoint --port 8770
    TYPESAFE_BASE_URL=http://127.0.0.1:8770/v1/systemone uv run jev

Wire contract
-------------
Caller -> this endpoint (an unchanged System One request):

    {"state": {"page": {url, title, text}, "elements": [...], "recent_actions": [...]},
     "questions": {"operation":   {"type": "choice", "criteria": {"CLICK": label, ..., "DONE": label},
                                   "instructions": {"goal": ..., "rules": ...}},
                   "click_target": {"type": "choice", "criteria": {"24": {"element": "[24] Search"}}},
                   "type_text_target": {...}, "select_target": {...}}}

This endpoint -> the chat model (one JSON message, one question, one answer):

    {"goal":  "<the caller's goal>",
     "rules": "<the joint rules below>",
     "page":  {"url": ..., "title": ..., "text": "<first page_chars characters>"},
     "actions": {"<operation>": "<what it means>",
                 "<operation> <element id>": "<what it means>: <element and its nearby text>"},
     "recent_actions": [{"action": ..., "page_changed": true|false, ...}]}

    system: "You choose exactly one next action for a browser agent. Reply with JSON only,
             shaped {\"action\": \"<one key from actions>\", \"confidence\": <0..1>}."

The chat model -> this endpoint (strict JSON, nothing else):

    {"action": "CLICK 24", "confidence": 0.8}

``action`` must be one of the keys of ``actions`` — an operation on its own (``DONE``, ``BLOCKED``)
or an operation plus one observed element id (``CLICK 24``). ``confidence`` is optional; when it is
absent or outside [0, 1] the answer is read at 0.9. The peak of the distribution handed back is
``max(confidence, 0.5)``: a chat model's self-reported number is not calibrated, and the caller
refuses a named choice that is not its own distribution's maximum, so a committed step is never
reported as a minority preference. A reply that is not readable, or that names a key that was not
offered, is asked for once more with the offending reply and the legal keys in the message; a second
failure is reported to the caller as a 400, so the loop executes nothing.

This endpoint -> caller (factored again, exactly what ``model.py`` validates):

    {"model": "chat:<model>",
     "answers": {"operation":    {"type": "choice", "choice": "CLICK", "confidence": 0.8,
                                  "probabilities": {"CLICK": 0.8, ...}},   # sums to 1
                 "click_target": {"type": "choice", "choice": "24", "confidence": 0.8,
                                  "probabilities": {"24": 0.8, ...}}}}

Only the chosen operation's target head is returned; the others were speculative and the caller
ignores them. Every returned distribution covers exactly the ids the caller offered, sums to 1, and
peaks on the returned choice, which is what ``validate_choice`` refuses otherwise.
"""

import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

# The rules travel with the question rather than living in the caller, because they describe the
# page conventions the answer has to respect. They are part of the contract above.
JOINT_RULES = (
    "Pick the single action that makes the most progress toward the goal. Prefer filling an empty or "
    "wrong field over clicking an unrelated element. Generic placeholder labels such as 请输入, 请选择, "
    "Search or Select are not field names: match each of those fields to the label that precedes it in "
    "the page text, in reading order. Opening, viewing or searching a record does not satisfy a goal "
    "that asks to change it: keep going until the requested change is made and submitted, and choose "
    "DONE only then."
)
SYSTEM = (
    "You choose exactly one next action for a browser agent. "
    'Reply with JSON only, shaped {"action": "<one key from actions>", "confidence": <number 0..1>}.'
)
DEFAULT_CONFIDENCE = 0.9
# A committed step is never handed back as a minority preference: the caller's validator refuses a
# named choice that is not the maximum of its own distribution.
MIN_PEAK = 0.5
DEFAULT_PAGE_CHARS = 4000
DEFAULT_PORT = 8770
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def load_env_file(path=None) -> None:
    """Fill os.environ from a .env file without overriding the real environment."""
    path = Path(path) if path else Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


class Candidate(NamedTuple):
    """One executable action rebuilt from the caller's factored heads."""

    operation: str
    index: Optional[str]
    label: str
    value: str


class Chooser:
    """One chat completion per decision, against an OpenAI-compatible endpoint."""

    def __init__(self, *, base_url: str, key: str, model: str, headers: Dict[str, str], timeout: float = 60.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.base_url = base_url
        self.key = key
        self.model = model
        self.headers = headers
        self.timeout = timeout

    def reasoning_flag(self) -> Dict[str, Any]:
        """The same switch ``model.py`` uses for the text helper, so one .env drives both."""
        deepseek = "api.deepseek.com/" in self.base_url
        flag = {"thinking": {"type": "disabled"}} if deepseek else {"reasoning": {"effort": "low"}}
        mode = os.environ.get("TEXT_MODEL_REASONING")
        if mode == "none":
            flag = {"reasoning": {"enabled": False}}
        elif mode == "thinking-disabled":
            flag = {"thinking": {"type": "disabled"}}
        return flag

    def ask(self, messages) -> str:
        """One round trip; raises RuntimeError with the endpoint's own words on failure."""
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 256,
            # A decision is one small JSON object; asking for JSON mode is what keeps a chat model
            # from answering a question about a page in prose.
            "response_format": {"type": "json_object"},
            **self.reasoning_flag(),
            "messages": messages,
        }
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"chat endpoint returned HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RuntimeError(f"chat endpoint unreachable ({type(error).__name__}: {error})") from None
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("chat endpoint answered without a message") from None


def chooser_from_env(*, base_url=None, model=None, key=None, timeout=60.0, page_chars=None) -> tuple:
    """Build a Chooser from TEXT_MODEL_*, plus the page budget it should send with a question."""
    base = base_url or os.environ.get("TEXT_MODEL_BASE_URL")
    chosen = model or os.environ.get("TEXT_MODEL")
    token = key if key is not None else os.environ.get("TEXT_MODEL_API_KEY", "")
    if not base or not chosen:
        raise ValueError("TEXT_MODEL_BASE_URL and TEXT_MODEL are required for the chat-model endpoint")
    headers = {"Content-Type": "application/json"}
    raw = os.environ.get("TEXT_MODEL_HEADERS")
    if raw:
        headers.update({k: v for k, v in json.loads(raw).items() if isinstance(v, str)})
    headers.setdefault("Authorization", f"Bearer {token or 'none'}")
    # Cloudflare in front of some gateways rejects the default Python-urllib agent (error 1010).
    headers.setdefault("User-Agent", "jev-ultrafast-llm/0.1")
    chars = page_chars if page_chars is not None else int(os.environ.get("LLM_PAGE_CHARS", DEFAULT_PAGE_CHARS))
    return Chooser(base_url=base, key=token, model=chosen, headers=headers, timeout=timeout), chars


def element_label(value: Any, fallback: Any) -> str:
    """The bare element label, without the ``[index] `` prefix the caller renders."""
    raw = value.get("element") if isinstance(value, dict) else None
    return str(raw or fallback).split("] ", 1)[-1]


def describe(value: Any, fallback: str) -> str:
    """One compact line for one candidate element."""
    if not isinstance(value, dict):
        return str(value)
    element = value.get("element") or fallback
    extras = [
        f"{key}={value[key]}"
        for key in ("nearby", "current_value", "role", "checked", "selected", "expanded")
        if value.get(key) not in (None, "")
    ]
    return f"{element} ({', '.join(extras)})" if extras else str(element)


def question_goal(questions: Dict[str, Any]) -> str:
    """The caller's goal, carried in the operation question's instructions."""
    instructions = (questions.get("operation") or {}).get("instructions")
    if isinstance(instructions, dict) and isinstance(instructions.get("goal"), str):
        return instructions["goal"]
    if isinstance(instructions, str):
        return instructions
    return json.dumps(instructions, ensure_ascii=False) if instructions else ""


def joint_actions(questions: Dict[str, Any]):
    """Rebuild the caller's factored heads as one flat action list.

    Returns ``(actions, mapping)``, or None when the request is not the factored shape this endpoint
    answers. ``actions`` maps one key per executable action to the line the model reads; ``mapping``
    says which operation and element id that key stands for.
    """
    operation_question = questions.get("operation")
    criteria = operation_question.get("criteria") if isinstance(operation_question, dict) else None
    if not isinstance(criteria, dict) or not criteria:
        return None
    actions: Dict[str, str] = {}
    mapping: Dict[str, Candidate] = {}
    for operation, label in criteria.items():
        head = questions.get(f"{operation.lower()}_target")
        targets = head.get("criteria") if isinstance(head, dict) else None
        text = label if isinstance(label, str) else json.dumps(label, ensure_ascii=False)
        if not isinstance(targets, dict) or not targets:
            actions[operation] = text  # DONE, BLOCKED, WAIT, SCROLL_*: one control action
            mapping[operation] = Candidate(operation, None, operation, "")
            continue
        for index, value in targets.items():
            key = f"{operation} {index}"
            actions[key] = f"{text}: {describe(value, index)}"
            current = value.get("current_value") if isinstance(value, dict) else None
            mapping[key] = Candidate(operation, index, element_label(value, index), str(current or ""))
    return actions, mapping


def peaked(keys, chosen: str, confidence: float) -> Dict[str, float]:
    """A valid distribution peaking on ``chosen``: it and the rest share exactly one."""
    others = [key for key in keys if key != chosen]
    share = round((1 - confidence) / len(others), 6) if others else 0.0
    probabilities = {key: share for key in others}
    probabilities[chosen] = round(1 - sum(probabilities.values()), 6)
    return probabilities


def answers_from_actions(probabilities: Dict[str, float], mapping: Dict[str, Candidate], model: str):
    """Marginalise one flat distribution back into the operation head and the winning target head."""
    operation_probabilities: Dict[str, float] = {}
    per_operation: Dict[str, Dict[str, float]] = {}
    for key, candidate in mapping.items():
        value = float(probabilities.get(key, 0.0))
        operation_probabilities[candidate.operation] = operation_probabilities.get(candidate.operation, 0.0) + value
        if candidate.index is not None:
            per_operation.setdefault(candidate.operation, {})[candidate.index] = value
    total = sum(operation_probabilities.values()) or 1.0
    operation_probabilities = {key: round(value / total, 6) for key, value in operation_probabilities.items()}
    operation = max(operation_probabilities, key=operation_probabilities.get)
    answers = {
        "operation": {
            "type": "choice",
            "choice": operation,
            "confidence": operation_probabilities[operation],
            "probabilities": operation_probabilities,
        }
    }
    targets = per_operation.get(operation)
    if targets:
        total = sum(targets.values()) or 1.0
        target_probabilities = {key: round(value / total, 6) for key, value in targets.items()}
        target = max(target_probabilities, key=target_probabilities.get)
        answers[f"{operation.lower()}_target"] = {
            "type": "choice",
            "choice": target,
            "confidence": target_probabilities[target],
            "probabilities": target_probabilities,
        }
    return {"model": model, "answers": answers}


def parse_action(content: str, keys) -> tuple:
    """Read ``{"action": ..., "confidence": ...}`` out of one reply.

    Returns ``(action, confidence, complaint)``: the action is None and the complaint says what was
    wrong when the reply cannot be used.
    """
    if not isinstance(content, str):
        return None, None, "the reply was not text"
    text = content.strip()
    match = FENCE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None, None, "the reply held no JSON object"
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return None, None, "the reply held no readable JSON object"
    if not isinstance(parsed, dict):
        return None, None, "the reply was JSON but not an object"
    action = parsed.get("action") or parsed.get("choice")
    if not isinstance(action, str):
        return None, None, 'the reply had no "action" string'
    action = action.strip()
    if action not in keys:
        return None, None, f"the reply named {action!r}, which is not one of the offered actions"
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        confidence = DEFAULT_CONFIDENCE
    return action, float(confidence), ""


def build_messages(state, questions, actions, page_chars: int):
    """The exact message pair the chat model receives; see the wire contract above."""
    page = state.get("page") if isinstance(state.get("page"), dict) else {}
    payload = {
        "goal": question_goal(questions),
        "rules": JOINT_RULES,
        "page": {
            "url": page.get("url"),
            "title": page.get("title"),
            "text": (page.get("text") or "")[:page_chars],
        },
        "actions": actions,
        "recent_actions": state.get("recent_actions") or [],
    }
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class Endpoint:
    """One decision per request, answered by one chat call, with one repair attempt."""

    def __init__(self, chooser: Chooser, page_chars: int = DEFAULT_PAGE_CHARS) -> None:
        self.chooser = chooser
        self.page_chars = page_chars
        self.stats = {"requests": 0, "errors": 0, "repairs": 0}

    def label(self) -> str:
        return f"chat:{self.chooser.model}"

    def decide(self, state, questions) -> Dict[str, Any]:
        joint = joint_actions(questions)
        if joint is None:
            raise ValueError("this endpoint answers the agent's factored operation/target questions only")
        actions, mapping = joint
        messages = build_messages(state, questions, actions, self.page_chars)
        keys = list(actions)
        reply = self.chooser.ask(messages)
        action, confidence, complaint = parse_action(reply, keys)
        if action is None:
            self.stats["repairs"] += 1
            messages = messages + [
                {"role": "assistant", "content": reply[:200]},
                {
                    "role": "user",
                    "content": (
                        f"That reply could not be used: {complaint}. Reply with JSON only, shaped "
                        '{"action": "<one of these keys>", "confidence": <0..1>}. The keys are: '
                        + json.dumps(keys, ensure_ascii=False)
                    ),
                },
            ]
            reply = self.chooser.ask(messages)
            action, confidence, complaint = parse_action(reply, keys)
        if action is None:
            raise ValueError(f"the chat model gave no usable action after one repair: {complaint}")
        self.stats["requests"] += 1
        peak = max(confidence, MIN_PEAK)
        print(f"[llm-endpoint] {action} ({confidence:.2f} -> peak {peak:.2f})", flush=True)
        return answers_from_actions(peaked(keys, action, peak), mapping, self.label())

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "service": "llm-endpoint", "model": self.chooser.model, **self.stats}

    def handler(self):  # noqa: ANN201 - handler factory
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                print(f"[llm-endpoint] {self.address_string()} {fmt % args}", flush=True)

            def send_json(self, status, payload):
                content = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                if self.path.split("?")[0] in ("/health", "/"):
                    self.send_json(200, endpoint.health())
                else:
                    self.send_json(404, {"error": "Not found"})

            def do_POST(self):
                if self.path.split("?")[0] != "/v1/systemone":
                    self.send_json(404, {"error": "Not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    started = time.perf_counter()
                    result = endpoint.decide(body["state"], body["questions"])
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                    self.send_json(200, result)
                except Exception as error:  # a bad request must not take the service down
                    endpoint.stats["errors"] += 1
                    print(f"[llm-endpoint] refused a request: {type(error).__name__}: {error}", flush=True)
                    self.send_json(400, {"error": {"type": type(error).__name__, "message": str(error)}})

        return Handler


def serve_in_thread(endpoint: Endpoint, host: str = "127.0.0.1", port: int = 0):
    """Serve an endpoint on a daemon thread. Returns ``(server, base_url)``."""
    server = ThreadingHTTPServer((host, port), endpoint.handler())
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="llm-endpoint", daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}/v1/systemone"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL; default TEXT_MODEL_BASE_URL")
    parser.add_argument("--model", default=None, help="model name; default TEXT_MODEL")
    parser.add_argument("--api-key", default=None, help="bearer key; default TEXT_MODEL_API_KEY")
    parser.add_argument("--page-chars", type=int, default=None, help="page text sent with a question")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for one decision")
    args = parser.parse_args()

    load_env_file()
    chooser, page_chars = chooser_from_env(
        base_url=args.base_url, model=args.model, key=args.api_key, timeout=args.timeout
    )
    endpoint = Endpoint(chooser, page_chars if args.page_chars is None else args.page_chars)
    server = ThreadingHTTPServer((args.host, args.port), endpoint.handler())
    server.daemon_threads = True
    print(f"llm endpoint on http://{args.host}:{args.port} (model={chooser.model}, page chars={endpoint.page_chars})")
    print(f"  TYPESAFE_BASE_URL=http://{args.host}:{args.port}/v1/systemone uv run jev")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
