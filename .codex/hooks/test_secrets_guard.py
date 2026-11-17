import base64
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "guard", Path(__file__).with_name("secrets_guard.py")
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class SecretGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.secret = "synthetic-credential-for-hook-testing"
        (self.root / ".env").write_text(
            "ANTHROPIC_FOUNDRY_API_KEY=" + self.secret + "\n"
        )
        (self.root / "backend").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def pre(self, command, tool="Bash", **extra):
        return guard.decision(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": str(self.root),
                "tool_input": {"command": command, **extra},
            },
            self.root,
            {},
        )

    def test_sensitive_commands_are_denied(self):
        for command in [
            "cat .env",
            "head .env.local",
            "python -c \"open('.env').read()\"",
            "cat ../.env",
            "cat /tmp/credentials.json",
            "cat ~/.aws/credentials",
            "cat ~/.codex/auth.json",
            "cat signing.pem",
            "printenv",
            "env",
            "export -p",
            "python -c 'print(os.environ)'",
            "docker inspect app",
            "docker compose config",
            "cat file:///tmp/%2eenv",
            "docker exec app env",
        ]:
            with self.subTest(command=command):
                result = self.pre(command)
                self.assertEqual(
                    result["hookSpecificOutput"]["permissionDecision"], "deny"
                )
                self.assertNotIn(self.secret, json.dumps(result))

    def test_safe_development_commands_are_not_auto_approved_or_denied(self):
        for command in [
            "cat .env.example",
            "cat backend/app/config.py",
            "git status --short",
            "uv run --directory backend pytest -q",
            "docker compose config --quiet",
            "docker compose --profile test run --rm tests",
        ]:
            with self.subTest(command=command):
                self.assertEqual(self.pre(command), {})

    def test_structured_mcp_read_is_denied(self):
        event = {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__fs__read_file",
            "cwd": str(self.root),
            "tool_input": {"path": str(self.root / ".env")},
        }
        self.assertEqual(
            guard.decision(event, self.root, {})["hookSpecificOutput"][
                "permissionDecision"
            ],
            "deny",
        )

    def test_source_edits_can_mention_env_but_cannot_target_it(self):
        safe_patch = (
            "*** Begin Patch\n*** Update File: backend/app.py\n"
            "+load_dotenv('.env')\n*** End Patch"
        )
        self.assertEqual(self.pre(safe_patch, tool="apply_patch"), {})
        blocked = self.pre(
            safe_patch.replace("backend/app.py", ".env"), tool="apply_patch"
        )
        self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_symlink_and_subdirectory_paths_are_denied(self):
        (self.root / "innocent.txt").symlink_to(self.root / ".env")
        self.assertTrue(self.pre("cat innocent.txt"))
        self.assertTrue(self.pre("cat ../.env", workdir=str(self.root / "backend")))

    def test_outputs_containing_credentials_are_withheld(self):
        for value in [
            self.secret,
            base64.b64encode(self.secret.encode()).decode(),
            self.secret.encode().hex(),
        ]:
            with self.subTest(encoding=len(value)):
                result = guard.decision(
                    {
                        "hook_event_name": "PostToolUse",
                        "tool_response": {"content": [{"text": "Output: " + value}]},
                    },
                    self.root,
                    {},
                )
                self.assertEqual(result["decision"], "block")
                self.assertNotIn(self.secret, json.dumps(result))

    def test_environment_secret_is_withheld(self):
        result = guard.decision(
            {"hook_event_name": "PostToolUse", "tool_response": "runtime-secret-value"},
            self.root,
            {"AZURE_API_KEY": "runtime-secret-value"},
        )
        self.assertEqual(result["decision"], "block")

    def test_examples_are_scanned_for_accidentally_inserted_keys(self):
        (self.root / ".env.example").write_text('API_KEY="example-contained-secret"\n')
        self.assertEqual(
            guard.decision(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_response": "example-contained-secret",
                },
                self.root,
                {},
            )["decision"],
            "block",
        )

    def test_ordinary_output_passes(self):
        self.assertEqual(
            guard.decision(
                {"hook_event_name": "PostToolUse", "tool_response": "224 passed"},
                self.root,
                {},
            ),
            {},
        )

    def test_scan_errors_block_without_disclosing_input(self):
        (self.root / ".env").write_text('API_KEY="unterminated-private-value\n')
        result = guard.decision(
            {"hook_event_name": "PostToolUse", "tool_response": "ok"}, self.root, {}
        )
        self.assertEqual(result["decision"], "block")
        self.assertNotIn("unterminated", json.dumps(result))

    def test_real_stdin_stdout_protocol_uses_only_a_temporary_fixture(self):
        scripts = self.root / ".codex" / "hooks"
        scripts.mkdir(parents=True)
        script = scripts / "secrets_guard.py"
        shutil.copyfile(Path(__file__).with_name("secrets_guard.py"), script)
        for event, blocked in [
            (
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "exec_command",
                    "cwd": str(self.root),
                    "tool_input": {"cmd": "cat .env"},
                },
                True,
            ),
            ({"hook_event_name": "PostToolUse", "tool_response": self.secret}, True),
            ({"hook_event_name": "PostToolUse", "tool_response": "safe output"}, False),
        ]:
            run = subprocess.run(
                ["/usr/bin/python3", str(script)],
                input=json.dumps(event),
                text=True,
                capture_output=True,
                env={"PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(bool(json.loads(run.stdout)), blocked)
            self.assertNotIn(self.secret, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
