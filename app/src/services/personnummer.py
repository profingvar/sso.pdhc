"""Personnummer shape and the pilot's synthetic-only rule (#1049).

Per-repo copy of the rule in eps (ips.pdhc `gateway/app/services/
personnummer.py`, #789/#990): there is deliberately no shared library across
the PDHC services. What is copied is the CLASSIFICATION, not the builders:

  malformed    not 10 or 12 digits, or not a real date
  synthetic    well-formed, a real date, and a WRONG check digit (#405).
               This is what the platform mints on purpose: it cannot be a
               real person's number.
  real_format  well-formed, a real date, and a CORRECT check digit — it can
               be a real person's.

The pilot takes ONLY synthetic numbers (`PATIENT_REGISTRATION_SYNTHETIC_ONLY`,
default true). So the check digit IS computed now, and the rule is the
opposite of the "obvious" hardening #904 warned about: a VALID number is the
one refused. A synthetic number is never refused for its check digit.

A personnummer is an identifier, never a join key (Rule 18). Never log one in
full; `mask()` is what audit details carry.
"""
import re
from datetime import date

MALFORMED = 'malformed'
SYNTHETIC = 'synthetic'
REAL_FORMAT = 'real_format'


def _luhn_digit(nine):
    total = 0
    for i, ch in enumerate(nine):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10


def normalise(value, today=None):
    """12 digits `YYYYMMDDNNNN`, or None when the shape is wrong.

    Accepts 12 or 10 digits, with or without one `-` (or `+` in the short
    form, meaning the person has turned 100) before the last four. The short
    form's century is inferred as eps does. The date is NOT checked here."""
    v = (value or '').strip().replace(' ', '')
    m = re.fullmatch(r'(\d{8})-?(\d{4})', v)
    if m:
        return m.group(1) + m.group(2)
    m = re.fullmatch(r'(\d{6})([-+]?)(\d{4})', v)
    if not m:
        return None
    today = today or date.today()
    yy = int(m.group(1)[:2])
    year = today.year // 100 * 100 + yy
    if year > today.year:
        year -= 100
    if m.group(2) == '+':
        year -= 100
    return f'{year:04d}{m.group(1)[2:]}{m.group(3)}'


def classify(value):
    """(kind, normalised 12 digits or None)."""
    n = normalise(value)
    if n is None:
        return MALFORMED, None
    try:
        date(int(n[:4]), int(n[4:6]), int(n[6:8]))
    except ValueError:
        return MALFORMED, None
    correct = _luhn_digit(n[2:11]) == int(n[11])
    return (REAL_FORMAT if correct else SYNTHETIC), n


def mask(n):
    """What an audit detail may carry: the birth year and nothing else."""
    return (n or '')[:4] + '****'
