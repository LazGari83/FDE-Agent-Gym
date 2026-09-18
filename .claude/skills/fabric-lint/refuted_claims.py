"""The REFUTED registry — data for stale_task_claims.py, kept separate so the routine edit
(add an entry whenever an ingest corrects a wiki claim) happens here, not in the check logic.

The registry starts empty. Add an entry when an ingest corrects a claim the gym still teaches;
`fabric-lint` reports UNREGISTERED for any wiki correction no entry watches for.

Entry shape:
    {"id":      "short-kebab-id",
     "pattern": re.compile(r"the claim as a task still words it", re.IGNORECASE),
     "truth":   "what is true instead",
     "wiki":    "3_wiki/<topic>/<page>.md",   # where the correction is recorded
     "corrected": "YYYY-MM-DD"}

SHOULD_FIRE holds (entry_id, line) pairs a correct registry must catch; SHOULD_NOT_FIRE holds
lines it must leave alone. Both are exercised by `stale_task_claims.py --self-test`.
"""
import re

REFUTED = [
    {"id": "on-demand-concurrent-collision-not-guaranteed",
     "pattern": re.compile(
         r"(concurrent|second submission)[^.\n]{0,80}(collide|Deduped is observed)",
         re.IGNORECASE),
     "truth": "On-demand notebook runs are never deduplicated (distinct job-instance ids "
              "every time), but two concurrent identical submissions do not necessarily "
              "collide over the same Delta path — AG-LAK-002 ran two concurrent "
              "mode('overwrite') submissions of byte-identical output and both reached "
              "Completed with no conflict. Collision is conditional, not the default outcome.",
     "wiki": "3_wiki/lakehouse/operate-framework.md",
     "corrected": "2026-09-18"},
]

# A line that quotes a refuted claim *and* marks it corrected is the honest annotation, not a
# violation. Suppress REFUTED when the same line carries the correction alongside the quote.
REFUTATION_MARKER = re.compile(
    r"refuted|CORRECTED|corrected \d{4}-\d{2}-\d{2}|no longer|~~|"
    r"originally described|previously (described|claimed|said)|is \*\*not\*\*|are \*\*not\*\*",
    re.IGNORECASE)

# ------------------------------------------------------------------- self-test fixtures

SHOULD_FIRE = [
    ("on-demand-concurrent-collision-not-guaranteed",
     "Concurrent identical submissions run for real and collide over the same Delta path "
     "-- one dies with System_Cancelled_Session_Statements_Failed."),
    ("on-demand-concurrent-collision-not-guaranteed",
     "run at least twice, because the second submission is where Deduped is observed."),
]
SHOULD_NOT_FIRE = [
    "Concurrent identical submissions do not necessarily collide (corrected 2026-09-18) "
    "-- see operate-framework.md.",
    "Two runs were submitted concurrently and both completed without incident.",
]
