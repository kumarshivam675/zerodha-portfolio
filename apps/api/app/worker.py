"""Scheduler process: runs the daily job on `DAILY_JOB_CRON` (in `TIMEZONE`)."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .db import init_db
from .services import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")


def main() -> None:
    settings = get_settings()
    init_db()
    scheduler = BlockingScheduler(timezone=settings.timezone)
    trigger = CronTrigger.from_crontab(settings.daily_job_cron, timezone=settings.timezone)
    scheduler.add_job(
        lambda: log.info("daily job: %s", jobs.run_daily("schedule")),
        trigger,
        id="daily",
        misfire_grace_time=3600,
        coalesce=True,
    )
    log.info("scheduled daily job: %s (%s)", settings.daily_job_cron, settings.timezone)
    scheduler.start()


if __name__ == "__main__":
    main()
