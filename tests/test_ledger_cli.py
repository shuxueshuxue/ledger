import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class LedgerCliTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "ledger@example.test")
        git(repo, "config", "user.name", "Ledger Test")
        (repo / "AGENTS.md").write_text("local repo rules\n")
        (repo / "README.md").write_text("hello\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "initial")
        return repo

    def test_init_creates_git_backed_ledger_and_required_checkpoint(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"

            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)

            ledger_dir = home / "ledgers" / "pr501"
            self.assertTrue((home / ".git").exists())
            self.assertTrue((ledger_dir / "AGENTS.md").exists())
            self.assertTrue((ledger_dir / "ledger.json").exists())
            self.assertTrue((ledger_dir / "ledger.md").exists())
            self.assertTrue((ledger_dir / "checkpoints" / "task-framing" / "metadata.json").exists())

            agents = (ledger_dir / "AGENTS.md").read_text()
            self.assertIn(str(repo / "AGENTS.md"), agents)
            self.assertNotIn(str(Path.home() / ".codex" / "AGENTS.md"), agents)

            workspaces = json.loads((home / "workspaces.json").read_text())
            self.assertEqual(workspaces[str(repo.resolve())], "pr501")

            checkpoint = json.loads((ledger_dir / "checkpoints" / "task-framing" / "metadata.json").read_text())
            self.assertEqual(checkpoint["state"], "draft")
            self.assertEqual(checkpoint["quality"], "draft")

    def test_message_input_captures_artifact_applies_agent_patch_and_commits(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)

            patch = {
                "decision": "accepted",
                "summary": "Task framing accepted.",
                "ledger_updates": {
                    "goal": "Judge whether PR #501 can be used as the next foundation.",
                    "current": "The task is framed as a foundation decision with no code edits.",
                    "status": "in_progress",
                    "quality": "usable",
                    "next_required_input": ["ledger -p 501", "ledger -r origin/dev..HEAD"],
                },
                "checkpoint_updates": [
                    {
                        "id": "task-framing",
                        "title": "Task Framing",
                        "from": "draft",
                        "to": "done",
                        "quality": "strict",
                        "reason": "The user supplied goal and stopline.",
                        "source": "stash/message.md",
                        "missing": [],
                    }
                ],
                "references_add": [],
                "inbox_add": [],
            }

            with mock.patch("ledger_agent.cli.run_ledger_agent", return_value=("thread-1", patch, "synced")):
                ledger.main(["--home", str(home), "-m", "Goal: foundation decision; stopline: no code edits."], cwd=repo)

            ledger_dir = home / "ledgers" / "pr501"
            state = json.loads((ledger_dir / "state.json").read_text())
            self.assertEqual(state["thread_id"], "thread-1")
            self.assertEqual(state["status"], "in_progress")
            self.assertEqual(state["quality"], "usable")

            ledger_model = json.loads((ledger_dir / "ledger.json").read_text())
            self.assertEqual(ledger_model["goal"], "Judge whether PR #501 can be used as the next foundation.")
            self.assertEqual(ledger_model["checkpoints"][0]["state"], "done")

            log = git(home, "log", "--oneline", "-1")
            self.assertIn("ledger: sync pr501 message", log)
            self.assertEqual(git(home, "status", "--porcelain"), "")

    def test_show_defaults_to_summary_not_full_ledger(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)

            output = ledger.main(["--home", str(home), "show"], cwd=repo)

            self.assertIn("Ledger: pr501", output)
            self.assertIn("Checkpoints:", output)
            self.assertNotIn("## Accepted Facts", output)

    def test_embedded_codex_runner_uses_codex_exec_json_not_shell_wrappers(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "AGENTS.md").write_text("ledger rules\n")
            state = {
                "name": "pr501",
                "workspace_root": str(base),
                "thread_id": None,
            }
            item = {
                "type": "message",
                "raw": "hello",
                "artifact": "stash/message.md",
            }
            stdout = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                    json.dumps(
                        {
                            "type": "agent_message",
                            "item": {
                                "text": '```json\n{"decision":"read_only","summary":"ok","ledger_updates":{},"checkpoint_updates":[],"references_add":[],"inbox_add":[]}\n```'
                            },
                        }
                    ),
                ]
            )
            completed = subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=stdout, stderr="")

            with mock.patch("ledger_agent.cli.run", return_value=completed) as run_cmd:
                thread_id, patch, answer = ledger.run_ledger_agent(base, state, item, "")

            self.assertEqual(thread_id, "thread-1")
            self.assertEqual(patch["decision"], "read_only")
            self.assertIn("summary", patch)
            command = run_cmd.call_args.args[0]
            self.assertEqual(command[:3], ["codex", "exec", "--json"])
            self.assertNotIn("codex-run", " ".join(command))


if __name__ == "__main__":
    unittest.main()
