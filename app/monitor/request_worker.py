"""Run the checks people requested on the online site.

The Vercel site cannot reach the publisher while it blocks cloud servers, so
its "Check now" button saves a request in Vercel Blob. This worker, run on
the office PC, checks each requested edition, uploads the result and removes
the request. A request that fails stays visible on the site with the reason,
so a requested edition is never dropped silently.

    python -m app.monitor.request_worker            # run pending requests once
    python -m app.monitor.request_worker --watch 5  # keep going, every 5 minutes

Nothing runs automatically: start it yourself, or schedule it later.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

from app.core.config import settings
from app.monitor import result_store
from app.monitor.epaper_monitor import PUBLICATIONS, run_edition_check

LOGGER = logging.getLogger(__name__)


def run_pending(report_root: Path, backend: result_store.Backend | None = None, check=run_edition_check) -> int:
    """Process queued requests; returns how many failed."""
    backend = backend or result_store.VercelBlobBackend()
    failed = 0
    for entry in result_store.pending_requests(backend):
        if entry.get("status") != "queued":
            continue
        publication = PUBLICATIONS.get(entry.get("publication", ""))
        try:
            edition_date = date.fromisoformat(entry.get("edition_date", ""))
        except ValueError:
            edition_date = None
        if publication is None or edition_date is None:
            result_store.fail_request(entry, "The request names an unknown newspaper or date.", backend)
            failed += 1
            continue
        label = f"{publication.name} {edition_date}"
        LOGGER.info("Checking requested edition %s", label)
        try:
            check(report_root, publication, edition_date, force=bool(entry.get("force")))
            summary = result_store.publish_edition(report_root, publication, edition_date, backend=backend)
        except Exception as exc:  # every failure must reach the site, not just the log
            LOGGER.exception("Requested check failed for %s", label)
            result_store.fail_request(entry, f"The office PC check failed: {exc}", backend)
            failed += 1
            continue
        result_store.finish_request(publication, edition_date, backend)
        missing = len(summary["image_failures"])
        print(f"Checked and uploaded {label}" + (f"; {missing} image(s) FAILED to upload" if missing else ""))
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run checks requested on the online site.")
    parser.add_argument("--watch", type=float, metavar="MINUTES",
                        help="Keep running, looking for new requests every MINUTES.")
    parser.add_argument("--output", default=str(settings.report_dir), help="Result directory.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not result_store.enabled():
        print("No Blob credentials: set BLOB_READ_WRITE_TOKEN in .env; no requests can be read.")
        return 2
    while True:
        try:
            failed = run_pending(Path(args.output))
        except result_store.StoreError as exc:
            print(f"Could not read the requests: {exc}")
            failed = 1
        if not args.watch:
            return 1 if failed else 0
        time.sleep(max(1.0, args.watch) * 60)


if __name__ == "__main__":
    sys.exit(main())
