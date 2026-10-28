# <Project> - feature spec

Single source of truth for the builder, tester and fixer agents. If code and spec disagree,
the spec wins. If the spec is ambiguous, pick the simplest reasonable behaviour and record it
under "Decisions log".

## Project facts
- Platform / runtime: <OS, language + version, path to the interpreter or package manager>
- How to run: <command>
- How to test: <full test command, run from the repo root>
- Entry points and main modules: <files and what they do>
- Machine quirks: <blocked shells, no internet from the runtime, proxies, slow imports...>
- Secrets: <where they live; agents must never print, log or copy them>

## Architecture rules
1. One module per feature area; existing files only get small hook points.
2. Pure logic separated from UI / OS / network, so it's testable without them.
3. Injectable clock and paths: time-based code takes `now` or uses a patchable function;
   file locations come from env-var overrides (list them here), so tests never touch real files.
4. External failures degrade the feature; they never crash the app. Log once, retry later.
5. Slow work off the UI thread.

## Shared data / state
<file locations, schema, which batch owns which fields; loading must tolerate missing,
corrupt and wrong-typed data>

---

## Batch A - <theme>
### A1. <feature>
- <behaviour, precise enough to test: numbers, thresholds, exact texts, edge cases>
- <what happens when the external dependency is missing or fails>
- <UI: where it appears, what the user clicks>

## Batch B - <theme>
### B1. <feature>
- ...

---

## Testing conventions
- Test command: `<command>`; tests live in `tests/<batch>/` with their own `conftest.py`.
- Every test runs with all path overrides pointing into a temp dir.
- Unit tests for pure logic with mocks; a few real integration tests where only they catch the bug.
- Screenshots (UI): render the window itself, never the desktop; save to `tests/screenshots/` (git-ignored).
- Results: append a round to `tests/test_log_<batch>.md` - commit tested, counts, a table of every
  test, per-failure details (spec bullet, short traceback, suspected cause), and a
  "Not testable automatically" list.

## Decisions log
(Agents append: date, agent, decision, reason.)
