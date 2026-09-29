"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


class NoTextValue(ValueError):
    """The text helper returned nothing usable, so no text was typed."""


def env_headers(name):
    """Optional JSON object of extra request headers, for gateways that require one."""
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        headers = json.loads(raw)
    except ValueError:
        raise ValueError(f"{name} must be a JSON object of header names to values.") from None
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError(f"{name} must be a JSON object of header names to values.")
    return headers


def post_json(url, key, body, headers=None, fallback=None):
    """Post one System One request, with a second endpoint for when the first is out of quota.

    A 429 or 402 is the provider refusing this key, not a bad request: another endpoint — a local
    model, for instance — can answer the same protocol while the quota recovers. The refusal is
    remembered for a while, because a rate-limited endpoint answers slowly and every decision would
    otherwise pay that round trip before falling back.
    """
    global quota_until
    if fallback and time.monotonic() < quota_until:
        return post_once(fallback, key, body, headers)
    try:
        return post_once(url, key, body, headers)
    except QuotaExceeded:
        if not fallback:
            raise
        quota_until = time.monotonic() + QUOTA_COOLDOWN
        return post_once(fallback, key, body, headers)


class QuotaExceeded(RuntimeError):
    """The provider refused the request for this key: no balance, or a rate limit."""


# How long a quota refusal keeps the primary endpoint out of the way, in seconds.
QUOTA_COOLDOWN = float(os.environ.get("TYPESAFE_QUOTA_COOLDOWN", "120"))
quota_until = 0.0


def post_once(url, key, body, headers=None):
    request_headers = dict(headers or {})
    if key:
        request_headers["Authorization"] = f"Bearer {key}"
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers=request_headers)
        except httpx.HTTPError as error:
            # A decision or a field value is not a browser mutation, so a dropped connection can
            # be retried; no action is executed until this returns.
            if attempt < 2:
                time.sleep(0.4 * 2**attempt)
                continue
            raise RuntimeError(
                f"Model connection failed ({type(error).__name__}); no action executed."
            ) from None
        if response.status_code == 429:
            # A quota, not a blip: waiting inside the request would only stall the console, so say
            # what happened and leave the choice to continue with the person watching.
            raise QuotaExceeded(
                "The model provider is rate limiting this key. Nothing was executed; continue in a moment."
            )
        if response.status_code == 402:
            raise QuotaExceeded(
                "The model provider has no funds for this model on this account. Nothing was executed; "
                "use a free model such as jev-1.13-free, or add credit."
            )
        if response.status_code in {529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def noop_repeats(history):
    """Targets that already ran against the same element and left the page unchanged.

    A model can keep choosing an action the page ignores, and each repeat costs a step. The
    answer already ranks every candidate, so the next-best ranking breaks the loop without
    another request.
    """
    repeats = set()
    for entry in history[-4:]:
        if entry.get("page_changed") is not False:
            continue
        operation, target = entry.get("operation"), entry.get("target")
        if operation in {"CLICK", "TYPE_TEXT", "SELECT"} and target is not None:
            repeats.add((operation, str(target)))
    return repeats


def ranked(probabilities, operation, repeats):
    """Candidate indices by probability, and those dropped for having just done nothing."""
    order = sorted(probabilities, key=lambda index: -probabilities[index])
    return order, [index for index in order if (operation, str(index)) in repeats]


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            keys = ("role", "value", "checked", "selected", "expanded", "nearby")
            element = {k: action[k] for k in keys if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history, avoid=(), avoid_labels=()):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded", "nearby") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed", "operation", "target")}
                for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    endpoint = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1/systemone")
    result = post_json(
        endpoint,
        os.environ.get("TYPESAFE_API_KEY", ""),
        body,
        fallback=os.environ.get("TYPESAFE_FALLBACK_URL") or None,
    )
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    repeats = noop_repeats(history) | set(avoid)
    looping = set(avoid_labels)
    skipped = []

    def fresh_head(name):
        """One operation's validated target head, its best candidate that has not just failed, and what it avoided."""
        candidates = targets.get(name)
        if not candidates:
            return None, None, []
        answer = validate_choice(result["answers"].get(name.lower() + "_target", {}), candidates)
        order, dropped = ranked(answer["probabilities"], name, repeats)

        def loops_here(index):
            # Matched by label: a reload can renumber the same control.
            label = candidates[index]["label"]
            return any(
                name == operation and (action == label or action.startswith(label + " -> "))
                for operation, action in looping
            )

        viable = [index for index in order if (name, str(index)) not in repeats and not loops_here(index)]
        return answer, (viable[0] if viable else None), dropped

    target_answer, best, dropped = fresh_head(operation)
    if target_answer is not None and best is None:
        # Every candidate of the model's operation already did nothing: take the next-best
        # operation that still has a candidate worth trying, from the same answer.
        for name in sorted(operation_answer["probabilities"], key=lambda key: -operation_answer["probabilities"][key]):
            if name == operation or name not in targets:
                continue
            answer, alternative, alternative_dropped = fresh_head(name)
            if alternative is not None:
                operation, target_answer, best, dropped = name, answer, alternative, alternative_dropped
                break
    if target_answer is not None:
        target = best if best is not None else target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
        skipped = dropped if target != target_answer["choice"] else []
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
        "skipped_noops": skipped,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value", "nearby")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    mode = os.environ.get("TEXT_MODEL_REASONING")
    if mode == "none":
        reasoning = {"reasoning": {"enabled": False}}
    elif mode == "thinking-disabled":
        reasoning = {"thinking": {"type": "disabled"}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
        env_headers("TEXT_MODEL_HEADERS"),
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise NoTextValue("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
