# Orchestrate It Out

`orchestrate-it-out` is a public agent skill and CLI for one opinionated build
topology: **GPT-5.6 Sol plans first, then exactly five Claude Fable 5 workers
implement the sealed plan.** There is no independent post-build semantic
reviewer.

```text
Sol (gpt-5.6-sol) -> sealed five-part plan -> 5 x claude-fable-5 -> mechanical gate
```

The outer controller owns transport and validation. This matters: Sol cannot
accidentally broadcast an assignment, create a sixth worker, or dispatch before
its plan passes the structural contract.

## Install

Clone the repository and run:

```bash
./install.sh
```

This links the skill into both `~/.codex/skills` and `~/.claude/skills`, then
links the launcher into `~/.local/bin` (or `~/bin`). It never overwrites a real
file or directory. To migrate existing symlinks only:

```bash
./install.sh --replace-links
```

Inspect prerequisites and install actions first:

```bash
./doctor.sh
./install.sh --dry-run
```

## Run

Use a clean Git working tree:

```bash
orchestrate-it-out \
  "build the complete feature, including tests and documentation" \
  --project /absolute/path/to/repository \
  --trusted-task
```

A non-mutating preview makes no model calls and creates no NTM session:

```bash
orchestrate-it-out "build the feature" --project "$PWD" --dry-run
```

Important controls:

```text
--max-plan-attempts 1..3      default 2
--plan-timeout 60..3600       default 1800 seconds
--worker-timeout 60..7200     default 1800 seconds per wave
--keep-session                retain the exact NTM worker session
--uncontained-orchestrator    explicit fallback if read-only Codex fails
```

The controller blocks dirty repositories. It retains a private runtime directory
with the immutable task, baseline, every Sol attempt, sealed validation, exact
worker briefs, atomic receipts, and final evidence.

## Plan contract

Sol must produce exactly five detailed workstreams named `fable-1` through
`fable-5`. Each includes required context, fixed decisions, ordered steps,
repository-relative write scope, dependencies, invariants, non-goals,
deliverables, acceptance criteria, and verification argv.

Unordered write scopes must be disjoint. Dependencies create deterministic
execution waves. Commands are executed directly, never through a shell, and the
validator refuses common network, package-install, publish, deploy, Git-mutation,
filesystem-mutation, package-executor, and inline-code forms.

## Why there is no reviewer

This companion is intentionally different from
[`build-it-out`](https://github.com/SubliminalCoding/build-it-out). It uses Sol's
planning strength and Fable 5's implementation strength without a separate
critic/reviewer role. Completion still requires five receipts and mechanically
enforced verification. A successful handoff says:

> Mechanically green; independent semantic review intentionally omitted.

Use `build-it-out` when the task warrants independent semantic judgment or a
bounded review/repair loop.

## Trust model

`--trusted-task` is mandatory for live runs. Fable panes use Claude's unattended
permission bypass so they can edit the repository. This is not OS containment.
The default Sol process uses Codex's read-only sandbox; the explicit
`--uncontained-orchestrator` option weakens that boundary. Git/scope checks can
detect repository-visible violations but cannot prevent external-service calls
or writes elsewhere on the machine.

Keep credentials out of the repository, use trusted tasks and project
instructions, inspect retained evidence before external actions, and never treat
mechanical green as proof of product correctness.

## Development

```bash
python3 scripts/validate-package.py
bash -n install.sh doctor.sh skills/orchestrate-it-out/bin/orchestrate-it-out \
  skills/orchestrate-it-out/scripts/install skills/orchestrate-it-out/scripts/doctor
python3 -m py_compile skills/orchestrate-it-out/scripts/protocol.py
node --test skills/orchestrate-it-out/tests/orchestrate-it-out.test.mjs
```

MIT licensed. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.
