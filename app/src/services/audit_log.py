"""File-based structured audit logging.

Logs: login attempts, admin actions, access decisions, token revocations.
Writes JSON lines to LOG_DIR, ONE FILE PER UTC DAY: audit-YYYY-MM-DD.log.

Why not TimedRotatingFileHandler (#997). gunicorn runs two workers, each with
its own handler on the same file. At midnight both try to RENAME audit.log to
the dated name; the second rename moves the first worker's fresh file, so
lines are lost or land in the wrong day. A dated filename needs no rename:
every worker only APPENDS (O_APPEND, one write per line), and the day's file
is chosen per record. Retention deletes dated files older than
RETENTION_DAYS; a race there is harmless (unlink of a file already gone).

LOG_DIR must be on a HOST volume (compose mounts ../shared/logs). Inside the
container's own filesystem the log was erased at every deploy (#997).
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

_audit_logger = None

RETENTION_DAYS = 90
_DATED = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.log$")


class DailyAppendHandler(logging.Handler):
    """Append each record to <dir>/audit-<UTC date>.log. Safe for several
    processes writing the same directory: nothing is ever renamed."""

    def __init__(self, log_dir, retention_days=RETENTION_DAYS, clock=None):
        super().__init__()
        self.log_dir = log_dir
        self.retention_days = retention_days
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._day = None
        self._fd = None

    def _path(self, day):
        return os.path.join(self.log_dir, f"audit-{day}.log")

    def _open(self, day):
        if self._fd is not None:
            os.close(self._fd)
        path = self._path(day)
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self._day = day
        self._prune(day)

    def _prune(self, today):
        cutoff = (datetime.strptime(today, "%Y-%m-%d")
                  - timedelta(days=self.retention_days)).strftime("%Y-%m-%d")
        for name in os.listdir(self.log_dir):
            m = _DATED.match(name)
            if m and m.group(1) < cutoff:
                try:
                    os.unlink(os.path.join(self.log_dir, name))
                except FileNotFoundError:
                    pass

    def emit(self, record):
        try:
            day = self._clock().strftime("%Y-%m-%d")
            self.acquire()
            try:
                if day != self._day:
                    self._open(day)
                os.write(self._fd, (self.format(record) + "\n").encode("utf-8"))
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def close(self):
        self.acquire()
        try:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
        finally:
            self.release()
        super().close()


def init_audit_log(log_dir):
    """Initialise the audit logger: one append-only file per UTC day."""
    global _audit_logger

    os.makedirs(log_dir, exist_ok=True)

    _audit_logger = logging.getLogger('audit')
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False

    if not _audit_logger.handlers:
        handler = DailyAppendHandler(log_dir)
        handler.setFormatter(logging.Formatter('%(message)s'))
        _audit_logger.addHandler(handler)


def audit(event, user_guid=None, detail=None, ip=None):
    """Write a structured audit log entry.

    Args:
        event: Event type (e.g. 'login_success', 'admin_promote_su')
        user_guid: GUID of the acting user (if authenticated)
        detail: Dict with additional context
        ip: Client IP address
    """
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event': event,
        'user_guid': user_guid,
        'ip': ip,
        'detail': detail or {},
    }

    if _audit_logger:
        _audit_logger.info(json.dumps(entry, default=str))
