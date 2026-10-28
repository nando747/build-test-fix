# Failure modes of multi-agent builds, and how the playbook handles them

Every item here happened, or nearly happened, in a real run (see [case-study.md](case-study.md)).

## Agents getting in each other's way

| What goes wrong | Why | What the playbook does |
|---|---|---|
| Two agents edit the same file at the same time and one loses its work | Parallel agents sharing one checkout | Every agent gets its own git worktree (`worktrees.py add`); only the coordinator merges |
| A tester's results are meaningless because the code changed under it | Testing a branch the builder is still committing to | Testers get a **frozen** snapshot at a specific commit |
| The user's running app breaks mid-run | An agent edits or restarts files the user is using | Agents never touch the main checkout; `main` moves only at the end, after the full suite passes there |
| Fixes for one batch are invisible to another batch's tester | Frozen snapshots on separate branches | Merge early into an `integrate` worktree and run the combined suite |
| Merge conflicts in shared test fixtures | Several testers each write "the" fixtures file | One fixtures file per batch (`tests/<batch>/conftest.py`); the coordinator unions anything shared |
| One batch's global test stub silently disables another batch's test | A stub placed in an auto-used fixture applies to every test | Put stubs in the fixture that needs them, not in global setup; check this when merging |

## Tests that lie

| What goes wrong | Why | What the playbook does |
|---|---|---|
| Everything passes, but the tests would also pass with bugs | Tests check that code runs, not that it's right | Plant bugs after every green batch (`plant_bugs.py`); any MISSED mutation sends the tester back |
| A "planted bug" never actually got planted | The text to replace didn't match exactly, so the tests ran on unchanged code and "missed" nothing | `plant_bugs.py` requires an exact match count and reports NOT APPLIED |
| Every planted bug looks "caught" | The suite was already failing before anything was planted | `plant_bugs.py` runs the unmutated code first and stops if it's red |
| Planted bugs leak into each other | Mutations applied on top of each other | Each mutation is applied alone to a freshly reset copy |
| A flaky test gets "fixed" in product code | The fixer can't tell environment noise from a real failure | Testers re-run failures once; flaky tests are the tester's to fix, and the log says so |
| A real problem gets dismissed as flakiness | A timing assertion only fails under load | Investigate before loosening a test - in the case study this found a 29-second startup |
| A fixer weakens a test to make it pass | Easier than fixing the code | Fixers may not edit tests or the tester's log; disagreements go to the coordinator |

## Fixes that miss the point

| What goes wrong | Why | What the playbook does |
|---|---|---|
| The same bug is patched in five places | Fixing symptoms where they crash | Fixers find the root cause first; often it's one shared place (data loading, state) |
| A related problem stays unfixed | It wasn't in the failing test | Fixers report related risks they noticed; the coordinator decides whether to assign them |
| The same bug survives round after round | The fixer is out of its depth | Cap at 3 rounds per batch; escalate the fixer to a stronger model after 2 |
| The spec itself is wrong | Requirements written before anyone ran the numbers | Builders record judgment calls in the spec's Decisions log and flag product-changing ones with evidence; the coordinator decides or asks the user |

## Safety and privacy

| What goes wrong | Why | What the playbook does |
|---|---|---|
| Screenshots capture the user's real desktop (names, documents, chats) | Screen grabs include whatever is behind the window | Render the window itself or put a plain backdrop behind it; never grab the desktop |
| An agent kills processes it doesn't own | "Kill by name" to clear a stuck command | Only kill PIDs the agent started |
| Tests overwrite real user settings or files | Tests run against default paths | Every path comes from an env-var override pointed at a temp dir |
| Secrets end up in logs or commits | Agents print config while debugging | Agents never read or print secrets; keys live outside the repo |
| Work silently runs under the wrong identity | Commits made with whatever git identity is lying around | `worktrees.py init` refuses to run without an identity; ask the user which one to use |

## Running on a real machine

| What goes wrong | Why | What the playbook does |
|---|---|---|
| Work stops overnight | The laptop sleeps | Confirm keep-awake with the user before long runs (a closed lid still sleeps) |
| Worktree folders can't be removed | On Windows a shell or editor is still "inside" them | `worktrees.py remove` retries and tells you what's still locked; branches are never lost |
| A test command works in one shell but not another | On Windows `plant_bugs.py` runs the command in cmd.exe | Use `C:/...` paths in `--test-cmd`, not Git Bash `/c/...` paths |
| Stray "agent finished" notices | Agents' leftover background commands finishing late | The coordinator trusts commits and test results, not notices |
