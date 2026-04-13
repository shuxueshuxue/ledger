#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any


DEFAULT_HOME = Path("~/.ledger").expanduser()
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
CHECKPOINT_STATES = {"draft", "ready", "in_progress", "blocked", "done", "dropped"}
LEDGER_STATUSES = {"open", "in_progress", "blocked", "parked", "done", "dropped"}
QUALITIES = {"draft", "usable", "strict", "blocked"}


class LedgerError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "item"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        raise LedgerError(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check).stdout.strip()


def repo_root(cwd: Path) -> Path:
    try:
        return Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()
    except LedgerError as exc:
        raise LedgerError(f"Current directory is not inside a git repo: {cwd}") from exc


def git_head(cwd: Path) -> str:
    return git(cwd, "rev-parse", "HEAD")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def home_git_init(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    if not (home / ".git").exists():
        git(home, "init")
    git(home, "config", "user.email", "ledger@example.local")
    git(home, "config", "user.name", "Ledger")


def ensure_clean_home(home: Path) -> None:
    if not (home / ".git").exists():
        return
    dirty = git(home, "status", "--porcelain")
    if dirty:
        raise LedgerError(
            "Ledger repository has uncommitted changes.\n"
            "Review with:\n"
            f"  git -C {home} status\n"
            f"  git -C {home} diff\n"
            "Then revert or commit the manual change before continuing."
        )


def commit_if_dirty(home: Path, message: str) -> None:
    dirty = git(home, "status", "--porcelain")
    if not dirty:
        return
    git(home, "add", ".")
    git(home, "commit", "-m", message)


def workspaces_path(home: Path) -> Path:
    return home / "workspaces.json"


def ledger_dir(home: Path, name: str) -> Path:
    return home / "ledgers" / name


def find_bound_ledger(home: Path, cwd: Path) -> str:
    workspaces = load_json(workspaces_path(home), {})
    current = cwd.resolve()
    for path in [current, *current.parents]:
        name = workspaces.get(str(path))
        if name:
            return name
    raise LedgerError("No ledger bound to this workspace. Run: ledger init <name>.")


def validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise LedgerError("Ledger name must match [A-Za-z0-9._-]+")


def initial_checkpoint(created_at: str) -> dict[str, Any]:
    return {
        "id": "task-framing",
        "title": "Task Framing",
        "state": "draft",
        "quality": "draft",
        "order": 10,
        "goal": "Define this ledger's goal, scope, stopline, and evidence bar.",
        "acceptance": [
            "Goal is explicit.",
            "Scope is explicit.",
            "Stopline is explicit.",
            "Evidence bar is explicit.",
        ],
        "evidence": [],
        "missing": ["Goal", "Scope", "Stopline", "Evidence bar"],
        "history": [
            {
                "at": created_at,
                "from": None,
                "to": "draft",
                "reason": "Created at init.",
                "source": "init",
            }
        ],
    }


def initial_ledger_model(name: str, created_at: str) -> dict[str, Any]:
    return {
        "name": name,
        "goal": "Unclear.",
        "current": "No accepted current summary yet.",
        "status": "open",
        "quality": "draft",
        "checkpoints": [initial_checkpoint(created_at)],
        "accepted_facts": [],
        "decisions": [],
        "evidence": [],
        "open_questions": [],
        "next_required_input": [],
        "inbox": [],
    }


def render_checkpoint_md(checkpoint: dict[str, Any]) -> str:
    lines = [
        f"# {checkpoint['title']}",
        "",
        "## Goal",
        checkpoint.get("goal", ""),
        "",
        "## Acceptance",
    ]
    lines.extend(f"- {item}" for item in checkpoint.get("acceptance", []))
    lines.extend(["", "## Current", f"State: {checkpoint.get('state', 'draft')}", f"Quality: {checkpoint.get('quality', 'draft')}", ""])
    lines.append("## Missing")
    missing = checkpoint.get("missing", [])
    lines.extend((f"- {item}" for item in missing) if missing else ["None."])
    lines.extend(["", "## Evidence"])
    evidence = checkpoint.get("evidence", [])
    lines.extend((f"- {item}" for item in evidence) if evidence else ["None."])
    lines.append("")
    return "\n".join(lines)


def render_ledger_md(model: dict[str, Any]) -> str:
    lines = [
        f"# {model['name']}",
        "",
        "## Goal",
        model.get("goal", ""),
        "",
        "## Current",
        model.get("current", ""),
        "",
        "## Status",
        model.get("status", "open"),
        "",
        "## Quality",
        model.get("quality", "draft"),
        "",
        "## Checkpoint Summary",
    ]
    for checkpoint in sorted(model.get("checkpoints", []), key=lambda item: item.get("order", 1000)):
        missing = checkpoint.get("missing") or []
        suffix = f" | missing: {', '.join(missing)}" if missing else ""
        lines.append(
            f"- {checkpoint.get('state', 'draft')} | {checkpoint['id']} | {checkpoint['title']}{suffix}"
        )
    lines.extend(["", "## Checkpoints"])
    for checkpoint in sorted(model.get("checkpoints", []), key=lambda item: item.get("order", 1000)):
        lines.extend(
            [
                "",
                f"### {checkpoint['id']} — {checkpoint['title']}",
                f"State: {checkpoint.get('state', 'draft')}",
                f"Quality: {checkpoint.get('quality', 'draft')}",
                "",
                "Goal:",
                checkpoint.get("goal", ""),
                "",
                "Missing:",
            ]
        )
        missing = checkpoint.get("missing") or []
        lines.extend((f"- {item}" for item in missing) if missing else ["- None"])
    for section, key in [
        ("Accepted Facts", "accepted_facts"),
        ("Decisions", "decisions"),
        ("Evidence", "evidence"),
        ("Open Questions", "open_questions"),
        ("Next Required Input", "next_required_input"),
        ("Inbox", "inbox"),
    ]:
        lines.extend(["", f"## {section}"])
        values = model.get(key, [])
        lines.extend((f"- {value}" for value in values) if values else ["None."])
    lines.append("")
    return "\n".join(lines)


def render_checkpoint_files(base: Path, model: dict[str, Any]) -> None:
    checkpoint_root = base / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    index = []
    for checkpoint in model.get("checkpoints", []):
        cp_dir = checkpoint_root / checkpoint["id"]
        cp_dir.mkdir(parents=True, exist_ok=True)
        write_json(cp_dir / "metadata.json", checkpoint)
        (cp_dir / "checkpoint.md").write_text(render_checkpoint_md(checkpoint))
        history = checkpoint.get("history", [])
        (cp_dir / "history.jsonl").write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in history)
        )
        index.append(
            {
                "id": checkpoint["id"],
                "title": checkpoint["title"],
                "state": checkpoint.get("state", "draft"),
                "quality": checkpoint.get("quality", "draft"),
                "order": checkpoint.get("order", 1000),
            }
        )
    write_json(checkpoint_root / "index.json", {"checkpoints": sorted(index, key=lambda item: item["order"])})


def render_all(base: Path, model: dict[str, Any]) -> None:
    write_json(base / "ledger.json", model)
    (base / "ledger.md").write_text(render_ledger_md(model))
    render_checkpoint_files(base, model)


def ledger_agents_md(_workspace_root: Path, local_agents: Path | None) -> str:
    local_agents_line = str(local_agents) if local_agents and local_agents.exists() else "(none)"
    template = resources.files("ledger_agent").joinpath("agent_instructions/AGENTS.md").read_text()
    return template.replace("{{LOCAL_AGENTS_PATH}}", local_agents_line)


def cmd_init(args: argparse.Namespace, *, cwd: Path) -> str:
    name = args.name
    validate_name(name)
    home = args.home
    root = repo_root(cwd)
    home_git_init(home)
    ensure_clean_home(home)
    base = ledger_dir(home, name)
    if base.exists():
        raise LedgerError(f"Ledger already exists: {name}")
    workspaces = load_json(workspaces_path(home), {})
    if str(root) in workspaces:
        raise LedgerError(f"Workspace already bound to ledger: {workspaces[str(root)]}")
    created = now_iso()
    base.mkdir(parents=True)
    (base / "stash").mkdir()
    (base / "logs").mkdir()
    (base / "references.md").write_text("# References\n\n")
    (base / "inbox.md").write_text("# Inbox\n\n")
    (base / "AGENTS.md").write_text(ledger_agents_md(root, root / "AGENTS.md"))
    state = {
        "name": name,
        "workspace_root": str(root),
        "init_cwd": str(cwd.resolve()),
        "thread_id": None,
        "synced_head": git_head(root),
        "status": "open",
        "quality": "draft",
        "local_agents_path": str(root / "AGENTS.md") if (root / "AGENTS.md").exists() else None,
        "created_at": created,
        "updated_at": created,
        "last_input_at": None,
        "last_sync_at": None,
    }
    write_json(base / "state.json", state)
    model = initial_ledger_model(name, created)
    render_all(base, model)
    workspaces[str(root)] = name
    write_json(workspaces_path(home), workspaces)
    commit_if_dirty(home, f"ledger: init {name}")
    return f"Initialized ledger: {name}\n"


def current_ledger(args: argparse.Namespace, *, cwd: Path) -> tuple[str, Path]:
    name = find_bound_ledger(args.home, cwd)
    base = ledger_dir(args.home, name)
    if not base.exists():
        raise LedgerError(f"Bound ledger does not exist: {name}")
    return name, base


def runs_dir(base: Path) -> Path:
    return base / "runs"


def current_run_path(base: Path) -> Path:
    return runs_dir(base) / "current.json"


def last_run_path(base: Path) -> Path:
    return runs_dir(base) / "last.json"


def run_record_dir(base: Path, run_id: str) -> Path:
    return runs_dir(base) / run_id


def load_current_run(base: Path) -> dict[str, Any] | None:
    path = current_run_path(base)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_last_run(base: Path) -> dict[str, Any] | None:
    path = last_run_path(base)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def assert_not_busy(base: Path) -> None:
    current = load_current_run(base)
    if current is None:
        return
    run_id = current.get("run_id", "")
    state_path = run_record_dir(base, run_id) / "state.json"
    state = load_json(state_path, {})
    raise LedgerError(
        "Ledger is busy with a running sync.\n"
        f"Run: {run_id}\n"
        f"Status: {state.get('status', 'running')}\n"
        "Wait for it to finish before starting another sync."
    )


def create_run_record(base: Path, run_id: str, item: dict[str, Any]) -> None:
    directory = run_record_dir(base, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "status": "running",
        "pid": None,
        "input_type": item["type"],
        "artifact": item["artifact"],
        "started_at": now_iso(),
        "finished_at": None,
    }
    write_json(directory / "state.json", state)
    write_json(directory / "input.json", item)
    write_json(current_run_path(base), {"run_id": run_id})


def update_run_state(base: Path, run_id: str, **updates: Any) -> None:
    path = run_record_dir(base, run_id) / "state.json"
    state = load_json(path, {})
    state.update(updates)
    write_json(path, state)


def clear_current_run(base: Path, run_id: str) -> None:
    path = current_run_path(base)
    if not path.exists():
        return
    current = json.loads(path.read_text())
    if current.get("run_id") == run_id:
        path.unlink()


def mark_last_run(base: Path, run_id: str) -> None:
    write_json(last_run_path(base), {"run_id": run_id})


def pid_is_running(pid: Any) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def close_terminal_run_if_owner_dead(home: Path, base: Path, run_id: str, state: dict[str, Any]) -> None:
    if pid_is_running(state.get("pid")):
        return
    clear_current_run(base, run_id)
    mark_last_run(base, run_id)
    commit_if_dirty(home, f"ledger: close run {run_id}")


def fail_dead_running_worker(home: Path, base: Path, run_id: str, state: dict[str, Any]) -> None:
    pid = state.get("pid")
    if not pid or pid_is_running(pid):
        return
    update_run_state(
        base,
        run_id,
        status="failed",
        finished_at=now_iso(),
        error=f"worker process exited before updating run state: {pid}",
    )
    clear_current_run(base, run_id)
    mark_last_run(base, run_id)
    commit_if_dirty(home, f"ledger: failed {run_id}")


def fail_run_before_worker(home: Path, base: Path, run_id: str, error: str) -> None:
    update_run_state(base, run_id, status="failed", finished_at=now_iso(), error=error)
    clear_current_run(base, run_id)
    mark_last_run(base, run_id)
    commit_if_dirty(home, f"ledger: failed {run_id}")


def ledger_command(home: Path, command: str) -> str:
    parts = ["ledger"]
    if home.resolve() != DEFAULT_HOME.resolve():
        parts.extend(["--home", str(home)])
    parts.append(command)
    return " ".join(shlex.quote(part) for part in parts)


def interrupted_wait_message(home: Path, run_id: str) -> str:
    return "\n".join(
        [
            "Ledger wait interrupted; the worker is still running in the background.",
            f"Run: {run_id}",
            "Check status:",
            f"  {ledger_command(home, 'show')}",
            "Continue waiting:",
            f"  {ledger_command(home, 'wait')}",
        ]
    )


def artifact_id(input_type: str, raw: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{input_type}-{safe_slug(raw)[:48]}"


def append_manifest(base: Path, item: dict[str, Any]) -> None:
    manifest = base / "stash" / "manifest.jsonl"
    with manifest.open("a") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def capture_message(base: Path, raw: str, workspace_root: Path) -> dict[str, Any]:
    item_id = artifact_id("message", "message")
    path = base / "stash" / f"{item_id}.md"
    path.write_text(f"# Message\n\n{raw}\n")
    return {"id": item_id, "type": "message", "raw": raw, "artifact": str(path.relative_to(base))}


def capture_commit(base: Path, rev: str, workspace_root: Path) -> dict[str, Any]:
    resolved = git(workspace_root, "rev-parse", rev)
    item_id = artifact_id("commit", resolved[:12])
    path = base / "stash" / f"{item_id}.md"
    summary = git(workspace_root, "show", "--stat", "--summary", resolved)
    names = git(workspace_root, "show", "--name-status", "--format=fuller", resolved)
    path.write_text(f"# Commit {resolved}\n\n## Summary\n\n```\n{summary}\n```\n\n## Name Status\n\n```\n{names}\n```\n")
    return {"id": item_id, "type": "commit", "raw": rev, "resolved": resolved, "artifact": str(path.relative_to(base))}


def capture_range(base: Path, rev_range: str, workspace_root: Path) -> dict[str, Any]:
    count = git(workspace_root, "rev-list", "--count", rev_range)
    commits = git(workspace_root, "log", "--oneline", rev_range)
    files = git(workspace_root, "diff", "--name-only", rev_range)
    item_id = artifact_id("range", rev_range.replace("..", "_"))
    path = base / "stash" / f"{item_id}.md"
    path.write_text(
        f"# Commit Range {rev_range}\n\n## Count\n\n{count}\n\n## Commits\n\n```\n{commits}\n```\n\n## Changed Files\n\n```\n{files}\n```\n"
    )
    return {"id": item_id, "type": "commit_range", "raw": rev_range, "artifact": str(path.relative_to(base))}


def capture_file(base: Path, raw: str, workspace_root: Path) -> dict[str, Any]:
    source = Path(raw).expanduser()
    if not source.is_absolute():
        source = workspace_root / source
    if not source.is_file():
        raise LedgerError(f"File not found: {raw}")
    item_id = artifact_id("file", source.name)
    dest = base / "stash" / f"{item_id}-{source.name}"
    shutil.copy2(source, dest)
    return {"id": item_id, "type": "file", "raw": raw, "source": str(source), "artifact": str(dest.relative_to(base))}


def capture_dir(base: Path, raw: str, workspace_root: Path) -> dict[str, Any]:
    source = Path(raw).expanduser()
    if not source.is_absolute():
        source = workspace_root / source
    if not source.is_dir():
        raise LedgerError(f"Directory not found: {raw}")
    files = [path for path in source.rglob("*") if path.is_file()]
    if len(files) > 200:
        raise LedgerError("Directory too large for v0; stash a narrower path")
    item_id = artifact_id("dir", source.name)
    dest = base / "stash" / f"{item_id}-{source.name}"
    ignore = shutil.ignore_patterns(".git", "node_modules", ".venv", "__pycache__")
    shutil.copytree(source, dest, ignore=ignore)
    return {"id": item_id, "type": "dir", "raw": raw, "source": str(source), "artifact": str(dest.relative_to(base))}


def capture_pr(base: Path, raw: str, workspace_root: Path) -> dict[str, Any]:
    fields = "title,state,updatedAt,baseRefName,headRefName,url"
    data = run(["gh", "pr", "view", raw, "--json", fields], cwd=workspace_root).stdout
    pr = json.loads(data)
    item_id = artifact_id("pr", raw)
    json_path = base / "stash" / f"{item_id}.json"
    md_path = base / "stash" / f"{item_id}.md"
    write_json(json_path, pr)
    md_path.write_text(
        f"# PR {raw}\n\n"
        f"- Title: {pr.get('title')}\n"
        f"- State: {pr.get('state')}\n"
        f"- Updated: {pr.get('updatedAt')}\n"
        f"- Base: {pr.get('baseRefName')}\n"
        f"- Head: {pr.get('headRefName')}\n"
        f"- URL: {pr.get('url')}\n"
    )
    return {"id": item_id, "type": "pr", "raw": raw, "artifact": str(md_path.relative_to(base))}


def capture_url(base: Path, raw: str, workspace_root: Path) -> dict[str, Any]:
    item_id = artifact_id("url", raw)
    path = base / "stash" / f"{item_id}.md"
    path.write_text(f"# URL\n\nSource: {raw}\n\nNo content fetched in v0.\n")
    return {"id": item_id, "type": "url", "raw": raw, "artifact": str(path.relative_to(base))}


def capture_input(
    base: Path,
    input_type: str,
    raw: str,
    workspace_root: Path,
    *,
    add_to_manifest: bool = True,
) -> dict[str, Any]:
    capture = {
        "message": capture_message,
        "file": capture_file,
        "dir": capture_dir,
        "commit": capture_commit,
        "range": capture_range,
        "pr": capture_pr,
        "url": capture_url,
    }[input_type]
    item = capture(base, raw, workspace_root)
    item.update({"captured_at": now_iso(), "workspace_head": git_head(workspace_root), "sync_status": "pending"})
    if add_to_manifest:
        append_manifest(base, item)
    return item


def capture_bundle(base: Path, inputs: list[tuple[str, str]], workspace_root: Path) -> dict[str, Any]:
    items = [capture_input(base, input_type, raw, workspace_root, add_to_manifest=False) for input_type, raw in inputs]
    item_id = artifact_id("bundle", f"{len(items)}-items")
    path = base / "stash" / f"{item_id}.md"
    lines = ["# Bundle", ""]
    for index, item in enumerate(items, 1):
        lines.extend(
            [
                f"## {index}. {item['type']}",
                "",
                f"- Raw: {item['raw']}",
                f"- Artifact: {item['artifact']}",
            ]
        )
        if item.get("source"):
            lines.append(f"- Source: {item['source']}")
        lines.append("")
    path.write_text("\n".join(lines))
    bundle = {
        "id": item_id,
        "type": "bundle",
        "raw": f"{len(items)} typed inputs",
        "items": items,
        "artifact": str(path.relative_to(base)),
        "captured_at": now_iso(),
        "workspace_head": git_head(workspace_root),
        "sync_status": "pending",
    }
    append_manifest(base, bundle)
    return bundle


def parse_ledger_patch(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = match.group(1) if match else text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError("LedgerPatch JSON missing or invalid") from exc
    return data


def run_ledger_agent(base: Path, state: dict[str, Any], item: dict[str, Any], stale_warning: str) -> tuple[str, dict[str, Any], str]:
    bundle_lines = ""
    if item.get("type") == "bundle":
        bundle_lines = "\nBundle items:\n" + "\n".join(
            f"- {child['type']}: {child['raw']} ({child['artifact']})" for child in item.get("items", [])
        )
    prompt = f"""# Ledger Sync Input

Read ./AGENTS.md first.
Return only a LedgerPatch JSON block in a fenced json code block.
Do not edit files directly.

Ledger: {state['name']}
Managed workspace: {state['workspace_root']}
Stale warning: {stale_warning or 'none'}

Input type: {item['type']}
Raw input: {item['raw']}
Artifact: {item['artifact']}
{bundle_lines}

Required LedgerPatch keys:
- decision
- summary
- ledger_updates
- checkpoint_updates
- references_add
- inbox_add

Allowed ledger_updates keys:
- goal
- current
- status
- quality
- next_required_input
- open_questions
- accepted_facts
- decisions
- evidence

Do not invent *_add fields. The CLI rejects unknown fields.

decision must be exactly one of:
- accepted
- parked
- rejected
- read_only

If you set ledger_updates.status, it must be exactly one of:
- open
- in_progress
- blocked
- parked
- done
- dropped

If you set ledger_updates.quality or checkpoint quality, it must be exactly one of:
- draft
- usable
- strict
- blocked

checkpoint_updates is only for state transitions.
If no checkpoint state transition is needed, use an empty checkpoint_updates list.
"""
    if state.get("thread_id"):
        cmd = [
            "codex",
            "exec",
            "resume",
            state["thread_id"],
            prompt,
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
    else:
        cmd = ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", prompt]
    result = run(cmd, cwd=base)
    thread_id = state.get("thread_id") or ""
    messages: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id", thread_id)
        if event.get("type") == "agent_message":
            text = event.get("item", {}).get("text", "")
            if text:
                messages.append(text)
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    messages.append(text)
    if not thread_id:
        raise LedgerError("Codex did not return a thread_id")
    if not messages:
        raise LedgerError("Codex did not return an agent_message")
    answer = messages[-1]
    return thread_id, parse_ledger_patch(answer), answer


def validate_patch(patch: dict[str, Any], model: dict[str, Any]) -> None:
    allowed_patch_keys = {"decision", "summary", "ledger_updates", "checkpoint_updates", "references_add", "inbox_add"}
    for key in patch:
        if key not in allowed_patch_keys:
            raise LedgerError(f"LedgerPatch unknown field: {key}")
    if patch.get("decision") not in {"accepted", "parked", "rejected", "read_only"}:
        raise LedgerError(f"LedgerPatch decision is invalid: {patch.get('decision')!r}")
    for key in ["checkpoint_updates", "references_add", "inbox_add"]:
        if key in patch and not isinstance(patch[key], list):
            raise LedgerError(f"LedgerPatch {key} must be a list")
    updates = patch.get("ledger_updates", {})
    allowed_update_keys = {
        "goal",
        "current",
        "status",
        "quality",
        "next_required_input",
        "open_questions",
        "accepted_facts",
        "decisions",
        "evidence",
    }
    for key in updates:
        if key not in allowed_update_keys:
            raise LedgerError(f"LedgerPatch unknown ledger_updates field: {key}")
    for key in ["next_required_input", "open_questions", "accepted_facts", "decisions", "evidence"]:
        if key in updates and not isinstance(updates[key], list):
            raise LedgerError(f"LedgerPatch ledger_updates.{key} must be a list")
    if "status" in updates and updates["status"] not in LEDGER_STATUSES:
        raise LedgerError(f"LedgerPatch status is invalid: {updates['status']!r}")
    if "quality" in updates and updates["quality"] not in QUALITIES:
        raise LedgerError(f"LedgerPatch quality is invalid: {updates['quality']!r}")
    checkpoints = {item["id"]: item for item in model.get("checkpoints", [])}
    for update in patch.get("checkpoint_updates", []):
        to_state = update.get("to")
        if to_state is None:
            continue
        if to_state not in CHECKPOINT_STATES:
            raise LedgerError(f"Invalid checkpoint state: {to_state}")
        if "quality" in update and update["quality"] not in QUALITIES:
            raise LedgerError(f"Invalid checkpoint quality: {update['quality']!r}")
        checkpoint = checkpoints.get(update["id"])
        expected_from = update.get("from")
        if checkpoint and expected_from is not None and checkpoint.get("state") != expected_from:
            raise LedgerError(f"Checkpoint {update['id']} state mismatch")


def apply_patch_to_model(model: dict[str, Any], patch: dict[str, Any], source: str) -> dict[str, Any]:
    updates = patch.get("ledger_updates", {})
    key_map = {
        "goal": "goal",
        "current": "current",
        "status": "status",
        "quality": "quality",
        "next_required_input": "next_required_input",
        "open_questions": "open_questions",
    }
    for patch_key, model_key in key_map.items():
        if patch_key in updates:
            model[model_key] = updates[patch_key]
    for list_key in ["accepted_facts", "decisions", "evidence"]:
        if list_key in updates:
            model.setdefault(list_key, [])
            model[list_key].extend(updates[list_key])
    checkpoints = {item["id"]: item for item in model.get("checkpoints", [])}
    for update in patch.get("checkpoint_updates", []):
        if update.get("to") is None:
            continue
        checkpoint = checkpoints.get(update["id"])
        if checkpoint is None:
            order = 10 + (len(model["checkpoints"]) * 10)
            checkpoint = {
                "id": update["id"],
                "title": update.get("title", update["id"]),
                "state": "draft",
                "quality": "draft",
                "order": order,
                "goal": update.get("goal", ""),
                "acceptance": update.get("acceptance", []),
                "evidence": [],
                "missing": [],
                "history": [],
            }
            model["checkpoints"].append(checkpoint)
        old_state = checkpoint.get("state")
        checkpoint["state"] = update["to"]
        checkpoint["quality"] = update.get("quality", checkpoint.get("quality", "draft"))
        if "missing" in update:
            checkpoint["missing"] = update["missing"]
        elif update["to"] == "done":
            checkpoint["missing"] = []
        checkpoint["title"] = update.get("title", checkpoint.get("title", update["id"]))
        checkpoint.setdefault("history", []).append(
            {
                "at": now_iso(),
                "from": old_state,
                "to": update["to"],
                "reason": update.get("reason", ""),
                "source": update.get("source", source),
            }
        )
    for item in patch.get("inbox_add", []):
        model.setdefault("inbox", []).append(item.get("text", str(item)) if isinstance(item, dict) else str(item))
    return model


def stale_warning(state: dict[str, Any]) -> str:
    root = Path(state["workspace_root"])
    current = git_head(root)
    synced = state.get("synced_head")
    if current == synced:
        return ""
    return f"STALE: git HEAD changed {synced} -> {current}"


def maybe_update_synced_head(state: dict[str, Any], input_type: str, raw: str) -> None:
    current = git_head(Path(state["workspace_root"]))
    if input_type == "commit" and raw == "HEAD":
        state["synced_head"] = current
    if input_type == "range" and raw.endswith("..HEAD"):
        state["synced_head"] = current


def maybe_update_synced_head_for_item(state: dict[str, Any], item: dict[str, Any]) -> None:
    if item.get("type") == "bundle":
        for child in item.get("items", []):
            maybe_update_synced_head(state, child["type"], child["raw"])
        return
    maybe_update_synced_head(state, item["type"], item["raw"])


def update_references(base: Path, patch: dict[str, Any]) -> None:
    additions = patch.get("references_add", [])
    if not additions:
        return
    with (base / "references.md").open("a") as handle:
        for item in additions:
            handle.write(f"- {json.dumps(item, sort_keys=True)}\n")


def update_inbox(base: Path, patch: dict[str, Any]) -> None:
    additions = patch.get("inbox_add", [])
    if not additions:
        return
    with (base / "inbox.md").open("a") as handle:
        for item in additions:
            handle.write(f"- {json.dumps(item, sort_keys=True)}\n")


def process_run(home: Path, name: str, run_id: str) -> str:
    base = ledger_dir(home, name)
    state = load_json(base / "state.json", {})
    run_state = load_json(run_record_dir(base, run_id) / "state.json", {})
    item = load_json(run_record_dir(base, run_id) / "input.json", {})
    warning = stale_warning(state)
    thread_id, patch, answer = run_ledger_agent(base, state, item, warning)
    model = load_json(base / "ledger.json", {})
    validate_patch(patch, model)
    apply_patch_to_model(model, patch, item["artifact"])
    render_all(base, model)
    update_references(base, patch)
    update_inbox(base, patch)
    state["thread_id"] = thread_id
    state["status"] = model.get("status", state.get("status", "open"))
    state["quality"] = model.get("quality", state.get("quality", "draft"))
    state["last_input_at"] = now_iso()
    state["last_sync_at"] = state["last_input_at"]
    state["updated_at"] = state["last_input_at"]
    maybe_update_synced_head_for_item(state, item)
    write_json(base / "state.json", state)
    log_path = base / "logs" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{item['type']}.md"
    log_path.write_text(f"# Ledger Sync\n\nArtifact: {item['artifact']}\n\n## Reply\n\n{answer}\n")
    (run_record_dir(base, run_id) / "reply.md").write_text(answer)
    update_run_state(
        base,
        run_id,
        status="done",
        finished_at=now_iso(),
        summary=patch.get("summary", ""),
        log=str(log_path.relative_to(base)),
    )
    clear_current_run(base, run_id)
    mark_last_run(base, run_id)
    commit_if_dirty(home, f"ledger: sync {name} {run_state.get('input_type', item['type'])}")
    prefix = f"{warning}\n\n" if warning else ""
    return prefix + answer + "\n"


def run_worker(home: Path, name: str, run_id: str) -> None:
    home_git_init(home)
    base = ledger_dir(home, name)
    try:
        process_run(home, name, run_id)
    except Exception as exc:
        update_run_state(base, run_id, status="failed", finished_at=now_iso(), error=str(exc))
        clear_current_run(base, run_id)
        mark_last_run(base, run_id)
        commit_if_dirty(home, f"ledger: failed {name} {run_id}")
        raise


def start_worker_process(home: Path, name: str, run_id: str) -> int:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--home",
        str(home),
        "__worker",
        name,
        run_id,
    ]
    stdout_path = ledger_dir(home, name) / "runs" / run_id / "worker.stdout"
    stderr_path = ledger_dir(home, name) / "runs" / run_id / "worker.stderr"
    stdout = stdout_path.open("w")
    stderr = stderr_path.open("w")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=ledger_dir(home, name),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    finally:
        stdout.close()
        stderr.close()
    update_run_state(ledger_dir(home, name), run_id, pid=process.pid)
    return process.pid


def wait_for_run(base: Path, run_id: str, *, home: Path) -> str:
    def interrupt_wait(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    state_path = run_record_dir(base, run_id) / "state.json"
    try:
        signal.signal(signal.SIGINT, interrupt_wait)
        signal.signal(signal.SIGTERM, interrupt_wait)
        while True:
            state = load_json(state_path, {})
            if state.get("status") == "done":
                reply = run_record_dir(base, run_id) / "reply.md"
                close_terminal_run_if_owner_dead(home, base, run_id, state)
                return reply.read_text() + "\n"
            if state.get("status") == "failed":
                close_terminal_run_if_owner_dead(home, base, run_id, state)
                raise LedgerError(f"Ledger sync failed: {state.get('error', 'unknown error')}")
            if state.get("status") == "running":
                fail_dead_running_worker(home, base, run_id, state)
                state = load_json(state_path, {})
                if state.get("status") == "failed":
                    raise LedgerError(f"Ledger sync failed: {state.get('error', 'unknown error')}")
            time.sleep(0.5)
    except KeyboardInterrupt as exc:
        raise LedgerError(interrupted_wait_message(home, run_id)) from exc
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def cmd_typed_input(args: argparse.Namespace, *, cwd: Path, inputs: list[tuple[str, str]]) -> str:
    home_git_init(args.home)
    name, base = current_ledger(args, cwd=cwd)
    assert_not_busy(base)
    ensure_clean_home(args.home)
    state = load_json(base / "state.json", {})
    workspace_root = Path(state["workspace_root"])
    item = capture_input(base, inputs[0][0], inputs[0][1], workspace_root) if len(inputs) == 1 else capture_bundle(base, inputs, workspace_root)
    run_id = artifact_id("run", f"{item['type']}-{item['id']}")
    create_run_record(base, run_id, item)
    commit_if_dirty(args.home, f"ledger: start {name} {item['type']}")
    if os.environ.get("LEDGER_INLINE_WORKER") == "1":
        run_worker(args.home, name, run_id)
    else:
        try:
            start_worker_process(args.home, name, run_id)
        except Exception as exc:
            fail_run_before_worker(args.home, base, run_id, str(exc))
            raise
    return wait_for_run(base, run_id, home=args.home)


def summarize_checkpoints(model: dict[str, Any]) -> tuple[list[str], str]:
    lines = []
    counts: dict[str, int] = {}
    for checkpoint in sorted(model.get("checkpoints", []), key=lambda item: item.get("order", 1000)):
        state = checkpoint.get("state", "draft")
        counts[state] = counts.get(state, 0) + 1
        missing = checkpoint.get("missing") or []
        suffix = f" missing: {', '.join(missing)}" if missing else ""
        lines.append(f"- {state:<11} {checkpoint['id']}{suffix}")
    summary = " / ".join(f"{count} {state}" for state, count in sorted(counts.items()))
    return lines, summary or "0 checkpoints"


def cmd_show(args: argparse.Namespace, *, cwd: Path) -> str:
    name = args.name
    if name is None:
        name, base = current_ledger(args, cwd=cwd)
    else:
        base = ledger_dir(args.home, name)
        if not base.exists():
            raise LedgerError(f"Ledger not found: {name}")
    state = load_json(base / "state.json", {})
    model = load_json(base / "ledger.json", {})
    if args.full:
        return (base / "ledger.md").read_text()
    warning = stale_warning(state)
    current_run = load_current_run(base)
    checkpoint_lines, _ = summarize_checkpoints(model)
    output = [
        f"Ledger: {name}",
        f"Workspace: {state.get('workspace_root')}",
        f"Git: {warning if warning else 'fresh'}",
        f"Run: busy {current_run.get('run_id')}" if current_run else "Run: idle",
        f"Status: {model.get('status', 'open')}",
        f"Quality: {model.get('quality', 'draft')}",
        "",
        "Goal:",
        model.get("goal", ""),
        "",
        "Current:",
        model.get("current", ""),
        "",
        "Checkpoints:",
        *checkpoint_lines,
        "",
        "Next Required Input:",
    ]
    next_input = model.get("next_required_input") or []
    output.extend((f"- {item}" for item in next_input) if next_input else ["- None"])
    output.extend(["", "Open Questions:"])
    questions = model.get("open_questions") or []
    output.extend((f"- {item}" for item in questions) if questions else ["- None"])
    return "\n".join(output) + "\n"


def cmd_wait(args: argparse.Namespace, *, cwd: Path) -> str:
    name = args.name
    if name is None:
        name, base = current_ledger(args, cwd=cwd)
    else:
        base = ledger_dir(args.home, name)
        if not base.exists():
            raise LedgerError(f"Ledger not found: {name}")
    current = load_current_run(base)
    if current is None:
        current = load_last_run(base)
        if current is None:
            raise LedgerError(f"No running or finished ledger sync for: {name}")
    run_id = current.get("run_id")
    if not run_id:
        raise LedgerError(f"Malformed run record for: {name}")
    return wait_for_run(base, run_id, home=args.home)


def cmd_ls(args: argparse.Namespace, *, cwd: Path) -> str:
    home = args.home
    workspaces = load_json(workspaces_path(home), {})
    active = None
    try:
        active = find_bound_ledger(home, cwd)
    except LedgerError:
        pass
    rows = ["NAME\tACTIVE\tGIT\tRUN\tSTATUS\tQUALITY\tCHECKPOINTS\tWORKSPACE"]
    for path in sorted((home / "ledgers").glob("*")) if (home / "ledgers").exists() else []:
        if not path.is_dir():
            continue
        state = load_json(path / "state.json", {})
        model = load_json(path / "ledger.json", {})
        _, checkpoint_summary = summarize_checkpoints(model)
        run_state = "busy" if load_current_run(path) else "idle"
        git_state = "fresh"
        try:
            if stale_warning(state):
                git_state = "stale"
        except Exception:
            git_state = "broken"
        marker = "*" if path.name == active else ""
        rows.append(
            f"{path.name}\t{marker}\t{git_state}\t{run_state}\t{model.get('status', 'open')}\t{model.get('quality', 'draft')}\t{checkpoint_summary}\t{state.get('workspace_root', '')}"
        )
    return "\n".join(rows) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ledger",
        description=(
            "Workspace-bound long-horizon task ledger. Typed inputs start a "
            "detached ledger-agent sync while the foreground waits for convenience."
        ),
    )
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME, help="ledger storage directory")
    subparsers = parser.add_subparsers(dest="command", metavar="{init,show,wait,ls}")
    init = subparsers.add_parser("init", help="create and bind a ledger to this git workspace")
    init.add_argument("name")
    worker = subparsers.add_parser("__worker")
    worker.add_argument("name")
    worker.add_argument("run_id")
    subparsers._choices_actions = [action for action in subparsers._choices_actions if action.dest != "__worker"]
    show = subparsers.add_parser("show", help="show a compact ledger summary")
    show.add_argument("name", nargs="?")
    show.add_argument("--full", action="store_true", help="print the full rendered ledger")
    wait = subparsers.add_parser("wait", help="continue waiting for the current background sync")
    wait.add_argument("name", nargs="?")
    subparsers.add_parser("ls", help="list known ledgers")
    for flag, dest, help_text in [
        ("-m", "message", "sync a typed message"),
        ("-f", "file", "sync a file reference"),
        ("-d", "dir", "sync a directory reference"),
        ("-c", "commit", "sync a commit reference"),
        ("-r", "range", "sync a commit range reference"),
        ("-p", "pr", "sync a pull request number or URL"),
        ("-u", "url", "sync a URL reference"),
    ]:
        parser.add_argument(flag, dest=dest, action="append", help=help_text)
    return parser.parse_args(argv)


def selected_inputs(args: argparse.Namespace) -> list[tuple[str, str]]:
    values = [
        ("message", args.message),
        ("file", args.file),
        ("dir", args.dir),
        ("commit", args.commit),
        ("range", args.range),
        ("pr", args.pr),
        ("url", args.url),
    ]
    present: list[tuple[str, str]] = []
    for kind, raw_values in values:
        if raw_values is None:
            continue
        present.extend((kind, value) for value in raw_values)
    return present


def main(argv: list[str] | None = None, *, cwd: Path | None = None) -> str:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])
    args.home = args.home.expanduser().resolve()
    target_cwd = (cwd or Path.cwd()).resolve()
    try:
        if args.command == "init":
            output = cmd_init(args, cwd=target_cwd)
        elif args.command == "__worker":
            run_worker(args.home, args.name, args.run_id)
            output = ""
        elif args.command == "show":
            output = cmd_show(args, cwd=target_cwd)
        elif args.command == "wait":
            output = cmd_wait(args, cwd=target_cwd)
        elif args.command == "ls":
            output = cmd_ls(args, cwd=target_cwd)
        else:
            inputs = selected_inputs(args)
            if not inputs:
                raise LedgerError("Expected init, show, ls, or at least one typed input flag")
            output = cmd_typed_input(args, cwd=target_cwd, inputs=inputs)
    except LedgerError as exc:
        if argv is None:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        raise
    if argv is None:
        print(output, end="")
    return output


if __name__ == "__main__":
    main()
