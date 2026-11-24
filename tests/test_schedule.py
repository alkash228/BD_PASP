from datetime import datetime

from pasp.application.schedule import ScheduleJob, due_slot


def _job(**changes) -> ScheduleJob:
    base = dict(
        id="1",
        name="Выгрузка",
        enabled=True,
        profile_id="p",
        sql="select 1",
        target_schema="dbo",
        target_table="t",
        output_dir=r"D:\export",
        mode="monthly",
        day=1,
        hour=12,
        minute=0,
    )
    base.update(changes)
    return ScheduleJob(**base)


def test_monthly_runs_on_the_first_after_noon():
    job = _job()
    assert due_slot(job, datetime(2026, 11, 1, 12, 0)) == "2026-11-01"
    assert due_slot(job, datetime(2026, 11, 1, 11, 59)) is None
    assert due_slot(job, datetime(2026, 11, 2, 12, 0)) is None


def test_monthly_does_not_repeat_the_same_day():
    job = _job(last_slot="2026-11-01")
    assert due_slot(job, datetime(2026, 11, 1, 18, 0)) is None


def test_dates_run_only_on_listed_days_and_remember_the_folder():
    job = _job(mode="dates", dates=["2026-11-24"], output_dir=r"D:\reports")
    assert job.output_dir == r"D:\reports"
    assert due_slot(job, datetime(2026, 11, 24, 12, 5)) == "2026-11-24"
    done = _job(mode="dates", dates=["2026-11-24"], ran_dates=["2026-11-24"])
    assert due_slot(done, datetime(2026, 11, 24, 13, 0)) is None


def test_retry_pause_blocks_the_slot():
    job = _job(retry_after="2026-11-01T12:15:00")
    assert due_slot(job, datetime(2026, 11, 1, 12, 10)) is None
    assert due_slot(job, datetime(2026, 11, 1, 12, 16)) == "2026-11-01"
