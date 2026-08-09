"""This module provides utility functions for calculating time windows, specifically for determining the start of the current and previous week, as well as parsing job window days from request parameters. It is frequently used in statistical dashboard generations and other time-sensitive data retrieval operations across the application.

"""
import calendar
import enum
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone


def get_time_windows():
    """Week starts on Sunday and ends on Saturday. This function returns the start datetimes for the current and previous week."""
    local_now = timezone.localtime()
    days_since_sunday = (local_now.weekday() + 1) % 7
    current_week_start_date = (local_now - timedelta(days=days_since_sunday)).date()
    current_week_start = timezone.make_aware(
        datetime.combine(current_week_start_date, time.min),
        timezone.get_current_timezone(),
    )
    previous_week_start = current_week_start - timedelta(days=7)
    return local_now, current_week_start, previous_week_start


def get_stats_window_days(stats_window_days):
    """Returns the days to look back for wallet transactions, ensuring it's a positive integer. Defaults to 7 if invalid."""
    raw_days = stats_window_days or 7

    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        days = 7

    return max(days, 1)


def sum_amount(queryset, field_name="amount"):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )["total"]


def _serialize_value(value):
    if isinstance(value, Decimal):
        return round(float(value), 2)
    return value


def build_metric_payload(total, current_week_value, previous_week_value):
    current_value = Decimal(str(current_week_value or 0))
    previous_value = Decimal(str(previous_week_value or 0))

    if previous_value == 0:
        percentage_change = 100.0 if current_value > 0 else 0.0
    else:
        percentage_change = round(float(((current_value - previous_value) / previous_value) * 100), 2)

    return {
        "total": _serialize_value(total or 0),
        "percentage_change": percentage_change,
    }


def get_time_series_period_windows(period_label):
    """Returns the start and end datetimes for the current dashboard period (week, month, or year) based on the provided local_now reference time, as well as the start and end datetimes for the previous equivalent period needed to calculate percentage change vs previous period, and a list of component dates for breaking down sales by day or month in the dashboard."""

    local_now = timezone.localtime()
    current_date = local_now.date()
    tz = timezone.get_current_timezone()

    if period_label == TimeSeriesPeriodEnum.THIS_WEEK.value:
        days_since_sunday = (current_date.weekday() + 1) % 7
        start_date = current_date - timedelta(days=days_since_sunday)
        previous_start_date = start_date - timedelta(days=7)
        component_dates = [start_date + timedelta(days=offset) for offset in range(7)]
    elif period_label == TimeSeriesPeriodEnum.THIS_MONTH.value:
        start_date = current_date.replace(day=1)
        previous_month = current_date.month - 1 or 12
        previous_year = current_date.year - 1 if current_date.month == 1 else current_date.year
        previous_start_date = date(previous_year, previous_month, 1)
        days_in_month = calendar.monthrange(current_date.year, current_date.month)[1]
        component_dates = [start_date + timedelta(days=offset) for offset in range(days_in_month)]
    else:
        start_date = date(current_date.year, 1, 1)
        previous_start_date = date(current_date.year - 1, 1, 1)
        component_dates = [date(current_date.year, month, 1) for month in range(1, 13)]

    start_datetime = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_datetime = local_now
    previous_start_datetime = timezone.make_aware(datetime.combine(previous_start_date, time.min), tz)
    return start_datetime, end_datetime, previous_start_datetime, component_dates


def dashboard_period_display_label(period_label):
    return {
        "this week": "This week",
        "this month": "This month",
        "this year": "This year",
    }.get(period_label, "This week")


def dashboard_period_components(period_label, component_dates, sales_by_bucket, qty_label):
    local_now = timezone.localtime()
    current_date = local_now.date()
    components = []

    if period_label == TimeSeriesPeriodEnum.THIS_YEAR.value:
        labels = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        for component_date, label in zip(component_dates, labels):
            # for a period label of "this year", component_date is already normalized to the first day of each month
            bucket_key = component_date
            component_total = sales_by_bucket.get(bucket_key)
            if component_date.month > current_date.month:
                value = None
            else:
                value = component_total or Decimal("0.00")
            components.append({"label": label, f"total_{qty_label}": value})
        return components

    for component_date in component_dates:
        if period_label == TimeSeriesPeriodEnum.THIS_WEEK.value:
            label = component_date.strftime("%A")
        else:
            label = str(component_date.day)

        if component_date > current_date:
            value = None
        else:
            value = sales_by_bucket.get(component_date, Decimal("0.00"))
        components.append({"label": label, f"total_{qty_label}": value})

    return components


class TimeSeriesPeriodEnum(enum.StrEnum):
    THIS_WEEK = "This week"
    THIS_MONTH = "This month"
    THIS_YEAR = "This year"