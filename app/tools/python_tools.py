import ast
import os
import resource
import signal
import subprocess
import sys
import time

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# Where the model's scripts land. Inside the app so you and the model share
# the same eye on side effects; delete the dir anytime.
WORKSPACE_ROOT = os.path.join(os.getcwd(), "python_workspace")

# Hard resource ceilings applied inside the child before exec. Tunes:
#   memory  2 GB   - big-but-finite lists are fine, 16 GB leaks are not
#   fsize   64 MB  - disk-filling write loops stop
#   nproc   512    - fork bombs stop (per-user in-container; 512 is plenty for
#                        normal scripts and still lets the harness breathe)
#   nofile  256    - fd-exhaustion loops stop
#   cpu     2 * timeout (set per-call)
_MEM_LIMIT = 2 * 1024 ** 3
_FSIZE_LIMIT = 64 * 1024 ** 2
_NPROC_LIMIT = 512
_NOFILE_LIMIT = 256
_OUTPUT_CAP = 64 * 1024  # bytes of stdout+stderr we will keep in the report


def _apply_limits(cpu_seconds: int):
    """Runs in the child after fork(), before exec() - kernel-enforced."""
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (_MEM_LIMIT, _MEM_LIMIT))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FSIZE_LIMIT, _FSIZE_LIMIT))
    resource.setrlimit(resource.RLIMIT_NPROC, (_NPROC_LIMIT, _NPROC_LIMIT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (_NOFILE_LIMIT, _NOFILE_LIMIT))


def _truncate(data: bytes, cap: int = _OUTPUT_CAP) -> str:
    """Keep head + tail within a byte cap so a 2 GB log can't blow the context."""
    text = data.decode("utf-8", errors="replace")
    if len(text) <= cap:
        return text, False
    half = cap // 2
    head = text[:half]
    tail = text[-half:]
    omitted = len(text) - len(head) - len(tail)
    return head + f"\n... [{omitted} chars truncated ...]\n" + tail, True


def _execute_isolated(
    script_path: str,
    cwd: str,
    args: list[str],
    stdin: str,
    timeout: int,
    workspace_label: str,
) -> str:
    """Shared execution core: run an EXISTING file in an isolated child process.

    This is the single place that owns the isolation behavior - kernel rlimits
    via _apply_limits, its own process group (start_new_session) so a timeout
    kills the whole tree, and output truncation via _truncate. Both run_python
    (after it writes a fresh main.py) and run_file (an existing file) funnel
    through here, so their isolation is identical and stays in one code path.

    Args:
      script_path:      the .py file to execute (must already exist on disk).
      cwd:              working directory for the child (where relative paths resolve).
      args:             CLI args, exposed to the script as sys.argv[1:].
      stdin:            text to feed the script's standard input.
      timeout:          wall-clock seconds before the whole group is killed.
      workspace_label:  human label for the report's `workspace:` line.
    """
    timeout = max(1, int(timeout))
    cmd = [sys.executable, "-I", script_path, *list(args or [])]

    proc = None
    timed_out = False
    stdout_b = b""
    stderr_b = b""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # own process group -> killpg reaches children
            preexec_fn=lambda: _apply_limits(cpu_seconds=2 * timeout),
        )
        try:
            stdout_b, stderr_b = proc.communicate(
                input=(stdin or "").encode("utf-8"), timeout=timeout
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            # kill the whole group (script + anything it spawned)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                stdout_b, stderr_b = proc.communicate(timeout=5)
            except Exception:
                stdout_b, stderr_b = b"", b""
            proc.wait(timeout=5)
    except Exception as e:
        return (
            "=== RUN RESULT ===\n"
            "status: FAIL\n"
            f"reason: failed to launch subprocess: {e}\n"
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    exit_code = proc.returncode if proc is not None else -1

    stdout_s, out_trunc = _truncate(stdout_b or b"")
    stderr_s, err_trunc = _truncate(stderr_b or b"")
    truncated = out_trunc or err_trunc

    status = "OK" if (exit_code == 0 and not timed_out) else "FAIL"
    if timed_out:
        status_detail = f"FAIL (timeout after {timeout}s; process group killed)"
    else:
        status_detail = f"{'OK' if exit_code == 0 else 'FAIL'} (exit {exit_code})"

    lines = [
        "=== RUN RESULT ===",
        f"status: {status_detail}",
        f"duration_ms: {duration_ms}",
        f"workspace: {workspace_label}",
        "stdout:",
        stdout_s.strip(),
        "stderr:",
        stderr_s.strip(),
        f"truncated: {'yes' if truncated else 'no'}",
    ]
    return "\n".join(lines)


class RunPythonInput(BaseModel):
    code: str = Field(description="The Python source code snippet to run.")
    args: list[str] = Field(default_factory=list, description="Optional list of CLI arguments.")
    stdin: str = Field(default="", description="Optional text to feed into standard input.")
    timeout: int = Field(default=10, description="Seconds before the process is killed.")


@tool(args_schema=RunPythonInput)
def run_python(
    code: str,
    args: list[str] = [],
    stdin: str = "",
    timeout: int = 10,
) -> str:
    """
    Actually EXECUTE a Python snippet in an isolated child process and report
    the real result (stdout, stderr, exit code, timing). Use validate_python
    first for a free static check; use this when you need the code to run.

    Isolation (the boundary, honestly scoped):
      - separate process, no shared state with the harness
      - kernel rlimits: 2 GB memory, 64 MB max file size, 512 processes,
        256 open files, CPU bounded by timeout
      - wall-clock timeout (default 10 s); the whole process group is killed
        on exceed, including any child processes this script spawned
    It stops RUNAWAY behavior (infinite loops, memory/disk/fd blowups). It is
    not a security sandbox against adversarial code (that needs a container).

    Args:
      code:     the Python source to run. Its top level IS the entry point.
      args:     optional list of CLI args, available as sys.argv[1:] in the code.
      stdin:    optional text to feed the script's standard input.
      timeout:  wall-clock seconds before the whole process group is killed.

    Returns a === RUN RESULT === block with status OK / FAIL, duration_ms,
    the workspace dir (inspect it with read_file), stdout, and stderr.
    """
    code = (code or "").strip()
    if not code:
        return (
            "=== RUN RESULT ===\n"
            "status: FAIL\n"
            "reason: no code provided (empty)\n"
        )

    # 1) fresh workspace dir per run, write the script, run with cwd there
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    run_dir = os.path.join(WORKSPACE_ROOT, time.strftime("%Y%m%d-%H%M%S-") + str(int(time.time() * 1e6) % 1_000_000))
    os.makedirs(run_dir, exist_ok=True)
    main_path = os.path.join(run_dir, "main.py")
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(code)

    # 2) run it in place via the shared isolation core
    return _execute_isolated(main_path, run_dir, args, stdin, timeout, run_dir)


class RunFileInput(BaseModel):
    file_path: str = Field(description="Path to an existing Python file to execute in place.")
    args: list[str] = Field(default_factory=list, description="Optional list of CLI arguments.")
    stdin: str = Field(default="", description="Optional text to feed into standard input.")
    timeout: int = Field(default=10, description="Seconds before the process is killed.")


@tool(args_schema=RunFileInput)
def run_file(
    file_path: str,
    args: list[str] = [],
    stdin: str = "",
    timeout: int = 10,
) -> str:
    """
    Execute a previously-written .py file from disk using the shared
    _execute_isolated helper. Reuses the exact same isolation as run_python:
    kernel rlimits (_apply_limits), process-group kill on timeout, and output
    truncation (_truncate). Unlike run_python, the file is NOT recreated - it
    is run as-is, so edits made with edit_file take effect on the next call.

    This is the 're-run the same file' half of the iterative coding-and-testing
    loop: create once with write_file, patch with edit_file, re-run with run_file.

    Args:
      file_path: path to the .py file to run (created with write_file or
                 edited with edit_file).
      args:      optional list of CLI args, available as sys.argv[1:] in the code.
      stdin:     optional text to feed the script's standard input.
      timeout:   wall-clock seconds before the whole process group is killed.

    Returns the same === RUN RESULT === block as run_python.
    """
    if not file_path or not str(file_path).strip():
        return (
            "=== RUN RESULT ===\n"
            "status: FAIL\n"
            "reason: no file_path provided (empty)\n"
        )
    target = os.path.abspath(os.path.expanduser(str(file_path)))
    if not os.path.isfile(target):
        return (
            "=== RUN RESULT ===\n"
            "status: FAIL\n"
            f"reason: {file_path} not found (use write_file to create it first)\n"
        )
    return _execute_isolated(target, os.path.dirname(target), args, stdin, timeout, os.path.dirname(target))


class EditFileInput(BaseModel):
    path: str = Field(description="Path to the existing file to edit.")
    old: str = Field(description="Exact text to find in the file (must match, including whitespace).")
    new: str = Field(description="Replacement text (may be empty to delete the match).")
    replace_all: bool = Field(default=False, description="Replace every occurrence instead of just the first.")


@tool(args_schema=EditFileInput)
def edit_file(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> str:
    """
    Surgically edit an EXISTING file WITHOUT resupplying the whole thing.
    Finds the first occurrence of `old` (or all of them if replace_all) and
    replaces it with `new`. This is the 'mutate' half of the iterative
    coding-and-testing loop: create once with write_file, then patch with
    edit_file and re-run with run_file - no need to reopen or re-paste the file.

    Args:
      path:        path to the file to edit (must already exist).
      old:         exact text to find (must match, including whitespace).
      new:         replacement text (empty string deletes the match).
      replace_all: replace every occurrence instead of just the first.

    Returns a short confirmation, or a clear error if `old` is not found or is
    ambiguous (multiple matches with replace_all=False).
    """
    if not path or not str(path).strip():
        return "Error: 'path' is required."
    if old is None or old == "":
        return "Error: 'old' must be a non-empty string to locate the edit."
    target = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(target):
        return f"Error: {path} does not exist. Use write_file to create it first."
    try:
        with open(target, encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return f"Error reading {path}: {e}"

    count = text.count(old)
    if count == 0:
        return "Error: `old` not found in file (nothing to replace). Check whitespace/indentation."
    if count > 1 and not replace_all:
        return (
            f"Error: `old` is ambiguous ({count} matches). "
            "Pass replace_all=True or a more specific `old`."
        )

    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception as e:
        return f"Error writing {path}: {e}"
    replaced = count if replace_all else 1
    return f"Edited {target}: replaced {replaced} occurrence(s)."


class RunShellInput(BaseModel):
    command: str = Field(description="The shell command to execute (e.g., \"ls -la /tmp\", \"grep -r pattern .\").")
    working_directory: str = Field(default="", description="Absolute path to the directory in which to run the command (default: workspace dir).")
    timeout: int = Field(default=10, description="Wall-clock seconds before the whole process group is killed (default: 10).")
    env: dict[str, str] = Field(default_factory=dict, description="Optional environment variables to set for the command (key-value pairs).")
    stdin: str = Field(default="", description="Optional text to feed into the command's standard input.")


@tool(args_schema=RunShellInput)
def run_shell(
    command: str,
    working_directory: str = "",
    timeout: int = 10,
    env: dict[str, str] = {},
    stdin: str = "",
) -> str:
    """
    Execute a Linux shell command (bash/sh) in an isolated child process and report
    the real result (stdout, stderr, exit code, timing).

    Isolation (the boundary, honestly scoped):
      - separate process, no shared state with the harness
      - kernel rlimits: 2 GB memory, 64 MB max file size, 512 processes,
        256 open files, CPU bounded by timeout
      - wall-clock timeout (default 10 s); the whole process group is killed
        on exceed, including any child processes the command spawned
    It stops RUNAWAY behavior (infinite loops, memory/disk/fd blowups). It is
    not a security sandbox against adversarial code (that needs a container).

    Args:
      command:            the shell command to execute (e.g., "ls -la /tmp").
      working_directory:  absolute path to run the command from (default: cwd).
      timeout:            wall-clock seconds before the whole process group is killed.
      env:                optional environment variables to set for the command.
      stdin:              optional text to feed the command's standard input.

    Returns a === RUN RESULT === block with status OK / FAIL, duration_ms,
    stdout, stderr, and exit code.
    """
    command = (command or "").strip()
    if not command:
        return (
            "=== RUN RESULT ===\n"
            "status: FAIL\n"
            "reason: no command provided (empty)\n"
        )

    timeout = max(1, int(timeout))
    cwd = working_directory or os.getcwd()

    # Merge env: start from os.environ, overlay user-supplied vars
    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    cmd = ["bash", "-c", command]

    proc = None
    timed_out = False
    stdout_b = b""
    stderr_b = b""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=full_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # own process group -> killpg reaches children
            preexec_fn=lambda: _apply_limits(cpu_seconds=2 * timeout),
        )
        try:
            stdout_b, stderr_b = proc.communicate(
                input=(stdin or "").encode("utf-8"), timeout=timeout
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            # kill the whole group (command + anything it spawned)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                stdout_b, stderr_b = proc.communicate(timeout=5)
            except Exception:
                stdout_b, stderr_b = b"", b""
            proc.wait(timeout=5)
    except Exception as e:
        return (
            "=== RUN RESULT ===\n"
            "status: FAIL\n"
            f"reason: failed to launch subprocess: {e}\n"
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    exit_code = proc.returncode if proc is not None else -1

    stdout_s, out_trunc = _truncate(stdout_b or b"")
    stderr_s, err_trunc = _truncate(stderr_b or b"")
    truncated = out_trunc or err_trunc

    status = "OK" if (exit_code == 0 and not timed_out) else "FAIL"
    if timed_out:
        status_detail = f"FAIL (timeout after {timeout}s; process group killed)"
    else:
        status_detail = f"{'OK' if exit_code == 0 else 'FAIL'} (exit {exit_code})"

    lines = [
        "=== RUN RESULT ===",
        f"status: {status_detail}",
        f"duration_ms: {duration_ms}",
        f"working_directory: {cwd}",
        "stdout:",
        stdout_s.strip(),
        "stderr:",
        stderr_s.strip(),
        f"truncated: {'yes' if truncated else 'no'}",
    ]
    return "\n".join(lines)


# Modules that are network / process / exec-related. Flagged (not blocked) so
# the model is aware; the run tool is where you'd hard-enforce if you want.
DANGEROUS_IMPORTS = {
    "socket", "subprocess", "http", "urllib", "requests",
    "importlib", "ctypes", "asyncio", "multiprocessing", "threading", "ssl",
}



@tool
def validate_python(code: str, filename: str = "snippet.py") -> str:
    """
    Statically validate a Python snippet WITHOUT executing it - safe to call on
    every draft, no side effects.

    Checks:
      - syntax / byte-compile (compile() stops before running)
      - imports present
      - risky imports and dynamic calls (eval/exec/importlib) as warnings

    Returns a readable report with `status: OK` or `status: FAIL`.
    Warnings are heads-ups, not failures. To actually execute, use run_python.
    """
    code = (code or "").strip()
    if not code:
        return (
            "=== VALIDATION ===\n"
            "status: FAIL\n"
            "reason: no code provided (empty)\n"
        )

    imports: list[str] = []
    warnings: list[str] = []
    lines = ["=== VALIDATION ===", f"filename: {filename}"]

    # 1) syntax / byte-compile — NEVER runs the code.
    try:
        tree = ast.parse(code, filename=filename)
    except SyntaxError as e:
        return "\n".join([
            *lines,
            "status: FAIL",
            "syntax: ERROR",
            f"  line {e.lineno}: {e.msg or 'invalid syntax'}",
            "hint: fix the syntax error, then re-validate.",
        ]) + "\n"

    lines.append("syntax: OK")

    # 2) walk the AST for imports + dynamic calls.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                if alias.name.split(".")[0] in DANGEROUS_IMPORTS:
                    warnings.append(f"line {node.lineno}: imports sensitive module '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if node.module:
                imports.append(node.module)
                if top in DANGEROUS_IMPORTS:
                    warnings.append(f"line {node.lineno}: imports sensitive module '{node.module}'")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in ("eval", "exec"):
                warnings.append(f"line {node.lineno}: uses {f.id}() - dynamic, can't be fully validated")

    # 3) assemble report. Warnings never fail the run.
    lines.append("status: OK")
    lines.append("imports: " + (", ".join(sorted(set(imports))) if imports else "(none)"))
    if warnings:
        lines.append("warnings:")
        lines.extend("  " + w for w in warnings)
    else:
        lines.append("warnings: (none)")
    lines.append("note: static checks only - nothing was executed. Use run_python to actually run it.")
    return "\n".join(lines) + "\n"
