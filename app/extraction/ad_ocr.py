"""Text recognition for image-only newspaper objects (tender notices, adverts).

The publisher supplies advertisements as images without text, so tender and
procurement notices are invisible to text matching. The Windows built-in OCR
engine reads them locally; results are cached per publisher object.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

LOGGER = logging.getLogger(__name__)
SCRIPT = Path(__file__).resolve().parent / "ocr_windows.ps1"


class OcrUnavailable(RuntimeError):
    pass


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    # WinRT projections are only available in Windows PowerShell 5.1.
    return shutil.which("powershell.exe") or shutil.which("powershell")


def ocr_available() -> bool:
    return _powershell() is not None and SCRIPT.is_file()


def _run_batch(executable: str, paths: list[Path], timeout: float) -> dict[str, tuple[str, str]]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump([str(path.resolve()) for path in paths], handle)
        list_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-ListPath",
                str(list_path),
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        list_path.unlink(missing_ok=True)
    results: dict[str, tuple[str, str]] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        results[str(item.get("path"))] = (str(item.get("text") or ""), str(item.get("error") or ""))
    if completed.returncode != 0 and not results:
        message = completed.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise OcrUnavailable(f"Windows OCR failed: {message or completed.returncode}")
    return results


def recognize(
    paths: list[Path],
    workers: int = 3,
    timeout: float = 300,
    progress=None,
) -> dict[Path, tuple[str, str]]:
    """Return {path: (text, error)} for each image. Missing results carry an error."""
    executable = _powershell()
    if executable is None or not SCRIPT.is_file():
        raise OcrUnavailable("Windows OCR is not available on this computer.")
    if not paths:
        return {}
    # Small chunks keep progress reporting smooth; each chunk is one process.
    chunk_size = max(1, min(20, -(-len(paths) // workers)))
    chunks = [paths[index : index + chunk_size] for index in range(0, len(paths), chunk_size)]
    combined: dict[str, tuple[str, str]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
        futures = {pool.submit(_run_batch, executable, chunk, timeout): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            try:
                combined.update(future.result())
            except (OcrUnavailable, subprocess.TimeoutExpired, OSError) as exc:
                LOGGER.warning("OCR batch failed: %s", exc)
                for path in chunk:
                    combined.setdefault(str(path.resolve()), ("", f"OCR failed: {exc}"[:200]))
            done += len(chunk)
            if progress:
                progress(done, len(paths))
    return {
        path: combined.get(str(path.resolve()), ("", "OCR returned no result for this image."))
        for path in paths
    }
