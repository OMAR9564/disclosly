"""tools.registry per-node tool sets (T012), one assertion per row of contracts/agent-graph.md."""

import pytest

from llm.types import ToolCall
from tests.unit.tools.test_documents import _hit, _registry
from tools.registry import (
    GRAPH_TOOL_SCHEMA_VERSION,
    NODE_TOOLS,
    TOOL_SCHEMA_VERSION,
    GraphNode,
    ToolNotAllowed,
)

RETRIEVAL = {"search_documents", "get_financial_facts", "list_documents"}


@pytest.mark.parametrize(
    ("node", "tools"),
    [
        ("router", set()),
        ("memory", set()),
        ("quota_gate", set()),
        ("planner", set()),
        ("decision", set()),
        ("retriever", RETRIEVAL),
        ("retriever_simple", RETRIEVAL),
        ("analyst", {"run_python"}),
        ("verifier", {"run_python"}),
        ("writer", set()),
        ("writer_simple", set()),
        ("finalize_partial", set()),
    ],
)
def test_node_rows(node: GraphNode, tools: set[str]) -> None:
    registry, _ = _registry([])
    view = registry.for_node(node)
    assert set(view.names) == tools
    # Model-facing schemas are the registry's tools the node may use (run_python is called
    # in code by the analyst and verifier, not offered to the model).
    assert {schema.name for schema in view.schemas} == tools - {"run_python"}


def test_every_graph_node_has_a_row() -> None:
    assert set(NODE_TOOLS) == {
        "router",
        "memory",
        "quota_gate",
        "planner",
        "decision",
        "retriever",
        "retriever_simple",
        "analyst",
        "verifier",
        "writer",
        "writer_simple",
        "finalize_partial",
    }


def test_schema_versions() -> None:
    registry, _ = _registry([])
    assert registry.tool_schema_version == TOOL_SCHEMA_VERSION == "1"
    assert registry.for_node("retriever").tool_schema_version == GRAPH_TOOL_SCHEMA_VERSION == "2"


async def test_allowed_tool_dispatches() -> None:
    registry, search = _registry([_hit("abc", "Net borç azaldı.")])
    result = await registry.for_node("retriever").dispatch(
        ToolCall(id="t1", name="search_documents", arguments={"query": "net borç"})
    )
    assert result.count == 1
    assert len(search.calls) == 1


@pytest.mark.parametrize(
    ("node", "tool"),
    [
        ("writer", "search_documents"),
        ("planner", "get_financial_facts"),
        ("router", "list_documents"),
        ("analyst", "search_documents"),
        ("retriever", "run_python"),
    ],
)
async def test_tool_outside_the_node_set_is_refused(node: GraphNode, tool: str) -> None:
    registry, search = _registry([_hit("abc", "x")])
    with pytest.raises(ToolNotAllowed) as excinfo:
        await registry.for_node(node).dispatch(ToolCall(id="t1", name=tool, arguments={}))
    assert excinfo.value.node == node
    assert search.calls == []
