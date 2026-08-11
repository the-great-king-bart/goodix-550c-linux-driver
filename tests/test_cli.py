from pathlib import Path

import goodix550c.cli as cli
from goodix550c.cli import main


def test_live_probe_requires_explicit_gate(capsys):
    assert main(["probe"]) == 2
    assert "explicit read-only probe flag is required" in capsys.readouterr().err


def test_packet_preview_is_offline(capsys):
    assert main(["packets"]) == 0
    output = capsys.readouterr().out
    assert "a00600a6a803000000ff" in output


def test_dpapi_backup_requires_second_safety_gate(capsys):
    assert (
        main(
            [
                "read-dpapi-backup",
                "--i-understand-this-sends-two-read-only-requests",
                "--output",
                "research/secrets/test.bin",
            ]
        )
        == 2
    )
    assert "machine-sealed-secret flag is required" in capsys.readouterr().err


def test_dpapi_backup_never_prints_blob(monkeypatch, capsys):
    marker = bytes.fromhex("feedface")
    output = Path.cwd() / "research/secrets/test.bin"
    monkeypatch.setattr(cli, "read_dpapi_backup", lambda: (marker, {"length": len(marker)}))
    monkeypatch.setattr(cli, "write_private_blob", lambda blob, path: output)

    assert (
        main(
            [
                "read-dpapi-backup",
                "--i-understand-this-sends-two-read-only-requests",
                "--i-understand-this-reads-a-machine-sealed-secret",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    rendered = capsys.readouterr().out
    assert "feedface" not in rendered
    assert '"length": 4' in rendered
