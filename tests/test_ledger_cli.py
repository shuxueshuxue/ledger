import json
import shutil
import subprocess
import sys
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
            self.assertTrue((ledger_dir / "notes" / "triage.md").exists())
            self.assertFalse((ledger_dir / "inbox.md").exists())

            agents = (ledger_dir / "AGENTS.md").read_text()
            self.assertIn(str(repo / "AGENTS.md"), agents)
            self.assertNotIn(str(Path.home() / ".codex" / "AGENTS.md"), agents)

            workspaces = json.loads((home / "workspaces.json").read_text())
            self.assertEqual(workspaces[str(repo.resolve())], "pr501")

            checkpoint = json.loads((ledger_dir / "checkpoints" / "task-framing" / "metadata.json").read_text())
            self.assertEqual(checkpoint["state"], "draft")
            self.assertEqual(checkpoint["quality"], "draft")
            self.assertIn("verification_required", checkpoint)
            self.assertIn("Verification level", checkpoint["missing"])

    def test_init_renders_agents_from_packaged_ledger_agent_instructions(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"

            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)

            agents = (home / "ledgers" / "pr501" / "AGENTS.md").read_text()
            self.assertIn("Source Template: ledger_agent/agent_instructions/AGENTS.md", agents)
            self.assertIn(f"Managed workspace local AGENTS: {(repo / 'AGENTS.md').resolve()}", agents)
            self.assertIn("## Directory Structure", agents)
            self.assertIn("notes/", agents)
            self.assertIn("checkpoints/", agents)
            self.assertNotIn("Inbox: ./inbox.md", agents)
            self.assertNotIn("{{", agents)

    def test_direct_script_init_renders_agent_instructions(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"

            result = subprocess.run(
                [sys.executable, str(Path(ledger.__file__)), "--home", str(home), "init", "pr501"],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            agents = (home / "ledgers" / "pr501" / "AGENTS.md").read_text()
            self.assertIn("Source Template: ledger_agent/agent_instructions/AGENTS.md", agents)

    def test_mixed_typed_inputs_are_captured_as_one_bundle_sync(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            note = repo / "checkpoint.md"
            note.write_text("# Checkpoint\n\nRuntime fact.\n")
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)

            captured: dict[str, object] = {}
            patch = {
                "decision": "accepted",
                "summary": "Bundle accepted.",
                "ledger_updates": {"current": "Bundle processed."},
                "checkpoint_updates": [],
                "references_add": [],
                "inbox_add": [],
            }

            def fake_run_ledger_agent(_base, _state, item, _warning):
                captured["item"] = item
                return "thread-1", patch, "synced"

            with (
                mock.patch("ledger_agent.cli.run_ledger_agent", side_effect=fake_run_ledger_agent),
                mock.patch.dict("os.environ", {"LEDGER_INLINE_WORKER": "1"}),
            ):
                ledger.main(
                    [
                        "--home",
                        str(home),
                        "-m",
                        "Import runtime fact.",
                        "-f",
                        str(note),
                    ],
                    cwd=repo,
                )

            item = captured["item"]
            self.assertIsInstance(item, dict)
            self.assertEqual(item["type"], "bundle")
            self.assertEqual([child["type"] for child in item["items"]], ["message", "file"])
            bundle_text = (home / "ledgers" / "pr501" / item["artifact"]).read_text()
            self.assertIn("Import runtime fact.", bundle_text)
            self.assertIn("checkpoint.md", bundle_text)
            self.assertIn("message", bundle_text)
            self.assertIn("file", bundle_text)
            manifest_lines = (home / "ledgers" / "pr501" / "stash" / "manifest.jsonl").read_text().splitlines()
            manifest_items = [json.loads(line) for line in manifest_lines]
            self.assertEqual([manifest_item["type"] for manifest_item in manifest_items], ["bundle"])

            log = git(home, "log", "--oneline", "-1")
            self.assertIn("ledger: sync pr501 bundle", log)

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

            with (
                mock.patch("ledger_agent.cli.run_ledger_agent", return_value=("thread-1", patch, "synced")),
                mock.patch.dict("os.environ", {"LEDGER_INLINE_WORKER": "1"}),
            ):
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

    def test_embedded_codex_runner_reads_current_item_completed_agent_message_events(self):
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
                            "type": "item.completed",
                            "item": {
                                "id": "item_0",
                                "type": "agent_message",
                                "text": '```json\n{"decision":"read_only","summary":"ok","ledger_updates":{},"checkpoint_updates":[],"references_add":[],"inbox_add":[]}\n```',
                            },
                        }
                    ),
                ]
            )
            completed = subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=stdout, stderr="")

            with mock.patch("ledger_agent.cli.run", return_value=completed):
                thread_id, patch, _answer = ledger.run_ledger_agent(base, state, item, "")

            self.assertEqual(thread_id, "thread-1")
            self.assertEqual(patch["decision"], "read_only")

    def test_patch_validation_ignores_checkpoint_notes_without_state_transition(self):
        from ledger_agent import cli as ledger

        model = {
            "checkpoints": [
                {
                    "id": "task-framing",
                    "state": "draft",
                }
            ]
        }
        patch = {
            "decision": "accepted",
            "ledger_updates": {},
            "checkpoint_updates": [
                {
                    "id": "task-framing",
                    "note": "No transition requested.",
                }
            ],
        }

        ledger.validate_patch(patch, model)

    def test_patch_validation_rejects_scalar_list_fields(self):
        from ledger_agent import cli as ledger

        model = {"checkpoints": []}
        patch = {
            "decision": "accepted",
            "ledger_updates": {"next_required_input": "Choose one narrow checkpoint lane."},
            "checkpoint_updates": [],
        }

        with self.assertRaisesRegex(ledger.LedgerError, "next_required_input must be a list"):
            ledger.validate_patch(patch, model)

    def test_patch_validation_rejects_unknown_patch_fields(self):
        from ledger_agent import cli as ledger

        model = {"checkpoints": []}
        patch = {
            "decision": "accepted",
            "ledger_updates": {},
            "checkpoint_updates": [],
            "accepted_facts_add": [{"fact": "Silently ignored today."}],
        }

        with self.assertRaisesRegex(ledger.LedgerError, "unknown field"):
            ledger.validate_patch(patch, model)

    def test_patch_validation_rejects_unknown_ledger_update_fields(self):
        from ledger_agent import cli as ledger

        model = {"checkpoints": []}
        patch = {
            "decision": "accepted",
            "ledger_updates": {"synced_head": "HEAD"},
            "checkpoint_updates": [],
        }

        with self.assertRaisesRegex(ledger.LedgerError, "unknown ledger_updates field"):
            ledger.validate_patch(patch, model)

    def test_notes_updates_write_markdown_under_notes(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            ledger_dir = home / "ledgers" / "pr501"
            item = ledger.capture_input(ledger_dir, "message", "Remember runtime know-how.", repo)
            run_id = "run-test"
            ledger.create_run_record(ledger_dir, run_id, item)
            patch = {
                "decision": "accepted",
                "summary": "noted",
                "ledger_updates": {},
                "checkpoint_updates": [],
                "references_add": [],
                "notes_updates": [
                    {
                        "path": "runtime/restart.md",
                        "mode": "append",
                        "content": "Restart backend with platform API key injected.",
                    }
                ],
            }

            with mock.patch("ledger_agent.cli.run_ledger_agent", return_value=("thread-1", patch, "synced")):
                ledger.run_worker(home, "pr501", run_id)

            note = (ledger_dir / "notes" / "runtime" / "restart.md").read_text()
            self.assertIn("Restart backend with platform API key injected.", note)

    def test_notes_updates_reject_path_traversal_before_writes(self):
        from ledger_agent import cli as ledger

        model = {"checkpoints": []}
        patch = {
            "decision": "accepted",
            "ledger_updates": {},
            "checkpoint_updates": [],
            "notes_updates": [{"path": "../outside.md", "mode": "append", "content": "bad"}],
        }

        with self.assertRaisesRegex(ledger.LedgerError, "Invalid notes path"):
            ledger.validate_patch(patch, model)

    def test_inbox_add_is_written_to_notes_triage(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            ledger_dir = home / "ledgers" / "pr501"
            item = ledger.capture_input(ledger_dir, "message", "Ambiguous input.", repo)
            run_id = "run-test"
            ledger.create_run_record(ledger_dir, run_id, item)
            patch = {
                "decision": "parked",
                "summary": "needs triage",
                "ledger_updates": {},
                "checkpoint_updates": [],
                "references_add": [],
                "inbox_add": [{"text": "Needs human ruling."}],
            }

            with mock.patch("ledger_agent.cli.run_ledger_agent", return_value=("thread-1", patch, "synced")):
                ledger.run_worker(home, "pr501", run_id)

            triage = (ledger_dir / "notes" / "triage.md").read_text()
            self.assertIn("Needs human ruling.", triage)
            self.assertFalse((ledger_dir / "inbox.md").exists())

    def test_typed_input_migrates_legacy_inbox_and_agents_before_sync(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            ledger_dir = home / "ledgers" / "pr501"
            shutil.rmtree(ledger_dir / "notes")
            (ledger_dir / "inbox.md").write_text("# Inbox\n\n- legacy triage item\n")
            (ledger_dir / "AGENTS.md").write_text("old agent rules mentioning Inbox: ./inbox.md\n")
            git(home, "add", ".")
            git(home, "commit", "-m", "simulate legacy layout")

            patch = {
                "decision": "accepted",
                "summary": "ok",
                "ledger_updates": {},
                "checkpoint_updates": [],
                "references_add": [],
                "notes_updates": [],
            }

            def fake_run_ledger_agent(base, _state, _item, _warning):
                agents = (base / "AGENTS.md").read_text()
                self.assertIn("## Directory Structure", agents)
                self.assertNotIn("Inbox: ./inbox.md", agents)
                return "thread-1", patch, "synced"

            with (
                mock.patch("ledger_agent.cli.run_ledger_agent", side_effect=fake_run_ledger_agent),
                mock.patch.dict("os.environ", {"LEDGER_INLINE_WORKER": "1"}),
            ):
                ledger.main(["--home", str(home), "-m", "trigger migration"], cwd=repo)

            triage = (ledger_dir / "notes" / "triage.md").read_text()
            self.assertIn("legacy triage item", triage)
            self.assertFalse((ledger_dir / "inbox.md").exists())

    def test_done_checkpoint_clears_missing_and_list_updates_are_recorded(self):
        from ledger_agent import cli as ledger

        model = {
            "checkpoints": [
                {
                    "id": "task-framing",
                    "title": "Task Framing",
                    "state": "draft",
                    "quality": "draft",
                    "missing": ["Goal"],
                    "history": [],
                }
            ],
            "accepted_facts": [],
            "decisions": [],
            "evidence": [],
        }
        patch = {
            "decision": "accepted",
            "ledger_updates": {
                "accepted_facts": [{"fact": "Goal exists", "source": "stash/message.md"}],
                "decisions": [{"decision": "Accept framing", "source": "stash/message.md"}],
                "evidence": [{"evidence": "Message has goal", "source": "stash/message.md"}],
            },
            "checkpoint_updates": [
                {
                    "id": "task-framing",
                    "from": "draft",
                    "to": "done",
                    "quality": "usable",
                    "reason": "Goal supplied.",
                    "source": "stash/message.md",
                }
            ],
        }

        updated = ledger.apply_patch_to_model(model, patch, "stash/message.md")

        self.assertEqual(updated["checkpoints"][0]["missing"], [])
        self.assertEqual(updated["accepted_facts"][0]["fact"], "Goal exists")
        self.assertEqual(updated["decisions"][0]["decision"], "Accept framing")
        self.assertEqual(updated["evidence"][0]["evidence"], "Message has goal")

    def test_checkpoint_update_records_verification_required_and_checkpoint_evidence(self):
        from ledger_agent import cli as ledger

        model = {
            "checkpoints": [
                {
                    "id": "runtime-proof",
                    "title": "Runtime Proof",
                    "state": "draft",
                    "quality": "draft",
                    "verification_required": [],
                    "evidence": [],
                    "missing": ["Verification level"],
                    "history": [],
                }
            ]
        }
        patch = {
            "decision": "accepted",
            "ledger_updates": {},
            "checkpoint_updates": [
                {
                    "id": "runtime-proof",
                    "from": "draft",
                    "to": "ready",
                    "quality": "usable",
                    "reason": "Verification bar is explicit.",
                    "source": "stash/message.md",
                    "verification_required": [
                        "backend-api-yatu",
                        "playwright-cli-yatu",
                        "play-as-test",
                    ],
                    "evidence": [
                        {
                            "tier": "source/test-layer",
                            "source": "tests/test_ledger_cli.py",
                            "result": "checkpoint records verification requirements",
                        }
                    ],
                    "missing": [],
                }
            ],
        }

        updated = ledger.apply_patch_to_model(model, patch, "stash/message.md")
        checkpoint = updated["checkpoints"][0]

        self.assertEqual(
            checkpoint["verification_required"],
            ["backend-api-yatu", "playwright-cli-yatu", "play-as-test"],
        )
        self.assertEqual(checkpoint["evidence"][0]["tier"], "source/test-layer")
        self.assertEqual(checkpoint["missing"], [])

    def test_patch_validation_rejects_scalar_checkpoint_verification_required(self):
        from ledger_agent import cli as ledger

        model = {"checkpoints": [{"id": "runtime-proof", "state": "draft"}]}
        patch = {
            "decision": "accepted",
            "ledger_updates": {},
            "checkpoint_updates": [
                {
                    "id": "runtime-proof",
                    "from": "draft",
                    "to": "ready",
                    "verification_required": "play-as-test",
                }
            ],
        }

        with self.assertRaisesRegex(ledger.LedgerError, "verification_required must be a list"):
            ledger.validate_patch(patch, model)

    def test_running_worker_blocks_new_typed_input(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            ledger_dir = home / "ledgers" / "pr501"
            run_dir = ledger_dir / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 999999,
                        "started_at": "2026-04-13T00:00:00Z",
                    }
                )
            )
            (ledger_dir / "runs" / "current.json").write_text(json.dumps({"run_id": "run-1"}))

            with self.assertRaisesRegex(ledger.LedgerError, "busy"):
                ledger.main(["--home", str(home), "-m", "new input"], cwd=repo)

    def test_worker_success_clears_current_run_and_applies_patch(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            name, base = ledger.current_ledger(ledger.parse_args(["--home", str(home), "show"]), cwd=repo)
            item = ledger.capture_input(base, "message", "Goal: keep worker alive.", repo)
            run_id = "run-test"
            ledger.create_run_record(base, run_id, item)
            patch = {
                "decision": "accepted",
                "summary": "ok",
                "ledger_updates": {"current": "Worker applied patch."},
                "checkpoint_updates": [],
                "references_add": [],
                "inbox_add": [],
            }

            with mock.patch("ledger_agent.cli.run_ledger_agent", return_value=("thread-1", patch, "synced")):
                ledger.run_worker(home, name, run_id)

            self.assertFalse((base / "runs" / "current.json").exists())
            model = json.loads((base / "ledger.json").read_text())
            self.assertEqual(model["current"], "Worker applied patch.")
            state = json.loads((base / "state.json").read_text())
            self.assertEqual(state["thread_id"], "thread-1")

    def test_start_worker_process_closes_parent_log_handles(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            name, base = ledger.current_ledger(ledger.parse_args(["--home", str(home), "show"]), cwd=repo)
            item = ledger.capture_input(base, "message", "Goal: keep worker alive.", repo)
            run_id = "run-test"
            ledger.create_run_record(base, run_id, item)
            captured = {}

            def fake_popen(*_args, **kwargs):
                captured["stdout"] = kwargs["stdout"]
                captured["stderr"] = kwargs["stderr"]
                return mock.Mock(pid=123)

            with mock.patch("subprocess.Popen", fake_popen):
                ledger.start_worker_process(home, name, run_id)

            self.assertTrue(captured["stdout"].closed)
            self.assertTrue(captured["stderr"].closed)

    def test_typed_input_marks_run_failed_when_worker_start_fails(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"

            with mock.patch("ledger_agent.cli.start_worker_process", side_effect=ledger.LedgerError("spawn failed")):
                with self.assertRaisesRegex(ledger.LedgerError, "spawn failed"):
                    ledger.main(["--home", str(home), "-m", "Goal: start failure."], cwd=repo)

            self.assertFalse((base / "runs" / "current.json").exists())
            last = json.loads((base / "runs" / "last.json").read_text())
            state = json.loads((base / "runs" / last["run_id"] / "state.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertIn("spawn failed", state["error"])
            self.assertEqual(git(home, "status", "--porcelain"), "")

    def test_inline_worker_failure_is_not_committed_twice(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)

            def fail_inside_worker(worker_home, name, run_id):
                ledger.fail_run_before_worker(worker_home, ledger.ledger_dir(worker_home, name), run_id, "handled inside worker")
                raise ledger.LedgerError("inline failed")

            with (
                mock.patch("ledger_agent.cli.run_worker", side_effect=fail_inside_worker),
                mock.patch.dict("os.environ", {"LEDGER_INLINE_WORKER": "1"}),
            ):
                with self.assertRaisesRegex(ledger.LedgerError, "inline failed"):
                    ledger.main(["--home", str(home), "-m", "Goal: inline failure."], cwd=repo)

            commits = git(home, "log", "--oneline", "--grep", "ledger: failed").splitlines()
            self.assertEqual(len(commits), 1)

    def test_show_reports_busy_run(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"
            run_dir = base / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(json.dumps({"status": "running", "pid": 123}))
            (base / "runs" / "current.json").write_text(json.dumps({"run_id": "run-1"}))

            output = ledger.main(["--home", str(home), "show"], cwd=repo)

            self.assertIn("Run: busy", output)
            self.assertIn("run-1", output)

    def test_wait_rejoins_current_run_and_returns_reply(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"
            run_dir = base / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(json.dumps({"status": "done"}))
            (run_dir / "reply.md").write_text("finished\n")
            (base / "runs" / "current.json").write_text(json.dumps({"run_id": "run-1"}))

            output = ledger.main(["--home", str(home), "wait"], cwd=repo)

            self.assertEqual(output, "finished\n\n")
            self.assertFalse((base / "runs" / "current.json").exists())
            self.assertEqual(git(home, "status", "--porcelain"), "")

    def test_wait_returns_last_finished_run_when_current_already_cleared(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"
            run_dir = base / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(json.dumps({"status": "done"}))
            (run_dir / "reply.md").write_text("finished after interruption\n")
            (base / "runs" / "last.json").write_text(json.dumps({"run_id": "run-1"}))

            output = ledger.main(["--home", str(home), "wait"], cwd=repo)

            self.assertEqual(output, "finished after interruption\n\n")

    def test_wait_returns_last_failed_run_error_when_current_already_cleared(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"
            run_dir = base / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(json.dumps({"status": "failed", "error": "agent rejected patch"}))
            (base / "runs" / "last.json").write_text(json.dumps({"run_id": "run-1"}))

            with self.assertRaisesRegex(ledger.LedgerError, "agent rejected patch"):
                ledger.main(["--home", str(home), "wait"], cwd=repo)

    def test_wait_closes_dead_running_worker_as_failed(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"
            run_dir = base / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(json.dumps({"status": "running", "pid": 999999}))
            (base / "runs" / "current.json").write_text(json.dumps({"run_id": "run-1"}))

            with self.assertRaisesRegex(ledger.LedgerError, "worker process exited"):
                ledger.main(["--home", str(home), "wait"], cwd=repo)

            state = json.loads((run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertFalse((base / "runs" / "current.json").exists())
            self.assertEqual(json.loads((base / "runs" / "last.json").read_text())["run_id"], "run-1")
            self.assertEqual(git(home, "status", "--porcelain"), "")

    def test_interrupted_wait_tells_user_how_to_resume(self):
        from ledger_agent import cli as ledger

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            home = root / "ledger-home"
            ledger.main(["--home", str(home), "init", "pr501"], cwd=repo)
            base = home / "ledgers" / "pr501"
            run_dir = base / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(json.dumps({"status": "running"}))
            (base / "runs" / "current.json").write_text(json.dumps({"run_id": "run-1"}))

            with (
                mock.patch("time.sleep", side_effect=KeyboardInterrupt),
                self.assertRaisesRegex(ledger.LedgerError, "ledger .*wait"),
            ):
                ledger.wait_for_run(base, "run-1", home=home)


if __name__ == "__main__":
    unittest.main()
