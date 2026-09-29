"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import NoTextValue, action_space, choose, field_context, field_text
from .questions import MAX_STEPS, PROBE_SCROLLS

# Distinct targets the executor may refuse on one unchanged page before the run stops.
UNREACHABLE_LIMIT = 3
# Distinct actions that repeated while the same controls stayed on screen before the run stops.
LOOP_LIMIT = 3
# Repeating an action this many times over an unchanged set of controls is a loop, not progress.
LOOP_REPEATS = 2
# How far back a repeat still counts as a loop: a submit and its dismiss dialog alternate, so the
# repeat is not always adjacent.
LOOP_WINDOW = 4


def controls_signature(page):
    """What the page offers, ignoring scrolling and waiting.

    Clicking the link you are already on reloads the page: the state has genuinely changed, so a
    no-op check never sees it, but the controls on screen are exactly the same.
    """
    return tuple(
        sorted(
            {
                action["label"]
                for action in page["actions"]
                if action.get("kind") not in {"scroll", "wait"} and action.get("label")
            }
        )
    )


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, reuse=False):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.browser = Browser(url, reuse=reuse)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
            probe_scrolls=0,
            blocked_dones=0,
            unreachable=[],
            loops=[],
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def refresh(self):
        """Re-observe after a decision went stale. Nothing was executed, so the run stays ready."""
        state = self.state
        state["decision"] = None
        state["status"] = "ready"
        state["page"] = self.browser.observe(screenshot=self.screenshots)
        if state["started_at"] is not None:
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)

    def instruct(self, text, *, index, plan):
        """Continue the same page with another instruction from the user.

        A task is one page plus a list of instructions. Appending one keeps the tab, the trail, and
        the page, so the next decisions start where the last instruction stopped.
        """
        state = self.state
        state["goal"] = text
        state["plan"] = list(plan)
        state["plan_index"] = index
        state["decision"] = None
        state["status"] = "ready"
        state["unreachable"] = []
        state["loops"] = []
        state["page"] = self.browser.observe(screenshot=self.screenshots)
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        # The trail marks every instruction boundary, so one task reads as one story.
        state["history"].append(
            {
                "step": len(state["history"]) + 1,
                "action": text,
                "kind": "instruction",
                "choice": None,
                "probability": 0.0,
                "confidence": 0.0,
                "latency_ms": 0,
                "text": None,
                "text_helper": None,
                "text_latency_ms": 0,
                "operation": "INSTRUCTION",
                "target": None,
                "page_changed": None,
                "via": None,
                "url": state["page"]["url"],
                "usage": {},
                "executed_ms": state["elapsed_ms"],
                "elapsed_ms": state["elapsed_ms"],
                "instruction": index,
            }
        )
        return self.snapshot()

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            # Targets the executor just refused on this very page (covered, off screen) are not offered
            # again: the same answer's next-best candidate is used instead of spinning on one choice.
            fingerprint = state["page"]["fingerprint"]
            avoid = {(o, t) for o, t, f in state.get("unreachable", []) if f == fingerprint}
            if len(avoid) >= UNREACHABLE_LIMIT:
                state["status"] = "blocked"
                raise ValueError("The chosen targets could not be reached on this page. Stopped instead of retrying.")
            looping = {pair for pair in state.get("loops", [])}
            if len(looping) >= LOOP_LIMIT:
                state["status"] = "blocked"
                raise ValueError(
                    "The same actions kept being chosen without moving the page. Stopped instead of looping."
                )
            state["decision"] = choose(
                state["page"], state["goal"], state["history"], avoid=avoid, avoid_labels=looping
            )
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                if selected == "DONE" and state["browser"].blocking_dialog():
                    if state["blocked_dones"] < 2:
                        # A dialog on screen means the page has something left to say. Reporting
                        # success over it would claim an outcome nobody has seen.
                        state["blocked_dones"] += 1
                        state["status"] = "ready"
                        raise StalePage("The page is still showing a dialog. Choose again.")
                    # The choice insists while the page still reports a problem: that is a block, not
                    # a finished goal, and saying otherwise would be a false success.
                    state["status"] = "blocked"
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                if selected == "BLOCKED" and state["browser"].wait_for_change():
                    # A page that is still loading is not evidence that nothing can progress: look
                    # again instead of stopping on a half-rendered screen.
                    state["status"] = "ready"
                    raise StalePage("The page was still moving when the block was reported. Choose again.")
                below = next((a for a in page["actions"] if a["kind"] == "scroll" and a["delta"] > 0), None)
                if selected == "BLOCKED" and below is not None and state["probe_scrolls"] < PROBE_SCROLLS:
                    # An unexplored page is not a blocked one. Scroll on and look again, bounded, so a
                    # goal that names a control below the fold is not reported as impossible.
                    state["probe_scrolls"] += 1
                    state["browser"].act(below, page)
                    state["browser"].settle("scroll")
                    state["history"].append(
                        {
                            "step": len(state["history"]) + 1,
                            "action": below["label"],
                            "kind": "scroll",
                            "choice": below["id"],
                            "probability": 0.0,
                            "confidence": 0.0,
                            "latency_ms": 0,
                            "text": None,
                            "text_helper": None,
                            "text_latency_ms": 0,
                            "operation": "SCROLL_DOWN",
                            "target": None,
                            "page_changed": None,
                            "via": None,
                            "url": page["url"],
                            "usage": {},
                            "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                            "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                        }
                    )
                    state["page"] = state["browser"].observe(screenshot=self.screenshots)
                    state["status"] = "ready"
                    return self.snapshot()
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    try:
                        text, helper = field_text(context)
                    except NoTextValue:
                        # The helper would not supply words for this field. Choosing it again would
                        # spend another decision and another helper call to learn the same thing, so
                        # the field is left alone and the next choice looks elsewhere.
                        refused = (decision["operation"], action["label"])
                        if refused not in state["loops"]:
                            state["loops"].append(refused)
                        raise
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            before = controls_signature(page) if action["kind"] in {"click", "fill", "select"} else None
            try:
                executed = state["browser"].act(action, page, text=text)
            except StalePage:
                if decision.get("target") is not None:
                    state.setdefault("unreachable", []).append(
                        (decision["operation"], str(decision["target"]), page["fingerprint"])
                    )
                raise
            # Let an asynchronous page show the effect before the next observation judges it.
            state["browser"].settle(action["kind"])
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "via": (executed or {}).get("via"),
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            # Repeating an action from a screen it has already been tried on is a loop, whether the
            # repeat is adjacent (a reload changes state and shows the same controls) or alternating
            # (a submit that raises a dialog, which is dismissed, and then the submit runs again).
            # What the action did is beside the point: a form that keeps refusing a submit looks the
            # same every time it refuses.
            repeats = [
                h
                for h in state["history"][:-1][-LOOP_WINDOW:]
                if h["action"] == action["label"] and h["kind"] == action["kind"] and h.get("controls") == before
            ]
            if before is not None and len(repeats) >= LOOP_REPEATS:
                pair = (decision["operation"], action["label"])
                if pair not in state["loops"]:
                    state["loops"].append(pair)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                controls=before,
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
