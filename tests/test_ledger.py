import threading
from datetime import UTC, datetime, timedelta

import pytest

from surface_zero.ledger import Ledger, LedgerError, OutboundAction, RunStatus
from surface_zero.runner import AgentResult, RunContext, RunRequest, run_request
from surface_zero.synthetic import (
    synthetic_abstain_agent,
    synthetic_failure_agent,
    synthetic_success_agent,
)


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.db")


def req(key="evt-1", **kw):
    return RunRequest(
        idempotency_key=key, event_type="synthetic", payload={"title": "t"}, **kw
    )


def test_run_row_exists_before_agent_runs(ledger):
    seen = {}

    def agent(payload, ctx: RunContext) -> AgentResult:
        row = ledger.get_run(ctx.run_id)
        seen["status"] = row.status
        seen["finished_at"] = row.finished_at
        return AgentResult()

    run_request(req(), agent, ledger)
    assert seen["status"] == RunStatus.STARTED
    assert seen["finished_at"] is None


def test_synthetic_success_is_fully_recorded(ledger):
    out = run_request(req(), synthetic_success_agent, ledger)
    run = ledger.get_run(out.run.run_id)
    assert out.duplicate is False
    assert run.status == RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.latency_ms is not None and run.latency_ms >= 0
    assert run.request == {"title": "t"}
    assert run.response["suggested_label"] == "synthetic"
    assert run.error_type is None and run.error_message is None
    assert run.outbound_action == OutboundAction.NONE


def test_synthetic_failure_is_fully_recorded_and_does_not_raise(ledger):
    out = run_request(req(), synthetic_failure_agent, ledger)
    run = ledger.get_run(out.run.run_id)
    assert run.status == RunStatus.FAILED
    assert run.error_type == "RuntimeError"
    assert run.error_message == "synthetic agent failure"
    assert run.finished_at is not None and run.latency_ms is not None
    assert run.response is None
    calls = ledger.llm_calls_for_run(run.run_id)
    assert len(calls) == 1
    assert calls[0].error_type == "SyntheticModelError"


def test_abstention_is_its_own_terminal_state(ledger):
    out = run_request(req(), synthetic_abstain_agent, ledger)
    assert out.run.status == RunStatus.ABSTAINED
    assert out.run.response == {"reason": "synthetic abstention"}


def test_unmeasured_tokens_and_cost_are_null_not_zero(ledger):
    out = run_request(req(), synthetic_success_agent, ledger)
    (call,) = ledger.llm_calls_for_run(out.run.run_id)
    assert call.input_tokens is None
    assert call.output_tokens is None
    assert call.cost_usd is None
    assert call.latency_ms == 0.0


def test_measured_llm_fields_round_trip(ledger):
    record, _ = ledger.begin_run(idempotency_key="k", event_type="e")
    ledger.record_llm_call(
        record.run_id,
        model="m",
        latency_ms=12.5,
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.0031,
    )
    (call,) = ledger.llm_calls_for_run(record.run_id)
    assert (call.input_tokens, call.output_tokens, call.cost_usd) == (100, 20, 0.0031)


def test_duplicate_delivery_does_not_rerun_agent_or_alter_record(ledger):
    calls = []

    def agent(payload, ctx):
        calls.append(1)
        return AgentResult(response={"n": len(calls)})

    first = run_request(req("dup"), agent, ledger)
    second = run_request(req("dup"), agent, ledger)
    assert len(calls) == 1
    assert second.duplicate is True
    assert second.run.run_id == first.run.run_id
    assert second.run == ledger.get_run(first.run.run_id)
    assert len(ledger.list_runs()) == 1


def test_duplicate_of_failed_run_is_still_a_duplicate(ledger):
    run_request(req("dup"), synthetic_failure_agent, ledger)
    again = run_request(req("dup"), synthetic_success_agent, ledger)
    assert again.duplicate is True
    assert again.run.status == RunStatus.FAILED


def test_concurrent_duplicate_deliveries_create_one_run(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        _, created = ledger.begin_run(idempotency_key="race", event_type="e")
        results.append(created)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert len(ledger.list_runs()) == 1


def test_terminal_states_are_immutable(ledger):
    out = run_request(req(), synthetic_success_agent, ledger)
    with pytest.raises(LedgerError, match="immutable"):
        ledger.finish_run(out.run.run_id, status=RunStatus.FAILED)
    assert ledger.get_run(out.run.run_id).status == RunStatus.SUCCEEDED


def test_finish_run_rejects_non_terminal_and_unknown(ledger):
    record, _ = ledger.begin_run(idempotency_key="k", event_type="e")
    with pytest.raises(LedgerError, match="terminal"):
        ledger.finish_run(record.run_id, status=RunStatus.STARTED)
    with pytest.raises(LedgerError, match="unknown"):
        ledger.finish_run("nope", status=RunStatus.SUCCEEDED)


def test_llm_call_for_unknown_run_is_rejected(ledger):
    with pytest.raises(LedgerError, match="unknown run"):
        ledger.record_llm_call("nope", model="m")


def test_records_survive_reopen(tmp_path):
    path = tmp_path / "ledger.db"
    out = run_request(req(), synthetic_success_agent, Ledger(path))
    reopened = Ledger(path)
    assert reopened.get_run(out.run.run_id).status == RunStatus.SUCCEEDED
    assert len(reopened.llm_calls_for_run(out.run.run_id)) == 1


def test_crash_between_begin_and_finish_leaves_findable_orphan(ledger):
    record, _ = ledger.begin_run(idempotency_key="crash", event_type="e")
    assert ledger.find_orphans(older_than_seconds=300) == []
    orphans = ledger.find_orphans(older_than_seconds=-1)
    assert [o.run_id for o in orphans] == [record.run_id]
    assert orphans[0].status == RunStatus.STARTED


def test_timestamps_are_utc_iso(ledger):
    out = run_request(req(), synthetic_success_agent, ledger)
    started = datetime.fromisoformat(out.run.started_at)
    assert started.tzinfo is not None
    assert abs(datetime.now(UTC) - started) < timedelta(seconds=30)


def test_in_memory_ledger_works():
    ledger = Ledger(":memory:")
    out = run_request(req(), synthetic_success_agent, ledger)
    assert ledger.get_run(out.run.run_id).status == RunStatus.SUCCEEDED
