"""Tier 1+ subprocess sandbox: stripped env + ulimits + timeout.

Per plan §7. Railway empirically blocks user namespaces; this is what's
possible there. Real isolation (Modal, E2B) is deferred to v1.

Public API: `run_user_code(source, entry_point, payload, ...)` returns the
parsed JSON output, or raises SandboxError.
"""

from __future__ import annotations

import asyncio
import json
import resource
import shutil
import subprocess
import tempfile
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scryer.server.services.errors import ScryerError


class SandboxError(ScryerError):
    """User code failed: timeout, exit code, malformed output, etc."""


@dataclass(frozen=True)
class SandboxResult:
    output: dict[str, Any]
    duration_ms: int
    stderr: str


_DEFAULT_TIMEOUT_S = 300
_DEFAULT_MEMORY_MB = 512
_DEFAULT_CPU_S = 60


def _setrlimits(memory_mb: int, cpu_s: int) -> None:
    """Best-effort. RLIMIT_AS is Linux-only; on macOS we get CPU only.
    Failures (incl. macOS RLIMIT_AS) are swallowed — not a security wall on
    dev. Production runs on Linux/Railway where both apply."""
    soft_mem = memory_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (soft_mem, soft_mem))
    except (OSError, ValueError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    except (OSError, ValueError):
        pass


_RUNNER = textwrap.dedent("""
    import json, os, sys, traceback
    # Redirect stdout so user prints / library noise can't corrupt envelope.
    # Envelope written to the file path passed in argv[1].
    envelope_path = sys.argv[1]
    sys.stdout = sys.stderr
    src = sys.stdin.readline()
    payload_line = sys.stdin.readline()
    meta = json.loads(src)
    src_text = meta["source"]
    entry = meta["entry"]
    payload = json.loads(payload_line)
    try:
        ns: dict = {}
        exec(src_text, ns)
        if entry not in ns:
            raise NameError(f"entry {entry!r} not defined in source")
        fn = ns[entry]
        try:
            result = fn(**payload)
        except TypeError as exc:
            raise TypeError(
                f"{entry}{tuple(payload.keys())} signature mismatch: {exc}"
            ) from exc
        envelope = {"ok": True, "result": result}
    except Exception as exc:
        envelope = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
    with open(envelope_path, "w") as f:
        f.write(json.dumps(envelope))
""")


async def run_user_code(
    *,
    source: str,
    entry: str,
    payload: dict[str, Any],
    env: dict[str, str] | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    memory_mb: int = _DEFAULT_MEMORY_MB,
    cpu_s: int = _DEFAULT_CPU_S,
) -> SandboxResult:
    """Execute user-supplied Python source in a stripped subprocess."""
    workdir = Path(tempfile.mkdtemp(prefix=f"scryer_run_{uuid.uuid4().hex[:8]}_"))
    runner_path = workdir / "_runner.py"
    runner_path.write_text(_RUNNER)

    sub_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "PYTHONUNBUFFERED": "1",
        # Determinism: stable hash seed makes set/dict ordering reproducible
        # across runs of the same Scorer; .pyc disabled so source changes
        # take immediate effect (no stale bytecode-cache surprises).
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if env:
        sub_env.update(env)

    envelope_path = workdir / "envelope.json"
    start = asyncio.get_event_loop().time()
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            str(runner_path),
            str(envelope_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workdir),
            env=sub_env,
            preexec_fn=lambda: _setrlimits(memory_mb, cpu_s),
        )
        stdin_blob = (
            json.dumps({"source": source, "entry": entry}) + "\n" + json.dumps(payload) + "\n"
        ).encode("utf-8")
        try:
            _stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_blob), timeout=timeout_s
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise SandboxError(f"User code exceeded timeout {timeout_s}s") from exc

        envelope_bytes = envelope_path.read_bytes() if envelope_path.exists() else b""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)
    stderr_str = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise SandboxError(f"User code exited {proc.returncode}: {stderr_str[:500]}")

    if not envelope_bytes:
        raise SandboxError(f"User code wrote no envelope (stderr: {stderr_str[:500]})")

    try:
        envelope = json.loads(envelope_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SandboxError(f"User code envelope not valid JSON: {exc}") from exc

    if not envelope.get("ok"):
        raise SandboxError(f"User code raised: {envelope.get('error')}")

    return SandboxResult(
        output=envelope["result"]
        if isinstance(envelope.get("result"), dict)
        else {"value": envelope.get("result")},
        duration_ms=duration_ms,
        stderr=stderr_str,
    )
