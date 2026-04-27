"""
ECO Technology — Automated Scheduler
=====================================
Runs inside the same Render process as webhook_server.py.
Add this import to webhook_server.py startup to activate.

Schedule (UAE = UTC+4):
  07:00 UAE daily  → daily_report.main()  (report + Touch 2/3 auto-fire)
  Every 6 hours    → run_overdue_touches() (safety net for urgent leads)
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

log = logging.getLogger(__name__)
UAE = pytz.timezone("Asia/Dubai")


def start_scheduler():
    from daily_report import main as run_daily_report, run_overdue_touches

    scheduler = BackgroundScheduler(timezone=UAE)

    # Daily report at 7:00 AM UAE time
    scheduler.add_job(
        run_daily_report,
        CronTrigger(hour=7, minute=0, timezone=UAE),
        id="daily_report",
        name="Daily Intelligence Report",
        replace_existing=True,
        misfire_grace_time=300,  # 5 min window if server was down
    )

    # Touch 2/3 safety check every 6 hours (catches urgent leads between daily runs)
    scheduler.add_job(
        run_overdue_touches,
        IntervalTrigger(hours=6, timezone=UAE),
        id="touch_check",
        name="Overdue Touch Check",
        replace_existing=True,
    )

    scheduler.start()
    log.info("Scheduler started — daily report 7AM UAE · touch check every 6h")
    return scheduler
