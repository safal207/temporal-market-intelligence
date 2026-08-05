from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tmi.experiment import build_preflight_report, load_experiment_plan, main

PLAN = Path("experiments/bls-employment-2026-08-07-btc-down.plan.json")


def test_first_experiment_plan_is_ready_before_deadline() -> None:
    plan = load_experiment_plan(PLAN)

    report = build_preflight_report(
        plan,
        now=datetime(2026, 8, 4, 19, 30, tzinfo=UTC),
    )

    assert report["status"] == "ready_for_preregistration"
    assert report["scheduled_event_at"] == "2026-08-07T12:30:00+00:00"
    assert report["capture_seconds"] == 5400
    assert report["hypothesis"] == {
        "asset": "BTC-USD",
        "direction": "down",
        "horizon_minutes": 30,
        "minimum_move_pct": 0.5,
    }
    actions = report["operator_actions"]
    assert len(actions) == 8
    assert "tmi.preregister create" in actions[0]["command"]
    assert "smart_market_data_gateway.research_capture" in actions[3]["command"]
    assert "tmi.evidence_anchor prepare" in actions[4]["command"]
    assert "--evidence-anchor-payload" in actions[-1]["command"]


def test_preflight_rejects_late_preregistration() -> None:
    plan = load_experiment_plan(PLAN)

    with pytest.raises(ValueError, match="deadline has passed"):
        build_preflight_report(
            plan,
            now=datetime(2026, 8, 7, 11, 30, tzinfo=UTC),
        )


def test_plan_rejects_capture_window_that_misses_event(tmp_path: Path) -> None:
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["capture"]["end_at"] = "2026-08-07T12:15:00+00:00"
    path = tmp_path / "bad.plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="capture window must contain"):
        load_experiment_plan(path)


def test_plan_rejects_outputs_outside_private_directories(tmp_path: Path) -> None:
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["paths"]["receipt"] = "public/receipt.json"
    path = tmp_path / "bad.plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must be inside recordings"):
        load_experiment_plan(path)


def test_cli_prints_operator_actions(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(
        [
            str(PLAN),
            "--now",
            "2026-08-04T19:30:00Z",
        ]
    )

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["experiment_id"] == "bls-employment-2026-08-07-btc-down"
    assert report["operator_actions"][2]["name"] == "sign hypothesis anchor"
