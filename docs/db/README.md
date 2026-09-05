# Database refactor: specification and evidence

Plan: `C:\Users\trist\.claude\plans\the-last-2-chats-transient-frog.md` (execution order, done-criteria, blocked items).

- `BRIEF.md` - requirements R1-R6, hard constraints C1-C13, required sections, rubric S1-S12.
- `design/01..14_*.md` - the design. `03a/03b/03c` are the DDL. Section numbering matches the plan.
- `JUDGE_B.md` - adversarial review, 43/60, 3 blockers + 9 major + 11 minor.
- `research/` - the nine measurement lanes the design is derived from, plus `decode_probe.txt`,
  `type_census.txt` (pack decode) and `change_rate.out` (per-collection change rates).

PENDING: the review amendments B1-B3, M1-M9, m1-m11 are listed in the plan file and are NOT yet
applied to `design/`. Applying them is the rest of step 0.0. Where the two disagree, the plan wins.

The post-mortem of the reverted attempt is deliberately outside this repo at
`D:\twdata\db_refactor_quarantine\reverted_attempt_report.md`; it is derived from material known to
contain false claims and must not be used as evidence.
