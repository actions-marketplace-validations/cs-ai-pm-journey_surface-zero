"""Small CLI for exercising the ledger path by hand."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from surface_zero.advisor import Advisor
from surface_zero.labels import LabelSet
from surface_zero.ledger import Ledger
from surface_zero.runner import RunRequest, run_request
from surface_zero.synthetic import (
    synthetic_abstain_agent,
    synthetic_failure_agent,
    synthetic_success_agent,
)

DEFAULT_DB = "data/ledger.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="surface-zero")
    sub = parser.add_subparsers(dest="cmd", required=True)

    syn = sub.add_parser("synthetic", help="push a fake request through the ledger path")
    syn.add_argument("--db", default=DEFAULT_DB)
    syn.add_argument("--mode", choices=["success", "failure", "abstain"], default="success")
    syn.add_argument("--key", default=None, help="idempotency key (default: random)")

    runs = sub.add_parser("runs", help="list ledger runs")
    runs.add_argument("--db", default=DEFAULT_DB)

    advise = sub.add_parser("advise", help="run a real issue through the advisor (calls the API)")
    advise.add_argument("--db", default=DEFAULT_DB)
    advise.add_argument("--labels", default="data/label_sets/fixture-repo.json")
    advise.add_argument("--title", required=True)
    advise.add_argument("--body", default="")
    advise.add_argument("--key", default=None)
    advise.add_argument("--number", type=int, default=None, help="this issue's own GitHub issue number")
    advise.add_argument(
        "--candidates",
        default=None,
        help=(
            'path to a JSON file listing other currently-open issues for duplicate '
            'comparison, e.g. [{"number": 12, "title": "...", "body": "..."}]'
        ),
    )

    args = parser.parse_args(argv)
    ledger = Ledger(args.db)

    if args.cmd == "synthetic":
        agent = {
            "success": synthetic_success_agent,
            "failure": synthetic_failure_agent,
            "abstain": synthetic_abstain_agent,
        }[args.mode]
        request = RunRequest(
            idempotency_key=args.key or f"synthetic-{uuid.uuid4().hex[:12]}",
            event_type="synthetic",
            payload={"title": "fake issue", "body": "synthetic request"},
        )
        outcome = run_request(request, agent, ledger)
        print(outcome.model_dump_json(indent=2))
        return 0

    if args.cmd == "advise":
        label_set = LabelSet.from_file(args.labels)
        advisor = Advisor.from_env(label_set)
        payload = {"title": args.title, "body": args.body}
        if args.number is not None:
            payload["number"] = args.number
        if args.candidates:
            payload["candidates"] = json.loads(Path(args.candidates).read_text())
        request = RunRequest(
            idempotency_key=args.key or f"advise-{uuid.uuid4().hex[:12]}",
            event_type="issue.opened",
            target=label_set.repo,
            payload=payload,
        )
        outcome = run_request(request, advisor, ledger)
        print(outcome.model_dump_json(indent=2))
        return 0

    for run in ledger.list_runs():
        print(json.dumps(run.model_dump(mode="json")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
