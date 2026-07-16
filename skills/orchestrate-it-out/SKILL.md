---
name: orchestrate-it-out
description: Run a high-level GPT-5.6 Sol orchestration workflow for substantial coding work. Sol inspects a clean Git repository and produces a comprehensive sealed plan with exactly five bounded workstreams; the controller then spawns exactly five Claude Fable 5 workers, dispatches each brief to an exact pane, waits by dependency wave, requires receipts, and runs mechanically enforced scope and verification gates. Use when the user asks for Sol to orchestrate five Fables, wants a thorough plan followed by a five-agent build, or explicitly says "orchestrate it out." Do not use for small edits, non-coding requests, dirty repositories, or requests that require independent semantic review.
---

# Orchestrate It Out

Use this topology for substantial implementation work:

```text
GPT-5.6 Sol (read-only planning)
        |
        v
sealed comprehensive plan with exactly five bounded workstreams
        |
        v
Fable 1   Fable 2   Fable 3   Fable 4   Fable 5
        |
        v
receipts + mechanically enforced scope/tests/final gate
        |
        v
DONE or BLOCKED — no semantic reviewer
```

## Run it

From the target repository:

```bash
orchestrate-it-out "implement the exact substantial task" --project "$PWD" --trusted-task
```

Inspect the full Sol prompt, pinned topology, and command sequence without model
calls or NTM mutation:

```bash
orchestrate-it-out "implement the exact substantial task" --project "$PWD" --dry-run
```

Stay with the command until it reports `DONE` or `BLOCKED`. Do not declare the
task finished merely because the NTM panes became idle. Completion requires five
atomic `COMPLETE` receipts and a passing final evidence artifact.

Trigger exclusions take precedence over the magic phrase: do not run on a dirty
repository, for a task too small to support five genuine bounded workstreams, or
when the user asks for independent review. A security-sensitive task may still
use this topology when the user explicitly chooses it after the absence of
independent review is clear; do not infer that choice. An explicit request to
execute `orchestrate-it-out`, “orchestrate it out,” or the exact Sol/five-Fable
workflow authorizes adding `--trusted-task`. A request to inspect or discuss the
workflow does not.

## What the controller enforces

- Sol is pinned to `gpt-5.6-sol` and plans before any Fable session exists.
- Sol receives a JSON output schema and runs in Codex's read-only sandbox by
  default. If that sandbox is unavailable, stop and explain the issue; use
  `--uncontained-orchestrator` only when the operator explicitly accepts it.
- The plan must contain IDs `fable-1` through `fable-5`, specific context,
  decisions, steps, exact write scopes, dependencies, invariants, non-goals,
  deliverables, acceptance criteria, and direct-argv verification commands.
- A structural validator rejects cycles, unsafe paths/commands, glob scopes, and
  unordered scope overlap. It seals the task, baseline, plan, and validation.
- Only after sealing does the controller create exactly five
  `claude-fable-5` panes. It cross-checks NTM's manifest against tmux and sends
  each mechanically rendered brief to one exact pane—never a provider broadcast.
- Dependency waves may run concurrently only where Sol declared disjoint work.
  Workers may not spawn agents, nest this workflow, commit, push, install,
  deploy, publish, or contact external services.
- The final gate verifies unchanged HEAD, protected ignored-file fingerprints,
  union write scope, `git diff --check`, every workstream command, and every
  final integration command. Verification runs without a shell, with bounded
  output, closed stdin, scrubbed sensitive environment variables, timeouts, and
  repository-mutation detection.

## Deliberate omission

Do not add a critic, reviewer pane, worker cross-review, review schema, or
semantic repair round. This workflow intentionally trusts Sol's plan and the
five Fable implementations, then requires mechanical evidence. “No reviewer”
means no independent post-build semantic review; Sol and the workers still use
semantic reasoning while planning and implementing. The handoff must state:
`Mechanically green; independent semantic review intentionally omitted.`

Use `$build-it-out` instead when independent semantic review is part of the
request, or when risk warrants review and this no-review topology was not
explicitly chosen.

## Safety boundary

Live execution requires `--trusted-task` and a clean Git repository. Fable
workers run unattended with the caller's Claude permissions. The Sol sandbox,
prompts, seals, and Git gates reduce mistakes and stale-state errors; they are
not a hostile same-account security boundary. Repository content, task context,
and bounded evidence are sent through the configured Codex and Claude clients,
subject to those providers' accounts, retention, and cost policies.

If either exact pinned model is unavailable or the default Sol read-only sandbox
cannot start, stop as `BLOCKED`; never silently substitute a model. The operator
may explicitly choose `--uncontained-orchestrator` for the documented sandbox
fallback.

## Verify the package

```bash
node --test tests/orchestrate-it-out.test.mjs
python3 /path/to/skill-creator/scripts/quick_validate.py .
```
