#!/usr/bin/env python3
"""Record what each agent run cost and took, and summarise it.

The coordinator logs one line each time an agent finishes (Claude Code's task-completion
notice reports total tokens, tool uses and duration), then prints a summary for the user
or the write-up.

    python agent_ledger.py log --role tester --batch A --round 1 --model sonnet \
        --tokens 116524 --tool-uses 33 --duration-ms 678300 --result "176 passed"
    python agent_ledger.py summary              # markdown tables, per run / role / model
    python agent_ledger.py summary --json

The ledger is a JSON-lines file, default <repo>/.agent-team/ledger.jsonl (override with
--ledger). Costs are ROUGH estimates: the notice gives one total token count with no
input/output/cache split, so a single blended $/million-token rate per model is applied
(default table below, override with --price model=dollars_per_mtok). Treat them as an
order of magnitude, not a bill. Standard library only.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

# Blended $ per million tokens, assuming mostly input (~90% input / 10% output, list prices,
# no cache discount). Override per run with --price.
DEFAULT_PRICES = {
    "opus": 5.6,     # $4 in / $20 out
    "sonnet": 2.8,   # $2 in / $10 out
    "haiku": 0.14,   # $0.10 in / $0.50 out
    "fable": 14.0,   # $10 in / $50 out
}
ROLES = ("builder", "tester", "fixer", "coordinator", "other")


def default_ledger():
    return Path.cwd() / ".agent-team" / "ledger.jsonl"


def read(path):
    rows = []
    if not Path(path).exists():
        return rows
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            print(f"warning: skipping unreadable line {n} in {path}", file=sys.stderr)
    return rows


def append(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def model_key(model):
    m = (model or "").lower()
    for key in DEFAULT_PRICES:
        if key in m:
            return key
    return m or "unknown"


def cost(row, prices):
    rate = prices.get(model_key(row.get("model")))
    return None if rate is None else row.get("tokens", 0) / 1e6 * rate


def fmt_secs(s):
    s = int(round(s))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def fmt_cost(c):
    return "n/a" if c is None else f"${c:,.2f}"


def summarise(rows, prices):
    by_role, by_model = defaultdict(lambda: [0, 0, 0.0, 0.0, 0]), defaultdict(lambda: [0, 0, 0.0, 0.0, 0])
    total = [0, 0, 0.0, 0.0, 0]
    for r in rows:
        c = cost(r, prices) or 0.0
        for bucket in (by_role[r.get("role", "other")], by_model[model_key(r.get("model"))], total):
            bucket[0] += r.get("tokens", 0)
            bucket[1] += r.get("tool_uses", 0)
            bucket[2] += r.get("duration_ms", 0) / 1000
            bucket[3] += c
            bucket[4] += 1
    return by_role, by_model, total


def cmd_log(args):
    if args.tokens < 0 or args.duration_ms < 0 or args.tool_uses < 0:
        sys.exit("tokens, tool uses and duration must be >= 0")
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "role": args.role, "model": args.model,
           "batch": args.batch, "round": args.round, "tokens": args.tokens,
           "tool_uses": args.tool_uses, "duration_ms": args.duration_ms, "result": args.result}
    append(args.ledger, {k: v for k, v in row.items() if v is not None})
    print(f"logged {args.role} {args.batch or ''} -> {args.ledger}")
    return 0


def cmd_summary(args):
    rows = read(args.ledger)
    prices = dict(DEFAULT_PRICES)
    for item in args.price or []:
        name, _, value = item.partition("=")
        try:
            prices[model_key(name)] = float(value)
        except ValueError:
            sys.exit(f"bad --price {item!r}; use model=dollars_per_million_tokens")
    by_role, by_model, total = summarise(rows, prices)
    if args.json:
        print(json.dumps({"runs": rows, "by_role": by_role, "by_model": by_model, "total": total,
                          "prices_per_mtok": prices}, indent=1))
        return 0
    if not rows:
        print(f"no runs logged yet in {args.ledger}")
        return 0
    out = ["| # | role | batch | round | model | tokens | tool uses | time | est. cost | result |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | {r.get('role', '')} | {r.get('batch', '')} | {r.get('round', '')} | "
                   f"{r.get('model', '')} | {r.get('tokens', 0):,} | {r.get('tool_uses', 0)} | "
                   f"{fmt_secs(r.get('duration_ms', 0) / 1000)} | {fmt_cost(cost(r, prices))} | "
                   f"{str(r.get('result', '')).replace('|', '/')} |")
    for title, groups in (("role", by_role), ("model", by_model)):
        out += ["", f"| by {title} | runs | tokens | tool uses | agent time | est. cost |",
                "|---|---|---|---|---|---|"]
        for name, (tok, tools, secs, c, n) in sorted(groups.items()):
            out.append(f"| {name} | {n} | {tok:,} | {tools} | {fmt_secs(secs)} | {fmt_cost(c)} |")
    tok, tools, secs, c, n = total
    out += ["", f"**Total:** {n} agent runs, {tok:,} tokens, {tools} tool uses, "
                f"{fmt_secs(secs)} of agent time (agents overlap, so this exceeds wall-clock time), "
                f"estimated {fmt_cost(c)} (rough - see the note at the top of agent_ledger.py)."]
    print("\n".join(out))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Record and summarise agent runs.")
    p.add_argument("--ledger", type=Path, default=None, help="ledger file (default .agent-team/ledger.jsonl)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("log", help="append one finished agent run")
    s.add_argument("--role", required=True, choices=ROLES)
    s.add_argument("--model", required=True, help="opus / sonnet / haiku / fable or a model id")
    s.add_argument("--tokens", type=int, required=True)
    s.add_argument("--tool-uses", type=int, default=0)
    s.add_argument("--duration-ms", type=int, default=0)
    s.add_argument("--batch")
    s.add_argument("--round", type=int)
    s.add_argument("--result", help="one-line outcome, e.g. '176 passed' or 'commit ee09c92'")
    s.set_defaults(fn=cmd_log)
    s = sub.add_parser("summary", help="print the summary")
    s.add_argument("--json", action="store_true")
    s.add_argument("--price", action="append", metavar="MODEL=USD_PER_MTOK")
    s.set_defaults(fn=cmd_summary)
    args = p.parse_args(argv)
    args.ledger = args.ledger or default_ledger()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
