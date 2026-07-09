# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterator, Set, Tuple

from mcdr2restic.core.models import CronError


class CronExpression:
    def __init__(self, expression: str):
        self.expression = expression.strip()
        fields = self.expression.split()
        if len(fields) != 6:
            raise CronError('error.cron.fields')

        self.seconds, _ = self._parse_field(fields[0], 0, 59)
        self.minutes, _ = self._parse_field(fields[1], 0, 59)
        self.hours, _ = self._parse_field(fields[2], 0, 23)
        self.days, self.any_day = self._parse_field(fields[3], 1, 31)
        self.months, _ = self._parse_field(fields[4], 1, 12)
        self.weekdays, self.any_weekday = self._parse_field(fields[5], 0, 7, sunday_7=True)

    @staticmethod
    def _parse_field(text: str, minimum: int, maximum: int, sunday_7: bool = False) -> Tuple[Set[int], bool]:
        values: Set[int] = set()
        wildcard = text == '*'
        for part in text.split(','):
            field_part = parse_field_part(part, minimum, maximum)
            wildcard = wildcard or field_part.wildcard
            values.update(iter_field_values(field_part, minimum, maximum, sunday_7))

        return values, wildcard

    def next_after(self, after: datetime) -> datetime:
        start = after.replace(microsecond=0) + timedelta(seconds=1)
        for candidate in self.iter_candidate_datetimes(start):
            return candidate
        raise CronError('error.cron.timeout')

    def iter_candidate_datetimes(self, start: datetime) -> Iterator[datetime]:
        for day in self.iter_candidate_days(start):
            yield from self.iter_candidate_times_on_day(start, day)

    def iter_candidate_days(self, start: datetime) -> Iterator[date]:
        for day_offset in range(0, 366 * 5):
            day = (start + timedelta(days=day_offset)).date()
            if day.month not in self.months:
                continue
            if self._day_matches(day):
                yield day

    def iter_candidate_times_on_day(self, start: datetime, day: date) -> Iterator[datetime]:
        for hour in self.iter_candidate_hours(start, day):
            yield from self.iter_candidate_minutes_on_hour(start, day, hour)

    def iter_candidate_hours(self, start: datetime, day: date) -> Iterator[int]:
        for hour in sorted(self.hours):
            if self.is_start_day(day, start) and hour < start.hour:
                continue
            yield hour

    def iter_candidate_minutes_on_hour(self, start: datetime, day: date, hour: int) -> Iterator[datetime]:
        for minute in self.iter_candidate_minutes(start, day, hour):
            yield from self.iter_candidate_seconds_on_minute(start, day, hour, minute)

    def iter_candidate_minutes(self, start: datetime, day: date, hour: int) -> Iterator[int]:
        for minute in sorted(self.minutes):
            if self.is_start_hour(day, hour, start) and minute < start.minute:
                continue
            yield minute

    def iter_candidate_seconds_on_minute(
        self,
        start: datetime,
        day: date,
        hour: int,
        minute: int
    ) -> Iterator[datetime]:
        for second in sorted(self.seconds):
            if self.is_start_minute(day, hour, minute, start) and second < start.second:
                continue
            yield datetime(day.year, day.month, day.day, hour, minute, second)

    @staticmethod
    def is_start_day(day: date, start: datetime) -> bool:
        return day == start.date()

    @staticmethod
    def is_start_hour(day: date, hour: int, start: datetime) -> bool:
        return CronExpression.is_start_day(day, start) and hour == start.hour

    @staticmethod
    def is_start_minute(day: date, hour: int, minute: int, start: datetime) -> bool:
        return CronExpression.is_start_hour(day, hour, start) and minute == start.minute

    def _day_matches(self, day) -> bool:
        dom_match = day.day in self.days
        cron_weekday = (day.weekday() + 1) % 7
        dow_match = cron_weekday in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return dow_match
        if self.any_weekday:
            return dom_match
        return dom_match or dow_match


class FieldPart:
    def __init__(self, start: int, end: int, step: int, wildcard: bool):
        self.start = start
        self.end = end
        self.step = step
        self.wildcard = wildcard


def parse_field_part(text: str, minimum: int, maximum: int) -> FieldPart:
    part = text.strip()
    if not part:
        raise CronError('error.cron.empty_part')
    base, step = split_step(part)
    start, end, wildcard = parse_range(base, minimum, maximum)
    if start > end:
        raise CronError('error.cron.range_order', value=base)
    return FieldPart(start, end, step, wildcard)


def split_step(part: str) -> Tuple[str, int]:
    if '/' not in part:
        return part, 1
    base, step_text = part.split('/', 1)
    try:
        step = int(step_text)
    except ValueError:
        raise CronError('error.cron.step_not_integer', value=step_text)
    if step <= 0:
        raise CronError('error.cron.step_positive')
    return base, step


def parse_range(base: str, minimum: int, maximum: int) -> Tuple[int, int, bool]:
    if base == '*':
        return minimum, maximum, True
    if '-' in base:
        start_text, end_text = base.split('-', 1)
        return int(start_text), int(end_text), False
    value = int(base)
    return value, value, False


def iter_field_values(part: FieldPart, minimum: int, maximum: int, sunday_7: bool) -> Iterator[int]:
    for value in range(part.start, part.end + 1, part.step):
        if value < minimum or value > maximum:
            raise CronError('error.cron.value_out_of_bounds', value=value)
        yield 0 if sunday_7 and value == 7 else value
