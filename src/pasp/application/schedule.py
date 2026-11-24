"""Задания, которые сами выполняют запрос и создают файл выгрузки."""

from __future__ import annotations

import calendar
import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

from pasp.paths import schedules_path


@dataclass
class ScheduleJob:
    id: str
    name: str
    enabled: bool
    profile_id: str
    sql: str
    target_schema: str
    target_table: str
    output_dir: str
    mode: str
    day: int
    hour: int
    minute: int
    dates: list[str] = field(default_factory=list)
    identity: bool = False
    last_slot: str = ""
    ran_dates: list[str] = field(default_factory=list)
    last_run: str = ""
    last_error: str = ""
    retry_after: str = ""

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def describe_job(job: ScheduleJob) -> str:
    clock = f"{job.hour:02d}:{job.minute:02d}"
    if job.mode == "dates":
        when = ", ".join(job.dates) if job.dates else "даты не заданы"
        return f"{when} в {clock}"
    return f"каждый месяц, {job.day}-е число, в {clock}"


def due_slot(job: ScheduleJob, now: datetime) -> str | None:
    """Слот, который пора выполнить. Пропущенный прошлый день не догоняется."""
    if not job.enabled:
        return None
    if job.retry_after:
        try:
            if now < datetime.fromisoformat(job.retry_after):
                return None
        except ValueError:
            pass
    if job.mode == "dates":
        today = now.date().isoformat()
        if today not in job.dates or today in job.ran_dates:
            return None
        scheduled = now.replace(hour=job.hour, minute=job.minute, second=0, microsecond=0)
        if now < scheduled:
            return None
        return today
    last_day = calendar.monthrange(now.year, now.month)[1]
    day = min(max(job.day, 1), last_day)
    scheduled = now.replace(day=day, hour=job.hour, minute=job.minute, second=0, microsecond=0)
    if now.date() != scheduled.date() or now < scheduled:
        return None
    slot = scheduled.date().isoformat()
    if job.last_slot == slot:
        return None
    return slot


def load_jobs() -> list[ScheduleJob]:
    path = schedules_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    jobs: list[ScheduleJob] = []
    for item in payload.get("jobs", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mode = item.get("mode")
        jobs.append(
            ScheduleJob(
                id=str(item["id"]),
                name=str(item.get("name") or "Задание"),
                enabled=bool(item.get("enabled", True)),
                profile_id=str(item.get("profile_id") or ""),
                sql=str(item.get("sql") or ""),
                target_schema=str(item.get("target_schema") or "dbo"),
                target_table=str(item.get("target_table") or ""),
                output_dir=str(item.get("output_dir") or ""),
                mode=mode if mode in {"monthly", "dates"} else "monthly",
                day=int(item.get("day") or 1),
                hour=int(item.get("hour") or 12),
                minute=int(item.get("minute") or 0),
                dates=[str(value) for value in item.get("dates", [])],
                identity=bool(item.get("identity", False)),
                last_slot=str(item.get("last_slot") or ""),
                ran_dates=[str(value) for value in item.get("ran_dates", [])],
                last_run=str(item.get("last_run") or ""),
                last_error=str(item.get("last_error") or ""),
                retry_after=str(item.get("retry_after") or ""),
            )
        )
    return jobs


def save_jobs(jobs: list[ScheduleJob]) -> None:
    path = schedules_path()
    temporary = path.with_suffix(".json.tmp")
    payload = {"jobs": [asdict(job) for job in jobs]}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def upsert_job(job: ScheduleJob) -> None:
    others = [item for item in load_jobs() if item.id != job.id]
    save_jobs([job, *others])


def delete_job(job_id: str) -> None:
    save_jobs([item for item in load_jobs() if item.id != job_id])


def update_job(job_id: str, **changes) -> None:
    jobs = load_jobs()
    for index, job in enumerate(jobs):
        if job.id == job_id:
            jobs[index] = replace(job, **changes)
            save_jobs(jobs)
            return
