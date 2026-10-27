"""The daily job: refresh from Zerodha (if logged in), fetch EOD prices, snapshot, write a brief."""

import asyncio
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import DailyBrief, JobRun
from . import agent, kite, portfolio, prices

log = logging.getLogger(__name__)
settings = get_settings()
IST = ZoneInfo("Asia/Kolkata")
_lock = threading.Lock()


def run_daily(trigger: str) -> dict:
    if not _lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "already running"}
    try:
        return _run_daily(trigger)
    finally:
        _lock.release()


def _run_daily(trigger: str) -> dict:
    with session_scope() as db:
        run = JobRun(job="daily", trigger=trigger)
        db.add(run)
        db.flush()
        run_id = run.id

    detail: dict = {}
    status = "ok"
    try:
        with session_scope() as db:
            if kite.active_session(db):
                try:
                    detail["kite_sync"] = kite.sync_holdings(db)
                except Exception as exc:
                    detail["kite_sync"] = f"failed: {exc}"
            else:
                detail["kite_sync"] = "skipped: not logged in today (holdings unchanged, prices still refreshed)"

            if not kite.has_holdings(db):
                raise RuntimeError("No holdings yet; connect Zerodha and sync once")

            detail["prices"] = prices.update_equity_prices(db)

            if settings.auto_snapshot:
                snap = portfolio.take_snapshot(db, source="scheduled")
                detail["snapshot"] = snap.snapshot_date.isoformat()

        if settings.auto_brief and settings.agent_configured:
            try:
                content = asyncio.run(agent.write_brief())
                with session_scope() as db:
                    db.add(DailyBrief(brief_date=datetime.now(IST).date(), content=content))
                detail["brief"] = "written"
            except Exception as exc:
                log.exception("brief failed")
                detail["brief"] = f"failed: {exc}"
                status = "partial"
    except Exception as exc:
        log.exception("daily job failed")
        detail["error"] = str(exc)
        status = "failed"

    with session_scope() as db:
        run = db.get(JobRun, run_id)
        run.status = status
        run.detail = detail
        run.finished_at = datetime.now(IST)
    return {"status": status, **detail}


def recent_runs(limit: int = 10) -> list[dict]:
    with session_scope() as db:
        runs = db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit))
        return [
            {
                "id": r.id,
                "job": r.job,
                "trigger": r.trigger,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "status": r.status,
                "detail": r.detail,
            }
            for r in runs
        ]
