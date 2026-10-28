# Case study: 8 features in 94 minutes with a builder, a tester and a fixer

This is a write-up of the first real run of this playbook. The numbers come from the project's git
history and the agents' own completion reports. It's one run on one project, so read it as a
worked example, not a benchmark - see "Limits" at the end.

## The project

**Claude Buddy** is a small always-on-top desktop pet for Windows (Python + Tkinter). It chats
through the Claude API, takes little breaks during the day, and has a retro handheld-style
creature-battle mini-game. Before the run it had chat, web search, idle animations, a water-break
reminder and the battle game - about 1,400 lines of Python, with no tests.

The request was eight features at once:

| # | Feature | What it involves |
|---|---|---|
| 1 | Quiet mode | Hide the pet while Teams or Zoom is using the microphone, or while a full-screen presentation runs; postpone its breaks until the call ends |
| 2 | Start with Windows | A startup shortcut toggled from the menu, plus single-instance protection |
| 3 | API-key expiry reminder | Daily reminder from 7 days before the key expires |
| 4 | Water tracking | Click the pet to log a glass; a daily goal; each glass earns an in-game item |
| 5 | More breaks | A stretch every 60 minutes, a 20-20-20 eye break every 20 minutes, each with its own animation |
| 6 | The pet levels up | Persistent level, EXP curve, learnable moves with a "forget a move" flow |
| 7 | Catching creatures | Capture-ball items, a capture-rate formula with wobble animation, a collection view with silhouettes |
| 8 | Calendar reminders | A bubble 5 minutes before each Outlook meeting (skipping declined, cancelled and all-day ones) |

These touch the operating system (registry, startup folder, single-instance mutex), an external
app (Outlook via COM), persistent state shared by several features, UI animation, and game
balance. That mix is useful for a case study: most of the features have edge cases a quick demo
wouldn't show.

## The team

| Role | Model | Agents | Assignments |
|---|---|---|---|
| Coordinator (main session) | Opus 5.5 | 1 | planning, spec, merging, verification |
| Builder | Opus 5.5 | 1 | 4 (batch A, batch B, batch C, one balance change) |
| Tester | Sonnet 5.5 | 3 | 5 rounds |
| Fixer | Sonnet 5.5 | 3 | 5 fixes |

At most three agents worked at the same time, each in its own git worktree.

## How it ran

The coordinator wrote a spec first (architecture rules, shared state schema, every feature's
behaviour in testable detail), then split the eight features into three batches by the files
they touch: **A** system integration (1, 2, 3, 8), **B** wellbeing (4, 5), **C** game (6, 7).

Times are commit times from the same evening.

| Time | Event |
|---|---|
| 21:16 | Baseline commit |
| 21:19 | Spec committed; builder starts batch A |
| 21:28 | Batch A built. Tester A starts on a frozen copy; builder moves on to B |
| 21:35 | Batch B built. Tester B starts; builder moves on to C |
| 21:39 | **Tester A, round 1: 176 passed, 0 failed** |
| 21:49 | Tester B, round 1: 90 of 91 passed |
| 21:53 | Fixer B: root cause in the shared state loader, not in the failing feature |
| 21:57 | Fixer B follow-up: a related case the fixer had flagged |
| 21:59 | Tester B, round 2: 107 passed |
| 22:02 | Batch C built; the builder flags a balance problem in the spec (below) |
| 22:06 | Balance change committed |
| 22:08 | Coordinator merges A + B: 282 of 283 pass. The one failure points to a real problem (below) |
| 22:21 | Fixer: startup fix |
| 22:33 | Tester C, round 1: 275 of 292 passed (17 failures, 3 causes) |
| 22:34 | Coordinator merges C: 11 of the 17 failures already fixed by batch B's root-cause fix |
| 22:43 | Fixer C: the remaining two causes |
| 22:46 | Fixer C follow-up: a file-locking risk the fixer noticed |
| 22:50 | Tester C, round 2, on the fully merged code: 588 passed, 0 failed |
| | Final suite on `main`: **589 passed** |

**Total: 94 minutes** from baseline to the final tested commit, for about 1,900 lines of new
product code, a 320-line spec and 5,200 lines of tests.

## What the process caught

None of these were caught by the builder's own smoke tests.

| # | Problem | Found by | Fix |
|---|---|---|---|
| 1 | A state file with the wrong data type in one field (valid JSON, wrong shape) crashed water logging | Tester B | Fixed in the shared state loader, so it protected every feature; it later turned out to also fix 11 of batch C's failures |
| 2 | Non-numeric counts inside correctly-shaped data crashed the same paths | Fixer B (flagged), coordinator assigned | One safe-integer helper used where counts are read |
| 3 | **Startup took about 29 seconds.** The AI SDK import took ~20 s on this machine and ran before the single-instance check, so the pet appeared late and a duplicate copy needed 27 s to exit | Coordinator's merged test run: a timing assertion failed only while other agents were loading the machine | Load the SDK lazily: import time 28.8 s -> 0.4 s, duplicate exit 27 s -> 0.9 s |
| 4 | Corrupt item counts stopped every battle from starting | Tester C | Same safe-integer helper |
| 5 | A pet knowing fewer than 4 moves crashed the battle menu on the empty slot | Tester C | Cursor and clicks skip empty slots; empty slots render as "-" |
| 6 | On Windows, an antivirus scan or search indexer briefly locking the save file made saves fail silently | Fixer C (noticed a one-off test failure and asked why) | Retry the atomic file replace a few times |
| 7 | **Spec bug:** the level-scaling formula made fights much harder as the pet levelled up - a 40% win rate at level 40, down from 75% at level 25 | Builder, by simulating 150 fights against each of the 150 opponents | Coordinator approved proportional scaling: 72% at level 40 |

Problem 3 is the one worth dwelling on. It's not a bug any single-feature test would look for,
it only showed up when the batches were combined and the machine was busy, and it was the most
user-visible improvement of the whole run.

## Checking the tests themselves

The first test round came back 176 out of 176. A fully green first round is as likely to mean
weak tests as good code, so after each batch passed, the coordinator planted realistic bugs one
at a time and checked that the tests failed:

| Batch | Planted bugs (examples) | Caught |
|---|---|---|
| A | reminder 6 days early instead of 7; meeting reminder 4 min before instead of 5; postponed breaks piling up instead of one each; a second copy allowed to start | 5 / 5 |
| B | daily goal 7 instead of 8; item cap raised; ask window halved; no gap between breaks | 4 / 4 |
| C | wrong EXP curve; move learned one level late; wrong capture formula weight; wrong "always capture" threshold; the old level-scaling formula | 5 / 5 |

**14 of 14 caught.** In this run it was done by hand, and twice the improvised commands went
wrong without saying so: a pattern that didn't match, so nothing was mutated, and a cleanup that
failed on a locked folder. That's why the repo now ships `plant_bugs.py`, which reports NOT
APPLIED instead of quietly testing unchanged code, refuses to run when the tests are already red,
and cleans up after itself. Its own tests catch 5 of 5 bugs planted in it.

## What it cost

The agents' completion reports give a token count at the end of each run: roughly 257k for the
builder and between 52k and 192k for each tester and fixer, about 850k in total. Those figures
aren't a bill - they don't separate input, output and cached tokens, and a long-running agent
re-reads its context many times - so this run has no reliable dollar figure. `agent_ledger.py`
was written so the next run records every agent's numbers as it finishes and can report them
per role and model.

## Process problems along the way

These went into the playbook as rules (details in [failure-modes.md](failure-modes.md)):

- **Screenshots captured the real desktop.** The builder's first UI screenshots included whatever
  was behind the pet, including other people's names. It noticed, deleted them and switched to a
  plain backdrop; every tester was then told to do the same.
- **A tester killed processes by name** to clear a stuck command, without checking they were its
  own. No harm was done; the rule is now "kill only PIDs you started".
- **Three testers wrote three versions of the shared test fixtures**, which conflicted on merge.
  Worse, one tester's global stub of the microphone-registry reader would have silently disabled
  another batch's test of the real reader. The coordinator moved the stub into the fixture that
  needed it; the playbook now gives each batch its own fixtures file.
- **Frozen copies don't see fixes made on other branches.** Batch C's tester was still reporting
  failures that batch B's fix had already solved. Merging early made that visible.
- **Environment flakes vs real failures.** Tk occasionally failed to start a window on this
  machine. Testers retried that in their fixtures and said so in their logs, instead of reporting
  it as a product bug.

## Limits

- **One project, one run.** This shows the process working once; it doesn't prove it works
  better than other approaches.
- **No baseline.** The same eight features weren't built by a single agent for comparison, so
  "the team caught 7 problems" can't be turned into "the team is X% better".
- **The person running it still matters.** The coordinator made judgment calls (the balance
  formula, assigning the related fixes, investigating a timing failure instead of loosening the
  test) that the playbook describes but can't guarantee.
- **Some things need a human.** The testers listed what they couldn't automate: a real Teams or
  Zoom call, a real presentation, a real meeting reminder, starting at a real login. That list
  became the user's manual checklist.
