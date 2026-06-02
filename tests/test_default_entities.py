"""The base default entity extractor mines distinctive surface mentions so
record_entities populates for sections without a bespoke extractor."""
from worldscope.sections import Section
from worldscope.store import SnapshotStore


def _section(tmp_path):
    class _S(Section):
        id, title, emoji = "t_default", "t", "x"
        def pull(self):
            return []
    return _S(store=SnapshotStore(tmp_path / "s.sqlite"))


def test_mines_proper_nouns_and_cves_not_stopwords(tmp_path):
    s = _section(tmp_path)
    ents = s.extract_entities(
        {"title": "Acme Corporation hit by CVE-2026-1234 in Madagascar"})
    ids = {e["id"] for e in ents}
    assert any("cve-2026-1234" in i.lower() for i in ids)        # CVE kept
    assert any("acme-corporation" in i for i in ids)              # multi-word kept
    assert "mention:madagascar" in ids                           # single proper noun kept
    assert "mention:by" not in ids and "mention:in" not in ids   # stopwords dropped
    assert all(e["type"] == "mention" for e in ents)


def test_empty_title_yields_nothing(tmp_path):
    assert _section(tmp_path).extract_entities({"title": ""}) == []


def test_caps_at_six(tmp_path):
    s = _section(tmp_path)
    title = " ".join(f"Alpha Beta{i}" for i in range(20))
    assert len(s.extract_entities({"title": title})) <= 6
