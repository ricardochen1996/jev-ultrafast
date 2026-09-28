"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
# Count of actions the current page would offer. Cheap enough to poll while a page assembles.
ACTION_COUNT = f"(() => {{ const state={READ_STATE}; return state ? state.actions.length : 0; }})()"

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class LostSession(RuntimeError):
    """The tab or CDP session behind this run is gone; the run must be reopened."""


def lost_session(message):
    """True when a CDP error means the session or target no longer exists."""
    return "Session with given id not found" in message or "No target with given id found" in message


def same_page(current, wanted):
    """True when two addresses point at the same page, ignoring query and fragment."""

    def trim(url):
        return (url or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")

    return bool(current) and trim(current) == trim(wanted)


def open_target(url):
    """The id of an already-open tab showing this page, so a run can use the tab you see."""
    for target in cdp("Target.getTargets")["targetInfos"]:
        if target.get("type") == "page" and same_page(target.get("url"), url):
            return target["targetId"]
    return None


class Browser:
    def __init__(self, url, reuse=False):
        ensure_daemon()
        existing = open_target(url) if reuse else None
        self.owned = existing is None
        self.target = existing or cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        if self.owned:
            # The emulated viewport belongs to an owned tab; the tab you are watching keeps its own size.
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        else:
            # A reused tab can still carry an emulated viewport from an earlier owned run. Clear it so
            # this run works against the window the user actually sees, which is also its real width.
            self.call("Emulation.clearDeviceMetricsOverride")
        # Keep rAF/menus rendering in a background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        if self.owned:
            self.call("Page.navigate", url=url)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.evaluate("document.readyState") == "complete":
                    break
                time.sleep(0.02)
        # readyState completes before a single-page app has rendered its controls, and a shell can
        # render its chrome first. Wait until the offered action table stops growing, bounded, so
        # the first decision is made against the page the user can already see.
        deadline = time.monotonic() + 20
        previous, stable = -1, 0
        while time.monotonic() < deadline:
            try:
                count = self.evaluate(ACTION_COUNT) or 0
            except StalePage:
                # The page is still assembling; a snapshot taken mid-render is simply retried.
                count = previous + 1 if previous >= 0 else 0
            stable = stable + 1 if count == previous else 0
            previous = count
            if stable >= 2 and count > 2:
                break
            time.sleep(0.3)

    def call(self, method, **params):
        try:
            return cdp(method, session_id=self.session, **params)
        except RuntimeError as error:
            if lost_session(str(error)):
                raise LostSession(str(error)) from None
            raise

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def settle(self, kind, timeout=3.0, quiet=0.3):
        """Wait, bounded, until the page stops changing after an action.

        A click can start a request, a render, or a navigation. Observing in the same tick records
        "nothing changed", which reads as a no-op even though the action was real.
        """
        if kind not in {"click", "fill", "select"}:
            return
        deadline = time.monotonic() + timeout
        previous, stable = None, 0
        while time.monotonic() < deadline:
            try:
                marker = self.evaluate(MARKER)
            except StalePage:
                previous, stable = None, 0  # the document was replaced: keep watching it settle
                time.sleep(quiet)
                continue
            stable = stable + 1 if marker == previous else 1
            previous = marker
            if stable >= 2:
                return
            time.sleep(quiet)

    def wait_for_change(self, timeout=4.0, quiet=0.3):
        """True when the page moved within the window. Used before accepting a stalled conclusion."""
        try:
            before = self.evaluate(MARKER)
        except StalePage:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.evaluate(MARKER) != before:
                    return True
            except StalePage:
                return True
            time.sleep(quiet)
        return False

    def blocking_dialog(self):
        """True when a modal the page opened is still waiting on the user.

        A dialog on screen means the page has something left to say, so a goal is not finished yet.
        """
        return bool(
            self.evaluate(
                """(() => {
                  const shown = e => e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) &&
                    !e.closest('[aria-hidden="true"]') && e.getBoundingClientRect().width > 40;
                  for (const e of document.querySelectorAll(
                      '[role="dialog"],[role="alertdialog"],dialog[open],[aria-modal="true"],' +
                      '[class*="dialog"],[class*="modal"]')) {
                    if (!shown(e)) continue;
                    if (e.querySelector('button,[role="button"]') || (e.innerText || '').trim()) return true;
                  }
                  return false;
                })()"""
            )
        )

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.target and self.owned:
            cdp("Target.closeTarget", targetId=self.target)
        self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        try:
            return cdp(method, session_id=session, **params)
        except RuntimeError as error:
            if lost_session(str(error)):
                raise LostSession(str(error)) from None
            raise

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            # Wheel at the viewport centre: that is the panel the snapshot checked for scrollability.
            size = evaluate("[innerWidth,innerHeight]") or [1120, 780]
            call(
                "Input.dispatchMouseEvent",
                type="mouseWheel",
                x=size[0] // 2,
                y=size[1] // 2,
                deltaX=0,
                deltaY=action["delta"],
            )
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]')) return null;
              const r=e.getBoundingClientRect();
              let x=r.x+r.width/2;
              // Chips occupy the left of a multi-select and swallow the hit: the free space after
              // them is what opens the popup, which is where a person clicks too.
              if (action.aim==='free') x=r.x+r.width-Math.max(8,Math.min(24,r.width*0.12));
              const y=r.y+r.height/2;
              const sized = r.width>0 && r.height>0;
              const onScreen = sized && x>=0 && y>=0 && x<innerWidth && y<innerHeight;
              // A wrapper of the control may own the hit point (label, input group, picker shell);
              // an unrelated element on top of it is still a covered control and stays rejected.
              const hit = onScreen ? document.elementFromPoint(x,y) : null;
              const uncovered = !!hit && (e.contains(hit) || hit.contains(e));
              if (uncovered) {
                if (!e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
                if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
                if (action.kind==='select') {
                  if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                      !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                  e.value=action.value;
                  e.dispatchEvent(new Event('input',{bubbles:true}));
                  e.dispatchEvent(new Event('change',{bubbles:true}));
                }
                return {x,y};
              }
              // A control the snapshot itself marked clipped sits inside a container that hides it,
              // so pointer input can never land on it and it is not reported as visible either. Its
              // own observed node is activated instead; the node comes from the observation, never
              // from model-generated coordinates, selectors, or code.
              if (action.kind==='click' && action.clipped && sized) { e.click(); return {dom:true}; }
              return null;
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if target.get("dom"):
                return {"executed": action["id"], "via": "dom"}
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        # Boxes are drawn in percentages of the reported viewport, so only the aspect ratio of this
        # capture has to agree with it; a reused tab may return it at its own device scale.
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
