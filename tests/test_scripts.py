"""Tests for the skill's helper scripts. Every test works in throwaway git repos under tmp_path."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "build-test-fix" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_ledger  # noqa: E402
import plant_bugs  # noqa: E402
import worktrees  # noqa: E402

PY = f'"{sys.executable}"'


@pytest.fixture(autouse=True)
def git_identity(tmp_path, monkeypatch):
    """A private global git config so tests never read or write the user's real one."""
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = Test\n\temail = test@example.com\n"
                   "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return cfg


def make_repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name).write_bytes(text.encode("utf-8"))
    assert worktrees.main(["--repo", str(path), "init"]) == 0
    return path


CALC = "THRESHOLD = 5\n\ndef ok(x):\n    return x >= THRESHOLD\n\ndef untested(x):\n    return x * 2\n"
CHECK = "import sys\nfrom calc import ok\nsys.exit(0 if (ok(5) and not ok(4)) else 1)\n"


# ---------- worktrees.py ----------
def test_init_creates_repo_gitignore_and_baseline(tmp_path):
    repo = make_repo(tmp_path / "p", {"a.txt": "hi\n"})
    assert ".worktrees/" in (repo / ".gitignore").read_text()
    assert worktrees.git(repo, "log", "--format=%s") == "Baseline before build-test-fix run"
    assert worktrees.git(repo, "status", "--porcelain") == ""


def test_init_is_idempotent_and_keeps_existing_gitignore(tmp_path):
    repo = make_repo(tmp_path / "p", {".gitignore": "node_modules/", "a.txt": "x"})
    assert worktrees.main(["--repo", str(repo), "init"]) == 0
    lines = (repo / ".gitignore").read_text().splitlines()
    assert lines.count(".worktrees/") == 1 and "node_modules/" in lines
    assert len(worktrees.git(repo, "log", "--format=%h").splitlines()) == 1


def test_init_refuses_without_git_identity(tmp_path, git_identity):
    git_identity.write_text("")
    (tmp_path / "p").mkdir()
    with pytest.raises(SystemExit) as exc:
        worktrees.main(["--repo", str(tmp_path / "p"), "init"])
    assert "user.email" in str(exc.value)


def test_add_list_remove_keeps_branches(tmp_path, capsys):
    repo = make_repo(tmp_path / "p", {"a.txt": "x"})
    assert worktrees.main(["--repo", str(repo), "add", "build"]) == 0
    assert worktrees.main(["--repo", str(repo), "add", "test-a", "--from", "build"]) == 0
    assert (repo / ".worktrees" / "build" / "a.txt").exists()
    worktrees.main(["--repo", str(repo), "list"])
    out = capsys.readouterr().out
    assert "build" in out and "test-a" in out
    assert worktrees.main(["--repo", str(repo), "remove", "--all"]) == 0
    assert not (repo / ".worktrees" / "build").exists()
    branches = worktrees.git(repo, "branch", "--format=%(refname:short)").split()
    assert {"build", "test-a", "main"} <= set(branches)


def test_add_existing_branch_needs_reuse(tmp_path):
    repo = make_repo(tmp_path / "p", {"a.txt": "x"})
    worktrees.main(["--repo", str(repo), "add", "fix-a"])
    worktrees.main(["--repo", str(repo), "remove", "fix-a"])
    with pytest.raises(SystemExit):
        worktrees.main(["--repo", str(repo), "add", "fix-a"])
    assert worktrees.main(["--repo", str(repo), "add", "fix-a", "--reuse"]) == 0


def test_add_before_any_commit_is_refused(tmp_path):
    (tmp_path / "p").mkdir()
    subprocess.run(["git", "-C", str(tmp_path / "p"), "init", "-q"], check=True)
    with pytest.raises(SystemExit):
        worktrees.main(["--repo", str(tmp_path / "p"), "add", "build"])


def test_remove_requires_names_or_all(tmp_path):
    repo = make_repo(tmp_path / "p", {"a.txt": "x"})
    with pytest.raises(SystemExit):
        worktrees.main(["--repo", str(repo), "remove"])


def test_timeline_lists_commits_from_all_branches(tmp_path, capsys):
    repo = make_repo(tmp_path / "p", {"a.txt": "x"})
    worktrees.main(["--repo", str(repo), "add", "build"])
    wt = repo / ".worktrees" / "build"
    (wt / "b.txt").write_text("y")
    worktrees.git(wt, "add", "-A")
    worktrees.git(wt, "commit", "-q", "-m", "Batch A: feature")
    capsys.readouterr()
    worktrees.main(["--repo", str(repo), "timeline"])
    out = capsys.readouterr().out
    assert "Baseline before build-test-fix run" in out and "Batch A: feature" in out


# ---------- plant_bugs.py ----------
def mutations_file(tmp_path, items):
    p = tmp_path / "mutations.json"
    p.write_text(json.dumps(items))
    return p


def test_caught_missed_and_not_applied(tmp_path):
    repo = make_repo(tmp_path / "p", {"calc.py": CALC, "check.py": CHECK})
    muts = plant_bugs.load_mutations(mutations_file(tmp_path, [
        {"label": "threshold 5->6", "file": "calc.py", "find": "THRESHOLD = 5", "replace": "THRESHOLD = 6"},
        {"label": ">= to >", "file": "calc.py", "find": "x >= THRESHOLD", "replace": "x > THRESHOLD"},
        {"label": "untested code", "file": "calc.py", "find": "x * 2", "replace": "x * 3"},
        {"label": "typo in find", "file": "calc.py", "find": "THRESHOLD = 50", "replace": "THRESHOLD = 6"},
        {"label": "missing file", "file": "nope.py", "find": "a", "replace": "b"},
    ]))
    baseline, results = plant_bugs.plant(repo, muts, f"{PY} check.py", log=lambda *a: None)
    assert baseline[0] == "pass"
    assert [r["outcome"] for r in results] == ["CAUGHT", "CAUGHT", "MISSED", "NOT APPLIED", "NOT APPLIED"]
    # the user's checkout is never touched, and the scratch worktree is cleaned up
    assert (repo / "calc.py").read_text() == CALC
    assert not (repo / ".worktrees" / plant_bugs.SCRATCH).exists()
    assert worktrees.git(repo, "status", "--porcelain") == ""


def test_count_mismatch_is_not_applied(tmp_path):
    repo = make_repo(tmp_path / "p", {"calc.py": CALC, "check.py": CHECK})
    muts = plant_bugs.load_mutations(mutations_file(tmp_path, [
        {"label": "ambiguous", "file": "calc.py", "find": "THRESHOLD", "replace": "LIMIT"}]))
    _, results = plant_bugs.plant(repo, muts, f"{PY} check.py", log=lambda *a: None)
    assert results[0]["outcome"] == "NOT APPLIED" and "occurs 2" in results[0]["note"]


def test_red_baseline_stops_before_any_mutation(tmp_path):
    broken = CHECK.replace("not ok(4)", "ok(4)")
    repo = make_repo(tmp_path / "p", {"calc.py": CALC, "check.py": broken})
    muts = plant_bugs.load_mutations(mutations_file(tmp_path, [
        {"file": "calc.py", "find": "THRESHOLD = 5", "replace": "THRESHOLD = 6"}]))
    baseline, results = plant_bugs.plant(repo, muts, f"{PY} check.py", log=lambda *a: None)
    assert baseline[0] == "fail" and results == []
    assert "Baseline is not green" in plant_bugs.report("HEAD", "x", baseline, results)


def test_each_mutation_runs_alone(tmp_path):
    """The second mutation must not see the first one's change."""
    repo = make_repo(tmp_path / "p", {"calc.py": CALC, "check.py": CHECK})
    muts = plant_bugs.load_mutations(mutations_file(tmp_path, [
        {"label": "a", "file": "calc.py", "find": "THRESHOLD = 5", "replace": "THRESHOLD = 6"},
        {"label": "b", "file": "calc.py", "find": "THRESHOLD = 5", "replace": "THRESHOLD = 5  # same"},
    ]))
    _, results = plant_bugs.plant(repo, muts, f"{PY} check.py", log=lambda *a: None)
    assert [r["outcome"] for r in results] == ["CAUGHT", "MISSED"]


def test_crlf_files_match_lf_find(tmp_path):
    repo = make_repo(tmp_path / "p", {"calc.py": CALC.replace("\n", "\r\n"), "check.py": CHECK})
    muts = plant_bugs.load_mutations(mutations_file(tmp_path, [
        {"label": "multi-line", "file": "calc.py", "find": "def ok(x):\n    return x >= THRESHOLD",
         "replace": "def ok(x):\n    return True"}]))
    _, results = plant_bugs.plant(repo, muts, f"{PY} check.py", log=lambda *a: None)
    assert results[0]["outcome"] == "CAUGHT"


def test_timeout_is_reported(tmp_path):
    slow = "import time\ntime.sleep(30)\n"
    repo = make_repo(tmp_path / "p", {"calc.py": CALC, "check.py": CHECK, "slow.py": slow})
    muts = plant_bugs.load_mutations(mutations_file(tmp_path, [
        {"file": "check.py", "find": "import sys", "replace": "import sys, slow"}]))
    _, results = plant_bugs.plant(repo, muts, f"{PY} check.py", timeout=3, log=lambda *a: None)
    assert results[0]["outcome"] == "TIMEOUT"


@pytest.mark.parametrize("content", ["[]", "{}", "not json", '[{"file": "a"}]',
                                     '[{"file": "a", "find": "x", "replace": "x"}]'])
def test_bad_mutation_files_are_rejected(tmp_path, content):
    p = tmp_path / "m.json"
    p.write_text(content)
    with pytest.raises(ValueError):
        plant_bugs.load_mutations(p)


def test_cli_exit_codes_and_report_file(tmp_path):
    repo = make_repo(tmp_path / "p", {"calc.py": CALC, "check.py": CHECK})
    good = mutations_file(tmp_path, [{"file": "calc.py", "find": "THRESHOLD = 5", "replace": "THRESHOLD = 6"}])
    out = tmp_path / "report.md"
    assert plant_bugs.main([str(good), "--test-cmd", f"{PY} check.py", "--repo", str(repo),
                            "--out", str(out)]) == 0
    assert "1 / 1 caught" in out.read_text()
    weak = mutations_file(tmp_path, [{"file": "calc.py", "find": "x * 2", "replace": "x * 3"}])
    assert plant_bugs.main([str(weak), "--test-cmd", f"{PY} check.py", "--repo", str(repo)]) == 1
    (tmp_path / "bad.json").write_text("[]")
    assert plant_bugs.main([str(tmp_path / "bad.json"), "--test-cmd", "x", "--repo", str(repo)]) == 2


# ---------- agent_ledger.py ----------
def log(ledger, *extra):
    return agent_ledger.main(["--ledger", str(ledger), "log", *extra])


def test_ledger_log_and_summary(tmp_path, capsys):
    ledger = tmp_path / "l.jsonl"
    log(ledger, "--role", "builder", "--model", "opus", "--tokens", "1000000", "--tool-uses", "40",
        "--duration-ms", "600000", "--batch", "A", "--result", "commit abc")
    log(ledger, "--role", "tester", "--model", "sonnet", "--tokens", "500000", "--tool-uses", "30",
        "--duration-ms", "300000", "--batch", "A", "--round", "1", "--result", "176 | passed")
    capsys.readouterr()
    agent_ledger.main(["--ledger", str(ledger), "summary"])
    out = capsys.readouterr().out
    assert "$5.60" in out and "$1.40" in out        # 1M opus tokens, 0.5M sonnet tokens
    assert "1,500,000 tokens" in out and "15m00s" in out
    assert "176 / passed" in out                    # pipes can't break the table


def test_ledger_price_override_and_unknown_model(tmp_path, capsys):
    ledger = tmp_path / "l.jsonl"
    log(ledger, "--role", "fixer", "--model", "claude-sonnet-5-5", "--tokens", "2000000")
    log(ledger, "--role", "other", "--model", "mystery", "--tokens", "10")
    capsys.readouterr()
    agent_ledger.main(["--ledger", str(ledger), "summary", "--price", "sonnet=1.0"])
    out = capsys.readouterr().out
    assert "$2.00" in out and "n/a" in out


def test_ledger_skips_bad_lines_and_handles_empty(tmp_path, capsys):
    ledger = tmp_path / "l.jsonl"
    agent_ledger.main(["--ledger", str(ledger), "summary"])
    assert "no runs logged" in capsys.readouterr().out
    ledger.write_text('{"role": "tester", "model": "sonnet", "tokens": 10}\nnot json\n\n')
    agent_ledger.main(["--ledger", str(ledger), "summary", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data["runs"]) == 1


def test_ledger_rejects_negative_numbers(tmp_path):
    with pytest.raises(SystemExit):
        log(tmp_path / "l.jsonl", "--role", "tester", "--model", "sonnet", "--tokens", "-1")
