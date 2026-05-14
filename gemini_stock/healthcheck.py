from __future__ import annotations

import argparse
import sys

import requests

from gemini_stock.config import load_settings
from gemini_stock.health import evaluate_worker_health
from gemini_stock.schedule import get_schedule_decision
from gemini_stock.storage.db import Database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["dashboard", "worker"])
    args = parser.parse_args()
    return _check_dashboard() if args.target == "dashboard" else _check_worker()


def _check_dashboard() -> int:
    try:
        response = requests.get("http://127.0.0.1:8000/api/health", timeout=5)
        response.raise_for_status()
    except Exception:
        return 1
    return 0


def _check_worker() -> int:
    settings = load_settings()
    db = Database(settings.database_path)
    latest_worker_at = db.get_latest_worker_activity_time() if db.path.exists() else None
    health = evaluate_worker_health(
        latest_worker_at,
        get_schedule_decision(),
        stale_after_intervals=settings.worker_stale_after_intervals,
    )
    return 0 if not health["is_stale"] else 1


if __name__ == "__main__":
    sys.exit(main())
