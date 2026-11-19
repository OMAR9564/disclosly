from typing import List, Dict, Any, Optional
from typing_extensions import TypedDict

class ResearchState(TypedDict):
    run_id: str
    tickers: List[str]
    periods: List[str]
    mode: str  # "simple" or "deep"
    watchlist: List[str]
    plan: List[Dict[str, Any]]
    current_step: int
    retrieved_facts: List[Dict[str, Any]]
    analyst_findings: List[Dict[str, Any]]
    verification_rounds: int
    verification_passed: bool
    report_output: Optional[str]
    error: Optional[str]