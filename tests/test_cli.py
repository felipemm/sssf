from sssf.cli import main


def test_version_flag():
    assert main(["--version"]) == 0
