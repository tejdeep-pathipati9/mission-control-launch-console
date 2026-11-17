#!/usr/bin/env python3
"""Repository secret guard. Never log tool arguments, credentials, or raw output.

PreToolUse blocks common credential reads; PostToolUse withholds recognized secret
values before tool output reaches the model. This is not an OS security boundary.
"""

import base64
import json
import os
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
SECRET_NAME = re.compile(r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)
PATH_PART = re.compile(r"(?:file://)?[~\w./\\*?${}-]+")
SHELL_TOOLS = {"bash", "exec_command", "shell", "shell_command", "run", "write_stdin"}
MAX_INPUT = 8 * 1024 * 1024


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in {"description", "justification"}:
                yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def protected_path(text, cwd):
    text = unquote(text)
    if text.startswith("file://"):
        text = urlparse(text).path
    # Literal names also catch globs such as .env.* without expanding/reading files.
    for candidate in (
        Path(text),
        (Path(text).expanduser() if text.startswith("~") else Path(text)),
    ):
        path = candidate if candidate.is_absolute() else cwd / candidate
        path = path.resolve(strict=False)
        name = path.name.lower()
        parts = tuple(part.lower() for part in path.parts)
        if (
            name == ".envrc"
            or (name == ".env" or name.startswith(".env."))
            and name not in SAFE_ENV_FILES
        ):
            return True
        if name in {
            "secrets.json",
            "secrets.yaml",
            "secrets.yml",
            "credentials.json",
            ".netrc",
            ".npmrc",
            ".pypirc",
            ".git-credentials",
        }:
            return True
        if name.endswith((".pem", ".key", ".p12", ".pfx")):
            return True
        if any(part in {".aws", ".ssh", ".azure", ".gnupg"} for part in parts):
            return True
        if ".codex" in parts and name == "auth.json":
            return True
    return False


def pre_tool_reason(event, root):
    tool_input = event.get("tool_input", {})
    cwd = Path(event.get("tool_cwd") or event.get("cwd") or root)
    if isinstance(tool_input, dict):
        cwd = Path(tool_input.get("workdir") or tool_input.get("cwd") or cwd)
    if not cwd.is_absolute():
        cwd = root / cwd
    name = event.get("tool_name", "").lower().split(".")[-1]
    if name in {"apply_patch", "edit", "write"}:
        # Code edits may legitimately mention .env; inspect edit targets, not source text.
        if isinstance(tool_input, dict) and "file_path" in tool_input:
            targets = [tool_input["file_path"]]
        else:
            patch = (
                tool_input.get("command", tool_input.get("patch", ""))
                if isinstance(tool_input, dict)
                else tool_input
            )
            targets = re.findall(
                r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",
                patch,
                re.M,
            )
        if not targets:
            return "Unrecognized edit target blocked by secret guard."
        return (
            "Editing credential files is blocked."
            if any(protected_path(path, cwd) for path in targets)
            else None
        )
    for text in strings(tool_input):
        for token in PATH_PART.findall(unquote(text)):
            if protected_path(token, cwd):
                return "Credential-file access blocked. Use the sanitized .env.example template."
    if name in SHELL_TOOLS or name.endswith("exec_command"):
        command = (
            tool_input.get(
                "command", tool_input.get("cmd", tool_input.get("chars", ""))
            )
            if isinstance(tool_input, dict)
            else tool_input
        )
        if not isinstance(command, str):
            return "Unrecognized shell input blocked by secret guard."
        # Detect environment dumps even inside shell wrappers and inline scripts.
        if re.search(r"\bprintenv\b|\b(?:export|declare|typeset)\s+-p\b", command):
            return "Environment dumps are blocked because they can expose credentials."
        if re.search(r"(?:^|[\s;|&])(?:env|set)\s*(?:$|[;|&\n])", command):
            return "Environment dumps are blocked because they can expose credentials."
        if re.search(
            r"(?:print|pprint|console\.log|dump)\s*\([^\n]*(?:os\.environ|os\.getenv|process\.env)",
            command,
        ):
            return "Printing environment credentials is blocked."
        if re.search(r"\bdocker\s+(?:container\s+)?inspect\b", command):
            return "Docker inspection can expose container credentials."
        if re.search(
            r"\bdocker\s+compose\b[^\n]*\bconfig\b", command
        ) and not re.search(r"--quiet\b|-q\b", command):
            return "Expanded Compose configuration can expose credentials; use config --quiet."
    return None


def credential_values(root, environ):
    """Executed by the trusted hook only. Values never appear in hook output/logs."""
    values = set()

    def remember(name, value):
        if SECRET_NAME.search(name) and isinstance(value, str) and len(value) >= 8:
            values.add(value)

    for name, value in environ.items():
        remember(name, value)
    for path in root.glob(".env*"):
        if not path.is_file():
            continue
        if path.stat().st_size > 256 * 1024:
            raise ValueError("Oversized environment file")
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(
                r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line
            )
            if match and SECRET_NAME.search(match[1]):
                try:
                    parsed = shlex.split(match[2], comments=True)
                except ValueError:
                    raise ValueError("Unparseable credential assignment") from None
                if parsed:
                    remember(match[1], parsed[0])
    return values


def post_tool_reason(event, root, environ):
    text = json.dumps(event.get("tool_response"), ensure_ascii=False)
    for value in credential_values(root, environ):
        variants = (
            value,
            json.dumps(value, ensure_ascii=False)[1:-1],
            base64.b64encode(value.encode()).decode(),
            value.encode().hex(),
        )
        if any(secret in text for secret in variants):
            return "Tool output withheld because it contains a protected credential."
    return None


def decision(event, root, environ):
    name = event.get("hook_event_name", "PreToolUse")
    try:
        if name == "PreToolUse":
            reason = pre_tool_reason(event, root)
        elif name == "PostToolUse":
            reason = post_tool_reason(event, root, environ)
        else:
            return {}
    except Exception:
        reason = "Secret guard could not validate this operation; blocked without exposing details."
    if not reason:
        return {}  # Abstain; never override Codex's normal permissions with an allow decision.
    if name == "PostToolUse":
        return {"decision": "block", "reason": reason}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main():
    root = Path(__file__).resolve().parents[2]
    try:
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("Oversized hook event")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("Invalid hook event")
        output = decision(event, root, os.environ)
    except Exception:
        # A malformed event cannot safely distinguish pre/post; exit 2 blocks both.
        print(
            "Secret guard could not validate the event; operation blocked.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
