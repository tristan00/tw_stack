# tw_stack — project roles (coherent purposes)

One sentence per project: what it OWNS, and the boundary it must not cross. The north star is the
**advisor** (decide well); everything else exists to feed it or to test it online.

## Control/data flow
```
        ┌──────────────────────────── the GAME (WH3 + mod) ────────────────────────────┐
        │                                    ▲  ▲                                        │
        │                              (only via) bus                                    │
   COLLECT (read)                            │  │                         ACT (write)     │
   recorder ── ui-capture/manager/streams ───┘  └──── launcher (launch + actions) ────────┘
        │                                                        ▲
   run files (menus+coords, events, state, shots)               │ execute chosen option
        │                                                        │
   PROCESS: correlation / structurer / campaigns / runs         │
        │                                                        │
   DECIDE: advisor (score menus → ranked advice: advisor.jsonl)  │
        │                                                        │
   SERVE: tw_advisor_ui (/api/state: state + advice) ──► driver (bridge) ──────────────────┘
```

## Projects
- **bus** — the SOLE transport to the game (command bus ⇄ mod). Concurrency-safe (cross-process seq
  lock). Nothing else reimplements game I/O; everyone sends through here. *Boundary: transport only, no
  policy.*
- **launcher** — game LIFECYCLE + ACTIONS. (1) singleton launch: exactly one game, correct fast/consistent
  settings, reach a verified campaign, clear the Continue screen. (2) `actions.py`: execute a decision —
  a semantic verb parameterized by the CHOSEN key ("settlement builds building X", "lord attacks lord Y")
  — targeted click/commit + verify from game state. *Boundary: does NOT enumerate options or collect data
  (that's record+enumerate = other jobs); it launches and acts. Stays bus-light.*
- **ui-capture** (+ **manager**, **input/logs/shots**) — the RECORDER: passive DATA COLLECTION. Captures
  each menu's option-set WITH coords (enumeration), UI/events/state, shots, into a run dir. *This is the
  single home of enumeration + collection — including the comprehensive full-screen dump that beats the
  500-node/visible-only caps (migrate `launcher/dumps.py` here). Owns the bus-heavy reads.*
- **correlation / structurer / campaigns / runs** — offline PROCESSING: stream time-align, raw→structured
  state, per-campaign split, valid-run gating. *Boundary: read run files, no game.*
- **advisor** — DECISION INTELLIGENCE (the deliverable). Consumes recorder output; per-type models score
  each menu's options → ranked advice. `runtime.py watch` = live service → `advisor.jsonl`; `replay` =
  offline/full-context. Owns enumeration→features→scoring + `reference.sqlite`. *Boundary: read-only; never
  drives the game.*
- **tw_advisor_ui** — the advisor's SERVICE FRONT: localhost dashboard + `GET /api/state` (current run's
  campaign/health/menus + scored advice). This is the "state service that tells a consumer what's happening
  in-game + what to do" — the launcher/driver's read-side, so the launcher need not query the game itself.
- **driver** (`launcher/advisor_driver.py`) — thin BRIDGE = the online TEST HARNESS: read the advisor
  service's top-available advice per menu, join the recorder's captured coords, call the launcher action to
  execute it, verify. This is how the advisor gets online-tested. *Boundary: no scoring (advisor), no
  enumeration (recorder); it orchestrates.*

## Rules that keep it coherent
1. Only the **bus** talks to the game.
2. **Enumeration + data collection live in the recorder/advisor**, never the launcher (else the launcher
   becomes record+enumerate+act = 3 jobs).
3. The **launcher acts** on a chosen key + captured coords handed to it; it does not decide or discover.
4. The **advisor decides**; it is read-only.
5. If launcher and recorder both hammer the bus, that's contention — consolidate reads under recorder.
