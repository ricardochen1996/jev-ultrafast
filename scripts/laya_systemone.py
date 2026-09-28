"""Localhost System One decision service backed by the Laya-MLX typed-decision model.

Speaks the same request/response protocol as ``api.typesafe.ai/v1/systemone``, so the
agent's decision endpoint can point here with ``TYPESAFE_BASE_URL`` and no loop change:

    GET  /health        -> {"status": "ok"|"loading"|"error", "model": ...}
    POST /v1/systemone  -> {"model": ..., "answers": {qid: {choice, confidence, probabilities}}, "usage": ...}

Laya answers every question in one bidirectional forward pass and generates zero tokens.
The runtime lives in its own virtualenv, e.g.::

    python3 -m venv ~/venvs/laya
    ~/venvs/laya/bin/python3 -m pip install laya-mlx
    ~/venvs/laya/bin/python3 scripts/laya_systemone.py --port 8767

Binds to 127.0.0.1 only.

Decision shape
--------------
Laya discriminates between concrete elements far better than between coarse operation
categories, so by default this service re-expresses a factored request (one ``operation``
head plus per-operation target heads) as a single joint ``action`` question over every
executable action, then marginalises the answer back into the factored shape the caller
expects. Set ``LAYA_FACTORED=1`` to pass the caller's questions through untouched.

Escalation
----------
Laya is a routing classifier, not a browser policy: it can repeat an action that already
ran and changed nothing, and it has no notion of progress. One step therefore escalates to
a chat model (any OpenAI-compatible endpoint) when the joint answer is either low confidence
or an exact repeat of a no-op action. The escalation answers with the same joint key and is
marginalised the same way, so the caller sees one protocol either way. Any escalation
failure keeps the Laya answer. Configure with ``LAYA_FALLBACK_*``; the base URL, key, model
and headers default to the ``TEXT_MODEL_*`` values already in ``.env``.
"""

import argparse
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

DEFAULT_MODEL = "aac6fef/laya-multilingual-mlx"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
MAX_BODY_BYTES = 1024 * 1024
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
KIND_BY_OPERATION = {"CLICK": "click", "TYPE_TEXT": "fill", "SELECT": "select"}
FALLBACK_SYSTEM = (
    "You choose exactly one next action for a browser agent. "
    'Reply with JSON only, shaped {"action": "<one key from the given actions>"}.'
)
WARMUP_QUESTIONS = {
    "warmup": {
        "type": "choice",
        "instructions": "Is the request in `request` a warmup call?",
        "criteria": {"yes": "the request is a warmup call", "no": "the request is real work"},
    }
}
JOINT_INSTRUCTIONS = {
    "rules": "Pick the single action that makes the most progress toward the goal in `goal`. "
    "Prefer filling an empty or wrong field over clicking an unrelated element. "
    "Generic placeholder labels such as 请输入, 请选择, Search or Select are not field names: match each of "
    "those fields to the label that precedes it in the page text, in reading order. "
    "Opening, viewing or searching a record does not satisfy a goal that asks to change it: keep going "
    "until the requested change is made and submitted, and choose DONE only then.",
}


def load_env_file(path: Path = ENV_FILE) -> None:
    """Fill os.environ from the project .env without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


class Fallback:
    """One chat-model step, used only when the local typed decision cannot progress."""

    def __init__(self, *, base_url: str, key: str, model: str, headers: Dict[str, str], timeout: float) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = key
        self.model = model
        self.headers = headers
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["Fallback"]:
        base = os.environ.get("LAYA_FALLBACK_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL")
        key = os.environ.get("LAYA_FALLBACK_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY")
        model = os.environ.get("LAYA_FALLBACK_MODEL") or os.environ.get("TEXT_MODEL")
        if not (base and key and model):
            return None
        headers = {"Content-Type": "application/json"}
        raw = os.environ.get("LAYA_FALLBACK_HEADERS") or os.environ.get("TEXT_MODEL_HEADERS")
        if raw:
            headers.update({k: v for k, v in json.loads(raw).items() if isinstance(v, str)})
        headers.setdefault("Authorization", f"Bearer {key}")
        # Cloudflare in front of some gateways rejects the default Python-urllib agent (error 1010).
        headers.setdefault("User-Agent", "jev-ultrafast-laya/0.1")
        return cls(
            base_url=base,
            key=key,
            model=model,
            headers=headers,
            timeout=float(os.environ.get("LAYA_FALLBACK_TIMEOUT", "30")),
        )

    def choose(self, payload: Dict[str, Any]) -> Optional[str]:
        """Return one action key, or None when the escalation cannot answer."""
        body = {
            "model": self.model,
            "max_tokens": 64,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": FALLBACK_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
            chosen = json.loads(result["choices"][0]["message"]["content"]).get("action")
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            print(f"[laya-systemone] escalation unavailable: {type(exc).__name__}: {exc}", flush=True)
            return None
        return chosen if isinstance(chosen, str) else None



class Candidate(NamedTuple):
    """One executable action rebuilt from the caller's factored heads."""

    operation: str
    index: Optional[str]
    label: str
    value: str


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


def joint_question(questions: Dict[str, Any]):
    """Rebuild factored heads as one choice question over every executable action.

    Returns ``(criteria, key -> Candidate)``, or None when the request is not the factored
    shape this service knows how to re-express.
    """
    operation_q = questions.get("operation")
    criteria = operation_q.get("criteria") if isinstance(operation_q, dict) else None
    if not isinstance(criteria, dict) or not criteria:
        return None
    joint: Dict[str, str] = {}
    mapping: Dict[str, Candidate] = {}
    for operation, label in criteria.items():
        head = questions.get(f"{operation.lower()}_target")
        targets = head.get("criteria") if isinstance(head, dict) else None
        text = label if isinstance(label, str) else json.dumps(label, ensure_ascii=False)
        if not isinstance(targets, dict) or not targets:
            joint[operation] = text  # DONE, BLOCKED, WAIT, SCROLL_*: a single control action
            mapping[operation] = Candidate(operation, None, operation, "")
            continue
        for index, value in targets.items():
            key = f"{operation} {index}"
            joint[key] = f"{text}: {describe(value, index)}"
            current = value.get("current_value") if isinstance(value, dict) else None
            mapping[key] = Candidate(operation, index, element_label(value, index), str(current or ""))
    return joint, mapping


def element_label(value: Any, fallback: Any) -> str:
    """The bare element label, without the ``[index] `` prefix the caller renders."""
    raw = value.get("element") if isinstance(value, dict) else None
    return str(raw or fallback).split("] ", 1)[-1]


def question_goal(questions: Dict[str, Any]) -> str:
    """The caller's goal, carried in the operation question's instructions."""
    instructions = (questions.get("operation") or {}).get("instructions")
    if isinstance(instructions, dict) and isinstance(instructions.get("goal"), str):
        return instructions["goal"]
    if isinstance(instructions, str):
        return instructions
    return json.dumps(instructions, ensure_ascii=False) if instructions else ""


def repeats_no_progress(state: Dict[str, Any], mapping: Dict[str, Any], chosen: str) -> bool:
    """True when this candidate already ran against the same element and changed nothing visible.

    Matched by observed element, not by label: a form full of identical placeholders is normal.
    Control actions (WAIT, SCROLL_*) are repeatable and never count as no-ops.
    """
    recent = state.get("recent_actions")
    if not isinstance(recent, list) or not recent:
        return False
    candidate = mapping[chosen]
    if candidate.index is None:
        return False
    kind = KIND_BY_OPERATION.get(candidate.operation)
    for entry in recent[-4:]:
        if not isinstance(entry, dict) or entry.get("page_changed") or entry.get("kind") != kind:
            continue
        target = entry.get("target")
        if target is not None:
            if str(target) == str(candidate.index):
                return True
        elif entry.get("action") and str(entry["action"]) == candidate.label:
            return True
    return False


def fills_a_filled_field(mapping: Dict[str, Any], chosen: str) -> bool:
    """True when the local model wants to type into a field that already holds a value.

    That is the case where the text helper correctly answers "nothing to type", which the
    caller treats as fatal, so the step deserves a second opinion before it is committed.
    """
    candidate = mapping[chosen]
    return candidate.operation == "TYPE_TEXT" and bool(candidate.value)


def uniform_choice(keys, chosen: str) -> Dict[str, float]:
    """A valid distribution peaked on ``chosen``, for a step decided outside the model."""
    others = [key for key in keys if key != chosen]
    share = round(0.1 / len(others), 4) if others else 0.0
    probabilities = {key: share for key in others}
    probabilities[chosen] = round(1 - sum(probabilities.values()), 4)
    return probabilities


def factored_answers(prediction: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Marginalise one joint answer back into the operation head plus the winning target head."""
    probabilities = prediction["answers"]["action"]["probabilities"]
    return answers_from_joint(probabilities, mapping, prediction.get("model", "laya"))


def answers_from_joint(probabilities: Dict[str, float], mapping: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Marginalise joint action probabilities into the operation head and the winning target head."""
    operation_probs: Dict[str, float] = {}
    per_operation: Dict[str, Dict[str, float]] = {}
    for key, candidate in mapping.items():
        value = float(probabilities.get(key, 0.0))
        operation_probs[candidate.operation] = operation_probs.get(candidate.operation, 0.0) + value
        if candidate.index is not None:
            per_operation.setdefault(candidate.operation, {})[candidate.index] = value
    total = sum(operation_probs.values()) or 1.0
    operation_probs = {key: round(value / total, 4) for key, value in operation_probs.items()}
    operation = max(operation_probs, key=operation_probs.get)
    answers = {
        "operation": {
            "type": "choice",
            "choice": operation,
            "confidence": operation_probs[operation],
            "probabilities": operation_probs,
        }
    }
    targets = per_operation.get(operation)
    if targets:
        total = sum(targets.values()) or 1.0
        target_probs = {key: round(value / total, 4) for key, value in targets.items()}
        target = max(target_probs, key=target_probs.get)
        answers[f"{operation.lower()}_target"] = {
            "type": "choice",
            "choice": target,
            "confidence": target_probs[target],
            "probabilities": target_probs,
        }
    return {"model": model, "answers": answers}




class ModelState:
    """One loaded checkpoint, shared by every request thread."""

    def __init__(
        self,
        model: str,
        *,
        dtype: str,
        optimize: bool,
        warmup: bool,
        factored: bool = False,
        fallback: Optional[Fallback] = None,
        below: float = 0.5,
    ) -> None:
        self.model = model
        self.dtype = dtype
        self.optimize = optimize
        self.warmup = warmup
        self.factored = factored
        self.fallback = fallback
        self.below = below
        self.agent: Optional[Any] = None
        self.status = "loading"
        self.error: Optional[str] = None
        self.started_at = time.time()
        self.load_ms: Optional[int] = None
        self.escalations = 0
        self.lock = threading.Lock()

    def load_in_background(self) -> None:
        def run() -> None:
            try:
                import laya_mlx  # imported here so /health answers before MLX is present

                kwargs: Dict[str, Any] = {"dtype": self.dtype}
                if self.optimize:
                    kwargs.update(compile=True, pad_to_multiple=16, cache_prompts=True)
                self.agent = laya_mlx.load(self.model, **kwargs)
                if self.warmup:
                    # One throwaway decision so the first real request skips graph compilation.
                    self.agent.predict({"request": "warmup"}, WARMUP_QUESTIONS)
                self.status = "ok"
            except BaseException as exc:  # noqa: BLE001 - reported through /health, never raised
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            finally:
                self.load_ms = int((time.time() - self.started_at) * 1000)

        threading.Thread(target=run, name="laya-load", daemon=True).start()

    def health(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "model": self.model,
            "service": "laya-systemone",
            "uptimeMs": int((time.time() - self.started_at) * 1000),
            "escalations": self.escalations,
            "escalation": "off"
            if self.fallback is None
            else f"{self.fallback.model} on a no-op repeat, an already-filled field, or below {self.below}",
        }
        if self.load_ms is not None:
            payload["loadMs"] = self.load_ms
        if self.error is not None:
            payload["error"] = self.error
        return payload

    def decide(self, body: Dict[str, Any]) -> Dict[str, Any]:
        state = body.get("state")
        questions = body.get("questions")
        if not isinstance(state, dict) or not state:
            raise ValueError("field 'state' must be a nonempty object")
        if not isinstance(questions, dict) or not questions:
            raise ValueError("field 'questions' must be a nonempty object")
        joint = None if self.factored else joint_question(questions)
        with self.lock:  # one GPU evaluation at a time
            if joint is None:
                return self.agent.predict(state, questions)
            criteria, mapping = joint
            asked = {"action": {"type": "choice", "instructions": JOINT_INSTRUCTIONS, "criteria": criteria}}
            prediction = self.agent.predict(state, asked)
        probabilities = prediction["answers"]["action"]["probabilities"]
        chosen = max(probabilities, key=probabilities.get)
        reason = self.escalation_reason(state, mapping, chosen, probabilities[chosen])
        if reason is None:
            return factored_answers(prediction, mapping)
        escalated = self.escalate(state, questions, criteria, mapping, reason, blocked=chosen)
        if escalated is None:
            return factored_answers(prediction, mapping)
        self.escalations += 1
        return escalated

    def escalation_reason(
        self, state: Dict[str, Any], mapping: Dict[str, Any], chosen: str, confidence: float
    ) -> Optional[str]:
        """Why this step should leave the local model, or None to keep it."""
        if self.fallback is None:
            return None
        if repeats_no_progress(state, mapping, chosen):
            return "no-op repeat"
        if fills_a_filled_field(mapping, chosen):
            return "already-filled field"
        if confidence < self.below:
            return f"confidence {confidence:.2f} below {self.below}"
        return None

    def escalate(
        self,
        state: Dict[str, Any],
        questions: Dict[str, Any],
        criteria: Dict[str, str],
        mapping: Dict[str, Any],
        reason: str,
        blocked: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Ask the chat model for one action key and marginalise it like a joint answer.

        ``blocked`` is the candidate the local model already picked and that must not stand
        (a no-op repeat, or a fill into a filled field); it is withheld from the escalation so
        the step actually changes instead of re-committing the same action.
        """
        keys = [key for key in criteria if key != blocked]
        if not keys:
            return None
        offered = {key: criteria[key] for key in keys}
        page = state.get("page") if isinstance(state.get("page"), dict) else {}
        chosen = self.fallback.choose(
            {
                "goal": question_goal(questions),
                "rules": JOINT_INSTRUCTIONS["rules"],
                "page": {"url": page.get("url"), "title": page.get("title"), "text": (page.get("text") or "")[:2000]},
                "actions": offered,
                "recent_actions": state.get("recent_actions") or [],
                "why_escalated": reason,
                "unavailable": blocked,
            }
        )
        if chosen not in offered:
            print(f"[laya-systemone] escalation rejected {chosen!r}; keeping the local answer", flush=True)
            return None
        print(f"[laya-systemone] escalated ({reason}) -> {chosen}", flush=True)
        return answers_from_joint(uniform_choice(keys, chosen), mapping, f"{self.model}+{self.fallback.model}")


def make_handler(state: ModelState):  # noqa: ANN201 - handler factory
    class Handler(BaseHTTPRequestHandler):
        server_version = "laya-systemone/0.1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            print(f"[laya-systemone] {self.address_string()} {fmt % args}", flush=True)

        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValueError("empty request body")
            if length > MAX_BODY_BYTES:
                raise ValueError("request body too large")
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("request body must be a JSON object")
            return parsed

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path.split("?")[0] not in ("/health", "/"):
                self._send(404, {"error": "not found"})
                return
            self._send(200 if state.status != "error" else 503, state.health())

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path.split("?")[0] != "/v1/systemone":
                self._send(404, {"error": "not found"})
                return
            if state.status != "ok":
                self._send(503, {"error": f"model not ready: {state.status}", **state.health()})
                return
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": f"invalid request: {exc}"})
                return
            started = time.perf_counter()
            try:
                result = state.decide(body)
            except ValueError as exc:
                self._send(400, {"error": f"invalid questions: {exc}"})
                return
            except BaseException as exc:  # noqa: BLE001 - converted to 500
                traceback.print_exc()
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            self._send(200, result)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local System One decision service on the Laya-MLX model")
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAYA_SYSTEMONE_PORT", DEFAULT_PORT)))
    parser.add_argument("--model", default=os.environ.get("LAYA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--dtype", default="float16", choices=("float16", "float32", "bfloat16"))
    parser.add_argument("--optimize", action="store_true", help="enable compile and prompt-cache options")
    parser.add_argument("--no-warmup", action="store_true", help="skip the throwaway first decision")
    parser.add_argument(
        "--factored",
        action="store_true",
        help="ask the caller's operation/target heads verbatim instead of one joint action question",
    )
    parser.add_argument("--no-escalation", action="store_true", help="never leave the local model for a chat model")
    parser.add_argument(
        "--fallback-below",
        type=float,
        default=float(os.environ.get("LAYA_FALLBACK_BELOW", "0.5")),
        help="escalate below this joint confidence (default 0.5); no-op repeats escalate regardless",
    )
    args = parser.parse_args()

    load_env_file()
    fallback = None if args.no_escalation else Fallback.from_env()
    state = ModelState(
        args.model,
        dtype=args.dtype,
        optimize=args.optimize,
        warmup=not args.no_warmup,
        factored=args.factored or os.environ.get("LAYA_FACTORED") == "1",
        fallback=fallback,
        below=args.fallback_below,
    )
    state.load_in_background()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    server.daemon_threads = True
    escalation = "off" if fallback is None else f"{fallback.model} below {args.fallback_below} or on a no-op repeat"
    print(f"[laya-systemone] listening on http://{args.host}:{args.port} (model={args.model})", flush=True)
    print(f"[laya-systemone] escalation: {escalation}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[laya-systemone] shutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
