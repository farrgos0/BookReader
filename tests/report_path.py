"""Куда класть отчёты проверочных скриптов: папка reports/ рядом с программой (в git не попадает)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")


def resolve(name):
    """«accent-report.txt» → reports/accent-report.txt; путь с папкой оставляем как есть."""
    if os.path.dirname(name):
        return name
    os.makedirs(REPORTS, exist_ok=True)
    return os.path.join(REPORTS, name)
