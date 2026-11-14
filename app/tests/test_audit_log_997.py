"""#997: the audit log is one append-only file per UTC day, safe for several
gunicorn workers, kept RETENTION_DAYS, and lives on a host volume."""
import json
import logging
import os
import stat
from datetime import datetime, timezone

import pytest

from src.services import audit_log
from src.services.audit_log import DailyAppendHandler


class Clock:
    def __init__(self, iso):
        self.now = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def _rec(msg):
    return logging.LogRecord("audit", logging.INFO, __file__, 1, msg, None, None)


def _lines(path):
    with open(path) as f:
        return f.read().splitlines()


def test_a_record_goes_to_the_file_of_its_UTC_day(tmp_path):
    clock = Clock("2026-11-14T23:59:59")
    h = DailyAppendHandler(str(tmp_path), clock=clock)
    h.emit(_rec("before midnight"))
    clock.now = datetime(2026, 11, 15, 0, 0, 1, tzinfo=timezone.utc)
    h.emit(_rec("after midnight"))
    h.close()
    assert _lines(tmp_path / "audit-2026-11-14.log") == ["before midnight"]
    assert _lines(tmp_path / "audit-2026-11-15.log") == ["after midnight"]


def test_two_workers_on_one_directory_lose_nothing_across_midnight(tmp_path):
    """The defect TimedRotatingFileHandler has with two processes: each
    renames at midnight, and one rename moves the other's new file. Two
    handlers here stand in for two workers; nothing is renamed, so every
    line survives in the right day's file."""
    c1, c2 = Clock("2026-11-14T23:59:58"), Clock("2026-11-14T23:59:58")
    w1 = DailyAppendHandler(str(tmp_path), clock=c1)
    w2 = DailyAppendHandler(str(tmp_path), clock=c2)
    for i in range(50):
        w1.emit(_rec(f"w1-a{i}")); w2.emit(_rec(f"w2-a{i}"))
    c1.now = c2.now = datetime(2026, 11, 15, 0, 0, 0, tzinfo=timezone.utc)
    w1.emit(_rec("w1-b")); w2.emit(_rec("w2-b"))
    w1.close(); w2.close()
    day1 = _lines(tmp_path / "audit-2026-11-14.log")
    day2 = _lines(tmp_path / "audit-2026-11-15.log")
    assert sorted(day1) == sorted([f"w1-a{i}" for i in range(50)] + [f"w2-a{i}" for i in range(50)])
    assert sorted(day2) == ["w1-b", "w2-b"]
    assert sorted(os.listdir(tmp_path)) == ["audit-2026-11-14.log", "audit-2026-11-15.log"]


def test_files_older_than_retention_are_deleted_and_nothing_else(tmp_path):
    for name in ("audit-2026-08-15.log", "audit-2026-08-16.log", "audit-2026-11-13.log",
                 "app.log", "audit.log", "notes.txt"):
        (tmp_path / name).write_text("x\n")
    h = DailyAppendHandler(str(tmp_path), retention_days=90, clock=Clock("2026-11-14T08:00:00"))
    h.emit(_rec("today")); h.close()
    # 2026-11-14 minus 90 days = 2026-08-16: older than that goes, that day stays
    assert sorted(os.listdir(tmp_path)) == [
        "app.log", "audit-2026-08-16.log", "audit-2026-11-13.log", "audit-2026-11-14.log",
        "audit.log", "notes.txt"]


def test_the_file_is_created_600(tmp_path):
    h = DailyAppendHandler(str(tmp_path), clock=Clock("2026-11-14T08:00:00"))
    h.emit(_rec("x")); h.close()
    mode = stat.S_IMODE(os.stat(tmp_path / "audit-2026-11-14.log").st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_init_and_audit_write_json_to_the_dated_file(tmp_path, monkeypatch):
    lg = logging.getLogger("audit")
    saved = list(lg.handlers)
    for hd in saved:
        lg.removeHandler(hd)
    try:
        audit_log.init_audit_log(str(tmp_path))
        assert [type(hd) for hd in lg.handlers] == [DailyAppendHandler]
        audit_log.audit("login_success", user_guid="u-997", detail={"k": 1}, ip="1.2.3.4")
        files = [n for n in os.listdir(tmp_path) if n.startswith("audit-")]
        assert len(files) == 1
        entry = json.loads(_lines(tmp_path / files[0])[-1])
        assert entry["event"] == "login_success" and entry["user_guid"] == "u-997"
    finally:
        for hd in list(lg.handlers):
            lg.removeHandler(hd); hd.close()
        for hd in saved:
            lg.addHandler(hd)
