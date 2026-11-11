"""Download Foresee's public agent-facing documents into snapshot/<date>/ and check them against MANIFEST.json.

The documents are Foresee's, so this repository doesn't redistribute them; this script fetches them from their public
sources (HTTP GETs, the GitHub repository at a pinned commit, and one standard MCP handshake: initialize and
tools/list, no tool call). If a file no longer matches its recorded hash, Foresee has changed it since the capture:
the contract checks still run on what is fetched, but the recorded evaluation is scored against the pinned versions.

    python scripts/fetch_snapshot.py            # into snapshot/2026-11-10, verifying hashes
"""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "snapshot" / "2026-11-10"
MCP = "https://mcp.go-foresee.com/mcp"
AGENT = "agent-contract-bench (offline documentation check)"


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def github_raw(url: str) -> str:
    """https://github.com/<org>/<repo>/blob/<sha>/<path> -> raw.githubusercontent.com URL."""
    return url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/", 1)


def mcp_call(body: dict, session_headers: dict) -> bytes:
    """One JSON-RPC message; the server answers with a single SSE event whose data line is the JSON response."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": AGENT,
        **session_headers,
    }
    request = urllib.request.Request(MCP, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode()
    # Keep the event line exactly as sent (it ends in CRLF), so the bytes match the recorded hash.
    data = [line[len("data: ") :] + "\n" for line in text.split("\n") if line.startswith("data: ")]
    if not data:
        raise RuntimeError(f"no JSON-RPC response to {body.get('method')}")
    return data[0].encode()


def handshake() -> dict[str, bytes]:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "agent-contract-bench", "version": "0.1.0"},
        },
    }
    initialized = mcp_call(init, {})
    headers = {"MCP-Protocol-Version": "2025-06-18"}
    notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    request = urllib.request.Request(
        MCP,
        data=json.dumps(notify).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": AGENT, **headers},
    )
    urllib.request.urlopen(request, timeout=30).close()  # a notification has no response body
    tools = mcp_call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers)
    return {"initialize.json": initialized, "tools-list.json": tools}


def fetch(name: str, source: str, mcp: dict[str, bytes]) -> bytes:
    if source.startswith(MCP):
        return mcp[name]
    if source.startswith("https://github.com/"):
        return get(github_raw(source))
    return get(source)


def main() -> int:
    manifest = json.loads((ROOT / "MANIFEST.json").read_text())
    mcp = handshake()
    changed = []
    for name, entry in manifest["files"].items():
        content = fetch(name, entry["source"], mcp)
        (ROOT / name).write_bytes(content)
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            changed.append(name)
    print(f"fetched {len(manifest['files'])} files into {ROOT}")
    if changed:
        print("changed since the 10 Nov 2026 capture (hash differs):", ", ".join(changed))
        return 1
    print("all files match the recorded hashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
