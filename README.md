# ledger

Strict long-horizon task ledger for agentic engineering work.

`ledger` stores typed inputs, lets a ledger agent review them, and maintains
explicit checkpoints for a workspace-bound task ledger.

## Model

`ledger` is a local context manager for long tasks. Each git workspace can bind
to one named ledger. Each ledger owns one continuous Codex thread, stored in
`state.json` as `thread_id`; later syncs resume that thread instead of creating
a fresh agent.

The command line stays synchronous for convenience, but execution is async for
stability:

- typed inputs create a durable run record under `runs/<run_id>/`
- a detached worker runs the ledger-agent sync
- the foreground process waits and prints the reply when the worker finishes
- if the foreground process is interrupted, the worker keeps running
- while a run is active, new typed inputs are rejected as `busy`

## Storage

Default storage is `~/.ledger`.

```text
~/.ledger/
  .git/
  workspaces.json
  ledgers/<name>/
    AGENTS.md
    state.json
    ledger.json
    ledger.md
    checkpoints/
    stash/
    runs/
```

The ledger home is a git repo. A write operation refuses to start if the ledger
repo is already dirty. Successful CLI changes auto-commit only when files
changed.

## Commands

Initialize a ledger for the current git workspace:

```bash
ledger init <name>
```

Sync exactly one typed input:

```bash
ledger -m "message"
ledger -f path/to/file
ledger -d path/to/dir
ledger -c <commit>
ledger -r <commit-range>
ledger -p <pr-number-or-url>
ledger -u <url>
```

Read status without starting the agent:

```bash
ledger show
ledger show <name>
ledger show --full
ledger ls
```

Continue waiting for the current background run:

```bash
ledger wait
ledger wait <name>
```

If a foreground wait is interrupted, the worker remains active and the CLI
prints the exact `ledger show` and `ledger wait` commands to use next. If the
worker finishes before you run `ledger wait`, `ledger wait` returns the most
recent finished run instead of losing the reply.

## Checkpoints

Checkpoints are explicit state-machine records owned by the ledger agent. Normal
agents may read the checkpoint files, but should not edit them directly.

Allowed checkpoint states:

- `draft`
- `ready`
- `in_progress`
- `blocked`
- `done`
- `dropped`

Allowed quality labels:

- `draft`
- `usable`
- `strict`
- `blocked`
