# Capture traps

Every failure below is silent: nil comes back, no exception is raised, nothing reaches
`script_log`. Each one has cost a session at least once. Read this before adding a field
to `decisions/collect.py` or a table under `corpus.`.

## The eval channel

- Multi-line Lua in a bus payload returns nil. The bus is line-oriented; flatten first.
- `_G['common']` is nil while `common` resolves. The eval sandbox's global table is not
  `_G`, so introspecting through it lies.
- An invalid CCO query and a field that exists but is empty both return nil. They are
  indistinguishable from the caller.
- CCO context ids take a type prefix: `cco('CcoCampaignSettlement', 'settlement:'..name)`.
  A bare CQI resolves nothing, silently. Most fields once believed absent were this.
- Prefer the single-argument form. `common.get_context_value(expr)` evaluates an arbitrary
  CCO expression with no context id at all: `'PlayersFaction.TreasuryAmount'` returns 4850,
  `'1+1'` returns 2. Reach for it first.
- It is Lua 5.1: `unpack`, not `table.unpack`.
- Never compare two readings taken in separate evals. The game state moves between them;
  read comparanda in one call.
- Turn 1 empties most collections. An empty list is not evidence that a field is
  unavailable, and neither is an empty character with no skills taken or items held.
- `getmetatable(obj)` enumerates any interface (faction ~150 methods, character 114). It
  is the highest-yield way to find out what is actually reachable.

## Searching the UI tree

- Any walk of the component tree must report the caps it ran under. A depth or node
  limit truncates silently and the result is indistinguishable from absence. A scan
  capped at depth 6 reports "no finance panel"; the same scan at depth 17 finds
  `button_finance` at depth 7 and `sort_income` at depth 12. Always return
  `scanned`, `maxdepth` and whether the cap was hit, and treat a negative result as
  meaningless until those numbers say the search was exhaustive.
- The same applies to every in-game query, not just tree walks: the engine drops
  results silently rather than erroring, so an empty answer is evidence of nothing
  until the query has been shown to return something in a case you control.
- `core`, `UIComponent` and `find_uicomponent` do not exist in the `eval` sandbox.
  They exist in the mod environment, reachable through the `modeval` bus channel
  (`decisions/probe.py --mod`). Concluding a component is unreachable from `eval`
  alone is a false negative.

## The write path

- A new `*_id` column needs a real foreign key or it resolves to nothing, silently.
  `decisions/dicts.py` builds the column-to-dictionary map from actual foreign keys from
  `corpus.*` to `dict.*`. No FK, no resolution, no error.
- Never point a captured key at `dict.enum`. Enum domains are closed: an unseen value
  raises `KeyError`, which propagates through `journal.request_snapshot` and kills the
  campaign mid-turn. Use a family instead. Families auto-insert unknown keys and degrade
  rather than crashing. `stance` and `effect_scope` are the precedents.
- Set members are written with `COPY`, which does not cast float to integer. Row columns
  go through `INSERT`, which does. A value bound for a COPY'd integer column must be
  coerced with `_int()`; `_num()` yields `1.0` and `COPY` rejects it outright with
  `invalid input syntax for type smallint: "1.0"`.
- Fields are parsed positionally out of a delimited payload. Append new fields at the end
  of the query and read them with a length guard, so rows recorded by older code still
  parse.

## Schema survival

- A table declared outside `sql/` is invisible to a fresh build. `decisions/pgbootstrap.py`
  lists the files applied to a new database; a migration not named there is silently
  absent everywhere except the machine that ran it by hand.
- A table living in the `ref` schema is destroyed on every reference rebuild. The build
  renames `ref` to `ref_prev` and drops it. Anything that must survive belongs in `ops`.
  `ops.region_geometry` was lost this way before it was moved.
- A dictionary family added after the last reference build stays unresolved. The build
  returns early when the pack fingerprint is unchanged, so `resolve_dictionaries` now runs
  on that path too. A family whose keys never flip to `is_reference` is this bug.

## Verifying

`decisions/probe.py` runs an expression against the live game and reports exception, nil
and false distinctly, which none of the above do on their own.

    python decisions/probe.py "return cm:model():turn_number()"
    python decisions/probe.py --cco "PlayersFaction.TreasuryAmount"
    python decisions/probe.py --meta "cm:get_local_faction(true)"
