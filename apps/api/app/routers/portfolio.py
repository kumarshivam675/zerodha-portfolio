from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import DailyBrief, Snapshot
from ..services import jobs, kite, portfolio, prices, trades

settings = get_settings()
router = APIRouter(prefix="/api")


# --- Zerodha ---------------------------------------------------------------

@router.get("/kite/status")
def kite_status(db: Session = Depends(get_db)):
    return kite.status(db)


@router.get("/kite/login")
def kite_login():
    try:
        return RedirectResponse(kite.login_url())
    except kite.KiteNotConnected as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/kite/callback")
def kite_callback(request_token: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    """Redirect URL registered in the Kite developer console."""
    if status != "success" or not request_token:
        return RedirectResponse(f"{settings.web_url}/?kite=failed")
    try:
        kite.complete_login(db, request_token)
        kite.sync_holdings(db)
    except Exception:
        return RedirectResponse(f"{settings.web_url}/?kite=failed")
    return RedirectResponse(f"{settings.web_url}/?kite=connected")


@router.post("/kite/sync")
def kite_sync(db: Session = Depends(get_db)):
    try:
        return kite.sync_holdings(db)
    except kite.KiteNotConnected as exc:
        raise HTTPException(401, str(exc)) from exc


# --- Portfolio & snapshots -------------------------------------------------

@router.get("/portfolio")
def get_portfolio(db: Session = Depends(get_db)):
    return portfolio.current_portfolio(db)


class SnapshotIn(BaseModel):
    note: str | None = None


@router.post("/snapshots")
def create_snapshot(body: SnapshotIn, db: Session = Depends(get_db)):
    try:
        snap = portfolio.take_snapshot(db, source="manual", note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return portfolio.serialize_snapshot(snap)


@router.get("/snapshots")
def list_snapshots(db: Session = Depends(get_db)):
    snaps = db.scalars(select(Snapshot).order_by(Snapshot.snapshot_date.desc()))
    return [portfolio.serialize_snapshot(s) for s in snaps]


@router.get("/snapshots/{snapshot_date}")
def get_snapshot(snapshot_date: date, db: Session = Depends(get_db)):
    snap = db.scalar(select(Snapshot).where(Snapshot.snapshot_date == snapshot_date))
    if not snap:
        raise HTTPException(404, "No snapshot on that date")
    return portfolio.serialize_snapshot(snap, with_holdings=True)


@router.delete("/snapshots/{snapshot_date}")
def delete_snapshot(snapshot_date: date, db: Session = Depends(get_db)):
    snap = db.scalar(select(Snapshot).where(Snapshot.snapshot_date == snapshot_date))
    if not snap:
        raise HTTPException(404, "No snapshot on that date")
    db.delete(snap)
    db.commit()
    return {"deleted": snapshot_date.isoformat()}


# --- Trade history (XIRR) ----------------------------------------------------

class TradebookIn(BaseModel):
    csv: str


@router.post("/trades/import")
def import_tradebook(body: TradebookIn, db: Session = Depends(get_db)):
    """Import a Zerodha Console tradebook CSV (sent as text)."""
    try:
        return trades.import_console_csv(db, body.csv)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/trades/summary")
def trades_summary(db: Session = Depends(get_db)):
    return trades.summary(db)


# --- Prices, briefs, jobs --------------------------------------------------

@router.get("/prices/{exchange}/{tradingsymbol}")
def get_prices(exchange: str, tradingsymbol: str, days: int = 365, db: Session = Depends(get_db)):
    return prices.price_history(db, tradingsymbol, exchange, days)


@router.get("/briefs")
def list_briefs(limit: int = 10, db: Session = Depends(get_db)):
    briefs = db.scalars(select(DailyBrief).order_by(DailyBrief.created_at.desc()).limit(limit))
    return [
        {"id": b.id, "brief_date": b.brief_date.isoformat(), "content": b.content, "created_at": b.created_at.isoformat()}
        for b in briefs
    ]


@router.get("/jobs")
def list_jobs():
    return jobs.recent_runs()


@router.post("/jobs/daily/run", status_code=202)
def run_daily_job(background: BackgroundTasks):
    background.add_task(jobs.run_daily, "manual")
    return {"status": "started"}
