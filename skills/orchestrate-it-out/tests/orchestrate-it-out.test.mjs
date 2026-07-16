import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  chmodSync,
  cpSync,
  existsSync,
  lstatSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, test } from "node:test";

const SKILL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LAUNCHER = path.join(SKILL, "bin", "orchestrate-it-out");
const PROTOCOL = path.join(SKILL, "scripts", "protocol.py");
const INSTALLER = path.join(SKILL, "scripts", "install");
const roots = [];

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

function tempRoot(prefix = "orchestrate-it-out-test-") {
  const root = mkdtempSync(path.join(tmpdir(), prefix));
  roots.push(root);
  return root;
}

function run(file, args = [], options = {}) {
  return spawnSync(file, args, {
    cwd: options.cwd,
    encoding: "utf8",
    env: { ...process.env, LC_ALL: "C", ...options.env },
  });
}

function git(repo, ...args) {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8" }).trim();
}

function makeRepo() {
  const repo = tempRoot();
  git(repo, "init", "-q");
  git(repo, "config", "user.name", "Orchestration Test");
  git(repo, "config", "user.email", "test@example.com");
  writeFileSync(path.join(repo, "README.md"), "fixture\n");
  git(repo, "add", "README.md");
  git(repo, "commit", "-qm", "fixture");
  return repo;
}

function writeJson(root, name, value) {
  const target = path.join(root, name);
  writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
  return target;
}

function validPlan(task = "Build the fixture comprehensively") {
  const dependencies = [[], [], ["fable-1"], ["fable-2"], ["fable-3", "fable-4"]];
  const names = ["one", "two", "three", "four", "five"];
  return {
    version: 1,
    task,
    strategy:
      "Implement two independent foundations, extend each in a dependent workstream, then integrate their documented handoff in the fifth workstream.",
    successDefinition: ["All five scoped artifacts exist and the final Git diff is clean."],
    assumptions: ["The fixture has no dependency installation requirement."],
    globalInvariants: ["README.md and Git history remain unchanged."],
    nonGoals: ["No deployment, publishing, or unrelated refactoring."],
    workers: names.map((name, index) => ({
      id: `fable-${index + 1}`,
      title: `Implement fixture workstream ${name}`,
      mission: `Create the complete ${name} fixture artifact while preserving the sealed repository constraints.`,
      dependsOn: dependencies[index],
      requiredContext: ["README.md"],
      decisionsAlreadyMade: [`The ${name} artifact is a plain text fixture.`],
      steps: [`Inspect the required context for ${name}.`, `Create and verify the ${name} artifact.`],
      writeScope: [`src/${name}.txt`],
      invariants: ["Do not alter Git history or files outside scope."],
      nonGoals: ["Do not add dependencies or broaden the feature."],
      deliverables: [`src/${name}.txt`],
      acceptance: [`The ${name} artifact contains its intended fixture text.`],
      verification: [{ argv: ["git", "diff", "--check"], timeoutSeconds: 30 }],
      parallelSafe: index < 2,
    })),
    finalVerification: [{ argv: ["git", "diff", "--check"], timeoutSeconds: 30 }],
  };
}

function prepareSealed(repo, runtime, plan = validPlan()) {
  const task = path.join(runtime, "task.txt");
  const baseline = path.join(runtime, "baseline.json");
  const state = path.join(runtime, "state.json");
  const planPath = writeJson(runtime, "plan.json", plan);
  const validation = path.join(runtime, "validation.json");
  writeFileSync(task, `${plan.task}\n`);
  let result = run("python3", [PROTOCOL, "preflight", "--project", repo, "--output", baseline]);
  assert.equal(result.status, 0, result.stderr);
  result = run("python3", [
    PROTOCOL,
    "init-state",
    "--state",
    state,
    "--run-id",
    "fixture",
    "--task-file",
    task,
    "--baseline",
    baseline,
    "--orchestrator-model",
    "gpt-5.6-sol",
    "--worker-model",
    "claude-fable-5",
  ]);
  assert.equal(result.status, 0, result.stderr);
  result = run("python3", [
    PROTOCOL,
    "validate-plan",
    "--project",
    repo,
    "--task-file",
    task,
    "--plan",
    planPath,
    "--output",
    validation,
  ]);
  assert.equal(result.status, 0, result.stderr);
  result = run("python3", [
    PROTOCOL,
    "seal",
    "--state",
    state,
    "--plan",
    planPath,
    "--validation",
    validation,
  ]);
  assert.equal(result.status, 0, result.stderr);
  return { task, baseline, state, planPath, validation };
}

function registerFive(runtime, sealed) {
  let result = run("python3", [
    PROTOCOL,
    "claim-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
  ]);
  assert.equal(result.status, 0, result.stderr);
  const manifest = writeJson(runtime, "manifest.json", {
    session: "fixture--test",
    project_dir: JSON.parse(readFileSync(sealed.state, "utf8")).project,
    agents: Array.from({ length: 5 }, (_, index) => ({
      pane_id: `%${index + 1}`,
      pane_index: index,
      type: "cc",
      model: "claude-fable-5",
      command: "fixture",
    })),
  });
  const panes = path.join(runtime, "panes.txt");
  writeFileSync(panes, Array.from({ length: 5 }, (_, index) => `%${index + 1}:${index}`).join("\n") + "\n");
  const roster = path.join(runtime, "roster.json");
  result = run("python3", [
    PROTOCOL,
    "register-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--session",
    "fixture--test",
    "--worker-model",
    "claude-fable-5",
    "--manifest",
    manifest,
    "--tmux-panes",
    panes,
    "--output",
    roster,
  ]);
  assert.equal(result.status, 0, result.stderr);
  const briefs = path.join(runtime, "briefs");
  result = run("python3", [
    PROTOCOL,
    "render-briefs",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--output-dir",
    briefs,
  ]);
  assert.equal(result.status, 0, result.stderr);
  return { manifest, panes, roster, briefs };
}

test("launcher exposes pinned Sol/five-Fable topology in a non-mutating dry run", () => {
  const repo = makeRepo();
  const result = run(LAUNCHER, [
    "Build the fixture comprehensively",
    "--project",
    repo,
    "--run-id",
    "dry-test",
    "--dry-run",
  ]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /gpt-5\.6-sol/);
  assert.match(result.stdout, /workers: 5 x claude-fable-5/);
  assert.match(result.stdout, /--cc=5:claude-fable-5/);
  assert.match(result.stdout, /plan.*before any Fable session exists/is);
  assert.match(result.stdout, /semantic reviewer: intentionally absent/);
});

test("launcher requires explicit trust and a clean Git baseline", () => {
  const repo = makeRepo();
  let result = run(LAUNCHER, ["Build it", "--project", repo]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /--trusted-task/);

  writeFileSync(path.join(repo, "README.md"), "dirty\n");
  result = run(LAUNCHER, ["Build it", "--project", repo, "--dry-run"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /must start clean/);
});

test("valid plan seals exactly five workers into dependency waves", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const task = path.join(runtime, "task.txt");
  const plan = writeJson(runtime, "plan.json", validPlan());
  const validation = path.join(runtime, "validation.json");
  writeFileSync(task, "Build the fixture comprehensively\n");
  const result = run("python3", [
    PROTOCOL,
    "validate-plan",
    "--project",
    repo,
    "--task-file",
    task,
    "--plan",
    plan,
    "--output",
    validation,
  ]);
  assert.equal(result.status, 0, result.stderr);
  const value = JSON.parse(readFileSync(validation, "utf8"));
  assert.deepEqual(value.workerIds, ["fable-1", "fable-2", "fable-3", "fable-4", "fable-5"]);
  assert.deepEqual(value.waves, [["fable-1", "fable-2"], ["fable-3"], ["fable-4"], ["fable-5"]]);
});

test("plan validation rejects wrong count, cycles, overlap, globs, and unsafe commands", () => {
  const repo = makeRepo();
  const cases = [
    ["wrong count", (plan) => plan.workers.pop(), /exactly five/],
    [
      "cycle",
      (plan) => {
        plan.workers[0].dependsOn = ["fable-5"];
      },
      /cycle/,
    ],
    [
      "overlap",
      (plan) => {
        plan.workers[1].writeScope = ["src/one.txt"];
      },
      /overlapping write scopes/,
    ],
    [
      "glob",
      (plan) => {
        plan.workers[0].writeScope = ["src/*.txt"];
      },
      /glob syntax/,
    ],
    [
      "unsafe command",
      (plan) => {
        plan.workers[0].verification = [{ argv: ["curl", "https://example.com"], timeoutSeconds: 30 }];
      },
      /forbidden executable/,
    ],
  ];
  for (const [label, mutate, expected] of cases) {
    const runtime = tempRoot(`oio-${label.replaceAll(" ", "-")}-`);
    const value = validPlan();
    mutate(value);
    const plan = writeJson(runtime, "plan.json", value);
    const task = path.join(runtime, "task.txt");
    writeFileSync(task, `${value.task}\n`);
    const result = run("python3", [
      PROTOCOL,
      "validate-plan",
      "--project",
      repo,
      "--task-file",
      task,
      "--plan",
      plan,
      "--output",
      path.join(runtime, "validation.json"),
    ]);
    assert.notEqual(result.status, 0, label);
    assert.match(result.stderr, expected, label);
  }
});

test("sealed five-worker spawn claim is one-shot", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime);
  let result = run("python3", [
    PROTOCOL,
    "claim-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
  ]);
  assert.equal(result.status, 0, result.stderr);
  result = run("python3", [
    PROTOCOL,
    "claim-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
  ]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /already consumed/);
});

test("worker spawn claim fails if the repository drifts while Sol is planning", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime);
  writeFileSync(path.join(repo, "README.md"), "operator drift\n");
  const result = run("python3", [
    PROTOCOL,
    "claim-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
  ]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /drifted after preflight/);
});

test("robot-send evidence must prove one exact successful pane", () => {
  const runtime = tempRoot();
  const failed = writeJson(runtime, "failed.json", {
    success: false,
    session: "fixture--test",
    targets: [],
    successful: [],
    failed: ["2"],
  });
  let result = run("python3", [
    PROTOCOL,
    "validate-send",
    "--response",
    failed,
    "--session",
    "fixture--test",
    "--pane",
    "2",
  ]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /success=true/);

  const wrong = writeJson(runtime, "wrong.json", {
    success: true,
    send: {
      success: true,
      session: "fixture--test",
      blocked: false,
      targets: ["3"],
      successful: ["3"],
      failed: [],
    },
    ack: {
      success: true,
      session: "fixture--test",
      confirmations: [{ pane: "3", ack_type: "output_started" }],
      pending: [],
      failed: [],
      timed_out: false,
    },
  });
  result = run("python3", [
    PROTOCOL,
    "validate-send",
    "--response",
    wrong,
    "--session",
    "fixture--test",
    "--pane",
    "2",
  ]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /one exact successful pane/);
});

test("manifest registration requires exactly five pinned Fable panes cross-checked with tmux", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime);
  let result = run("python3", [
    PROTOCOL,
    "claim-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
  ]);
  assert.equal(result.status, 0, result.stderr);
  const manifestValue = {
    session: "fixture--test",
    project_dir: repo,
    agents: Array.from({ length: 5 }, (_, index) => ({
      pane_id: `%${index + 1}`,
      pane_index: index,
      type: "cc",
      model: index === 4 ? "wrong-model" : "claude-fable-5",
    })),
  };
  const manifest = writeJson(runtime, "bad-manifest.json", manifestValue);
  const panes = path.join(runtime, "panes.txt");
  writeFileSync(panes, "%1:0\n%2:1\n%3:2\n%4:3\n%5:4\n");
  result = run("python3", [
    PROTOCOL,
    "register-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--session",
    "fixture--test",
    "--worker-model",
    "claude-fable-5",
    "--manifest",
    manifest,
    "--tmux-panes",
    panes,
    "--output",
    path.join(runtime, "roster.json"),
  ]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /pinned Fable/);

  manifestValue.agents[4].model = "claude-fable-5";
  const correctManifest = writeJson(runtime, "correct-manifest.json", manifestValue);
  writeFileSync(panes, "%1:0\n%2:1\n%3:2\n%4:3\n%5:4\n%6:5\n");
  result = run("python3", [
    PROTOCOL,
    "register-workers",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--session",
    "fixture--test",
    "--worker-model",
    "claude-fable-5",
    "--manifest",
    correctManifest,
    "--tmux-panes",
    panes,
    "--output",
    path.join(runtime, "roster.json"),
  ]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /exact tmux pane set differ/);
});

test("rendered prompts are exact self-contained assignments with atomic receipts", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime);
  const registered = registerFive(runtime, sealed);
  const first = readFileSync(path.join(registered.briefs, "fable-1.md"), "utf8");
  assert.match(first, /Implement fixture workstream one/);
  assert.doesNotMatch(first, /Implement fixture workstream two/);
  assert.match(first, /record-worker.*--worker fable-1.*--status COMPLETE/);
  assert.match(first, /Do not spawn agents/);
  assert.doesNotMatch(first, /review the other worker/i);

  const wrongPane = run("python3", [
    PROTOCOL,
    "record-worker",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--worker",
    "fable-1",
    "--status",
    "COMPLETE",
    "--summary",
    "wrong pane",
  ], { env: { TMUX_PANE: "%2" } });
  assert.notEqual(wrongPane.status, 0);
  assert.match(wrongPane.stderr, /must originate from pane %1/);
});

test("final gate requires five receipts and independently reruns mechanical checks", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime);
  registerFive(runtime, sealed);
  mkdirSync(path.join(repo, "src"));
  for (const [index, name] of ["one", "two", "three", "four", "five"].entries()) {
    writeFileSync(path.join(repo, "src", `${name}.txt`), `${name}\n`);
    const result = run("python3", [
      PROTOCOL,
      "record-worker",
      "--state",
      sealed.state,
      "--validation",
      sealed.validation,
      "--worker",
      `fable-${index + 1}`,
      "--status",
      "COMPLETE",
      "--summary",
      `completed ${name}`,
    ], { env: { TMUX_PANE: `%${index + 1}` } });
    assert.equal(result.status, 0, result.stderr);
  }
  const evidence = path.join(runtime, "evidence.json");
  const result = run("python3", [
    PROTOCOL,
    "final-gate",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--output",
    evidence,
  ]);
  assert.equal(result.status, 0, result.stderr);
  const value = JSON.parse(readFileSync(evidence, "utf8"));
  assert.equal(value.passed, true);
  assert.equal(value.semanticReview, "intentionally omitted");
  assert.ok(value.checks.length >= 2);
});

test("final gate rejects an out-of-scope repository change", () => {
  const repo = makeRepo();
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime);
  registerFive(runtime, sealed);
  writeFileSync(path.join(repo, "outside.txt"), "escape\n");
  for (let index = 1; index <= 5; index += 1) {
    const result = run("python3", [
      PROTOCOL,
      "record-worker",
      "--state",
      sealed.state,
      "--validation",
      sealed.validation,
      "--worker",
      `fable-${index}`,
      "--status",
      "COMPLETE",
      "--summary",
      "fixture",
    ], { env: { TMUX_PANE: `%${index}` } });
    assert.equal(result.status, 0, result.stderr);
  }
  const evidence = path.join(runtime, "evidence.json");
  const result = run("python3", [
    PROTOCOL,
    "final-gate",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--output",
    evidence,
  ]);
  assert.notEqual(result.status, 0);
  assert.match(readFileSync(evidence, "utf8"), /outside\.txt/);
});

test("verification gate detects protected ignored-file mutation by a test command", () => {
  const repo = makeRepo();
  mkdirSync(path.join(repo, "scripts"));
  writeFileSync(path.join(repo, ".gitignore"), ".env\n");
  writeFileSync(
    path.join(repo, "scripts", "mutate.mjs"),
    "import { writeFileSync } from 'node:fs'; writeFileSync('.env', 'changed\\n');\n",
  );
  git(repo, "add", ".gitignore", "scripts/mutate.mjs");
  git(repo, "commit", "-qm", "add mutation fixture");
  writeFileSync(path.join(repo, ".env"), "baseline\n");

  const plan = validPlan();
  plan.finalVerification = [{ argv: ["node", "scripts/mutate.mjs"], timeoutSeconds: 30 }];
  const runtime = tempRoot();
  const sealed = prepareSealed(repo, runtime, plan);
  registerFive(runtime, sealed);
  mkdirSync(path.join(repo, "src"));
  for (const [index, name] of ["one", "two", "three", "four", "five"].entries()) {
    writeFileSync(path.join(repo, "src", `${name}.txt`), `${name}\n`);
    const receipt = run("python3", [
      PROTOCOL,
      "record-worker",
      "--state",
      sealed.state,
      "--validation",
      sealed.validation,
      "--worker",
      `fable-${index + 1}`,
      "--status",
      "COMPLETE",
      "--summary",
      "fixture",
    ], { env: { TMUX_PANE: `%${index + 1}` } });
    assert.equal(receipt.status, 0, receipt.stderr);
  }
  const evidence = path.join(runtime, "evidence.json");
  const result = run("python3", [
    PROTOCOL,
    "final-gate",
    "--state",
    sealed.state,
    "--validation",
    sealed.validation,
    "--output",
    evidence,
  ]);
  assert.notEqual(result.status, 0);
  const value = JSON.parse(readFileSync(evidence, "utf8"));
  assert.equal(value.passed, false);
  assert.equal(value.checks.at(-1).repositoryMutated, true);
});

test("full controller simulation plans first, then sends exactly five exact-pane briefs", () => {
  const repo = makeRepo();
  const root = tempRoot("oio-live-sim-");
  const fakeBin = path.join(root, "bin");
  const dataHome = path.join(root, "data");
  const runtimeHome = path.join(root, "runtime");
  mkdirSync(fakeBin);
  mkdirSync(dataHome);
  mkdirSync(runtimeHome);
  const plan = writeJson(root, "fake-plan.json", validPlan());
  const log = path.join(root, "calls.log");
  const marker = path.join(root, "session.exists");
  const session = `${path.basename(repo)}--live-test`;
  const manifest = path.join(dataHome, "ntm", "manifests", `${session}.json`);
  mkdirSync(path.dirname(manifest), { recursive: true });
  const manifestTemplate = writeJson(root, "manifest-template.json", {
    session,
    project_dir: repo,
    agents: Array.from({ length: 5 }, (_, index) => ({
      pane_id: `%${index + 1}`,
      pane_index: index,
      type: "cc",
      model: "claude-fable-5",
      command: "fake claude",
    })),
  });

  const executable = (name, contents) => {
    const target = path.join(fakeBin, name);
    writeFileSync(target, contents);
    chmodSync(target, 0o755);
  };
  executable(
    "timeout",
    `#!/usr/bin/env bash\nset -e\nshift\nshift\nexec "$@"\n`,
  );
  executable(
    "codex",
    `#!/usr/bin/env bash\nset -e\nprintf 'plan\\n' >> "$FAKE_LOG"\nout=''\nwhile (($#)); do\n  if [[ "$1" == '--output-last-message' ]]; then out="$2"; shift 2; else shift; fi\ndone\ncp "$FAKE_PLAN" "$out"\n`,
  );
  executable("claude", "#!/usr/bin/env bash\nexit 0\n");
  executable(
    "tmux",
    `#!/usr/bin/env bash\nset -e\ncase "$1" in\n  has-session) [[ -f "$FAKE_MARKER" ]] ;;\n  list-panes) printf '%%1:0\\n%%2:1\\n%%3:2\\n%%4:3\\n%%5:4\\n' ;;\n  kill-session) rm -f "$FAKE_MARKER" ;;\n  *) exit 1 ;;\nesac\n`,
  );
  executable(
    "ntm",
    `#!/usr/bin/env bash\nset -e\nif [[ "$1" == 'spawn' ]]; then\n  printf 'spawn\\n' >> "$FAKE_LOG"\n  touch "$FAKE_MARKER"\n  cp "$FAKE_MANIFEST_TEMPLATE" "$FAKE_MANIFEST"\n  exit 0\nfi\nif [[ "$1" == --robot-send=* ]]; then\n  msg=''\n  pane=''\n  for arg in "$@"; do\n    case "$arg" in --msg-file=*) msg="\${arg#*=}" ;; --panes=*) pane="\${arg#*=}" ;; esac\n  done\n  printf 'send:%s:%s\\n' "$pane" "$(basename "$msg")" >> "$FAKE_LOG"\n  line="$(grep '^python3 .* --status COMPLETE ' "$msg" | head -n 1)"\n  export TMUX_PANE="%$((pane + 1))"\n  if [[ "$pane" == '0' ]]; then (sleep 1; eval "$line") </dev/null >/dev/null 2>&1 & else eval "$line"; fi\n  printf '{"success":true,"send":{"success":true,"session":"%s","blocked":false,"targets":["%s"],"successful":["%s"],"failed":[]},"ack":{"success":true,"session":"%s","confirmations":[{"pane":"%s","ack_type":"output_started"}],"pending":[],"failed":[],"timed_out":false}}\\n' "$FAKE_SESSION" "$pane" "$pane" "$FAKE_SESSION" "$pane"\n  exit 0\nfi\nif [[ "$1" == --robot-wait=* ]]; then printf 'wait\\n' >> "$FAKE_LOG"; printf '{"success":true}\\n'; exit 0; fi\nif [[ "$1" == --robot-is-working=* ]]; then printf '{"success":true}\\n'; exit 0; fi\nif [[ "$1" == --robot-tail=* ]]; then exit 0; fi\nif [[ "$1" == 'kill' ]]; then exit 0; fi\nexit 1\n`,
  );

  const result = run(LAUNCHER, [
    "Build the fixture comprehensively",
    "--project",
    repo,
    "--run-id",
    "live-test",
    "--trusted-task",
    "--plan-timeout",
    "60",
    "--worker-timeout",
    "60",
  ], {
    env: {
      PATH: `${fakeBin}:${process.env.PATH}`,
      XDG_DATA_HOME: dataHome,
      XDG_RUNTIME_DIR: runtimeHome,
      FAKE_LOG: log,
      FAKE_PLAN: plan,
      FAKE_MARKER: marker,
      FAKE_MANIFEST: manifest,
      FAKE_MANIFEST_TEMPLATE: manifestTemplate,
      FAKE_SESSION: session,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /DONE — mechanically green/);
  const calls = readFileSync(log, "utf8").trim().split("\n");
  assert.equal(calls[0], "plan");
  assert.equal(calls[1], "spawn");
  const sends = calls.filter((line) => line.startsWith("send:"));
  assert.deepEqual(sends, [
    "send:0:fable-1.md",
    "send:1:fable-2.md",
    "send:2:fable-3.md",
    "send:3:fable-4.md",
    "send:4:fable-5.md",
  ]);
  assert.equal(existsSync(marker), false);
});

test("installer repairs stripped modes and links both skill clients without overwriting files", () => {
  const root = tempRoot();
  const copied = path.join(root, "skill");
  cpSync(SKILL, copied, { recursive: true });
  for (const relative of ["bin/orchestrate-it-out", "scripts/install", "scripts/doctor", "scripts/protocol.py"]) {
    chmodSync(path.join(copied, relative), 0o644);
  }
  const claudeDir = path.join(root, "claude-skills");
  const codexDir = path.join(root, "codex-skills");
  const binDir = path.join(root, "user-bin");
  const result = run("bash", [path.join(copied, "scripts", "install")], {
    env: {
      CLAUDE_SKILLS_DIR: claudeDir,
      CODEX_SKILLS_DIR: codexDir,
      ORCHESTRATE_IT_OUT_BIN_DIR: binDir,
    },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(lstatSync(path.join(claudeDir, "orchestrate-it-out")).isSymbolicLink());
  assert.ok(lstatSync(path.join(codexDir, "orchestrate-it-out")).isSymbolicLink());
  assert.ok(lstatSync(path.join(binDir, "orchestrate-it-out")).isSymbolicLink());
  assert.notEqual(lstatSync(path.join(copied, "bin", "orchestrate-it-out")).mode & 0o111, 0);

  rmSync(path.join(claudeDir, "orchestrate-it-out"));
  writeFileSync(path.join(claudeDir, "orchestrate-it-out"), "owned\n");
  const refused = run("bash", [path.join(copied, "scripts", "install"), "--replace-links"], {
    env: {
      CLAUDE_SKILLS_DIR: claudeDir,
      CODEX_SKILLS_DIR: codexDir,
      ORCHESTRATE_IT_OUT_BIN_DIR: binDir,
    },
  });
  assert.notEqual(refused.status, 0);
  assert.equal(readFileSync(path.join(claudeDir, "orchestrate-it-out"), "utf8"), "owned\n");
});
