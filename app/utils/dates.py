from __future__ import annotations

from datetime import date, datetime


def year_month_path(when: date) -> str:
    return f"{when.strftime('%Y')}/{when.strftime('%m')}"


def parse_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def month_paths_between(start_date: date, end_date: date) -> list[str]:
    paths: list[str] = []

    cursor = date(start_date.year, start_date.month, 1)
    end_cursor = date(end_date.year, end_date.month, 1)

    while cursor <= end_cursor:
        paths.append(year_month_path(cursor))
        next_month = cursor.month + 1
        next_year = cursor.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        cursor = date(next_year, next_month, 1)

    return paths
