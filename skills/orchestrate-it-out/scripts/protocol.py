#!/usr/bin/env python3
"""Deterministic protocol for the Sol -> five Fable orchestration board."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


PROTOCOL_VERSION = 1
WORKER_IDS = [f"fable-{number}" for number in range(1, 6)]
MAX_OUTPUT_BYTES = 64 * 1024
PRUNED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "dash",
    "env",
    "fish",
    "nu",
    "powershell",
    "pwsh",
    "sh",
    "xargs",
    "zsh",
}
NETWORK_EXECUTABLES = {
    "curl",
    "ftp",
    "gh",
    "nc",
    "netcat",
    "rsync",
    "scp",
    "sftp",
    "ssh",
    "telnet",
    "wget",
}
MUTATING_EXECUTABLES = {
    "chmod",
    "chown",
    "cp",
    "dd",
    "install",
    "ln",
    "mkdir",
    "mktemp",
    "mv",
    "rm",
    "rmdir",
    "sed",
    "tee",
    "touch",
    "truncate",
}
SENSITIVE_ENV_RE = re.compile(
    r"(?:AUTH|COOKIE|CREDENTIAL|KEY|PASS|SECRET|SESSION|TOKEN)", re.IGNORECASE
)
INJECTION_ENV = {
    "BASH_ENV",
    "CDPATH",
    "ENV",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "NODE_OPTIONS",
    "PERL5OPT",
    "PYTHONHOME",
    "PYTHONPATH",
    "RUBYOPT",
}


class ProtocolError(RuntimeError):
    """A fail-closed protocol error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError(f"cannot read valid JSON from {path}: {error}") from error


def write_new_json(path: Path, value: Any) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ProtocolError(f"refusing to overwrite artifact: {path}") from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_replace_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def locked_state(path: Path) -> Iterator[dict[str, Any]]:
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_value = load_json(path)
        if not isinstance(state_value, dict):
            raise ProtocolError("run state must be a JSON object")
        yield state_value
        atomic_replace_json(path, state_value)


def git(project: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(project), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ProtocolError(f"git {' '.join(arguments)} failed: {message}")
    return result


def git_head(project: Path) -> str:
    return git(project, "rev-parse", "HEAD").stdout.decode().strip()


def changed_paths(project: Path) -> list[str]:
    tracked = git(project, "diff", "--name-only", "-z", "HEAD", "--").stdout
    untracked = git(project, "ls-files", "--others", "--exclude-standard", "-z").stdout
    values = {
        item.decode("utf-8", "surrogateescape")
        for item in (tracked + untracked).split(b"\0")
        if item
    }
    return sorted(values)


def repo_snapshot(project: Path) -> dict[str, Any]:
    paths = changed_paths(project)
    return {
        "head": git_head(project),
        "paths": paths,
        "fingerprints": {path: fingerprint_path(project / path) for path in paths},
    }


def git_metadata_fingerprints(project: Path) -> dict[str, Any]:
    def resolved_git_path(name: str) -> Path:
        value = git(project, "rev-parse", "--git-path", name).stdout.decode().strip()
        path = Path(value)
        return path if path.is_absolute() else (project / path).resolve()

    return {
        "config": fingerprint_path(resolved_git_path("config")),
        "head": fingerprint_path(resolved_git_path("HEAD")),
        "hooks": fingerprint_tree(resolved_git_path("hooks")),
        "infoExclude": fingerprint_path(resolved_git_path("info/exclude")),
        "packedRefs": fingerprint_path(resolved_git_path("packed-refs")),
        "refs": fingerprint_tree(resolved_git_path("refs")),
    }


def fingerprint_path(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"kind": "missing"}
    mode = stat.S_IMODE(metadata.st_mode)
    if path.is_symlink():
        target = os.readlink(path)
        return {"kind": "symlink", "mode": mode, "targetSha256": sha256_bytes(target.encode())}
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "kind": "file",
            "mode": mode,
            "size": metadata.st_size,
            "sha256": digest.hexdigest(),
        }
    if path.is_dir():
        return {"kind": "directory", "mode": mode}
    return {"kind": "other", "mode": mode, "size": metadata.st_size}


def fingerprint_tree(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {".": fingerprint_path(root)}
    result: dict[str, Any] = {".": fingerprint_path(root)}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories + files):
            path = current_path / name
            result[path.relative_to(root).as_posix()] = fingerprint_path(path)
    return result


def looks_protected(relative: str) -> bool:
    path = PurePosixPath(relative)
    lowered = path.name.lower()
    suffix = path.suffix.lower()
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    if lowered in {".netrc", ".npmrc", ".pypirc", "id_ed25519", "id_rsa"}:
        return True
    if suffix in {".db", ".key", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}:
        return True
    sensitive_data_suffixes = {"", ".conf", ".ini", ".json", ".toml", ".txt", ".yaml", ".yml"}
    return suffix in sensitive_data_suffixes and bool(
        re.search(r"(?:credential|secret|token)", lowered)
    )


def protected_fingerprints(project: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for root, directories, files in os.walk(project, topdown=True, followlinks=False):
        directories[:] = [name for name in directories if name not in PRUNED_DIRS]
        root_path = Path(root)
        for name in files:
            path = root_path / name
            relative = path.relative_to(project).as_posix()
            if looks_protected(relative):
                result[relative] = fingerprint_path(path)
    return dict(sorted(result.items()))


def require_git_repo(project: Path) -> None:
    result = git(project, "rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode != 0 or result.stdout.strip() != b"true":
        raise ProtocolError(f"not a Git working tree: {project}")


def validate_relative_path(value: Any, label: str, *, directory_allowed: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ProtocolError(f"{label} must be a non-empty repository-relative path")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise ProtocolError(f"{label} is not a safe repository-relative path: {value!r}")
    directory = value.endswith("/")
    trimmed = value[:-1] if directory else value
    parts = PurePosixPath(trimmed).parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or parts[0] == ".git":
        raise ProtocolError(f"{label} is not a safe repository-relative path: {value!r}")
    if any(character in value for character in "*?[]{}"):
        raise ProtocolError(f"{label} may not contain glob syntax; use an exact file or trailing-/ directory")
    if PurePosixPath(trimmed).as_posix() != trimmed:
        raise ProtocolError(f"{label} must use canonical POSIX path spelling: {value!r}")
    if directory and not directory_allowed:
        raise ProtocolError(f"{label} must name a file, not a directory scope")
    return value


def scope_contains(scope: str, path: str) -> bool:
    return path.startswith(scope) if scope.endswith("/") else path == scope


def scopes_overlap(first: str, second: str) -> bool:
    if first == second:
        return True
    if first.endswith("/") and scope_contains(first, second.rstrip("/")):
        return True
    if second.endswith("/") and scope_contains(second, first.rstrip("/")):
        return True
    return False


def require_string(value: Any, label: str, *, minimum: int = 1, maximum: int = 5000) -> str:
    if not isinstance(value, str) or not (minimum <= len(value.strip()) <= maximum):
        raise ProtocolError(f"{label} must be a string of {minimum}..{maximum} characters")
    if value != value.strip():
        raise ProtocolError(f"{label} may not have leading or trailing whitespace")
    return value


def require_string_list(
    value: Any, label: str, *, minimum: int = 0, maximum: int = 64
) -> list[str]:
    if not isinstance(value, list) or not (minimum <= len(value) <= maximum):
        raise ProtocolError(f"{label} must contain {minimum}..{maximum} strings")
    result = [require_string(item, f"{label}[{index}]", maximum=2000) for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise ProtocolError(f"{label} contains duplicate entries")
    return result


def validate_command(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"argv", "timeoutSeconds"}:
        raise ProtocolError(f"{label} must contain exactly argv and timeoutSeconds")
    argv = value["argv"]
    timeout = value["timeoutSeconds"]
    if not isinstance(argv, list) or not (1 <= len(argv) <= 64):
        raise ProtocolError(f"{label}.argv must contain 1..64 literal arguments")
    for index, argument in enumerate(argv):
        if not isinstance(argument, str) or not argument or len(argument) > 4096:
            raise ProtocolError(f"{label}.argv[{index}] must be a non-empty literal string")
        if any(token in argument for token in ("\n", "\r", "\x00", "$(", "`", ";", "&&", "||", ">", "<")):
            raise ProtocolError(f"{label}.argv[{index}] contains shell control syntax")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 30 <= timeout <= 3600:
        raise ProtocolError(f"{label}.timeoutSeconds must be an integer from 30 to 3600")

    executable = Path(argv[0]).name.lower()
    if argv[0] != argv[0].strip() or argv[0].startswith("/") or ".." in PurePosixPath(argv[0]).parts:
        raise ProtocolError(f"{label} has an unsafe executable path: {argv[0]!r}")
    if executable in SHELL_EXECUTABLES | NETWORK_EXECUTABLES | MUTATING_EXECUTABLES:
        raise ProtocolError(f"{label} uses forbidden executable: {argv[0]}")
    if executable in {"npx", "bunx", "pnpx", "pipx"}:
        raise ProtocolError(f"{label} may not use package executors")
    if executable in {"npm", "pnpm", "yarn", "bun"} and len(argv) > 1:
        if argv[1].lower() in {"add", "ci", "exec", "install", "link", "publish", "remove", "uninstall"}:
            raise ProtocolError(f"{label} may not install, execute packages, or publish")
    if executable in {"pip", "pip3"} and len(argv) > 1 and argv[1].lower() in {"install", "uninstall"}:
        raise ProtocolError(f"{label} may not mutate Python packages")
    if executable == "python" or executable == "python3":
        if any(argument in {"-c", "-"} for argument in argv[1:]):
            raise ProtocolError(f"{label} may not use inline Python or stdin programs")
        if len(argv) > 2 and argv[1] == "-m" and argv[2].lower() in {"pip", "ensurepip"}:
            raise ProtocolError(f"{label} may not mutate Python packages")
    if executable in {"node", "ruby", "perl", "php"} and any(
        argument in {"-e", "--eval", "-r"} for argument in argv[1:]
    ):
        raise ProtocolError(f"{label} may not use inline interpreter code")
    if executable == "git" and len(argv) > 1:
        allowed = {"diff", "grep", "status"}
        command = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
        if command not in allowed:
            raise ProtocolError(f"{label} uses unsafe Git subcommand: {command or '<missing>'}")
        forbidden_git_options = {"--ext-diff", "--no-index", "--textconv"}
        if any(
            argument in forbidden_git_options
            or argument == "-c"
            or argument.startswith("--output=")
            or argument.startswith("--open-files-in-pager")
            for argument in argv[1:]
        ):
            raise ProtocolError(f"{label} uses unsafe Git execution/output options")
    if executable in {"cargo", "go"} and len(argv) > 1 and argv[1].lower() in {"install", "publish", "get"}:
        raise ProtocolError(f"{label} may not install or publish packages")
    return {"argv": argv, "timeoutSeconds": timeout}


def validate_plan_data(plan: Any, task: str, project: Path) -> tuple[dict[str, Any], list[list[str]]]:
    if not isinstance(plan, dict):
        raise ProtocolError("plan must be a JSON object")
    required = {
        "version",
        "task",
        "strategy",
        "successDefinition",
        "assumptions",
        "globalInvariants",
        "nonGoals",
        "workers",
        "finalVerification",
    }
    if set(plan) != required:
        missing = sorted(required - set(plan))
        extra = sorted(set(plan) - required)
        raise ProtocolError(f"plan keys mismatch; missing={missing}, extra={extra}")
    if plan["version"] != PROTOCOL_VERSION:
        raise ProtocolError(f"plan.version must be {PROTOCOL_VERSION}")
    if plan["task"] != task:
        raise ProtocolError("plan.task must exactly match the immutable operator task")
    require_string(plan["strategy"], "plan.strategy", minimum=40)
    require_string_list(plan["successDefinition"], "plan.successDefinition", minimum=1)
    require_string_list(plan["assumptions"], "plan.assumptions")
    require_string_list(plan["globalInvariants"], "plan.globalInvariants", minimum=1)
    require_string_list(plan["nonGoals"], "plan.nonGoals", minimum=1)

    workers = plan["workers"]
    if not isinstance(workers, list) or len(workers) != 5:
        raise ProtocolError("plan.workers must contain exactly five workstreams")
    by_id: dict[str, dict[str, Any]] = {}
    expected_worker_keys = {
        "id",
        "title",
        "mission",
        "dependsOn",
        "requiredContext",
        "decisionsAlreadyMade",
        "steps",
        "writeScope",
        "invariants",
        "nonGoals",
        "deliverables",
        "acceptance",
        "verification",
        "parallelSafe",
    }
    for index, worker in enumerate(workers):
        label = f"plan.workers[{index}]"
        if not isinstance(worker, dict) or set(worker) != expected_worker_keys:
            raise ProtocolError(f"{label} has missing or extra keys")
        worker_id = require_string(worker["id"], f"{label}.id", maximum=32)
        if worker_id in by_id:
            raise ProtocolError(f"duplicate worker id: {worker_id}")
        by_id[worker_id] = worker
        require_string(worker["title"], f"{label}.title", minimum=8, maximum=160)
        require_string(worker["mission"], f"{label}.mission", minimum=30)
        require_string_list(worker["dependsOn"], f"{label}.dependsOn", maximum=4)
        contexts = require_string_list(worker["requiredContext"], f"{label}.requiredContext", minimum=1)
        for path_index, path in enumerate(contexts):
            validate_relative_path(path, f"{label}.requiredContext[{path_index}]", directory_allowed=True)
        require_string_list(
            worker["decisionsAlreadyMade"], f"{label}.decisionsAlreadyMade", minimum=1
        )
        require_string_list(worker["steps"], f"{label}.steps", minimum=2)
        scopes = require_string_list(worker["writeScope"], f"{label}.writeScope", minimum=1)
        for scope_index, scope in enumerate(scopes):
            validate_relative_path(scope, f"{label}.writeScope[{scope_index}]", directory_allowed=True)
            if looks_protected(scope.rstrip("/")):
                raise ProtocolError(f"{label}.writeScope includes protected path: {scope}")
        require_string_list(worker["invariants"], f"{label}.invariants", minimum=1)
        require_string_list(worker["nonGoals"], f"{label}.nonGoals", minimum=1)
        require_string_list(worker["deliverables"], f"{label}.deliverables", minimum=1)
        require_string_list(worker["acceptance"], f"{label}.acceptance", minimum=1)
        verification = worker["verification"]
        if not isinstance(verification, list) or not (1 <= len(verification) <= 16):
            raise ProtocolError(f"{label}.verification must contain 1..16 commands")
        for command_index, command in enumerate(verification):
            validate_command(command, f"{label}.verification[{command_index}]")
        if not isinstance(worker["parallelSafe"], bool):
            raise ProtocolError(f"{label}.parallelSafe must be boolean")

    if set(by_id) != set(WORKER_IDS):
        raise ProtocolError(f"worker ids must be exactly {WORKER_IDS}")
    if [worker["id"] for worker in workers] != WORKER_IDS:
        raise ProtocolError(f"worker entries must be ordered exactly as {WORKER_IDS}")
    for worker_id, worker in by_id.items():
        dependencies = worker["dependsOn"]
        if worker_id in dependencies:
            raise ProtocolError(f"{worker_id} may not depend on itself")
        unknown = sorted(set(dependencies) - set(WORKER_IDS))
        if unknown:
            raise ProtocolError(f"{worker_id} has unknown dependencies: {unknown}")

    remaining = set(WORKER_IDS)
    complete: set[str] = set()
    waves: list[list[str]] = []
    while remaining:
        ready = sorted(worker_id for worker_id in remaining if set(by_id[worker_id]["dependsOn"]) <= complete)
        if not ready:
            raise ProtocolError("worker dependency graph contains a cycle")
        parallel_ready = [worker_id for worker_id in ready if by_id[worker_id]["parallelSafe"]]
        wave = parallel_ready if parallel_ready else [ready[0]]
        waves.append(wave)
        complete.update(wave)
        remaining.difference_update(wave)

    dependency_cache: dict[tuple[str, str], bool] = {}

    def depends_transitively(worker_id: str, ancestor: str) -> bool:
        key = (worker_id, ancestor)
        if key in dependency_cache:
            return dependency_cache[key]
        direct = by_id[worker_id]["dependsOn"]
        result = ancestor in direct or any(depends_transitively(parent, ancestor) for parent in direct)
        dependency_cache[key] = result
        return result

    for first_index, first_id in enumerate(WORKER_IDS):
        for second_id in WORKER_IDS[first_index + 1 :]:
            overlaps = any(
                scopes_overlap(first_scope, second_scope)
                for first_scope in by_id[first_id]["writeScope"]
                for second_scope in by_id[second_id]["writeScope"]
            )
            ordered = depends_transitively(first_id, second_id) or depends_transitively(second_id, first_id)
            if overlaps and not ordered:
                raise ProtocolError(
                    f"unordered workers {first_id} and {second_id} have overlapping write scopes"
                )

    final_commands = plan["finalVerification"]
    if not isinstance(final_commands, list) or not (1 <= len(final_commands) <= 24):
        raise ProtocolError("plan.finalVerification must contain 1..24 commands")
    for index, command in enumerate(final_commands):
        validate_command(command, f"plan.finalVerification[{index}]")

    canonical_workers = [by_id[worker_id] for worker_id in WORKER_IDS]
    plan["workers"] = canonical_workers
    return plan, waves


def assert_validation(plan_path: Path, validation_path: Path, task_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = load_json(plan_path)
    validation = load_json(validation_path)
    task = task_path.read_text(encoding="utf-8").rstrip("\n")
    if validation.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolError("validation protocol version mismatch")
    if validation.get("planSha256") != file_sha256(plan_path):
        raise ProtocolError("plan bytes no longer match sealed validation")
    if validation.get("taskSha256") != sha256_bytes(task.encode()):
        raise ProtocolError("task no longer matches sealed validation")
    validate_plan_data(plan, task, Path(validation["project"]))
    return plan, validation


def command_preflight(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    require_git_repo(project)
    output = Path(args.output)
    status = git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    baseline = {
        "protocolVersion": PROTOCOL_VERSION,
        "createdAt": utc_now(),
        "project": str(project),
        "gitHead": git_head(project),
        "gitMetadataFingerprints": git_metadata_fingerprints(project),
        "clean": status == b"",
        "dirtyPaths": changed_paths(project),
        "protectedFingerprints": protected_fingerprints(project),
    }
    write_new_json(output, baseline)
    print(json.dumps({"clean": baseline["clean"], "gitHead": baseline["gitHead"]}))
    return 0


def command_init_state(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    baseline_path = Path(args.baseline).resolve()
    task_path = Path(args.task_file).resolve()
    baseline = load_json(baseline_path)
    state = {
        "protocolVersion": PROTOCOL_VERSION,
        "runId": args.run_id,
        "createdAt": utc_now(),
        "project": baseline["project"],
        "taskPath": str(task_path),
        "taskSha256": file_sha256(task_path),
        "baselinePath": str(baseline_path),
        "baselineSha256": file_sha256(baseline_path),
        "orchestratorModel": args.orchestrator_model,
        "workerModel": args.worker_model,
        "plan": None,
        "workersClaimedAt": None,
        "roster": None,
        "receipts": {},
        "finalEvidence": None,
    }
    write_new_json(state_path, state)
    return 0


def command_validate_plan(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    task_path = Path(args.task_file).resolve()
    project = Path(args.project).resolve()
    task = task_path.read_text(encoding="utf-8").rstrip("\n")
    plan = load_json(plan_path)
    _, waves = validate_plan_data(plan, task, project)
    union_scopes = sorted({scope for worker in plan["workers"] for scope in worker["writeScope"]})
    validation = {
        "protocolVersion": PROTOCOL_VERSION,
        "validatedAt": utc_now(),
        "project": str(project),
        "taskSha256": sha256_bytes(task.encode()),
        "planSha256": file_sha256(plan_path),
        "workerIds": WORKER_IDS,
        "waves": waves,
        "unionWriteScope": union_scopes,
    }
    write_new_json(Path(args.output), validation)
    print(json.dumps({"planSha256": validation["planSha256"], "waves": waves}))
    return 0


def command_seal(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    plan_path = Path(args.plan).resolve()
    validation_path = Path(args.validation).resolve()
    with locked_state(state_path) as state:
        if state["plan"] is not None:
            raise ProtocolError("plan is already sealed")
        task_path = Path(state["taskPath"])
        if file_sha256(task_path) != state["taskSha256"]:
            raise ProtocolError("immutable task artifact changed before plan sealing")
        if file_sha256(Path(state["baselinePath"])) != state["baselineSha256"]:
            raise ProtocolError("baseline artifact changed before plan sealing")
        _, validation = assert_validation(plan_path, validation_path, task_path)
        if validation["project"] != state["project"]:
            raise ProtocolError("validation project does not match run state")
        state["plan"] = {
            "path": str(plan_path),
            "sha256": file_sha256(plan_path),
            "validationPath": str(validation_path),
            "validationSha256": file_sha256(validation_path),
            "sealedAt": utc_now(),
        }
    return 0


def assert_state_seal(state: dict[str, Any], validation_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    seal = state.get("plan")
    if not isinstance(seal, dict):
        raise ProtocolError("plan has not been sealed")
    plan_path = Path(seal["path"])
    task_path = Path(state["taskPath"])
    if file_sha256(task_path) != state["taskSha256"]:
        raise ProtocolError("immutable task artifact bytes changed")
    if file_sha256(Path(state["baselinePath"])) != state["baselineSha256"]:
        raise ProtocolError("immutable baseline artifact bytes changed")
    if file_sha256(plan_path) != seal["sha256"]:
        raise ProtocolError("sealed plan bytes changed")
    if validation_path.resolve() != Path(seal["validationPath"]).resolve():
        raise ProtocolError("unexpected validation artifact")
    if file_sha256(validation_path) != seal["validationSha256"]:
        raise ProtocolError("sealed validation bytes changed")
    _, validation = assert_validation(plan_path, validation_path, task_path)
    return plan_path, task_path, validation


def assert_clean_baseline_current(state: dict[str, Any]) -> None:
    baseline = load_json(Path(state["baselinePath"]))
    project = Path(state["project"])
    if git_head(project) != baseline["gitHead"]:
        raise ProtocolError("repository HEAD drifted after preflight")
    paths = changed_paths(project)
    if paths:
        raise ProtocolError(f"repository drifted after preflight: {paths}")
    if protected_fingerprints(project) != baseline["protectedFingerprints"]:
        raise ProtocolError("a protected ignored path drifted after preflight")
    if git_metadata_fingerprints(project) != baseline["gitMetadataFingerprints"]:
        raise ProtocolError("repository-local Git config drifted after preflight")


def command_claim_workers(args: argparse.Namespace) -> int:
    validation_path = Path(args.validation)
    with locked_state(Path(args.state)) as state:
        assert_state_seal(state, validation_path)
        assert_clean_baseline_current(state)
        if state["workersClaimedAt"] is not None:
            raise ProtocolError("the one-shot five-worker spawn claim was already consumed")
        state["workersClaimedAt"] = utc_now()
    return 0


def parse_tmux_panes(path: Path) -> dict[int, str]:
    panes: dict[int, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        parts = line.split(":", 1)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ProtocolError(f"invalid tmux pane row {number}")
        panes[int(parts[1])] = parts[0]
    return panes


def command_register_workers(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    roster_path = Path(args.output).resolve()
    validation_path = Path(args.validation).resolve()
    manifest = load_json(manifest_path)
    if manifest.get("session") != args.session:
        raise ProtocolError("NTM manifest session mismatch")
    state_preview = load_json(Path(args.state))
    if manifest.get("project_dir") != state_preview.get("project"):
        raise ProtocolError("NTM manifest project directory mismatch")
    agents = manifest.get("agents")
    if not isinstance(agents, list):
        raise ProtocolError("NTM manifest agents must be a list")
    if len(agents) != 5:
        raise ProtocolError(f"expected exactly five NTM agents, found {len(agents)}")
    for agent in agents:
        if agent.get("type") != "cc" or agent.get("model") != args.worker_model:
            raise ProtocolError("every NTM agent must be a pinned Fable worker")
        if not isinstance(agent.get("pane_index"), int) or not isinstance(agent.get("pane_id"), str):
            raise ProtocolError("NTM manifest agent is missing pane identity")
        if agent["pane_index"] < 0:
            raise ProtocolError("NTM manifest pane index must be non-negative")
    pane_ids = [agent["pane_id"] for agent in agents]
    pane_indices = [agent["pane_index"] for agent in agents]
    if len(set(pane_ids)) != 5 or len(set(pane_indices)) != 5:
        raise ProtocolError("NTM manifest pane identities must be unique")
    pane_rows = parse_tmux_panes(Path(args.tmux_panes))
    manifest_panes = {(agent["pane_index"], agent["pane_id"]) for agent in agents}
    if len(pane_rows) != 5 or set(pane_rows.items()) != manifest_panes:
        raise ProtocolError("NTM manifest and exact tmux pane set differ")
    sorted_agents = sorted(agents, key=lambda item: item["pane_index"])
    roster_workers: list[dict[str, Any]] = []
    for worker_id, agent in zip(WORKER_IDS, sorted_agents, strict=True):
        pane_index = agent["pane_index"]
        if pane_rows.get(pane_index) != agent["pane_id"]:
            raise ProtocolError(f"manifest/tmux pane mismatch for {worker_id}")
        roster_workers.append(
            {
                "id": worker_id,
                "model": agent["model"],
                "paneId": agent["pane_id"],
                "paneIndex": pane_index,
            }
        )
    roster = {
        "protocolVersion": PROTOCOL_VERSION,
        "session": args.session,
        "registeredAt": utc_now(),
        "workers": roster_workers,
    }
    write_new_json(roster_path, roster)
    with locked_state(Path(args.state)) as state:
        assert_state_seal(state, validation_path)
        assert_clean_baseline_current(state)
        if state["workersClaimedAt"] is None:
            raise ProtocolError("workers cannot be registered before the spawn claim")
        if state["roster"] is not None:
            raise ProtocolError("worker roster is already registered")
        if state["workerModel"] != args.worker_model:
            raise ProtocolError("worker model differs from run-state pin")
        state["roster"] = {
            "path": str(roster_path),
            "sha256": file_sha256(roster_path),
            "registeredAt": utc_now(),
        }
    return 0


def bullet_lines(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def numbered_lines(values: list[str]) -> str:
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def command_render_briefs(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    validation_path = Path(args.validation).resolve()
    output_dir = Path(args.output_dir).resolve()
    with locked_state(state_path) as state:
        plan_path, _, validation = assert_state_seal(state, validation_path)
        roster_seal = state.get("roster")
        if not isinstance(roster_seal, dict):
            raise ProtocolError("worker roster is not registered")
        roster_path = Path(roster_seal["path"])
        if file_sha256(roster_path) != roster_seal["sha256"]:
            raise ProtocolError("worker roster bytes changed")
        plan = load_json(plan_path)
        roster = load_json(roster_path)
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ProtocolError(f"refusing to overwrite non-empty briefs directory: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        project = state["project"]
        task = plan["task"]
        globals_text = bullet_lines(plan["globalInvariants"])
        global_non_goals = bullet_lines(plan["nonGoals"])
        for worker, roster_worker in zip(plan["workers"], roster["workers"], strict=True):
            worker_id = worker["id"]
            if roster_worker["id"] != worker_id:
                raise ProtocolError("plan/roster worker ordering mismatch")
            commands = "\n".join(
                f"- `{json.dumps(command['argv'])}` (timeout {command['timeoutSeconds']}s)"
                for command in worker["verification"]
            )
            complete_receipt = shlex.join(
                [
                    "python3",
                    str(Path(__file__).resolve()),
                    "record-worker",
                    "--state",
                    str(state_path.resolve()),
                    "--validation",
                    str(validation_path),
                    "--worker",
                    worker_id,
                    "--status",
                    "COMPLETE",
                    "--summary",
                    "implemented the sealed workstream and ran its checks",
                ]
            )
            blocked_receipt = shlex.join(
                [
                    "python3",
                    str(Path(__file__).resolve()),
                    "record-worker",
                    "--state",
                    str(state_path.resolve()),
                    "--validation",
                    str(validation_path),
                    "--worker",
                    worker_id,
                    "--status",
                    "BLOCKED",
                    "--summary",
                    "concrete reason the sealed workstream could not be completed",
                ]
            )
            prompt = f"""# ORCHESTRATE IT OUT — {worker_id}

You are one of exactly five Claude Fable 5 implementation workers. This is a
self-contained, sealed assignment produced by GPT-5.6 Sol. Work only in
`{project}`. Do not spawn agents or invoke another orchestration skill.

## Immutable operator task

{task}

## Your mission

**{worker['title']}**

{worker['mission']}

## Read first

{bullet_lines(worker['requiredContext'])}

## Decisions already made

{bullet_lines(worker['decisionsAlreadyMade'])}

## Ordered steps

{numbered_lines(worker['steps'])}

## Exact write scope

{bullet_lines(worker['writeScope'])}

Do not edit any repository path outside this scope. Preserve existing user
changes. Do not commit, push, install dependencies, deploy, publish, contact
external services, or edit orchestration artifacts.

## Global invariants

{globals_text}

## Workstream invariants

{bullet_lines(worker['invariants'])}

## Non-goals

Global:
{global_non_goals}

This workstream:
{bullet_lines(worker['nonGoals'])}

## Deliverables

{bullet_lines(worker['deliverables'])}

## Acceptance criteria

{bullet_lines(worker['acceptance'])}

## Verification

Run these commands exactly as argv, without a shell wrapper:

{commands}

## Completion receipt

After the work and checks are complete, run exactly one of these commands.
Use a concise single-line summary without secrets.

```bash
{complete_receipt}
{blocked_receipt}
```

Do not claim COMPLETE unless every acceptance criterion and verification command
passes. The controller will independently rerun all checks. When the receipt is
recorded, stop and wait.
"""
            path = output_dir / f"{worker_id}.md"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(prompt)
        state["briefs"] = {
            "directory": str(output_dir),
            "renderedAt": utc_now(),
            "validationSha256": file_sha256(validation_path),
        }
    print(json.dumps({"briefs": WORKER_IDS, "waves": validation["waves"]}))
    return 0


def command_record_worker(args: argparse.Namespace) -> int:
    if args.worker not in WORKER_IDS:
        raise ProtocolError(f"unknown worker id: {args.worker}")
    summary = require_string(args.summary, "summary", maximum=2000)
    validation_path = Path(args.validation).resolve()
    with locked_state(Path(args.state)) as state:
        assert_state_seal(state, validation_path)
        roster_seal = state.get("roster")
        if not isinstance(roster_seal, dict) or state.get("briefs") is None:
            raise ProtocolError("worker receipt cannot precede registered roster and rendered briefs")
        roster_path = Path(roster_seal["path"])
        if file_sha256(roster_path) != roster_seal["sha256"]:
            raise ProtocolError("worker roster bytes changed before receipt")
        roster = load_json(roster_path)
        expected_pane = next(
            worker["paneId"] for worker in roster["workers"] if worker["id"] == args.worker
        )
        actual_pane = os.environ.get("TMUX_PANE")
        if actual_pane != expected_pane:
            raise ProtocolError(
                f"receipt for {args.worker} must originate from pane {expected_pane}; got {actual_pane!r}"
            )
        if args.worker in state["receipts"]:
            raise ProtocolError(f"one-shot receipt already exists for {args.worker}")
        project = Path(state["project"])
        state["receipts"][args.worker] = {
            "status": args.status,
            "summary": summary,
            "recordedAt": utc_now(),
            "gitHead": git_head(project),
            "changedPaths": changed_paths(project),
        }
    return 0


def command_wave_status(args: argparse.Namespace) -> int:
    requested = args.workers.split(",") if args.workers else []
    if not requested or any(worker not in WORKER_IDS for worker in requested):
        raise ProtocolError("--workers must be a comma-separated subset of fable-1..fable-5")
    state = load_json(Path(args.state))
    receipts = state.get("receipts", {})
    missing = [worker for worker in requested if worker not in receipts]
    blocked = [
        worker
        for worker in requested
        if worker in receipts and receipts[worker].get("status") != "COMPLETE"
    ]
    result = {"workers": requested, "missing": missing, "blocked": blocked}
    print(json.dumps(result))
    return 0 if not missing and not blocked else 1


def command_validate_send(args: argparse.Namespace) -> int:
    response = load_json(Path(args.response))
    pane = str(args.pane)
    if response.get("success") is not True:
        raise ProtocolError("NTM send response did not report success=true")
    send = response.get("send")
    ack = response.get("ack")
    if not isinstance(send, dict) or not isinstance(ack, dict):
        raise ProtocolError("tracked NTM response must contain nested send and ack evidence")
    if send.get("success") is not True or send.get("session") != args.session:
        raise ProtocolError("nested NTM send evidence failed or named the wrong session")
    targets = [str(value) for value in send.get("targets", [])]
    successful = [str(value) for value in send.get("successful", [])]
    failed = send.get("failed", [])
    if targets != [pane] or successful != [pane] or failed != [] or send.get("blocked") is True:
        raise ProtocolError(
            f"NTM send did not prove one exact successful pane; "
            f"targets={targets}, successful={successful}, failed={failed}"
        )
    if (
        ack.get("success") is not True
        or ack.get("session") != args.session
        or ack.get("pending", []) != []
        or ack.get("failed", []) != []
        or ack.get("timed_out") is True
    ):
        raise ProtocolError("NTM acknowledgment evidence was incomplete or failed")
    confirmations = ack.get("confirmations", [])
    confirmed_panes = [str(item.get("pane")) for item in confirmations if isinstance(item, dict)]
    if confirmed_panes != [pane]:
        raise ProtocolError(f"NTM acknowledgment did not prove exact pane {pane}: {confirmed_panes}")
    return 0


def command_validate_robot_success(args: argparse.Namespace) -> int:
    response = load_json(Path(args.response))
    if response.get("success") is not True:
        raise ProtocolError(f"NTM robot response did not report success=true: {response.get('error', '')}")
    return 0


def sanitized_environment() -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in INJECTION_ENV or SENSITIVE_ENV_RE.search(key):
            continue
        result[key] = value
    result["CI"] = "1"
    return result


def verification_snapshot(project: Path) -> dict[str, Any]:
    staged = git(project, "diff", "--cached", "--name-only", "-z", "--").stdout
    return {
        "repository": repo_snapshot(project),
        "protectedFingerprints": protected_fingerprints(project),
        "gitMetadataFingerprints": git_metadata_fingerprints(project),
        "stagedPaths": sorted(
            value.decode("utf-8", "surrogateescape") for value in staged.split(b"\0") if value
        ),
    }


def run_verification(project: Path, command: dict[str, Any]) -> dict[str, Any]:
    argv = command["argv"]
    timeout = command["timeoutSeconds"]
    before = verification_snapshot(project)
    started = time.monotonic()
    timed_out = False
    launch_error: str | None = None
    return_code: int | None = None
    with tempfile.TemporaryFile() as output_handle:
        try:
            process = subprocess.Popen(
                argv,
                cwd=project,
                env=sanitized_environment(),
                stdin=subprocess.DEVNULL,
                stdout=output_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            launch_error = str(error)
        else:
            try:
                process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
            return_code = process.returncode
        output_handle.seek(0)
        output = output_handle.read(MAX_OUTPUT_BYTES + 1)
    if launch_error:
        output = launch_error.encode("utf-8", "replace")
    after = verification_snapshot(project)
    truncated = len(output) > MAX_OUTPUT_BYTES
    bounded_output = output[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")
    mutation = before != after
    return {
        "argv": argv,
        "timeoutSeconds": timeout,
        "exitCode": return_code,
        "timedOut": timed_out,
        "durationSeconds": round(time.monotonic() - started, 3),
        "output": bounded_output,
        "outputTruncated": truncated,
        "repositoryMutated": mutation,
        "beforeSnapshotSha256": sha256_bytes(json.dumps(before, sort_keys=True).encode()),
        "afterSnapshotSha256": sha256_bytes(json.dumps(after, sort_keys=True).encode()),
        "passed": return_code == 0 and not timed_out and not mutation and launch_error is None,
    }


def command_final_gate(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    validation_path = Path(args.validation).resolve()
    output_path = Path(args.output).resolve()
    state = load_json(state_path)
    plan_path, _, validation = assert_state_seal(state, validation_path)
    plan = load_json(plan_path)
    baseline_path = Path(state["baselinePath"])
    if file_sha256(baseline_path) != state["baselineSha256"]:
        raise ProtocolError("baseline artifact bytes changed")
    baseline = load_json(baseline_path)
    project = Path(state["project"])
    failures: list[str] = []
    receipts = state.get("receipts", {})
    for worker in WORKER_IDS:
        if receipts.get(worker, {}).get("status") != "COMPLETE":
            failures.append(f"missing COMPLETE receipt for {worker}")
    current_head = git_head(project)
    if current_head != baseline["gitHead"]:
        failures.append("Git HEAD changed; commits are forbidden during the workflow")
    if git_metadata_fingerprints(project) != baseline["gitMetadataFingerprints"]:
        failures.append("repository-local Git metadata changed during the workflow")
    staged_paths = [
        value.decode("utf-8", "surrogateescape")
        for value in git(project, "diff", "--cached", "--name-only", "-z", "--").stdout.split(b"\0")
        if value
    ]
    if staged_paths:
        failures.append(f"Git index contains staged changes: {sorted(staged_paths)}")
    current_protected = protected_fingerprints(project)
    if current_protected != baseline["protectedFingerprints"]:
        failures.append("a protected ignored/secret/database path changed")
    paths = changed_paths(project)
    out_of_scope = [
        path
        for path in paths
        if not any(scope_contains(scope, path) for scope in validation["unionWriteScope"])
    ]
    protected_changes = [path for path in paths if looks_protected(path)]
    if out_of_scope:
        failures.append(f"changed paths outside sealed union scope: {out_of_scope}")
    if protected_changes:
        failures.append(f"protected paths changed: {protected_changes}")

    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    for worker in plan["workers"]:
        for command in worker["verification"]:
            key = json.dumps(command, sort_keys=True)
            if key not in seen:
                seen.add(key)
                commands.append(command)
    for command in plan["finalVerification"]:
        key = json.dumps(command, sort_keys=True)
        if key not in seen:
            seen.add(key)
            commands.append(command)

    internal_diff = subprocess.run(
        ["git", "-C", str(project), "diff", "--check"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    checks: list[dict[str, Any]] = [
        {
            "argv": ["git", "diff", "--check"],
            "exitCode": internal_diff.returncode,
            "output": internal_diff.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
            "passed": internal_diff.returncode == 0,
        }
    ]
    if internal_diff.returncode != 0:
        failures.append("git diff --check failed")
    if not failures:
        for command in commands:
            result = run_verification(project, command)
            checks.append(result)
            if not result["passed"]:
                failures.append(f"verification failed: {json.dumps(command['argv'])}")
                break

    final_snapshot = verification_snapshot(project)
    final_repository = final_snapshot["repository"]
    final_head = final_repository["head"]
    final_paths = final_repository["paths"]
    final_staged_paths = final_snapshot["stagedPaths"]
    final_out_of_scope = [
        path
        for path in final_paths
        if not any(scope_contains(scope, path) for scope in validation["unionWriteScope"])
    ]

    def add_final_failure(message: str) -> None:
        if message not in failures:
            failures.append(message)

    if final_head != baseline["gitHead"]:
        add_final_failure("Git HEAD changed; commits are forbidden during the workflow")
    if final_snapshot["gitMetadataFingerprints"] != baseline["gitMetadataFingerprints"]:
        add_final_failure("repository-local Git metadata changed during the workflow")
    if final_staged_paths:
        add_final_failure(f"Git index contains staged changes: {final_staged_paths}")
    if final_snapshot["protectedFingerprints"] != baseline["protectedFingerprints"]:
        add_final_failure("a protected ignored/secret/database path changed")
    if final_out_of_scope:
        add_final_failure(f"changed paths outside sealed union scope: {final_out_of_scope}")
    final_protected_changes = [path for path in final_paths if looks_protected(path)]
    if final_protected_changes:
        add_final_failure(f"protected paths changed: {final_protected_changes}")

    evidence = {
        "protocolVersion": PROTOCOL_VERSION,
        "createdAt": utc_now(),
        "passed": not failures,
        "semanticReview": "intentionally omitted",
        "planSha256": validation["planSha256"],
        "validationSha256": file_sha256(validation_path),
        "baselineSha256": file_sha256(baseline_path),
        "gitHead": final_head,
        "changedPaths": final_paths,
        "stagedPaths": final_staged_paths,
        "outOfScopePaths": final_out_of_scope,
        "finalSnapshotSha256": sha256_bytes(json.dumps(final_snapshot, sort_keys=True).encode()),
        "failures": failures,
        "checks": checks,
    }
    write_new_json(output_path, evidence)
    with locked_state(state_path) as locked:
        assert_state_seal(locked, validation_path)
        if locked["finalEvidence"] is not None:
            raise ProtocolError("final evidence is already recorded")
        locked["finalEvidence"] = {
            "path": str(output_path),
            "sha256": file_sha256(output_path),
            "passed": evidence["passed"],
            "recordedAt": utc_now(),
        }
    print(json.dumps({"passed": evidence["passed"], "failures": failures}))
    return 0 if evidence["passed"] else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser("preflight")
    preflight.add_argument("--project", required=True)
    preflight.add_argument("--output", required=True)
    preflight.set_defaults(handler=command_preflight)

    init = commands.add_parser("init-state")
    init.add_argument("--state", required=True)
    init.add_argument("--run-id", required=True)
    init.add_argument("--task-file", required=True)
    init.add_argument("--baseline", required=True)
    init.add_argument("--orchestrator-model", required=True)
    init.add_argument("--worker-model", required=True)
    init.set_defaults(handler=command_init_state)

    validate = commands.add_parser("validate-plan")
    validate.add_argument("--project", required=True)
    validate.add_argument("--task-file", required=True)
    validate.add_argument("--plan", required=True)
    validate.add_argument("--output", required=True)
    validate.set_defaults(handler=command_validate_plan)

    seal = commands.add_parser("seal")
    seal.add_argument("--state", required=True)
    seal.add_argument("--plan", required=True)
    seal.add_argument("--validation", required=True)
    seal.set_defaults(handler=command_seal)

    claim = commands.add_parser("claim-workers")
    claim.add_argument("--state", required=True)
    claim.add_argument("--validation", required=True)
    claim.set_defaults(handler=command_claim_workers)

    register = commands.add_parser("register-workers")
    register.add_argument("--state", required=True)
    register.add_argument("--validation", required=True)
    register.add_argument("--session", required=True)
    register.add_argument("--worker-model", required=True)
    register.add_argument("--manifest", required=True)
    register.add_argument("--tmux-panes", required=True)
    register.add_argument("--output", required=True)
    register.set_defaults(handler=command_register_workers)

    render = commands.add_parser("render-briefs")
    render.add_argument("--state", required=True)
    render.add_argument("--validation", required=True)
    render.add_argument("--output-dir", required=True)
    render.set_defaults(handler=command_render_briefs)

    record = commands.add_parser("record-worker")
    record.add_argument("--state", required=True)
    record.add_argument("--validation", required=True)
    record.add_argument("--worker", required=True)
    record.add_argument("--status", choices=("COMPLETE", "BLOCKED"), required=True)
    record.add_argument("--summary", required=True)
    record.set_defaults(handler=command_record_worker)

    wave = commands.add_parser("wave-status")
    wave.add_argument("--state", required=True)
    wave.add_argument("--workers", required=True)
    wave.set_defaults(handler=command_wave_status)

    send = commands.add_parser("validate-send")
    send.add_argument("--response", required=True)
    send.add_argument("--session", required=True)
    send.add_argument("--pane", required=True)
    send.set_defaults(handler=command_validate_send)

    robot = commands.add_parser("validate-robot-success")
    robot.add_argument("--response", required=True)
    robot.set_defaults(handler=command_validate_robot_success)

    gate = commands.add_parser("final-gate")
    gate.add_argument("--state", required=True)
    gate.add_argument("--validation", required=True)
    gate.add_argument("--output", required=True)
    gate.set_defaults(handler=command_final_gate)
    return root


def main() -> int:
    try:
        arguments = parser().parse_args()
        return int(arguments.handler(arguments))
    except ProtocolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
