import json

import pytest

from bench import claude_cli, run


def stream(*events):
    return "\n".join(json.dumps(e) for e in events)


INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "tools": [], "model": "claude-haiku-5-5"}
RESULT = {"type": "result", "result": "hello", "is_error": False, "usage": {}, "duration_ms": 5}
WINDOWS = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"seven_day": {"utilization": 0.9}}}}


def test_cli_runs_without_tools_settings_or_keys():
    args = claude_cli.argv("claude", "claude-haiku-5-5", "sys")
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    env = claude_cli.environment()
    assert not {k for k in env if "KEY" in k or "TOKEN" in k or k.startswith("CLAUDE")}


def test_stream_reads_reply_model_and_usage():
    result, windows, model = claude_cli.read_stream(stream(INIT, WINDOWS, RESULT))
    assert (result["result"], windows, model) == ("hello", {"seven_day": 90.0}, "claude-haiku-5-5")


@pytest.mark.parametrize(
    ("event", "error"),
    [
        ({**INIT, "apiKeySource": "ANTHROPIC_API_KEY"}, claude_cli.UnsafeRun),
        ({**INIT, "tools": ["Bash"]}, claude_cli.UnsafeRun),
        ({"type": "rate_limit_event", "rate_limit_info": {"isUsingOverage": True}}, claude_cli.LimitReached),
        ({**INIT, "tools": None}, claude_cli.UnsafeRun),
        ({"type": "user"}, claude_cli.UnsafeRun),  # no init event at all
    ],
)
def test_stream_refuses_paid_or_tooled_runs(event, error):
    with pytest.raises(error):
        claude_cli.read_stream(stream(event, RESULT))


def test_stream_refuses_a_tool_call():
    call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}
    with pytest.raises(claude_cli.UnsafeRun):
        claude_cli.read_stream(stream(INIT, call, RESULT))


def test_cut_off_last_line_is_dropped_but_damage_elsewhere_raises(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text('{"a": 1}\n{"a": 2')
    assert run.recorded(path) == [{"a": 1}]
    path.write_text('{"a": 1\n{"a": 2}\n')
    with pytest.raises(ValueError, match="answers"):
        run.recorded(path)
