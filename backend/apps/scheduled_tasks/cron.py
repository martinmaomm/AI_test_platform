"""One Cron contract for validation, Beat registration and next-run display."""
import re
from zoneinfo import ZoneInfo

from celery.schedules import crontab_parser
from django.conf import settings
from django.utils import timezone
from django_celery_beat.tzcrontab import TzAwareCrontab


def schedule_timezone():
    return ZoneInfo(getattr(settings, 'CELERY_TIMEZONE', None) or settings.TIME_ZONE)


def parse_cron(expression):
    if not isinstance(expression, str) or len(expression.split()) != 5:
        raise ValueError('执行时间必须是 5 段 Cron：分 时 日 月 周')
    fields = expression.split()
    # A full match also rejects suffixes that Celery's regex parser ignores.
    atom = r'(?:\*|\d+(?:-\d+)?)(?:/[1-9]\d*)?'
    if any(not re.fullmatch(rf'{atom}(?:,{atom})*', field) for field in fields):
        raise ValueError('Cron 仅支持数字、*、逗号、范围及正整数间隔')
    minute, hour, day, month, weekday = fields
    try:
        weekdays = crontab_parser(8).parse(weekday)
        if any(value < 0 or value > 7 for value in weekdays):
            raise ValueError('星期应为 0–7')
        # Celery uses 0 for Sunday; the UI also accepts the conventional 7.
        weekday = ','.join(str(value) for value in sorted({value % 7 for value in weekdays}))
        params = dict(minute=minute, hour=hour, day_of_month=day,
                      month_of_year=month, day_of_week=weekday)
        TzAwareCrontab(**params, tz=schedule_timezone())
        return params
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError(f'Cron 字段范围无效：{exc}') from exc


def next_run_time(expression, *, now=None):
    base = (now or timezone.now()).astimezone(schedule_timezone())
    schedule = TzAwareCrontab(**parse_cron(expression), tz=schedule_timezone())
    schedule.nowfun = lambda: base
    try:
        delta = schedule.remaining_estimate(base)
        if delta.total_seconds() <= 0:
            raise ValueError('无法计算下一次执行时间')
        return base + delta
    except (RuntimeError, ValueError, OverflowError) as exc:
        raise ValueError('该 Cron 没有有效的后续执行日期') from exc
