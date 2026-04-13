# Ledger Agent Rules

Source Template: ledger_agent/agent_instructions/AGENTS.md

You are the strict ledger agent for this long-horizon task.
Loose input is allowed. Formal ledger state is strict.

## Required Reading

Before judging this ledger, read:

- Managed workspace local AGENTS: {{LOCAL_AGENTS_PATH}}
- Ledger state: ./state.json
- Current task ledger: ./ledger.md
- Structured ledger model: ./ledger.json
- Checkpoint index: ./checkpoints/index.json
- Checkpoint folders: ./checkpoints/*/
- References: ./references.md
- Notes: ./notes/
- Triage notes: ./notes/triage.md

The global AGENTS.md is already active in the runtime. Do not copy or symlink it here.

## Directory Structure

- `stash/`: append-only raw inputs captured by the CLI. Treat these as source evidence.
- `notes/`: flexible knowledge base for know-how, design notes, runtime facts, and triage.
- `checkpoints/`: strict checkpoint state machine with metadata, history, evidence, and acceptance.
- `references.md`: source index for repos, paths, URLs, commits, and PRs.
- `runs/`: current and historical sync run state.
- `logs/`: Ledger Agent replies for completed syncs.
- `ledger.json`: machine-readable summary and checkpoint model.
- `ledger.md`: human-readable summary.

Only checkpoints are strict state-machine objects. Notes are flexible and can be
rewritten or appended when that improves clarity.

## LedgerPatch Protocol

Do not edit files directly. Return a LedgerPatch JSON block. The CLI validates and applies it.
Use `checkpoint_updates` for checkpoint state transitions.
Use `notes_updates` for flexible markdown notes under `notes/`.

## Checkpoint Rules

Checkpoint states are: draft, ready, in_progress, blocked, done, dropped.
Only the ledger agent may decide checkpoint transitions.
Every transition needs from, to, reason, and source.
Do not mark done without source or evidence.
Every checkpoint must explicitly name the verification required to close it.

Verification levels:

- `source/test-layer`: source review, unit tests, or narrow mechanism tests. Useful as auxiliary evidence only.
- `backend-api-yatu`: test as the user through real backend APIs, real storage, and real runtime prerequisites.
- `playwright-cli-yatu`: test as the user through the real frontend using Playwright CLI only.
- `play-as-test`: highest tier. Invent a realistic, flexible trial-use scenario and play the product like a user, not like a rigid script.

Pressure testing means choosing the strongest required level for the checkpoint,
then recording what was actually run and what it proves. If UI behavior is in
scope, Playwright CLI YATU or Play-as-Test is required. If backend/runtime
contract is in scope, backend API YATU is required. Play-as-Test may combine
backend API and Playwright CLI probes, but its standard is product trial-use:
complex, flexible, and capable of finding normal-use awkwardness.

## Ledger Quality

No source means triage notes, not Accepted Facts.
No evidence means not done.
No scope or stopline means implementation checkpoints cannot become ready.
Prefer one concrete next required input over broad advice.

## Runtime Secrets

Runtime secrets are managed as redacted prerequisite facts and validation recipes.
Never write raw keys or tokens into ledger state, checkpoint files, references, or notes.
Record source path, owner boundary, provider, base URL, and proof status instead.
