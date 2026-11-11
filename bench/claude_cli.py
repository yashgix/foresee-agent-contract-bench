"""One tool-free reply from Claude through the Claude Code CLI on a subscription login.

The CLI runs with every tool, MCP server, setting and slash command off, in an empty directory and a minimal
environment with no API keys. A run that reports an API key, a tool, paid overage or a rejected limit stops.
"""

import glob
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

BINARY_GLOB = "~/.vscode/extensions/anthropic.claude-code-*-darwin-arm64/resources/native-binary/claude"
ANTHROPIC_TEAM = "Q6L2SF6YDW"
TIMEOUT = 600


class LimitReached(Exception):
    pass


class UnsafeRun(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    model: str
    usage: dict
    windows: dict
    duration_ms: int


def binary() -> str:
    found = sorted(glob.glob(os.path.expanduser(BINARY_GLOB)), key=_version)
    if not found:
        raise FileNotFoundError("no Claude Code binary from the VS Code extension")
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", found[-1]], capture_output=True, check=False
    )
    signed = subprocess.run(["/usr/bin/codesign", "-dv", found[-1]], capture_output=True, text=True, check=False)
    if verified.returncode != 0 or f"TeamIdentifier={ANTHROPIC_TEAM}" not in signed.stderr:
        raise UnsafeRun(f"{found[-1]} is not signed by Anthropic")
    return found[-1]


def _version(path: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)[0])


def environment() -> dict:
    user = os.environ.get("USER") or Path.home().name
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(Path.home()),
        "USER": user,
        "LOGNAME": user,
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": "en_US.UTF-8",
    }


def argv(executable: str, model: str, system: str) -> list[str]:
    return [
        executable,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--model",
        model,
        "--system-prompt",
        system,
    ]


def read_stream(out: str) -> tuple[dict, dict, str]:
    """(result event, usage windows, model); refuses anything not billed to the plan or not tool-free, and a
    stream without its init event."""
    windows, result, model, initialised = {}, None, "", False
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            if event.get("apiKeySource") != "none":
                raise UnsafeRun(f"billed through {event.get('apiKeySource')!r}, not the subscription")
            if event.get("tools") != []:
                raise UnsafeRun(f"tools were not confirmed off: {str(event.get('tools'))[:80]}")
            model, initialised = event.get("model", ""), True
        elif event.get("type") == "assistant" and any(
            part.get("type") == "tool_use" for part in event.get("message", {}).get("content", [])
        ):
            raise UnsafeRun("the model attempted a tool call")
        elif event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info", {})
            if info.get("status") == "rejected" or info.get("isUsingOverage"):
                raise LimitReached("subscription limit reached or overage in use")
            windows = {
                name: round(100 * w["utilization"], 1)
                for name, w in info.get("unifiedWindows", {}).items()
                if isinstance(w, dict) and "utilization" in w
            } or windows
        elif event.get("type") == "result":
            result = event
    if not initialised:
        raise UnsafeRun("no init event: billing and tools unverified")
    if result is None:
        raise RuntimeError("Claude Code returned no result")
    if result.get("is_error"):
        raise RuntimeError(f"Claude Code failed: {str(result.get('result'))[:200]}")
    return result, windows, model


def ask(model: str, system: str, user: str) -> Reply:
    with tempfile.TemporaryDirectory() as empty:
        done = subprocess.run(
            argv(binary(), model, system),
            input=user,
            capture_output=True,
            text=True,
            cwd=empty,
            env=environment(),
            timeout=TIMEOUT,
            check=False,
        )
    if done.returncode != 0:
        raise RuntimeError(f"Claude Code exited {done.returncode}: {done.stderr[:200]}")
    result, windows, reported = read_stream(done.stdout)
    return Reply(result["result"], reported, result.get("usage", {}), windows, result.get("duration_ms", 0))
