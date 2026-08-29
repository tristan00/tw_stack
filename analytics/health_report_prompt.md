# Health report task

You are writing a scheduled health report for the tw_stack run corpus.

Input: read exactly one file, the JSON extract at `{EXTRACT_PATH}`. It holds the last
window of campaign outcomes, postmortems with plausibility verdicts, action success
rates and latencies, interrupt handling, refusals and unhandled screens, each subset
by `code_version`.

Blindness rule: this report must be written blind. Do not read, list, or search any
previous report or anything under a reports directory, and do not carry in findings
from any other source. Analyze only the extract, so every report reflects fresh,
independent judgment.

Write a markdown report with:

- a short executive summary (3-6 bullets) for the window
- failures and unclear or suspicious exits, grouped by `code_version`: what happened,
  the plausibility verdicts, and anything that looks like a harness or instrumentation
  problem rather than gameplay
- action timing and success rates by `code_version`: worst confirm rates, latency
  outliers and refusal hotspots; compare versions when more than one appears
- interrupt handling and unhandled screens: anything recurring is a panel-handling bug
  worth naming
- suspicious trends worth investigating, each with the evidence that motivated it

Do not pad; if a section has nothing notable, say so in one line. Output only the
markdown report, starting with a title line that includes the window and generation
time from the extract.
