# services/jobs.py
from __future__ import annotations

def quick_summary_job() -> None:
    # Import dans le child uniquement (évite d’embarquer des états du parent)
    from services.quick_summary import QuickSummaryUpdater
    QuickSummaryUpdater().update_all()
