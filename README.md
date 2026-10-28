# Build-Test-Fix

**A three-agent team for Claude Code: a builder writes the features, a tester proves they work,
a fixer repairs what doesn't - in parallel, each in its own git worktree - and the coordinator
checks the tests themselves by planting bugs.**

![tests](https://github.com/nando747/build-test-fix/actions/workflows/test.yml/badge.svg)

In its first real run it built **8 features in 94 minutes** on a desktop app: 589 tests, **14 of
14 planted bugs caught**, and 7 real problems found that the builder's own checks missed -
including a 29-second startup that dropped to under a second.
[Read the case study](docs/case-study.md).

## Why

Asking one AI agent to "build X and test it" tends to produce tests that agree with the code,
because the same agent wrote both. Splitting the roles fixes part of that, but brings new
problems: agents overwriting each other's files, testers testing a moving target, tests that pass
because they're too weak, fixes that patch symptoms. This repo is the setup that dealt with
those problems in practice:

- **Separate roles with separate rules.** The builder implements from a spec. The tester writes
  tests against the spec, never edits product code, and logs every failure with a suspected
  cause. The fixer fixes root causes, never edits tests, and re-runs the full suite.
- **Parallel without collisions.** Every agent works in its own git worktree. Testers get frozen
  snapshots. Only the coordinator merges.
- **Pipelined.** While batch 1 is being tested, batch 2 is being built and batch 0 fixed.
- **Green isn't trusted.** After a batch passes, `plant_bugs.py` plants realistic bugs one at a
  time. Every one must turn the suite red, or the tests go back to the tester.
- **Merge early.** Batches are combined and tested together as soon as possible, because the
  worst problems live between batches.

## What's inside

| Part | What it is |
|---|---|
| `agents/builder.md` | Builder subagent (Opus): implements a batch from the spec in small, testable modules |
| `agents/tester.md` | Tester subagent (Sonnet): writes and runs tests on a frozen snapshot, logs results for the fixer |
| `agents/fixer.md` | Fixer subagent (Sonnet): root-cause fixes, full-suite re-run, hands back to the tester |
| `skills/build-test-fix/SKILL.md` | The coordinator's playbook: spec, batching, pipeline, verification, finish |
| `skills/build-test-fix/scripts/worktrees.py` | Set up the repo, create and remove per-agent worktrees, print the commit timeline |
| `skills/build-test-fix/scripts/plant_bugs.py` | Plant bugs one at a time in a throwaway copy and report which ones the tests caught |
| `skills/build-test-fix/scripts/agent_ledger.py` | Log each agent run (tokens, tool uses, time) and summarise by role and model |
| `skills/build-test-fix/templates/SPEC_TEMPLATE.md` | The spec all three agents work from |
| `docs/` | [Case study](docs/case-study.md) and [failure modes](docs/failure-modes.md) |

The scripts use only the Python standard library and have their own tests, including a
self-check that plants bugs in the scripts and confirms their tests catch them.

## Install

Requires [Claude Code](https://code.claude.com), git and Python 3.9+.

In Claude Code:

```
/plugin marketplace add nando747/build-test-fix
/plugin install build-test-fix@build-test-fix
```

Or from a terminal:

```bash
claude plugin marketplace add nando747/build-test-fix
claude plugin install build-test-fix@build-test-fix --scope user
```

## Use

Open Claude Code in your project folder and describe what you want built:

```
/build-test-fix add CSV export, a dark mode, and an undo history to the editor
```

Claude then acts as the coordinator. It restates the plan (features, batches, models) and waits
for your go-ahead, writes `docs/SPEC.md`, and runs the pipeline. At the end it gives you:

- what was built, per feature
- test totals and the planted-bug results
- the bugs found and fixed, and any decisions it made on your behalf
- time and token use per agent
- a short checklist of things only a human can verify

Everything stays local: nothing is pushed anywhere unless you ask.

The scripts also work on their own:

```bash
python skills/build-test-fix/scripts/plant_bugs.py mutations.json --test-cmd "python -m pytest -q -x"
```

```json
[
  {"label": "threshold off by one", "file": "app/limits.py", "find": "MAX_ITEMS = 20", "replace": "MAX_ITEMS = 21"},
  {"label": "guard removed", "file": "app/save.py", "find": "if not path.exists():", "replace": "if False:"}
]
```

## Cost and models

The builder runs on Opus and the tester and fixer on Sonnet: build quality matters most, while
testing and targeted fixes are more structured and cheaper to run repeatedly. Fix rounds are
capped at 3 per batch, and the fixer moves up to Opus if the same bug survives two rounds. The
coordinator is your main session. `agent_ledger.py summary` reports what each run used; its cost
figures are rough, because Claude Code reports a single token total per agent.

## Limits

This has been used on one real project so far, and that project wasn't also built by a single
agent for comparison. The case study shows the process working, not that it beats every
alternative. It works best when a feature set can be split into batches that touch mostly
separate files, and when the project can be tested automatically.

## License

MIT
