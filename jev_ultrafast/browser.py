"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path

from browser_harness import helpers
from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
# Component libraries paint their controls after readyState completes. Looking too soon is how a
# page reports a menu of one item, so a run gives the page a moment before it looks.
FIRST_LOOK_PAUSE = 2.0
# What a tab shows before a page loads: a launched browser opens one of these itself.
BLANK_PAGES = {"about:blank", "about:newtab", "chrome://newtab", "chrome://new-tab-page"}
# How long a just-launched browser has to publish that first tab before the run makes its own.
STARTUP_TAB_SECONDS = 5.0

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


def blank_page(url):
    """True for a tab that is showing nothing yet, so driving it costs nobody's view."""
    trimmed = (url or "").split("#", 1)[0].split("?", 1)[0]
    return trimmed.rstrip("/") in BLANK_PAGES


def startup_target(deadline=STARTUP_TAB_SECONDS):
    """The one blank tab a browser opens itself at launch, or None.

    A launched browser already owns exactly one tab before anything attaches to it, and that tab is
    showing nothing. Driving it is what a person does with a new window: the run happens in the tab
    that is already on screen, and no second blank tab is left behind. Only a browser whose whole
    page list is that single blank tab matches, so a browser someone is browsing with never does --
    unless it holds nothing but one blank tab, which is a browser with nothing to take.

    The page list can trail the DevTools endpoint by a moment, so this waits for the browser to
    publish its first tab before deciding; a browser that still shows nothing gets a tab of our own.
    """
    deadline = time.monotonic() + deadline
    while True:
        pages = [t for t in cdp("Target.getTargets")["targetInfos"] if t.get("type") == "page"]
        if len(pages) > 1 or (pages and not blank_page(pages[0].get("url"))):
            return None
        if pages:
            return pages[0]["targetId"]
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def daemon_tab():
    """The blank tab the connection layer already opened for this run, or None.

    A named daemon gives every caller a dedicated tab of its own before anything asks for one. Driving
    that tab instead of creating another is what keeps a run from adding a blank tab to a browser
    someone is using. Only a named daemon's tab qualifies: the default daemon attaches to a page the
    browser already had, and that page is not ours to navigate or close.
    """
    if helpers.NAME == "default":
        return None
    for attempt in range(2):
        try:
            tab = helpers.current_tab()
            break
        except Exception:
            if attempt:
                return None
            # An earlier run closed the daemon's tab along with its own. The daemon replaces a lost
            # tab the next time its own session is used, so one harmless call gives it a fresh one
            # now, instead of whenever a later call happens to need it.
            try:
                cdp("Runtime.evaluate", expression="0")
            except Exception:
                return None
    return tab["targetId"] if blank_page(tab.get("url")) else None


def run_spares(pages, daemon):
    """The blank tabs a run may close once its own tab shows a page.

    The daemon's dedicated tab is always ours. The other blank tabs are only the leftovers of a
    launch when blank tabs are all the browser holds: a browser that also shows real pages is one a
    person is using, and a blank new-tab page there is theirs, opened on purpose.
    """
    spares = {daemon} if daemon else set()
    if pages and all(blank_page(t.get("url")) for t in pages):
        spares.update(t["targetId"] for t in pages)
    return spares


def close_spare_blank_tabs(keep, only=None):
    """Close the blank tabs nobody is driving, now that one tab is on its page.

    A launch leaves the browser's own startup tab behind: the connection layer gives every caller its
    own tab (named daemons must not share one), so the tab the browser opened before anything
    attached is never the tab being driven. Left alone it is the second tab you see when a run
    starts. Only a blank tab counts, and only while a page that is not blank stays open beside it,
    so this never leaves the browser showing nothing and never closes a page a person was reading.
    ``only`` limits the closing to the tabs a run itself is responsible for.
    """
    try:
        pages = [t for t in cdp("Target.getTargets")["targetInfos"] if t.get("type") == "page"]
    except Exception:
        return
    spare = [
        t for t in pages
        if t["targetId"] != keep and blank_page(t.get("url")) and (only is None or t["targetId"] in only)
    ]
    if not any(not blank_page(t.get("url")) for t in pages):
        return  # every tab is blank, so any of them may be all the browser has to show
    for target in spare:
        try:
            cdp("Target.closeTarget", targetId=target["targetId"])
        except Exception:
            pass  # a tab that is already gone needs nothing here


def active_tabs(limit=12):
    """{window id: the tab that window is showing}, so a tab we open cannot steal a view.

    Chrome activates a target created over CDP even when it is asked for a background tab, and it
    chooses the window itself. Recording what each window is showing first lets the new tab be put
    back behind it. Only a few candidates are attached: this runs once per run, and the user may
    have many tabs open.
    """
    active = {}
    for target in cdp("Target.getTargets")["targetInfos"][:limit]:
        if target.get("type") != "page":
            continue
        session = None
        try:
            session = cdp("Target.attachToTarget", targetId=target["targetId"], flatten=True)["sessionId"]
            state = cdp(
                "Runtime.evaluate",
                expression="document.visibilityState==='visible'",
                session_id=session,
                returnByValue=True,
            )
            if state.get("result", {}).get("value"):
                window = cdp("Browser.getWindowForTarget", targetId=target["targetId"])["windowId"]
                active.setdefault(window, target["targetId"])
        except Exception:
            continue
        finally:
            if session:
                try:
                    cdp("Target.detachFromTarget", sessionId=session)
                except Exception:
                    pass
    return active


class Browser:
    def __init__(self, url, reuse=False):
        ensure_daemon()
        daemon = daemon_tab()
        spares = run_spares([t for t in cdp("Target.getTargets")["targetInfos"] if t.get("type") == "page"], daemon)
        existing = open_target(url) if reuse else None
        # The tab the connection layer opened for this run is the tab it drives, so no second blank
        # tab is ever created beside it. Without one, the tab a just-launched browser opened itself is
        # used, so the launch leaves one tab on screen instead of one blank tab beside the page. A tab
        # this run creates or adopts sits in the background and is put back behind the user's view; a
        # startup tab already is the view, so the restore below never has anything to restore for it.
        own = (daemon or startup_target()) if existing is None else None
        startup = own if own is not None and own != daemon else None
        self.owned = existing is None and startup is None
        watching = active_tabs() if self.owned else {}
        self.attach(existing or own or cdp("Target.createTarget", url="about:blank", background=True)["targetId"])
        if self.owned:
            # Put the tab the user was reading back in front, in the window this tab landed in.
            try:
                window = cdp("Browser.getWindowForTarget", targetId=self.target)["windowId"]
            except Exception:
                window = None
            previous = watching.get(window)
            if previous and previous != self.target:
                cdp("Target.activateTarget", targetId=previous)
        if self.owned:
            # The emulated viewport belongs to an owned tab; the tab you are watching keeps its own size.
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        else:
            # A tab this run did not create keeps the size it really has: the tab you are watching
            # when reusing yours, and the window a launch already put on screen when the startup tab
            # was adopted. Either way it can still carry an emulated viewport from an earlier owned
            # run, so that is cleared rather than replaced.
            self.call("Emulation.clearDeviceMetricsOverride")
        if self.owned:
            self.call("Page.navigate", url=url)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.evaluate("document.readyState") == "complete":
                    break
                time.sleep(0.02)
        # readyState completes before a single-page app has rendered its controls, and a shell can
        # render its chrome first. Give the page a moment, then wait until the offered action table
        # stops growing, bounded, so the first decision is made against the page the user can see.
        time.sleep(FIRST_LOOK_PAUSE)
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
            # Any settled page is ready, however few actions it offers: a menu of one item is a page
            # too, and waiting for a larger count only spent the whole deadline.
            if stable >= 2 and count >= 1:
                break
            time.sleep(0.3)
        # The page is up, so the blank tabs this run is responsible for have done their job: the
        # daemon's tab when a reused tab is driven instead, and a launch's startup tab. A blank tab in
        # a browser someone is using is theirs and stays.
        close_spare_blank_tabs(self.target, only=spares)

    def attach(self, target):
        self.target = target
        self.session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        # Keep rAF/menus rendering in a background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)

    def follow_opened_tab(self):
        """Continue in the tab our own click opened, as a person would after a target=_blank link.

        Only a page whose opener is the driven tab counts, so unrelated tabs are never taken over.
        """
        known, self.known_targets = getattr(self, "known_targets", None), None
        if known is None:
            return
        opened = [
            t["targetId"]
            for t in cdp("Target.getTargets")["targetInfos"]
            if t.get("type") == "page" and t.get("openerId") == self.target and t["targetId"] not in known
        ]
        if not opened:
            return
        previous = self.target
        self.attach(opened[-1])
        if self.owned:
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
            # The results tab was ours too; leaving it behind would leak one tab per followed link.
            cdp("Target.closeTarget", targetId=previous)
        # A new tab starts as about:blank before its navigation commits; wait for the real page and
        # for its action table to stop growing, bounded, like the first page of a run.
        deadline = time.monotonic() + 10
        previous, stable = -1, 0
        while time.monotonic() < deadline:
            try:
                ready = self.evaluate("location.href!=='about:blank' && document.readyState==='complete'")
                count = (self.evaluate(ACTION_COUNT) or 0) if ready else -1
            except StalePage:
                count = -1
            stable = stable + 1 if count == previous and count > 2 else 0
            previous = count
            if stable >= 2:
                break
            time.sleep(0.1)

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
        previous = getattr(self, "target", None)
        self.follow_opened_tab()
        if self.target != previous:
            self.after_input = None
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
        elif action["kind"] == "click":
            self.known_targets = {t["targetId"] for t in cdp("Target.getTargets")["targetInfos"]}
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
              let y=r.y+r.height/2;
              const sized = r.width>0 && r.height>0;
              const onScreen = (px,py) => sized && px>=0 && py>=0 && px<innerWidth && py<innerHeight;
              const at = (px,py) => onScreen(px,py) ? document.elementFromPoint(px,py) : null;
              let hit = at(x,y);
              // The box centre of a wrapped inline link can fall between its line boxes, onto the
              // parent or a neighbour. Aim at the element's own painted boxes, largest first, and
              // take the first point where the element itself receives the hit.
              if (!(hit && e.contains(hit))) {
                const boxes=[...e.getClientRects(),
                  ...[...e.querySelectorAll('*')].slice(0,40).map(c=>c.getBoundingClientRect())]
                  .filter(b=>b.width>=2 && b.height>=2).sort((a,b)=>b.width*b.height-a.width*a.height);
                for (const b of boxes) {
                  const px=b.x+b.width/2, py=b.y+b.height/2, h=at(px,py);
                  if (h && e.contains(h)) { x=px; y=py; hit=h; break; }
                }
              }
              // A wrapper of the control may own the hit point (label, input group, picker shell);
              // an unrelated element on top of it is still a covered control and stays rejected.
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
