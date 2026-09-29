import json

import pytest
from fastapi.testclient import TestClient

from surface_zero.killswitch import KillSwitch
from surface_zero.ledger import Ledger, OutboundAction, RunStatus
from surface_zero.main import create_app
from tests.test_pipeline import FakePoster

TOKEN = "test-metrics-token"


def noop_agent(payload, ctx):
    raise AssertionError("metrics tests never call the advisor")


@pytest.fixture
def parts(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    app = create_app(
        webhook_secret="whatever",
        ledger=ledger,
        kill_switch=KillSwitch(tmp_path / "kill.json"),
        agent=noop_agent,
        poster=FakePoster(),
        metrics_token=TOKEN,
    )
    return TestClient(app), ledger


def test_no_token_configured_is_401(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    app = create_app(
        webhook_secret="whatever",
        ledger=ledger,
        kill_switch=KillSwitch(tmp_path / "kill.json"),
        agent=noop_agent,
        poster=FakePoster(),
        # metrics_token intentionally omitted, defaults to None
    )
    resp = TestClient(app).get(
        "/metrics/ledger.ndjson", headers={"authorization": f"Bearer {TOKEN}"}
    )
    assert resp.status_code == 401


def test_wrong_token_is_401(parts):
    client, _ = parts
    resp = client.get("/metrics/ledger.ndjson", headers={"authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_missing_header_is_401(parts):
    client, _ = parts
    resp = client.get("/metrics/ledger.ndjson")
    assert resp.status_code == 401


def test_started_run_is_excluded_not_finished_yet(parts):
    client, ledger = parts
    ledger.begin_run(idempotency_key="k1", event_type="issues", target="owner/repo")
    resp = client.get("/metrics/ledger.ndjson", headers={"authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.text == ""


def test_terminal_runs_are_exported_as_ndjson(parts):
    client, ledger = parts
    run, _ = ledger.begin_run(idempotency_key="k1", event_type="issues", target="owner/repo")
    ledger.finish_run(
        run.run_id, status=RunStatus.SUCCEEDED, outbound_action=OutboundAction.COMMENT_POSTED
    )
    ledger.record_llm_call(run.run_id, cost_usd=0.02)
    ledger.record_llm_call(run.run_id, cost_usd=0.01)

    run2, _ = ledger.begin_run(idempotency_key="k2", event_type="issues", target="owner/repo")
    ledger.finish_run(run2.run_id, status=RunStatus.FAILED, error_type="RuntimeError")

    resp = client.get("/metrics/ledger.ndjson", headers={"authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [json.loads(l) for l in resp.text.strip().split("\n")]
    assert len(lines) == 2

    ok_row = next(l for l in lines if l["ok"])
    assert ok_row["cost_usd"] == pytest.approx(0.03)
    assert ok_row["rung"] == "A"
    assert ok_row["surface"] == "github:owner/repo"
    assert ok_row["status"] == "succeeded"
    assert "overridden" not in ok_row
    assert "verified" not in ok_row

    failed_row = next(l for l in lines if not l["ok"])
    assert failed_row["cost_usd"] == 0
    assert failed_row["status"] == "failed"


def test_abstained_and_ignored_runs_are_exported_as_not_ok(parts):
    client, ledger = parts
    run, _ = ledger.begin_run(idempotency_key="k3", event_type="issues", target="owner/repo")
    ledger.finish_run(run.run_id, status=RunStatus.ABSTAINED)

    resp = client.get("/metrics/ledger.ndjson", headers={"authorization": f"Bearer {TOKEN}"})
    line = json.loads(resp.text.strip())
    assert line["ok"] is False
    assert line["status"] == "abstained"


def test_status_field_distinguishes_ignored_from_abstained_and_failed(parts):
    """The whole point of adding `status` (2026-09-29): `ok` alone collapses
    these three into the same `false`, which made it impossible for the
    portfolio's metrics pipeline to tell a real (if unlabeled) abstain apart
    from a webhook delivery that was never processed at all."""
    client, ledger = parts

    run_a, _ = ledger.begin_run(idempotency_key="ka", event_type="issues", target="owner/repo")
    ledger.finish_run(run_a.run_id, status=RunStatus.ABSTAINED)

    run_b, _ = ledger.begin_run(idempotency_key="kb", event_type="issues", target="owner/repo")
    ledger.finish_run(run_b.run_id, status=RunStatus.IGNORED)

    run_c, _ = ledger.begin_run(idempotency_key="kc", event_type="issues", target="owner/repo")
    ledger.finish_run(run_c.run_id, status=RunStatus.FAILED, error_type="RuntimeError")

    resp = client.get("/metrics/ledger.ndjson", headers={"authorization": f"Bearer {TOKEN}"})
    lines = [json.loads(l) for l in resp.text.strip().split("\n")]
    statuses = {l["status"] for l in lines}
    assert statuses == {"abstained", "ignored", "failed"}
    assert all(l["ok"] is False for l in lines)
