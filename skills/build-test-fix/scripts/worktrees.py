#!/usr/bin/env python3
"""Manage the per-agent git worktrees for a build -> test -> fix run.

Every agent works in its own worktree under <repo>/.worktrees/<name>, on its own branch,
so builder, testers and fixers can run in parallel without overwriting each other.

    python worktrees.py init                     # make sure the project is a git repo with a baseline commit
    python worktrees.py add build                # long-lived builder worktree, branch "build" from HEAD
    python worktrees.py add test-a --from ee09c92   # frozen snapshot for a tester
    python worktrees.py list
    python worktrees.py remove test-a fix-a      # or: remove --all   (branches are kept)
    python worktrees.py timeline                 # commit timeline across all branches (for the write-up)

Options: --repo PATH (default: the git repo containing the current directory).
Standard library only; works with Git for Windows, macOS and Linux.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

WORKTREES_DIR = ".worktrees"
IGNORE_LINES = [".worktrees/"]


class GitError(RuntimeError):
    pass


def git(repo, *args, check=True):
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def find_repo(start):
    """Top level of the git repo containing start, or None if it isn't in one."""
    res = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(res.stdout.strip()) if res.returncode == 0 else None


def has_identity(repo):
    return bool(git(repo, "config", "user.email", check=False)) and \
        bool(git(repo, "config", "user.name", check=False))


def ensure_gitignore(repo):
    path = Path(repo) / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in IGNORE_LINES if line not in lines]
    if missing:
        with path.open("a", encoding="utf-8") as f:
            if lines and lines[-1].strip():
                f.write("\n")
            f.write("\n".join(missing) + "\n")
    return missing


def cmd_init(args):
    repo = Path(args.repo or os.getcwd()).resolve()
    top = find_repo(repo)
    if top is None:
        git(repo, "init", "-b", args.branch)
        print(f"initialised git repo in {repo} (local only - nothing is pushed)")
        top = repo
    if not has_identity(top):
        sys.exit("git has no user.name / user.email for this repo. Set them first, e.g.\n"
                 '  git config user.name "Your Name"\n  git config user.email you@example.com')
    added = ensure_gitignore(top)
    if added:
        print(f".gitignore: added {', '.join(added)}")
    git(top, "add", "-A")
    if git(top, "status", "--porcelain"):
        git(top, "commit", "-q", "-m", args.message)
        print(f"baseline commit {git(top, 'rev-parse', '--short', 'HEAD')}: {args.message}")
    else:
        head = git(top, "rev-parse", "--short", "HEAD", check=False)
        print(f"nothing to commit; baseline is {head or '(no commits yet)'}")
    return 0


def worktree_path(repo, name):
    return Path(repo) / WORKTREES_DIR / name


def cmd_add(args):
    repo = repo_or_exit(args)
    if git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False) == "":
        sys.exit("the repo has no commits yet - run `worktrees.py init` first")
    ensure_gitignore(repo)
    path = worktree_path(repo, args.name)
    if path.exists():
        sys.exit(f"{path} already exists - pick another name or remove it first")
    ref = args.ref or "HEAD"
    branch_exists = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{args.name}",
                        check=False) != ""
    if args.detach:
        git(repo, "worktree", "add", "--detach", str(path), ref)
    elif branch_exists:
        if not args.reuse:
            sys.exit(f"branch '{args.name}' already exists - use --reuse to check it out, "
                     "or pick another name")
        git(repo, "worktree", "add", str(path), args.name)
    else:
        git(repo, "worktree", "add", "-b", args.name, str(path), ref)
    head = git(path, "rev-parse", "--short", "HEAD")
    print(f"{path}  [{'detached' if args.detach else args.name}]  at {head}")
    return 0


def parse_worktrees(repo):
    """[{path, head, branch}] from `git worktree list --porcelain`."""
    out, cur = [], {}
    for line in git(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = Path(line[9:])
        elif line.startswith("HEAD "):
            cur["head"] = line[5:12]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "")
        elif line == "detached":
            cur["branch"] = "(detached)"
    return out


def cmd_list(args):
    repo = repo_or_exit(args)
    for wt in parse_worktrees(repo):
        print(f"{wt.get('branch', '?'):<20} {wt.get('head', '?'):<8} {wt['path']}")
    return 0


def _force_rmtree(path):
    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)  # read-only files (git objects) on Windows
        func(p)
    shutil.rmtree(path, onerror=onerror)


def remove_one(repo, path, tries=5):
    """git worktree remove with retries: on Windows a shell or indexer briefly holding a
    folder makes removal fail with 'Permission denied'. Returns None or an error string."""
    last = ""
    for attempt in range(tries):
        res = subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(path)],
                             capture_output=True, text=True)
        if res.returncode == 0 or not path.exists():
            return None
        last = res.stderr.strip()
        if "is not a working tree" in last:
            try:  # git already forgot it; just delete the folder
                _force_rmtree(path)
                return None
            except OSError as exc:
                last = str(exc)
        time.sleep(0.5 * (attempt + 1))
    return last


def cmd_remove(args):
    repo = repo_or_exit(args)
    root = (Path(repo) / WORKTREES_DIR).resolve()
    if args.all:
        targets = [wt["path"] for wt in parse_worktrees(repo)
                   if root in Path(wt["path"]).resolve().parents]
    else:
        targets = [worktree_path(repo, n) for n in args.names]
    if not targets:
        print("no worktrees to remove")
    failed = []
    for path in targets:
        err = remove_one(repo, Path(path))
        print(f"{'removed' if err is None else 'FAILED '}  {path}" + (f"\n    {err}" if err else ""))
        if err:
            failed.append(path)
    git(repo, "worktree", "prune")
    if failed:
        print("\nSome folders are still locked (often a shell or editor whose current directory "
              "is inside them). Close those and run the command again; branches are untouched.")
    if args.delete_branches and not args.all:
        for n in args.names:
            if git(repo, "branch", "-D", n, check=False):
                print(f"deleted branch {n}")
    return 1 if failed else 0


def cmd_timeline(args):
    repo = repo_or_exit(args)
    fmt = "%ad\t%h\t%an\t%s"
    rows = git(repo, "log", "--all", "--reverse", f"--format={fmt}",
               "--date=format:%Y-%m-%d %H:%M").splitlines()
    print("| time | commit | subject |\n|---|---|---|")
    for row in rows:
        when, short, _author, subject = row.split("\t", 3)
        print(f"| {when} | `{short}` | {subject} |")
    return 0


def repo_or_exit(args):
    repo = find_repo(Path(args.repo or os.getcwd()).resolve())
    if repo is None:
        sys.exit("not inside a git repo - run `worktrees.py init` in the project folder first")
    return repo


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", help="project folder (default: current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="git init if needed, ignore .worktrees/, baseline commit")
    s.add_argument("--branch", default="main")
    s.add_argument("--message", default="Baseline before build-test-fix run")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("add", help="create .worktrees/NAME on a new branch NAME")
    s.add_argument("name")
    s.add_argument("--from", dest="ref", help="commit/branch to start from (default HEAD)")
    s.add_argument("--reuse", action="store_true", help="check out an existing branch NAME")
    s.add_argument("--detach", action="store_true", help="no branch (throwaway checks)")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("list", help="list worktrees")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("remove", help="remove worktrees (keeps branches)")
    s.add_argument("names", nargs="*")
    s.add_argument("--all", action="store_true", help="every worktree under .worktrees/")
    s.add_argument("--delete-branches", action="store_true", help="also delete the named branches")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("timeline", help="markdown table of commits across all branches")
    s.set_defaults(fn=cmd_timeline)

    args = p.parse_args(argv)
    if args.cmd == "remove" and not args.all and not args.names:
        p.error("remove needs worktree names or --all")
    try:
        return args.fn(args)
    except GitError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
