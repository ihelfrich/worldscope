"""The source-actions issue: current, quiet when unchanged, closes when empty."""
from __future__ import annotations

from worldscope import source_actions as sa

REPO = "o/r"


def _report(**sections):
    return {"date": "2026-11-12", "sections": sections}


def _fail(etype, error, source_date="2026-11-12"):
    return {"state": "stale_after_failure", "error_type": etype,
            "error": f"{etype}: {error}", "source_date": source_date}


class FakeGitHub:
    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.comments: list[tuple[int, str]] = []
        self.next = 1

    def __call__(self, method, path, body):
        if method == "GET":
            return [i for i in self.issues.values() if i["state"] == "open"]
        if method == "POST" and path.endswith("/issues"):
            n = self.next
            self.next += 1
            self.issues[n] = {"number": n, "state": "open", **body}
            return {"number": n}
        if method == "POST" and path.endswith("/comments"):
            self.comments.append((int(path.split("/")[-2]), body["body"]))
            return {}
        if method == "PATCH":
            self.issues[int(path.split("/")[-1])].update(body)
            return {}
        raise AssertionError((method, path))


def test_classifies_only_what_a_person_must_fix():
    acts = sa.actions_from_report(_report(
        firms=_fail("MissingCredential", "FIRMS_MAP_KEY is not set"),
        acled=_fail("UpstreamAuthError", "rejected for a.b@example.com (check ACLED_EMAIL/ACLED_PASSWORD)",
                    "2026-06-02"),
        promed=_fail("FeedRetired", "gone", None),
        gdelt=_fail("UpstreamHTTPError", "429", "2026-11-11"),     # transient: skip
        old=_fail("UpstreamHTTPError", "down", "2026-11-01"),      # 11 days: list
        ok={"state": "fresh", "error_type": None},
    ))
    by = {a.section: a for a in acts}
    assert set(by) == {"firms", "acled", "promed", "old"}
    assert by["firms"].remedy == "Add repository secret `FIRMS_MAP_KEY`"
    assert "`ACLED_EMAIL`, `ACLED_PASSWORD`" in by["acled"].remedy
    assert "example.com" not in by["acled"].detail and "<email>" in by["acled"].detail
    assert by["old"].remedy == "Investigate: no good data for 11 days"


def test_lifecycle_open_quiet_update_close():
    gh = FakeGitHub()
    two = _report(firms=_fail("MissingCredential", "FIRMS_MAP_KEY is not set"),
                  mediacloud=_fail("MissingCredential", "MEDIACLOUD_API_KEY is not set"))
    assert sa.sync(REPO, two, owner="me", transport=gh).startswith("opened #1")
    issue = gh.issues[1]
    assert issue["title"].startswith("[pipeline-alert:source-actions]")
    assert "@me" in issue["body"] and "**firms**" in issue["body"]

    # Same list again: body may refresh, but no comment (no daily notification).
    gh.issues[1]["body"] = sa.render(sa.actions_from_report(two), "2026-11-12")
    assert sa.sync(REPO, two, owner="me", transport=gh).startswith("refreshed #1")
    assert gh.comments == []

    one = _report(mediacloud=_fail("MissingCredential", "MEDIACLOUD_API_KEY is not set"))
    msg = sa.sync(REPO, one, owner="me", transport=gh)
    assert "Fixed: firms" in msg and "**firms**" not in gh.issues[1]["body"]
    assert len(gh.comments) == 1

    assert sa.sync(REPO, _report(), owner="me", transport=gh) == "closed #1"
    assert gh.issues[1]["state"] == "closed"


def test_dry_run_prints_body(tmp_path, capsys):
    import json
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_report(firms=_fail("MissingCredential", "FIRMS_MAP_KEY is not set"))))
    assert sa.main(["--report", str(p), "--dry-run"]) == 0
    assert "Add repository secret `FIRMS_MAP_KEY`" in capsys.readouterr().out


def test_duplicate_from_a_race_is_closed_and_oldest_kept():
    gh = FakeGitHub()
    rep = _report(firms=_fail("MissingCredential", "FIRMS_MAP_KEY is not set"))
    sa.sync(REPO, rep, owner="me", transport=gh)
    # simulate a racing run that did not see #1 yet
    gh.issues[2] = {**gh.issues[1], "number": 2}
    gh.next = 3
    msg = sa.sync(REPO, rep, owner="me", transport=gh)
    assert "#1" in msg
    assert gh.issues[2]["state"] == "closed" and gh.issues[1]["state"] == "open"
    assert (2, "Duplicate of #1.") in gh.comments
