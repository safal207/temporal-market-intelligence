import json
import stat
from pathlib import Path

import pytest

from tmi.cli import analyze_file, analyze_gateway_recording, main


def test_example_produces_confirmed_report() -> None:
    report = analyze_file(Path("examples/btc_event.json"))

    assert report["event_id"] == "btc-demo-001"
    assert report["asset"] == "BTC/USDT"
    assert report["verdict"] == "confirmed"
    assert report["score"] >= 0.7
    assert len(report["event_fingerprint_sha256"]) == 64


def test_gateway_recording_produces_confirmed_receipt() -> None:
    report = analyze_gateway_recording(
        Path("examples/btc_gateway_event.json"),
        Path("examples/gateway_quotes.jsonl"),
    )

    assert report["event_id"] == "btc-policy-shock-001"
    assert report["verdict"] == "confirmed"
    assert report["features"]["relative_volume"] > 2.0
    manifest = report["recording_manifest"]
    assert manifest["ledger_verified"] is True
    assert manifest["symbols"] == ["BTC/USDT"]
    assert manifest["ledger_head_hash"] == manifest["evidence_fingerprint_sha256"]


def test_cli_writes_new_private_replay_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "recordings" / "btc.receipt.json"

    result = main(
        [
            "examples/btc_gateway_event.json",
            "--gateway-recording",
            "examples/gateway_quotes.jsonl",
            "--receipt-output",
            str(output),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "confirmed"
    assert payload["recording_manifest"]["ledger_verified"] is True
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    stdout = json.loads(capsys.readouterr().out)
    assert stdout == payload


def test_cli_refuses_receipt_outside_recordings(tmp_path: Path) -> None:
    output = tmp_path / "btc.receipt.json"

    with pytest.raises(SystemExit, match="inside a recordings/ directory"):
        main(
            [
                "examples/btc_gateway_event.json",
                "--gateway-recording",
                "examples/gateway_quotes.jsonl",
                "--receipt-output",
                str(output),
            ]
        )


def test_cli_refuses_to_overwrite_receipt(tmp_path: Path) -> None:
    output = tmp_path / "recordings" / "btc.receipt.json"
    output.parent.mkdir(parents=True)
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="File exists"):
        main(
            [
                "examples/btc_gateway_event.json",
                "--gateway-recording",
                "examples/gateway_quotes.jsonl",
                "--receipt-output",
                str(output),
            ]
        )
