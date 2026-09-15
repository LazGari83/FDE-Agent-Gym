"""
gym_steps — the step-type registry `gym_run.py` executes.

The engine (`gym_run.py`) knows nothing about any Fabric item type. Everything it needs to
validate and run a step is declared here, per step type, by the topic module that owns it:

    common.py     topic-agnostic steps: `provision` (local fixture script) and `validate`
                  (the task's acceptance test — every plan's single terminal step)
    lakehouse.py  the lakehouse family, proven by the AG-LAK reps; steps take an
                  optional `workspace:` alias for multi-workspace plans
    cicd.py       the cicd family's core — workspace lifecycle + git integration
                  (workspace · github-repo · git-bind · git-commit · git-update ·
                  git-merge) — plus the helpers its sibling modules share
    cicd_publish.py / cicd_matter.py / cicd_engagement.py / cicd_featureset.py
                  the rest of the cicd family: the build-publish lane with its record
                  steps, and the matter / engagement / feature-set lifecycles — 22 step
                  types across the five modules, proven by the AG-CICD-013…019 live
                  engine runs (AG-CICD-019's plan alone ran feature-set-standup 3x)
    azure_app.py  the azure-app family (app-write · app-serve · app-probe) — the only
                  family whose steps are ALL `local`, because an azure-app task's Fabric
                  half is its fixture and its graded half is an application. `app-serve`
                  is the one step type that holds a child process alive ACROSS later
                  steps; its teardown is guaranteed outside the step (atexit + signal
                  handlers + PR_SET_PDEATHSIG), which is why that module owns the only
                  process-lifetime code here. Named by the AG-APP-004/-005/-006 reps,
                  which converged on these three independently.
    pipelines.py  the pipelines family (pipeline · pipeline-patch · pipeline-schedule ·
                  pipeline-run · connection · variable-library) — a Fabric pipeline carries
                  no separate Dataset or Linked Service item, so the whole build is one
                  authored `pipeline-content.json` and the steps deliver and run it
    ontology.py   ontology · ontology-definition · graph-refresh — one module for both the
                  ontology and graph gyms, since an ontology auto-provisions its graph
    openmirror.py the open-mirroring lane (mirrored-database, the landing-zone steps, the
                  mirror waiters and controls)
    data_agent.py data-agent · data-agent-config · data-agent-publish

    Every one of them landed with the live re-run that proved it — each executor a thin
    composition of the already-proven `code/` client. Never add a step type speculatively.

A step type declares its whole contract, so plan validation is generic in the engine:

    required   plan fields that must be present and truthy
    provides   resources the step leaves in the runner context (e.g. "lakehouse")
    needs      resources that must have been provided by an earlier step; a callable
               receiving the step dict, so a step can waive a need it satisfies itself
    check      optional hook returning extra structural errors for this step's fields
    terminal   True for the single step every plan must end with (validate)

Executors receive `(runner, step)` and return a one-line note for the telemetry. They use
`runner.context` (shared ids), `runner.cache` (memoised clients), `runner.task_dir`,
`runner.task_id`, and `runner.args` (CLI pass-through, e.g. --payload).
"""
import importlib
import json
import pkgutil
from dataclasses import dataclass


@dataclass(frozen=True)
class StepType:
    name: str
    run: callable                    # (runner, step) -> note str
    category: str                    # "fabric" (waiting on the platform) | "local"
    required: tuple = ()
    provides: tuple = ()
    needs: object = ()               # iterable, or callable (step) -> iterable
    check: object = None             # callable (step) -> list[str]
    label: object = None             # callable (step) -> display label
    terminal: bool = False

    def needs_for(self, step: dict) -> tuple:
        return tuple(self.needs(step)) if callable(self.needs) else tuple(self.needs)

    def label_for(self, step: dict) -> str:
        return step.get("label") or (self.label(step) if self.label else self.name)


REGISTRY: dict[str, StepType] = {}


def step(**kwargs):
    """Register a step executor: @step(name=..., category=..., ...)."""
    def register(fn):
        spec = StepType(run=fn, **kwargs)
        if spec.name in REGISTRY:
            raise ValueError(f"duplicate step type '{spec.name}'")
        REGISTRY[spec.name] = spec
        return fn
    return register


def resolve_ctx(runner, value):
    """Substitute `"$ctx:<key>"` strings with the runner-context value they name, recursively.

    This is how a later step consumes an earlier step's output — the gap AG-LAK-013 hit: its
    run-ledger notebook needed the job-instance ids `run-notebook` had collected, and no step
    could pass them on. A plan writes `{"value": "$ctx:job_instances", "type": "string"}` and
    the list arrives JSON-encoded (notebook parameters are scalars; the notebook json.loads it).
    Scalar context values pass through with their own type. An unknown key is a loud error —
    a silent empty string would run the notebook against nothing and fail at validate, later
    and worse.

    `"$env:<VAR>"` resolves from the process environment instead. It exists for one reason:
    a step that provisions a credentialed resource — an SPN-authenticated connection, say —
    would otherwise need the secret written into a plan file, and a plan is an artifact the
    agent writes to a scratch directory. A missing variable is the same loud error, because a
    credential silently resolving to empty produces a connection that authenticates as
    nobody and fails much later with an unrelated-looking permission error.
    """
    if isinstance(value, str) and value.startswith("$ctx:"):
        key = value[5:]
        if key not in runner.context:
            raise RuntimeError(f"'$ctx:{key}' names nothing in the runner context "
                               f"(known: {sorted(runner.context)})")
        resolved = runner.context[key]
        return resolved if isinstance(resolved, (str, int, float, bool)) else json.dumps(resolved)
    if isinstance(value, str) and value.startswith("$env:"):
        name = value[5:]
        import os
        if not os.environ.get(name):
            raise RuntimeError(f"'$env:{name}' is unset or empty in the environment — "
                               f"a credential that resolves to nothing authenticates as "
                               f"nobody and fails later as a permission error")
        return os.environ[name]
    if isinstance(value, str) and "${ctx:" in value:
        # Interpolation, for the case a whole-string `$ctx:` cannot express: a value that
        # EMBEDS an id rather than being one. AG-PIP-007 needs a OneLake ADLS path
        # "${ctx:lakehouse_workspace_id}/${ctx:lakehouse_id}/Files/prod", which is neither a
        # bare context value nor something a plan can hard-code without naming a GUID.
        import re

        def _sub(match):
            key = match.group(1)
            if key not in runner.context:
                raise RuntimeError(f"'${{ctx:{key}}}' names nothing in the runner context "
                                   f"(known: {sorted(runner.context)})")
            return str(runner.context[key])

        return re.sub(r"\$\{ctx:([^}]+)\}", _sub, value)
    if isinstance(value, dict):
        return {k: resolve_ctx(runner, v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_ctx(runner, v) for v in value]
    return value


def load_all() -> dict[str, StepType]:
    """Import every module in this package (each registers its step types) and return
    the registry. Idempotent."""
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
    return REGISTRY
