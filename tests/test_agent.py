"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def click_decision(target="2", action="e3"):
    return {**decision(action), "operation": "CLICK", "target": target}


def test_refused_target_is_avoided_on_the_unchanged_page(runner, monkeypatch):
    runner.state["decision"] = click_decision()
    runner.state["browser"].act.side_effect = StalePage("Target changed or is covered. Observe again.")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    chosen = Mock(return_value=click_decision())
    monkeypatch.setattr(loop, "choose", chosen)
    runner.state["status"] = "ready"
    runner.command("predict")
    assert chosen.call_args.kwargs["avoid"] == {("CLICK", "2")}


def test_repeatedly_refused_targets_stop_instead_of_spinning(runner, monkeypatch):
    fp = runner.state["page"]["fingerprint"]
    runner.state["unreachable"] = [("CLICK", str(i), fp) for i in range(loop.UNREACHABLE_LIMIT)]
    chosen = Mock()
    monkeypatch.setattr(loop, "choose", chosen)
    runner.state["status"] = "ready"
    with pytest.raises(ValueError, match="could not be reached"):
        runner.command("predict")
    chosen.assert_not_called()
    assert runner.state["status"] == "blocked"


def test_avoided_target_falls_back_to_next_best_candidate(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": {"choice": "1", "confidence": 1.0, "probabilities": {"1": 0.7, "2": 0.3}},
                "type_text_target": choice(["1"], "1"),
            },
        }

    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Go", [], avoid={("CLICK", "1")})
    assert d["target"] == "2" and d["choice"] == "e3"


def test_only_a_tab_opened_by_the_driven_tab_is_followed(monkeypatch):
    import jev_ultrafast.browser as browser

    targets = [
        {"targetId": "mine", "type": "page"},
        {"targetId": "other", "type": "page", "openerId": "someone-else"},
        {"targetId": "popup", "type": "page", "openerId": "mine"},
    ]
    calls = []

    def cdp(method, **params):
        calls.append((method, params))
        return {"targetInfos": targets} if method == "Target.getTargets" else {"sessionId": "s2"}

    monkeypatch.setattr(browser, "cdp", cdp)
    b = browser.Browser.__new__(browser.Browser)
    b.target, b.session, b.owned, b.known_targets = "mine", "s1", False, {"mine"}
    b.evaluate = Mock(side_effect=[True, 5, True, 5, True, 5])
    b.follow_opened_tab()
    assert b.target == "popup" and b.session == "s2"
    assert ("Target.closeTarget", {"targetId": "mine"}) not in calls  # never close the user's own tab
    b.follow_opened_tab()  # consumed: a second observation does not switch again
    assert b.target == "popup"


def test_run_records_can_be_deleted_and_replayed_from_their_first_page(tmp_path, monkeypatch):
    from jev_ultrafast import demo

    monkeypatch.setattr(demo, "RUNS", tmp_path)
    legacy = {"id": "20260101T000000-abcd", "url": "https://example.com/result", "plan": ["a", "b"],
              "history": [{"url": "https://example.com/"}]}
    (tmp_path / f"{legacy['id']}.json").write_text(json.dumps(legacy))
    (tmp_path / f"{legacy['id']}.jpg").write_bytes(b"x")
    [summary] = demo.list_runs()
    assert summary["start_url"] == "https://example.com/"
    assert summary["plan"] == ["a", "b"]

    monkeypatch.setitem(demo.RUN, "record", {"id": "another"})
    assert demo.command(f"runs/{legacy['id']}/delete", {}) == {"deleted": legacy["id"], "cleared": False}
    assert not list(tmp_path.iterdir())
    assert demo.RUN["record"] == {"id": "another"}
    with pytest.raises(ValueError):
        demo.command("runs/../x/delete", {})


def test_the_command_that_finishes_a_run_writes_it_despite_the_rate_limit(tmp_path, monkeypatch):
    from jev_ultrafast import demo

    state = {"status": "done", "history": [{"url": "https://example.com/"}], "page": {"url": "https://example.com/"}}
    fake = Mock(state=state, snapshot=Mock(return_value=state))
    monkeypatch.setattr(demo, "RUNS", tmp_path)
    monkeypatch.setattr(demo, "AGENT", fake)
    monkeypatch.setitem(demo.RUN, "saved", time.monotonic())
    monkeypatch.setitem(demo.RUN, "record", {"id": "r1", "status": "ready", "instructions": [{"text": "a"}]})
    demo.save_run()
    saved = json.loads((tmp_path / "r1.json").read_text())
    assert saved["status"] == "done"
    assert saved["instructions"][-1]["status"] == "done"
    assert saved["finished_at"]


def test_clearing_a_finished_run_resets_the_console(tmp_path, monkeypatch):
    from jev_ultrafast import demo

    closed = Mock()
    monkeypatch.setattr(demo, "AGENT", Mock(close=closed))
    monkeypatch.setitem(demo.RUN, "record", {"id": "r1"})
    monkeypatch.setitem(demo.NOTICE, "text", "a stale notice")
    state = demo.command("clear", {})
    assert state["status"] == "idle" and state["page"] is None
    closed.assert_called_once()  # the tab a finished run left behind is closed with it
    assert demo.RUN == {} and "text" not in demo.NOTICE
    assert demo.AGENT is None


def test_a_clickable_label_can_be_avoided(monkeypatch):
    """A looping label is skipped by name, because a reload renumbers the same control."""

    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": {"choice": "1", "confidence": 1.0, "probabilities": {"1": 0.7, "2": 0.3}},
                "type_text_target": choice(["1"], "1"),
            },
        }

    monkeypatch.setattr(model, "post_json", post)
    decided = model.choose(page(), "Go", [], avoid_labels={("CLICK", "Open Search")})
    assert decided["target"] == "2" and decided["choice"] == "e3"


def test_controls_signature_ignores_scrolling():
    from jev_ultrafast.agent import controls_signature

    page_a = {"actions": [{"label": "Home", "kind": "click"}, {"label": "Scroll down", "kind": "scroll"}]}
    page_b = {"actions": [{"label": "Home", "kind": "click"}, {"label": "Wait for the page to update", "kind": "wait"}]}
    assert controls_signature(page_a) == controls_signature(page_b) == ("Home",)


def test_repeating_an_action_over_the_same_controls_is_recorded_as_a_loop(runner, monkeypatch):
    """Clicking the link you are on reloads the page: changed=True, same controls, no progress."""
    state = runner.state
    state["status"] = "ready"
    state["loops"] = []
    state["decision"] = {**decision("e2"), "operation": "CLICK", "target": "2"}
    same = page()  # the reload hands back exactly the same controls
    state["browser"].observe = Mock(return_value=same)
    state["history"] = [
        {
            "step": 1,
            "action": "Open Search",
            "kind": "click",
            "page_changed": True,
            "probability": 0.6,
            "latency_ms": 10,
        },
    ]
    runner.command("act", {"fingerprint": state["page"]["fingerprint"]})
    assert state["loops"] == [("CLICK", "Open Search")]

    chosen = Mock(return_value={**decision("e3"), "operation": "CLICK", "target": "2"})
    monkeypatch.setattr(loop, "choose", chosen)
    state["status"] = "ready"
    runner.command("predict")
    assert chosen.call_args.kwargs["avoid_labels"] == {("CLICK", "Open Search")}


def test_a_page_that_keeps_looping_stops_instead_of_spending_the_budget(runner, monkeypatch):
    state = runner.state
    state["loops"] = [("CLICK", f"Step {i}") for i in range(loop.LOOP_LIMIT)]
    state["status"] = "ready"
    chosen = Mock()
    monkeypatch.setattr(loop, "choose", chosen)
    with pytest.raises(ValueError, match="kept changing nothing"):
        runner.command("predict")
    chosen.assert_not_called()
    assert state["status"] == "blocked"


def test_deleting_the_run_on_screen_ends_it(tmp_path, monkeypatch):
    from jev_ultrafast import demo

    monkeypatch.setattr(demo, "RUNS", tmp_path)
    (tmp_path / "r2.json").write_text("{}")
    fake = Mock()
    monkeypatch.setattr(demo, "AGENT", fake)
    monkeypatch.setitem(demo.RUN, "record", {"id": "r2"})
    assert demo.command("runs/r2/delete", {}) == {"deleted": "r2", "cleared": True}
    fake.close.assert_called_once()
    assert demo.AGENT is None and not demo.RUN
    assert demo.response_state()["page"] is None
