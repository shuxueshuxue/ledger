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
- Inbox: ./inbox.md

The global AGENTS.md is already active in the runtime. Do not copy or symlink it here.

## Patch Protocol

Do not edit files directly. Return a LedgerPatch JSON block. The CLI validates and applies it.

## Checkpoint Rules

Checkpoint states are: draft, ready, in_progress, blocked, done, dropped.
Only the ledger agent may decide checkpoint transitions.
Every transition needs from, to, reason, and source.
Do not mark done without source or evidence.

## Ledger Quality

No source means Inbox, not Accepted Facts.
No evidence means not done.
No scope or stopline means implementation checkpoints cannot become ready.
Prefer one concrete next required input over broad advice.

## Runtime Secrets

Runtime secrets are managed as redacted prerequisite facts and validation recipes.
Never write raw keys or tokens into ledger state, checkpoint files, references, or inbox.
Record source path, owner boundary, provider, base URL, and proof status instead.
