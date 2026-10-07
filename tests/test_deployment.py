import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_vercel_startup_uses_configured_writable_storage():
    env = {**os.environ, "VERCEL": "1", "ADMIN_PASSWORD": "deployment-test-password"}
    for name in ("DATABASE_PATH", "UPLOAD_DIR", "REPORT_DIR"):
        env[name] = str(Path(tempfile.mkdtemp()) / name.lower())
    result = subprocess.run(
        [sys.executable, "-c", (
            "from app.main import app; from fastapi.testclient import TestClient; "
            "c=TestClient(app); assert c.get('/health').status_code == 200; "
            "assert c.get('/monitor').status_code == 401; "
            "assert c.get('/static/styles.css').status_code == 200; "
            "assert c.get('/monitor', auth=('admin', 'deployment-test-password')).status_code == 200"
        )], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_vercel_requires_non_default_admin_password():
    env = {**os.environ, "VERCEL": "1", "ADMIN_PASSWORD": ""}
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import Settings; Settings().prepare_directories()"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "Set a unique ADMIN_PASSWORD" in result.stderr


def test_vercel_default_paths_are_in_temporary_storage():
    env = {**os.environ, "VERCEL": "1"}
    for name in ("DATABASE_PATH", "UPLOAD_DIR", "REPORT_DIR"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", (
            "import dotenv; dotenv.load_dotenv = lambda: None; "
            "from app.config import Settings; from pathlib import Path; import tempfile; "
            "s=Settings(); root=Path(tempfile.gettempdir()); "
            "assert all(p.is_relative_to(root) for p in "
            "(s.database_path, s.upload_dir, s.report_dir))"
        )], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
