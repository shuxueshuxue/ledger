# ledger

Strict long-horizon task ledger for agentic engineering work.

`ledger` stores typed inputs, lets a ledger agent review them, and maintains
explicit checkpoints for a workspace-bound task ledger.

## Install

From a clone:

```bash
pip install -e .
```

Or run without installing:

```bash
python -m ledger_agent.cli --help
```

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
    references.md
    notes/
    checkpoints/
    stash/
    runs/
    logs/
```

The ledger home is a git repo. A write operation refuses to start if the ledger
repo is already dirty. Successful CLI changes auto-commit only when files
changed.

## Ledger Agent Instructions

Ledger agent behavior lives in source at:

```text
ledger_agent/agent_instructions/AGENTS.md
```

`ledger init <name>` renders that template into the runtime ledger
`AGENTS.md`, injecting the managed workspace's local `AGENTS.md` path. The
global Codex `AGENTS.md` is not copied or symlinked; it is already active in
the runtime.

## Ledger Layers

`stash/` is append-only evidence. Every typed input is captured there before the
ledger agent judges it.

`notes/` is the flexible knowledge base. Runtime know-how, design notes,
triage, and operating facts belong there. `notes/triage.md` replaces the old
top-level `inbox.md` concept for unresolved items.

`checkpoints/` is the strict state-machine layer. Checkpoint status, quality,
history, acceptance, and evidence are structured and must be updated through
the ledger agent protocol.

`references.md` is a source index for repos, paths, URLs, commits, and PRs.

Ledger Agent is a judge, not a worker. It should question, accept, reject,
park, record, and steer; it must not implement managed-workspace code itself.
Worker reports stay flexible free text. The strict schema is Ledger Agent's
output, not the worker's input.

Ledger uses soft adhesion: replies should give a short next Ledger touchpoint
through `next_required_input` when useful, without controlling the worker's full
implementation rhythm.

Before implementation checkpoints are created, Ledger and the worker should use
free-form messages for design debate. Ledger can be wrong; the worker may
challenge it with evidence. The checkpoint should be created only after the
discussion covers testing strategy, architecture complexity, abstraction cost,
decoupling value, expected code size, and prerequisite legacy or architecture
cleanup.

Ledger's engineering taste is intentionally biased toward core mechanisms,
backend/lower-layer correctness, stability, robustness, decoupling, and clean
code. The anti-pattern is a supervisor that obsesses over tiny edge cases,
safety theater, micro-performance trivia, or if/else-heavy patch PRs while
missing the underlying mechanism quality.

## Commands

Initialize a ledger for the current git workspace:

```bash
ledger init <name>
```

Bind another git worktree or clone to an existing ledger:

```bash
ledger attach <name>
```

`attach` does not create a second ledger. It records the current git repo root
in Ledger's workspace map and keeps the ledger's original managed workspace as
the primary workspace. Typed inputs from the attached worktree are captured
against the invoking worktree so commit/file references are not resolved through
the wrong checkout.

Concurrency is scoped by ledger identity:

- One ledger may have multiple attached folders or git worktrees.
- One ledger can run only one Ledger Agent sync at a time; all attached folders
  share the same busy lock.
- Different ledgers are isolated project contexts. Each ledger has its own
  worker state, so separate ledgers may run their Ledger Agents in parallel.

After init, ask the ledger to explain itself:

```bash
ledger -m "gang help"
```

This is normal free-form input, not a separate command grammar. Ledger should
answer by explaining what it manages, where notes/references/checkpoints live,
how to use typed inputs, and when the worker should return for design debate or
closure review.

Sync one or more typed inputs:

```bash
ledger -m "message"
ledger -f path/to/file
ledger -d path/to/dir
ledger -c <commit>
ledger -r <commit-range>
ledger -p <pr-number-or-url>
ledger -u <url>
```

Multiple typed flags in one invocation are captured as a single bundle and
produce one ledger-agent sync:

```bash
ledger -m "runtime fact" -f path/to/checkpoint.md -c HEAD
```

Use Ledger as a discussion partner before implementation checkpoints, not only
as an after-the-fact reporting sink. If a task needs design debate, test
strategy, architecture boundary, or checkpoint scope, send that discussion to
Ledger first. One-way completion reports are still accepted as free-form input,
but the Ledger Agent should correct the interaction pattern when the missing
pre-work matters.

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

Several minutes is normal for a Ledger Agent run. Do not start another typed
input to "unstick" it. While the ledger is busy, new typed input is blocked
before stash capture or worker start, and the error points back to `ledger show`
and `ledger wait`.

If the detached worker dies before writing a terminal state, `ledger wait`
marks that run as failed, clears the busy marker, and commits the failure state.

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

Each checkpoint carries `verification_required`, a list of evidence levels
needed before closure. The important levels are:

- `source/test-layer`: source review, unit tests, or narrow mechanism tests.
- `backend-api-yatu`: real backend API proof using real runtime prerequisites.
- `playwright-cli-yatu`: real frontend proof through Playwright CLI.
- `play-as-test`: highest tier; realistic trial-use that plays the product like
  a user instead of following a rigid script.

Code implementation checkpoints also need closure review for real LLM API usage
when model behavior is in scope, no fallback growth, no patch-stack development,
architecture blockers promoted to prerequisite checkpoints, redundant test
scaffolding removed or consolidated, and legacy/database mismatches handled as
their own prerequisite checkpoints instead of being accommodated silently.
