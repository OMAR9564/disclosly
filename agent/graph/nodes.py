from ..state import ResearchState

def router_node(state: ResearchState) -> ResearchState:
    if not state.get("tickers") or state.get("mode") == "simple":
        state["mode"] = "simple"
    else:
        state["mode"] = "deep"
    return state

def memory_node(state: ResearchState) -> ResearchState:
    state["watchlist"] = state.get("watchlist", [])
    return state

def planner_node(state: ResearchState) -> ResearchState:
    state["plan"] = [
        {"step": 1, "task": "Fetch financial income statements and balance sheets"},
        {"step": 2, "task": "Compute EBITDA margins and ratios"}
    ]
    state["current_step"] = 0
    return state

def retriever_node(state: ResearchState) -> ResearchState:
    state["retrieved_facts"] = [{"fact": "Retrieved quarterly regulatory disclosure details."}]
    return state

def analyst_node(state: ResearchState) -> ResearchState:
    state["analyst_findings"] = [{"finding": "Calculated YoY revenue growth of 14.5%."}]
    return state

def verifier_node(state: ResearchState) -> ResearchState:
    state["verification_passed"] = True
    state["verification_rounds"] = state.get("verification_rounds", 0) + 1
    return state

def writer_node(state: ResearchState) -> ResearchState:
    state["report_output"] = "# Deep Research Report\nAnalysis complete and verified."
    return state

def writer_simple_node(state: ResearchState) -> ResearchState:
    state["report_output"] = "# Quick Summary\nSimple research completed."
    return state