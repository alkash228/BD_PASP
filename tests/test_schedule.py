from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from pasp.application.schedule import ScheduleJob, due_slot, existing_export, scheduled_export_name


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


class ScheduleTests(unittest.TestCase):
    def test_monthly_runs_on_the_first_after_noon(self):
        job = _job()
        self.assertEqual(due_slot(job, datetime(2026, 11, 1, 12, 0)), "2026-11-01")
        self.assertIsNone(due_slot(job, datetime(2026, 11, 1, 11, 59)))
        self.assertIsNone(due_slot(job, datetime(2026, 11, 2, 12, 0)))

    def test_monthly_does_not_repeat_the_same_day(self):
        job = _job(last_slot="2026-11-01")
        self.assertIsNone(due_slot(job, datetime(2026, 11, 1, 18, 0)))

    def test_dates_run_only_on_listed_days_and_remember_the_folder(self):
        job = _job(mode="dates", dates=["2026-11-24"], output_dir=r"D:\reports")
        self.assertEqual(job.output_dir, r"D:\reports")
        self.assertEqual(due_slot(job, datetime(2026, 11, 24, 12, 5)), "2026-11-24")
        done = _job(mode="dates", dates=["2026-11-24"], ran_dates=["2026-11-24"])
        self.assertIsNone(due_slot(done, datetime(2026, 11, 24, 13, 0)))

    def test_folder_check_finds_the_same_days_file(self):
        day = "2026-11-01"
        self.assertEqual(scheduled_export_name("dbo", "Orders", day), "dbo.Orders_20261101.sql")
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(existing_export(folder, "dbo", "Orders", day))
            target = Path(folder) / "dbo.Orders_20261101.sql"
            target.write_text("INSERT", encoding="utf-8")
            self.assertEqual(existing_export(folder, "dbo", "Orders", day), target)

    def test_retry_pause_blocks_the_slot(self):
        job = _job(retry_after="2026-11-01T12:15:00")
        self.assertIsNone(due_slot(job, datetime(2026, 11, 1, 12, 10)))
        self.assertEqual(due_slot(job, datetime(2026, 11, 1, 12, 16)), "2026-11-01")
