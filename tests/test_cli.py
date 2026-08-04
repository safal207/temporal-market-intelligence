from pathlib import Path

from tmi.cli import analyze_file, analyze_gateway_recording


def test_example_produces_confirmed_report() -> None:
    report = analyze_file(Path("examples/btc_event.json"))

    assert report["event_id"] == "btc-demo-001"
    assert report["asset"] == "BTC/USDT"
    assert report["verdict"] == "confirmed"
    assert report["score"] >= 0.7


def test_gateway_recording_produces_confirmed_report() -> None:
    report = analyze_gateway_recording(
        Path("examples/btc_gateway_event.json"),
        Path("examples/gateway_quotes.jsonl"),
    )

    assert report["event_id"] == "btc-policy-shock-001"
    assert report["verdict"] == "confirmed"
    assert report["features"]["relative_volume"] > 2.0
