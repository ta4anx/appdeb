"""Subprocess helper with streaming output, timeout, and cancellation."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

TAIL_LINES = 200


class BackendError(Exception):
    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.output = output

    def __str__(self) -> str:
        return self.message


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    output: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    args: Sequence[str],
    *,
    timeout: float | None = 300,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike | None = None,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    check: bool = False,
) -> CommandResult:
    command = [str(a) for a in args]
    full_env = {**os.environ, "LC_ALL": "C"}
    if env:
        full_env.update(env)
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=full_env,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise BackendError(f"command not found: {command[0]}", str(exc)) from exc
    except OSError as exc:
        raise BackendError(f"cannot start: {command[0]}", str(exc)) from exc

    lines: list[str] = []
    killed_reason: str | None = None
    deadline = None
    if timeout:
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.daemon = True
        timer.start()
    else:
        timer = None

    assert proc.stdout is not None
    while True:
        if cancel is not None and cancel.is_set():
            killed_reason = "cancelled"
        elif deadline is not None and deadline.is_set():
            killed_reason = "timeout"
        if killed_reason:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        line = proc.stdout.readline()
        if line == "":
            if proc.poll() is not None:
                break
            continue
        text = line.rstrip("\n")
        lines.append(text)
        if len(lines) > TAIL_LINES:
            del lines[: len(lines) - TAIL_LINES]
        if progress is not None:
            try:
                progress(text)
            except Exception:  # noqa: BLE001 - progress callbacks must never abort a command
                pass

    returncode = proc.wait()
    if timer is not None:
        timer.cancel()
    output = "\n".join(lines)
    if killed_reason == "cancelled":
        raise BackendError("cancelled", output)
    if killed_reason == "timeout":
        raise BackendError("timed out", output)
    result = CommandResult(args=command, returncode=returncode, output=output, lines=lines)
    if check and returncode != 0:
        raise BackendError(f"command failed: {command[0]}", output)
    return result


def capture(args: Sequence[str], *, timeout: float = 60, env: Mapping[str, str] | None = None) -> CommandResult:
    return run(args, timeout=timeout, env=env)
