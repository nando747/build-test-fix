---
name: build-test-fix
description: Build a set of features with a three-agent team - a builder (Opus) implements, a tester (Sonnet) writes and runs tests and logs results, a fixer (Sonnet) repairs failures and hands back for re-test - pipelined in parallel git worktrees, with the main session as coordinator, and verified by planting bugs. Use when the user asks to build several features "with subagents", "with a builder/tester/fixer", "with an agent team", or runs /build-test-fix.
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/*), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/*)
---

# Build -> Test -> Fix agent team

You are the COORDINATOR. Three agent types do the work: `build-test-fix:builder`, `build-test-fix:tester` and `build-test-fix:fixer` (their models are set in their definitions: builder Opus, tester and fixer Sonnet). You plan, hand out work, keep the pipeline moving, merge, and verify. Never take an agent's report as proof: check results yourself before telling the user something is done.

Helper scripts (standard-library Python, run them with the project folder as the current directory):
- `python ${CLAUDE_SKILL_DIR}/scripts/worktrees.py` - `init`, `add NAME [--from REF]`, `list`, `remove NAMES|--all`, `timeline`
- `python ${CLAUDE_SKILL_DIR}/scripts/plant_bugs.py MUTATIONS.json --test-cmd "..." [--ref REF]`
- `python ${CLAUDE_SKILL_DIR}/scripts/agent_ledger.py log ... | summary`
- Spec template: `${CLAUDE_SKILL_DIR}/templates/SPEC_TEMPLATE.md`

## 0. Confirm with the user before starting
- Restate the features, the batches (step 2), the models (builder Opus, tester Sonnet, fixer Sonnet; escalate the fixer to Opus if the same bug survives two rounds; the coordinator is this session) and anything that needs the user's input (credentials, which apps/accounts, choices that change the product).
- Tell them the work runs on their machine: if it sleeps, the agents pause. Offer keep-awake for long runs (a closed lid still sleeps).
- Wait for "go".

## 1. Set up
- **Git**: `worktrees.py init` - initialises a local repo if needed (nothing is pushed), ignores `.worktrees/`, and commits a baseline. If git has no user identity, ask the user which name/email to use; never invent one or reuse a work email for a personal project.
- **Spec**: write `docs/SPEC.md` from the template - the single source of truth all three agents share: project facts (runtime path, how to run, machine quirks), architecture rules, shared data schema, per-feature behaviour precise enough to test (numbers, thresholds, exact texts, edge cases), testing conventions, and a "Decisions log". Commit it.
- Install the test tooling the tester will need.

## 2. Batch the work by file ownership
Group features into 2-4 batches so each batch mostly touches its own files - that's what makes parallel work merge cleanly. Give each batch's tester its own names: tests in `tests/<batch>/` with their own `conftest.py` (or `tests/test_<batch>_*.py`) and a log `tests/test_log_<batch>.md`. One shared fixtures file written by several testers *will* conflict on merge.

## 3. Run the pipeline
Each agent works in its own worktree (`worktrees.py add NAME --from REF`):
- `build` - one long-lived worktree; the builder builds batch after batch, committing each;
- `test-<batch>` - a frozen snapshot at the builder's commit for that batch;
- `fix-<batch>` - branched from the tester's commit (code + tests).

Overlap the stages: while the tester tests batch N, the builder builds batch N+1 and the fixer fixes batch N-1. Run agents in the background and act on each completion notice. Reuse an agent for its next step by messaging it (it keeps its context and is cheaper than a fresh one). Keep to about 3 agents at once.

Every agent's first message includes: its worktree path and branch/commit; the spec path and scope; for testers, the builder's notes (hook points, test seams, deviations); for fixers, the failures; the environment (shell, interpreter, test command, quirks); and the safety rules below.

After each agent finishes, log it: `agent_ledger.py log --role ROLE --model MODEL --batch B --round N --tokens T --tool-uses U --duration-ms D --result "..."`, using the numbers from the completion notice.

Per batch the loop is: tester round 1 -> (if failures) fixer -> tester round 2 (fast-forward to the fix, add tests for the fixed behaviour, run the FULL suite) -> ... **Cap at 3 fix rounds per batch**; if something still fails, stop and tell the user exactly what.

When the builder flags a spec problem (e.g. a formula that produces bad results), decide it yourself if it's low-stakes, reversible and clearly serves the feature's intent - and tell the user what you decided and why. Ask the user if it changes what they asked for.

## 4. Verify - don't trust green
- **Plant bugs** when a batch passes: write 4-6 realistic one-line mutations of its core logic (thresholds, formulas, off-by-ones, a guard removed) as a JSON file and run `plant_bugs.py mutations.json --test-cmd "<full test command>" --ref <batch commit>`. It applies each mutation alone in a throwaway worktree, refuses to run if the baseline is red, and reports NOT APPLIED instead of silently testing unchanged code. Every mutation must be CAUGHT; a MISSED one means the tests are weak - send the tester back with the gap. On Windows the test command runs in cmd.exe, so use `C:/...` paths.
- **Integrate early**: as soon as two batches are done, merge their code and test branches in an `integrate` worktree and run the combined suite. Cross-batch problems only show up here. Resolve conflicts in shared fixtures yourself: union them, keep the most protective isolation, and don't let one batch's global stub break another batch's tests.
- Investigate failures that only appear under load - a timing failure can be a real product problem (it was, in the case study: a 29 s startup).
- Look at screenshots of visual features yourself.
- Do one real end-to-end check of whatever the mocks can't cover (e.g. one real API call through the user's actual configuration).

## 5. Finish
- Fast-forward `main` to the final tested commit; run the full suite on `main` itself.
- `worktrees.py remove --all` (branches are kept as history), then `agent_ledger.py summary` and `worktrees.py timeline` for the report.
- Tell the user: what was built (per feature), test totals, planted-bug results, bugs found and fixed, decisions you made, cost/time from the ledger, and a short manual checklist from the testers' "Not testable automatically" lists. Tell them how to run or restart the result.

## Safety rules (put them in every agent's brief)
- Agents edit only their own worktree - never the main checkout (the user may be running it) or another agent's worktree.
- Never kill processes they didn't start (check PIDs; never kill by name).
- Never read, print or copy secrets. Tests use temp paths and never touch real user files, system folders, or write to real external services.
- Screenshots never capture the user's real desktop (render the window directly or put a plain backdrop behind it) - it can contain private or confidential content.
- No pushing, no merging (the coordinator merges), no branches beyond their own.
