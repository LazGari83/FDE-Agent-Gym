"""Topic-agnostic step types: the fixture script and the acceptance test."""
import re
import subprocess
import sys
from pathlib import Path

from . import resolve_ctx, step

SKILL_DIR = Path(__file__).resolve().parents[1]
_TALLY_RE = re.compile(r"== (\d+) passed, (\d+) failed, (\d+) skipped ==")
# The acceptance test is a suite of live Fabric reads, so the ceiling is generous — but it is
# a ceiling. Without one a check that hangs on a call with no timeout of its own hangs the
# whole rep, and a circuit running unattended never reaches its next task.
VALIDATE_TIMEOUT = 3600


def _stream(cmd, timeout, **kwargs):
    """Run a child process, echoing its stdout LINE BY LINE as it arrives, and return
    (captured_stdout, returncode).

    Streamed rather than captured because every other step prints live (`gym_run.py` sets
    `flush=True` globally) and this one is the longest: fully buffering it means a member
    watching a rep sees nothing at all until the entire suite has finished. stderr is merged
    into the stream so the ordering a reader sees is the ordering that happened.
    """
    lines = []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, bufsize=1, encoding="utf-8", errors="replace",
                          **kwargs) as proc:
        try:
            for line in proc.stdout:
                lines.append(line)
                print(line.rstrip("\n"), flush=True)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
    return "".join(lines), proc.returncode


@step(name="provision", category="local", required=("script",),
      label=lambda s: f"provision fixture ({s['script']})")
def run_provision(runner, spec):
    """Run the task's local fixture script (provision.py), cwd = the task folder."""
    result = subprocess.run([sys.executable, spec["script"]], cwd=runner.task_dir,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{spec['script']} exited {result.returncode}: "
                           f"{(result.stderr or result.stdout)[-400:]}")
    return f"{spec['script']} exit 0"


@step(name="validate", category="fabric", terminal=True,
      label=lambda s: "validate (gym_validate.py)")
def run_validate(runner, spec):
    """Invoke the task's acceptance test. The engine's exit code follows this step's.

    `args` accept `$ctx:` references, so a plan passes an id an earlier step produced —
    `["--mirror-id", "$ctx:mirror_id"]` — rather than the validator re-resolving the item by
    display name. Skipping that lookup is what makes a validate step deterministic when two
    items in the workspace share a name prefix.

    The suite's output is STREAMED, not buffered: this is the longest step in a rep and the
    only one that used to go dark for its whole duration. `timeout` (default
    `VALIDATE_TIMEOUT`) bounds it, because a check blocking on a call with no timeout of its
    own would otherwise hold the rep — and an unattended circuit — open indefinitely.
    """
    cmd = [sys.executable, str(SKILL_DIR / "gym_validate.py"), runner.task_id,
           *[str(a) for a in resolve_ctx(runner, spec.get("args", []))]]
    timeout = spec.get("timeout", VALIDATE_TIMEOUT)
    try:
        output, returncode = _stream(cmd, timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"gym_validate.py did not finish within {timeout}s and was killed — the run has "
            f"NO acceptance-test verdict") from None
    match = _TALLY_RE.search(output)
    if match:
        runner.tally = tuple(int(x) for x in match.groups())
    if returncode != 0:
        raise RuntimeError(f"gym_validate.py exited {returncode}"
                           + (f" ({runner.tally[1]} check(s) failed)" if runner.tally else ""))
    if not runner.tally:
        return "exit 0"
    passed, failed, skipped = runner.tally
    # A skipped check is NOT one of the passes and must not vanish from the step's note
    # either: with the environment gate (`requiresEnv`) a rep can exit 0 with a delegated leg
    # parked, and "9/9 checks passed" would be the last place that fact could hide.
    return (f"{passed}/{passed + failed} checks passed"
            + (f", {skipped} skipped/parked — NOT passed" if skipped else ""))
