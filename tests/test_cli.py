from pathlib import Path

from tmi.cli import analyze_file


def test_example_produces_confirmed_report() -> None:
    report = analyze_file(Path("examples/btc_event.json"))

    assert report["event_id"] == "btc-demo-001"
    assert report["asset"] == "BTC/USDT"
    assert report["verdict"] == "confirmed"
    assert report["score"] >= 0.7
