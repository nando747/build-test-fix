#!/usr/bin/env python3
"""Plant bugs one at a time and check the test suite catches each one.

A green test suite only means something if it would turn red when the code is wrong.
This takes a list of small, realistic mutations (a threshold off by one, a formula
weight changed, a guard removed), applies each one alone to a throwaway copy of the
code, runs the tests, and reports which mutations the tests caught and which they missed.

    python plant_bugs.py mutations.json --test-cmd "python -m pytest tests -q -x"
    python plant_bugs.py mutations.json --test-cmd "npm test" --ref fix-b --out report.md

mutations.json:
    [
      {"label": "goal 8 -> 7",  "file": "wellbeing.py", "find": "GOAL = 8", "replace": "GOAL = 7"},
      {"label": "lead 5 -> 4 min", "file": "meetings.py",
       "find": "timedelta(minutes=5)", "replace": "timedelta(minutes=4)", "count": 1}
    ]
--test-cmd runs through the operating system's shell (cmd.exe on Windows, sh elsewhere) with
the scratch copy as the working directory - on Windows use C:/... paths, not Git Bash /c/... paths.

"count" (default 1) is how many times "find" must occur - if it doesn't occur exactly that
many times the mutation is reported as NOT APPLIED instead of silently testing unchanged code.

Safety: work happens in a detached worktree at <repo>/.worktrees/_plant_bugs, never in your
checkout. Before any mutation, the unmutated code is tested; if that run already fails, the
script stops (otherwise every mutation would look "caught").

Exit code: 0 = every mutation caught, 1 = something missed / not applied / baseline red,
2 = bad input. Standard library only.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worktrees  # noqa: E402  (sibling script)

SCRATCH = "_plant_bugs"


def load_mutations(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"can't read {path}: {exc}")
    if not isinstance(data, list) or not data:
        raise ValueError("mutations file must be a non-empty JSON list")
    out = []
    for i, m in enumerate(data):
        if not isinstance(m, dict) or not all(isinstance(m.get(k), str) for k in ("file", "find", "replace")):
            raise ValueError(f"mutation #{i + 1} needs string fields file, find, replace")
        if m["find"] == m["replace"]:
            raise ValueError(f"mutation #{i + 1} ('{m.get('label', '')}'): find == replace")
        out.append({"label": m.get("label") or f"{m['file']}: {m['find']!r} -> {m['replace']!r}",
                    "file": m["file"], "find": m["find"], "replace": m["replace"],
                    "count": int(m.get("count", 1))})
    return out


def apply_mutation(root, m):
    """Exact text replacement. Returns None if applied, else the reason it wasn't.
    Line endings are preserved; a find written with \\n also matches \\r\\n files."""
    path = Path(root) / m["file"]
    if not path.is_file():
        return f"file not found: {m['file']}"
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    find, replace = m["find"], m["replace"]
    if "\r\n" in text and "\n" in find and "\r\n" not in find:
        find, replace = find.replace("\n", "\r\n"), replace.replace("\n", "\r\n")
    n = text.count(find)
    if n != m["count"]:
        return f"'find' occurs {n} time(s), expected {m['count']}"
    path.write_bytes(text.replace(find, replace).encode("utf-8"))
    return None


def run_tests(cwd, cmd, timeout):
    start = time.monotonic()
    try:
        res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                             timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "timeout", time.monotonic() - start, ""
    tail = (res.stdout + res.stderr).strip().splitlines()[-1:] or [""]
    return ("pass" if res.returncode == 0 else "fail"), time.monotonic() - start, tail[0]


def reset(root):
    worktrees.git(root, "checkout", "-q", "--", ".")
    worktrees.git(root, "clean", "-q", "-fd")


def plant(repo, mutations, test_cmd, ref="HEAD", timeout=900, log=print):
    """Returns (baseline, results). baseline = (status, secs, tail); results = list of dicts."""
    scratch = worktrees.worktree_path(repo, SCRATCH)
    if scratch.exists():
        worktrees.remove_one(repo, scratch)
    worktrees.ensure_gitignore(repo)
    worktrees.git(repo, "worktree", "add", "--detach", str(scratch), ref)
    results = []
    try:
        log(f"baseline: running tests on unmutated {ref} ...")
        baseline = run_tests(scratch, test_cmd, timeout)
        if baseline[0] != "pass":
            return baseline, results
        for m in mutations:
            reset(scratch)
            why = apply_mutation(scratch, m)
            if why:
                results.append({**m, "outcome": "NOT APPLIED", "note": why, "secs": 0.0})
                log(f"[{m['label']}] NOT APPLIED - {why}")
                continue
            status, secs, tail = run_tests(scratch, test_cmd, timeout)
            outcome = {"fail": "CAUGHT", "pass": "MISSED", "timeout": "TIMEOUT"}[status]
            results.append({**m, "outcome": outcome, "note": tail, "secs": secs})
            log(f"[{m['label']}] {outcome} ({secs:.0f}s) {tail}")
        reset(scratch)
    finally:
        err = worktrees.remove_one(repo, scratch)
        worktrees.git(repo, "worktree", "prune", check=False)
        if err:
            log(f"note: couldn't remove {scratch} ({err}); run `worktrees.py remove {SCRATCH}` later")
    return baseline, results


def report(ref, test_cmd, baseline, results):
    lines = [f"## Planted-bug check - `{ref}`", "", f"Test command: `{test_cmd}`", ""]
    if baseline[0] != "pass":
        lines += [f"**Baseline is not green ({baseline[0]})** - fix the failing tests before "
                  f"planting bugs. Last line: `{baseline[2]}`"]
        return "\n".join(lines)
    caught = sum(r["outcome"] == "CAUGHT" for r in results)
    lines += [f"**{caught} / {len(results)} caught** (baseline passed in {baseline[1]:.0f}s)", "",
              "| planted bug | file | result | test output |", "|---|---|---|---|"]
    for r in results:
        note = r["note"].replace("|", "\\|")
        lines.append(f"| {r['label']} | `{r['file']}` | {r['outcome']} | {note} |")
    missed = [r for r in results if r["outcome"] != "CAUGHT"]
    if missed:
        lines += ["", "Not caught - send the tester back to cover these:"]
        lines += [f"- {r['label']} ({r['outcome'].lower()}: {r['note']})" for r in missed]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Plant bugs one at a time and check the tests catch them.")
    p.add_argument("mutations", help="JSON file with the list of mutations")
    p.add_argument("--test-cmd", required=True, help='e.g. "python -m pytest tests -q -x"')
    p.add_argument("--ref", default="HEAD", help="commit/branch to test (default HEAD)")
    p.add_argument("--repo", help="project folder (default: current directory)")
    p.add_argument("--timeout", type=int, default=900, help="seconds per test run (default 900)")
    p.add_argument("--out", help="also write the markdown report to this file")
    args = p.parse_args(argv)
    try:
        mutations = load_mutations(args.mutations)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    repo = worktrees.find_repo(Path(args.repo or ".").resolve())
    if repo is None:
        print("not inside a git repo", file=sys.stderr)
        return 2
    baseline, results = plant(repo, mutations, args.test_cmd, args.ref, args.timeout)
    text = report(args.ref, args.test_cmd, baseline, results)
    print("\n" + text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    ok = baseline[0] == "pass" and all(r["outcome"] == "CAUGHT" for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
