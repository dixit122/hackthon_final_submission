from pathlib import Path


def test_openenv_manifest_exists() -> None:
    assert Path("openenv.yaml").exists()
