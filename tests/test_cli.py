import json

from surface_zero.cli import main


def test_cli_synthetic_success_and_failure_then_list(tmp_path, capsys):
    db = str(tmp_path / "l.db")
    assert main(["synthetic", "--db", db, "--mode", "success", "--key", "a"]) == 0
    assert main(["synthetic", "--db", db, "--mode", "failure", "--key", "b"]) == 0
    capsys.readouterr()
    assert main(["runs", "--db", db]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [r["status"] for r in rows] == ["succeeded", "failed"]
