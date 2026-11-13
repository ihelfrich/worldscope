"""Shared claim ranking used by the brief, the evidence board and the homepage."""
from worldscope import claim_rank as cr


class _Ev:
    def __init__(self, section, title="t", source_id=None):
        self.section, self.title, self.source_id = section, title, source_id or section


class _C:
    def __init__(self, text, ev, status="multi_source", conf=0.8):
        self.claim_text, self.evidence, self.status, self.confidence = text, ev, status, conf
        self.topics = sorted({e.section for e in ev})
        self.n_sources, self.n_sections = len(ev), len(self.topics)


def test_rank_orders_by_independence_and_drops_non_news():
    syndicated = _C("Wire story", [_Ev("local_news", source_id=f"s{i}") for i in range(8)]
                    + [_Ev("foreign_news")])
    independent = _C("Cross-checked", [_Ev("foreign_news"), _Ev("tech_news"), _Ev("ukraine_theater")])
    local = _C("Local only", [_Ev("local_news", source_id=f"l{i}") for i in range(9)])
    roundup = _C("HT morning brief: everything", [_Ev("foreign_news"), _Ev("tech_news")])
    assert cr.rank([syndicated, local, roundup, independent]) == [independent, syndicated]


def test_lang_tag_and_display_text():
    assert cr.lang_tag("Анонс") == "RU" and cr.lang_tag("Plain English") == ""
    c = _C("Санкции против банков", [_Ev("foreign_news", "Sanctions imposed on four Russian banks")])
    assert cr.display_text(c) == "Sanctions imposed on four Russian banks"
