import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_importing_the_module_writes_no_log_file(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-c", "import manga_dl"], cwd=tmp_path, env=env, check=True)
    assert list(tmp_path.iterdir()) == []


def test_help_writes_no_log_file(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(
        [sys.executable, "-m", "manga_dl", "--help"],
        cwd=tmp_path, env=env, check=True, capture_output=True,
    )
    assert list(tmp_path.iterdir()) == []
