"""Offline contracts for the chat-model decision endpoint and the mode switch. No paid APIs.

The page and the goal below are invented and deliberately plain: the tests describe shapes and
refusals, not any particular site.
"""

import json
import os
import urllib.request

import pytest

from jev_ultrafast import demo
from jev_ultrafast import llm_endpoint as endpoint
from jev_ultrafast.model import validate_choice

OPERATIONS = {
    "CLICK": "Click an element, button, menu option or suggestion.",
    "TYPE_TEXT": "Enter or replace text in an editable field.",
    "SELECT": "Select an observed dropdown value.",
    "DONE": "Every requirement is visibly satisfied.",
    "BLOCKED": "No supported operation can progress.",
}
GOAL = "Fill in the name field and press Save."
QUESTIONS = {
    "operation": {"type": "choice", "criteria": OPERATIONS, "instructions": {"goal": GOAL}},
    "click_target": {
        "type": "choice",
        "criteria": {"2": {"element": "[2] Save", "nearby": "Name"}, "3": {"element": "[3] Cancel"}},
        "instructions": {},
    },
    "type_text_target": {
        "type": "choice",
        "criteria": {"1": {"element": "[1] Name", "current_value": ""}},
        "instructions": {},
    },
    "select_target": {
        "type": "choice",
        "criteria": {"5:2": {"element": "[5] Country", "current_value": "Choose one"}},
        "instructions": {},
    },
}
STATE = {
    "page": {"url": "https://example.test/form", "title": "A plain form", "text": "Name Country Choose one Save"},
    "elements": ["[1] textbox Name · empty"],
    "recent_actions": [{"action": "click", "page_changed": False}],
}


class FakeChooser:
    """A chat endpoint that answers from a script instead of the network."""

    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)

    def ask(self, messages):
        assert messages[0]["role"] == "system"
        return self.replies.pop(0)


def endpoint_with(*replies):
    return endpoint.Endpoint(FakeChooser(replies), page_chars=endpoint.DEFAULT_PAGE_CHARS)


def test_actions_flatten_the_operation_and_target_heads():
    actions, mapping = endpoint.joint_actions(QUESTIONS)
    assert set(actions) == {"CLICK 2", "CLICK 3", "TYPE_TEXT 1", "SELECT 5:2", "DONE", "BLOCKED"}
    assert actions["DONE"] == OPERATIONS["DONE"]
    assert "[2] Save" in actions["CLICK 2"]
    assert "nearby=Name" in actions["CLICK 2"]
    assert mapping["TYPE_TEXT 1"].operation == "TYPE_TEXT"
    assert mapping["TYPE_TEXT 1"].index == "1"
    assert mapping["DONE"].index is None


def test_a_request_that_is_not_the_factored_shape_is_refused():
    assert endpoint.joint_actions({"sentiment": {"type": "choice", "criteria": {"good": None}}}) is None
    with pytest.raises(ValueError, match="factored"):
        endpoint_with("{}").decide(STATE, {"sentiment": {"type": "choice", "criteria": {"good": None}}})


@pytest.mark.parametrize("key", ["CLICK 2", "TYPE_TEXT 1", "SELECT 5:2", "DONE", "BLOCKED"])
def test_every_action_maps_back_to_an_answer_the_agent_accepts(key):
    actions, mapping = endpoint.joint_actions(QUESTIONS)
    answer = endpoint.answers_from_actions(endpoint.peaked(list(actions), key, 0.9), mapping, "test")
    chosen = validate_choice(answer["answers"]["operation"], set(OPERATIONS))["choice"]
    assert chosen == mapping[key].operation
    head = f"{chosen.lower()}_target"
    if mapping[key].index is None:
        assert head not in answer["answers"], "a control action has nothing to aim at"
    else:
        offered = set(QUESTIONS[head]["criteria"])
        assert validate_choice(answer["answers"][head], offered)["choice"] == mapping[key].index


@pytest.mark.parametrize("confidence", [0.0, 0.02, 0.5, 1.0, 4])
def test_the_reported_confidence_never_undermines_the_chosen_action(confidence):
    reply = json.dumps({"action": "CLICK 2", "confidence": confidence})
    result = endpoint_with(reply).decide(STATE, QUESTIONS)
    answer = validate_choice(result["answers"]["operation"], set(OPERATIONS))
    assert answer["choice"] == "CLICK"
    assert 0 <= answer["confidence"] <= 1
    validate_choice(result["answers"]["click_target"], {"2", "3"})


def test_the_decision_carries_a_model_name_and_a_latency():
    result = endpoint_with('{"action": "DONE"}').decide(STATE, QUESTIONS)
    assert result["model"] == "chat:fake-model"
    assert result["answers"]["operation"]["choice"] == "DONE"


def test_an_unreadable_reply_is_asked_for_once_more():
    prose = "I would fill the name field first"
    fenced = '```json\n{"action": "TYPE_TEXT 1"}\n```'
    model = endpoint_with(prose, fenced)
    result = model.decide(STATE, QUESTIONS)
    assert result["answers"]["operation"]["choice"] == "TYPE_TEXT"
    assert model.stats["repairs"] == 1
    assert model.stats["requests"] == 1


def test_two_unusable_replies_leave_the_step_unexecuted():
    with pytest.raises(ValueError, match="no usable action"):
        endpoint_with("nothing here", '{"action": "CLICK 99"}').decide(STATE, QUESTIONS)


def test_a_reply_that_names_an_unoffered_action_is_asked_again_with_the_keys():
    model = endpoint_with('{"action": "CLICK 99"}', '{"action": "CLICK 3"}')
    assert model.decide(STATE, QUESTIONS)["answers"]["click_target"]["choice"] == "3"


def test_parse_action_reads_the_three_shapes_a_model_actually_sends():
    keys = ["CLICK 2", "DONE"]
    assert endpoint.parse_action('{"action": "CLICK 2", "confidence": 0.7}', keys)[:2] == ("CLICK 2", 0.7)
    assert endpoint.parse_action('```json\n{"action": "DONE"}\n```', keys)[:2] == ("DONE", endpoint.DEFAULT_CONFIDENCE)
    assert endpoint.parse_action('Sure: {"action": "CLICK 2"} — that is my pick.', keys)[:2] == (
        "CLICK 2",
        endpoint.DEFAULT_CONFIDENCE,
    )


def test_parse_action_says_what_was_wrong():
    keys = ["CLICK 2"]
    assert "not one of the offered actions" in endpoint.parse_action('{"action": "CLICK 7"}', keys)[2]
    assert endpoint.parse_action('{"choice": "CLICK 2"}', keys)[0] == "CLICK 2"
    assert "no JSON object" in endpoint.parse_action("no json at all", keys)[2]


def test_the_message_keeps_the_goal_and_bounds_the_page():
    actions, _ = endpoint.joint_actions(QUESTIONS)
    long_state = {**STATE, "page": {**STATE["page"], "text": "x" * 500}}
    messages = endpoint.build_messages(long_state, QUESTIONS, actions, page_chars=100)
    payload = json.loads(messages[1]["content"])
    assert payload["goal"] == GOAL
    assert len(payload["page"]["text"]) == 100
    assert payload["actions"] == actions
    assert payload["recent_actions"] == STATE["recent_actions"]
    assert payload["rules"] == endpoint.JOINT_RULES
    assert "Reply with JSON only" in messages[0]["content"]


def test_peaked_is_a_distribution_that_peaks_where_it_should():
    keys = ["a", "b", "c"]
    distribution = endpoint.peaked(keys, "b", 0.6)
    assert set(distribution) == set(keys)
    assert abs(sum(distribution.values()) - 1) < 1e-9
    assert distribution["b"] == max(distribution.values())


def test_chooser_from_env_needs_an_endpoint_and_a_model(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("TEXT_MODEL", raising=False)
    with pytest.raises(ValueError, match="required"):
        endpoint.chooser_from_env()


def test_chooser_reads_the_same_reasoning_switch_the_text_helper_uses(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("TEXT_MODEL", "some-model")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "thinking-disabled")
    chooser, _ = endpoint.chooser_from_env()
    assert chooser.reasoning_flag() == {"thinking": {"type": "disabled"}}
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    assert chooser.reasoning_flag() == {"reasoning": {"enabled": False}}


def test_env_file_fills_gaps_without_overriding_the_real_environment(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("KEPT=from-file\nSET=from-file\n# comment\n")
    monkeypatch.setenv("KEPT", "from-process")
    monkeypatch.delenv("SET", raising=False)
    endpoint.load_env_file(path)
    assert os.environ["KEPT"] == "from-process"
    assert os.environ["SET"] == "from-file"


@pytest.fixture
def fresh_decisions():
    demo.DECISIONS.clear()
    yield
    demo.DECISIONS.clear()


def test_jev_mode_uses_the_configured_endpoint(monkeypatch, fresh_decisions):
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setenv("TYPESAFE_API_KEY", "key")
    monkeypatch.setenv("TYPESAFE_MODEL", "some-jev")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://example.test/v1/systemone")
    demo.resolve_decisions()
    assert demo.DECISIONS["label"] == "jev:some-jev"
    assert demo.DECISIONS.get("endpoint") is None, "Jev mode starts nothing locally"


def test_jev_mode_without_a_key_stops_with_a_readable_message(monkeypatch, fresh_decisions):
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="JEV_ENABLED=false"):
        demo.resolve_decisions()


def test_plain_mode_serves_a_local_endpoint_for_the_loop(monkeypatch, fresh_decisions):
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("TEXT_MODEL", "some-chat-model")
    monkeypatch.setenv("LLM_SERVER_PORT", "0")  # an ephemeral port, so tests never collide
    demo.resolve_decisions()
    url = os.environ["TYPESAFE_BASE_URL"]
    assert url.startswith("http://127.0.0.1:")
    assert demo.DECISIONS["label"] == "chat:some-chat-model"
    health = json.loads(urllib.request.urlopen(url.replace("/v1/systemone", "/health"), timeout=5).read())
    assert health["status"] == "ok"
    assert health["model"] == "some-chat-model"


def test_plain_mode_without_a_text_model_stops_with_a_readable_message(monkeypatch, fresh_decisions):
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.delenv("TEXT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("TEXT_MODEL", raising=False)
    with pytest.raises(SystemExit, match="text model cannot decide"):
        demo.resolve_decisions()

