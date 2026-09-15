"""
AG-LAK-002 (Ravensworth) — source-system fixture.

Writes the calibration lab's instrument log to `source-data/calibration_run.csv`:
twenty rows, byte-identical on every run, because `validate.json`'s expected values are
computed from them.

The data is deliberately ORDINARY. This task is not about the data — it is about the
surfaces you drive Fabric through, and a fixture with traps in it would only distract.
Every expected number below is a plain aggregate any correct implementation produces,
which is the point: when two execution surfaces disagree, the fixture is never the
suspect.

This fixture stays OUTSIDE Fabric on purpose: the lakehouse does not exist yet when you
run this. Getting the file into `Files/raw/` is part of the build.

Run:  python provision.py          (from this folder)
Then: /execute-gym-rep AG-LAK-002
"""
import csv
from collections import Counter
from pathlib import Path

OUT_DIR = Path(__file__).parent / "source-data"

# run_id, instrument_id, technician, run_date, deviation_ppm, passed
RUNS = [
    ("RW-0001", "INST-A", "okafor",   "2026-04-01",  0.42, True),
    ("RW-0002", "INST-A", "okafor",   "2026-04-01",  0.51, True),
    ("RW-0003", "INST-A", "lindqvist", "2026-04-02", 1.88, False),
    ("RW-0004", "INST-A", "lindqvist", "2026-04-02", 0.33, True),
    ("RW-0005", "INST-B", "okafor",   "2026-04-02",  0.75, True),
    ("RW-0006", "INST-B", "okafor",   "2026-04-03",  0.64, True),
    ("RW-0007", "INST-B", "moreau",   "2026-04-03",  2.10, False),
    ("RW-0008", "INST-B", "moreau",   "2026-04-03",  0.28, True),
    ("RW-0009", "INST-B", "lindqvist", "2026-04-04", 0.95, True),
    ("RW-0010", "INST-C", "moreau",   "2026-04-04",  0.17, True),
    ("RW-0011", "INST-C", "moreau",   "2026-04-04",  0.22, True),
    ("RW-0012", "INST-C", "okafor",   "2026-04-05",  1.55, False),
    ("RW-0013", "INST-C", "okafor",   "2026-04-05",  0.49, True),
    ("RW-0014", "INST-C", "lindqvist", "2026-04-06", 0.38, True),
    ("RW-0015", "INST-D", "moreau",   "2026-04-06",  0.81, True),
    ("RW-0016", "INST-D", "moreau",   "2026-04-07",  0.60, True),
    ("RW-0017", "INST-D", "lindqvist", "2026-04-07", 0.44, True),
    ("RW-0018", "INST-D", "okafor",   "2026-04-08",  3.02, False),
    ("RW-0019", "INST-D", "okafor",   "2026-04-08",  0.29, True),
    ("RW-0020", "INST-D", "moreau",   "2026-04-08",  0.36, True),
]

HEADER = ["run_id", "instrument_id", "technician", "run_date", "deviation_ppm", "passed"]

# The four runtime facts the lab's standard requires an onboarding report to establish.
# These names are the CONTRACT — validate.json asserts this exact set in lab.runtime_facts.
REQUIRED_FACTS = ("python_version", "spark_version", "runtime_jsonschema", "default_lakehouse")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    target = OUT_DIR / "calibration_run.csv"

    # newline="" so csv writes \r\n itself; newline handling is a real trap elsewhere in
    # this module, and a CSV that disagrees with itself about line endings is not the
    # lesson here.
    with target.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for run_id, inst, tech, date, dev, passed in RUNS:
            w.writerow([run_id, inst, tech, date, f"{dev:.2f}", "true" if passed else "false"])

    per_instrument = Counter(r[1] for r in RUNS)
    failed = [r for r in RUNS if not r[5]]

    print(f"wrote {target} ({len(RUNS)} rows)")
    print(f"  distinct instruments    : {len(per_instrument)}")
    for inst in sorted(per_instrument):
        rows = [r for r in RUNS if r[1] == inst]
        worst = max(r[4] for r in rows)
        print(f"    {inst}  runs={len(rows)}  failed={sum(1 for r in rows if not r[5])}  "
              f"max_deviation_ppm={worst:.2f}")
    print(f"  failed runs (total)     : {len(failed)}")
    print(f"  SUM(deviation_ppm)      : {sum(r[4] for r in RUNS):.2f}")
    print(f"  MAX(deviation_ppm)      : {max(r[4] for r in RUNS):.2f}")
    print(f"  required runtime facts  : {len(REQUIRED_FACTS)} -> {', '.join(REQUIRED_FACTS)}")
    print("\nNext: create the lakehouse, upload this file to Files/raw/calibration_run.csv, "
          "and work the three surfaces. See task.md.")


if __name__ == "__main__":
    main()
