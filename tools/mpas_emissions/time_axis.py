"""Time discovery, target schedules, and gap interpolation for emissions.

The workflow intentionally resolves missing inventory times *offline*.  A target
valid time is either read exactly or linearly interpolated between the nearest
available records.  Extrapolation is disabled by default and large gaps are
rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from bisect import bisect_left
from typing import Iterable, Sequence


@dataclass(frozen=True, order=True)
class SourceRecord:
    valid_time: datetime
    path: str
    time_index: int = 0


@dataclass(frozen=True)
class TimeBracket:
    target: datetime
    before: SourceRecord
    after: SourceRecord
    alpha_after: float

    @property
    def exact(self) -> bool:
        return self.before == self.after

    @property
    def gap_seconds(self) -> float:
        return (self.after.valid_time - self.before.valid_time).total_seconds()


def make_schedule(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    if step.total_seconds() <= 0:
        raise ValueError("time step must be positive")
    if end < start:
        raise ValueError("end must not precede start")
    out: list[datetime] = []
    t = start
    while t <= end:
        out.append(t)
        t += step
    return out


def parse_datetime(text: str) -> datetime:
    text = str(text).strip().replace("Z", "")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d_%H:%M:%S",
        "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y%m%d%H", "%Y%m%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"unsupported datetime format: {text!r}")


def resolve_brackets(
    records: Sequence[SourceRecord],
    targets: Iterable[datetime],
    *,
    method: str = "linear",
    max_gap: timedelta | None = None,
    allow_extrapolation: bool = False,
) -> list[TimeBracket]:
    """Resolve source records for every requested target time.

    ``linear`` permits exact records or interpolation between adjacent source
    records. ``nearest`` and ``hold`` are also available for inventories whose
    temporal semantics require them.  Extrapolation is rejected unless
    explicitly enabled.
    """
    method = method.lower()
    if method not in {"linear", "nearest", "hold"}:
        raise ValueError(f"unsupported interpolation method {method!r}")
    uniq: dict[datetime, SourceRecord] = {}
    for rec in records:
        if rec.valid_time in uniq:
            raise ValueError(f"duplicate source valid time {rec.valid_time}: {uniq[rec.valid_time]} and {rec}")
        uniq[rec.valid_time] = rec
    ordered = sorted(uniq.values(), key=lambda r: r.valid_time)
    if not ordered:
        raise ValueError("no source time records")
    times = [r.valid_time for r in ordered]
    out: list[TimeBracket] = []
    for target in targets:
        k = bisect_left(times, target)
        if k < len(times) and times[k] == target:
            rec = ordered[k]
            out.append(TimeBracket(target, rec, rec, 0.0))
            continue

        before = ordered[k - 1] if k > 0 else None
        after = ordered[k] if k < len(ordered) else None
        if before is None or after is None:
            if not allow_extrapolation:
                raise ValueError(
                    f"target {target} lies outside available source range "
                    f"[{times[0]}, {times[-1]}]; extrapolation disabled"
                )
            rec = after if before is None else before
            assert rec is not None
            out.append(TimeBracket(target, rec, rec, 0.0))
            continue

        gap = after.valid_time - before.valid_time
        if max_gap is not None and gap > max_gap:
            raise ValueError(
                f"cannot fill target {target}: bracketing gap {gap} exceeds allowed {max_gap} "
                f"({before.valid_time} -> {after.valid_time})"
            )
        total = gap.total_seconds()
        if total <= 0:
            raise ValueError("source times are not strictly increasing")

        if method == "linear":
            alpha = (target - before.valid_time).total_seconds() / total
            out.append(TimeBracket(target, before, after, float(alpha)))
        elif method == "nearest":
            db = abs((target - before.valid_time).total_seconds())
            da = abs((after.valid_time - target).total_seconds())
            rec = before if db <= da else after
            out.append(TimeBracket(target, rec, rec, 0.0))
        else:  # hold / latest-before
            out.append(TimeBracket(target, before, before, 0.0))
    return out


def resolve_calendar_day_brackets(
    records: Sequence[SourceRecord],
    targets: Iterable[datetime],
    *,
    missing_method: str = "linear",
    max_gap_days: float | None = None,
    allow_extrapolation: bool = False,
) -> list[TimeBracket]:
    """Map sub-daily targets to daily-mean source records by calendar date.

    A source record anywhere within a calendar day (for example QFED files
    timestamped at 12 UTC) represents that entire day's mean and is therefore
    repeated for every requested hour of that same day.  Only *missing source
    days* are interpolated/filled between neighboring daily records.

    This is intentionally distinct from :func:`resolve_brackets`: linearly
    interpolating each hour between adjacent daily means changes the temporal
    semantics of GFAS/QFED and can create artificial intraday trends.
    """
    method = str(missing_method).lower()
    if method not in {"linear", "nearest", "hold"}:
        raise ValueError(f"unsupported daily missing method {method!r}")

    by_day: dict[datetime, SourceRecord] = {}
    for rec in records:
        day = datetime(rec.valid_time.year, rec.valid_time.month, rec.valid_time.day)
        if day in by_day:
            raise ValueError(
                f"multiple source records represent calendar day {day.date()}: "
                f"{by_day[day]} and {rec}; daily_mean semantics require one record/day"
            )
        by_day[day] = rec
    if not by_day:
        raise ValueError("no source time records")

    days = sorted(by_day)
    out: list[TimeBracket] = []
    max_gap = None if max_gap_days in (None, 0) else timedelta(days=float(max_gap_days))

    for target in targets:
        day = datetime(target.year, target.month, target.day)
        if day in by_day:
            rec = by_day[day]
            out.append(TimeBracket(target, rec, rec, 0.0))
            continue

        k = bisect_left(days, day)
        before_day = days[k - 1] if k > 0 else None
        after_day = days[k] if k < len(days) else None
        if before_day is None or after_day is None:
            if not allow_extrapolation:
                raise ValueError(
                    f"target day {day.date()} lies outside available source-day range "
                    f"[{days[0].date()}, {days[-1].date()}]; extrapolation disabled"
                )
            use_day = after_day if before_day is None else before_day
            assert use_day is not None
            rec = by_day[use_day]
            out.append(TimeBracket(target, rec, rec, 0.0))
            continue

        gap = after_day - before_day
        if max_gap is not None and gap > max_gap:
            raise ValueError(
                f"cannot fill missing source day {day.date()}: bracketing daily gap {gap} "
                f"exceeds allowed {max_gap} ({before_day.date()} -> {after_day.date()})"
            )
        before = by_day[before_day]
        after = by_day[after_day]
        if method == "linear":
            alpha = (day - before_day).total_seconds() / gap.total_seconds()
            out.append(TimeBracket(target, before, after, float(alpha)))
        elif method == "nearest":
            rec = before if (day - before_day) <= (after_day - day) else after
            out.append(TimeBracket(target, rec, rec, 0.0))
        else:
            out.append(TimeBracket(target, before, before, 0.0))
    return out
