"""tools.citations (T036): the verifier reads facts and chunks again from the corpus."""

from decimal import Decimal

from tests.integration.agent.conftest import facts_corpus  # noqa: F401  (fixture)
from tests.integration.retrieval.corpus import Corpus
from tools.citations import CorpusCitations


async def test_reload_facts_and_chunks(facts_corpus: Corpus) -> None:  # noqa: F811
    chunk = facts_corpus.ids["thy-debt"]
    found = await CorpusCitations(facts_corpus.engine).reload(
        ["f:501", "f:601", f"c:{chunk}", "f:999999", "c:missing", "k:1", "f:x"]
    )

    assert set(found) == {"f:501", "f:601", f"c:{chunk}"}
    assert (found["f:501"].value, found["f:501"].scale) == (Decimal("1234.5"), Decimal(10) ** 6)
    assert (found["f:601"].value, found["f:601"].scale) == (Decimal("23.4"), Decimal(10) ** 9)
    assert found[f"c:{chunk}"].text
    assert found[f"c:{chunk}"].source["document_id"] == 10
    assert await CorpusCitations(facts_corpus.engine).reload([]) == {}
