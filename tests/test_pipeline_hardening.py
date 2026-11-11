"""Contracts added after the 2026-09-05..11-11 desk-officer brief outage.

Covers the producer gate, dist retention, tracked alert issues, the Pushover
fallback, and the validator that decides which routine pull requests land.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from worldscope import alerting, pushover_delivery
from worldscope.dist_maintenance import prune
from worldscope.readiness import should_run

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"

_spec = importlib.util.spec_from_file_location(
    "validate_brief_pr", ROOT / "tools" / "validate_brief_pr.py")
vbp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vbp)


# ── producer gate ────────────────────────────────────────────────────────────

def _manifest(dist: Path, day: str, status: str = "ready") -> None:
    path = dist / "status" / "daily" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": status, "data_date": day}))


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_gate_refuses_before_the_utc_date_is_the_chicago_date(tmp_path):
    run, reason = should_run(_utc("2026-11-12T05:30:00"), tmp_path)
    assert run is False and "06:10Z" in reason


def test_gate_runs_first_firing_after_cutoff_and_only_once(tmp_path):
    assert should_run(_utc("2026-11-12T06:10:00"), tmp_path)[0] is True
    _manifest(tmp_path, "2026-11-12")
    run, reason = should_run(_utc("2026-11-12T14:22:00"), tmp_path)
    assert run is False and "already published" in reason


def test_gate_ignores_yesterdays_manifest_and_rebuilds_a_bad_one(tmp_path):
    _manifest(tmp_path, "2026-11-11")
    assert should_run(_utc("2026-11-12T08:00:00"), tmp_path)[0] is True
    _manifest(tmp_path, "2026-11-12", status="failed")
    assert should_run(_utc("2026-11-12T08:00:00"), tmp_path)[0] is True
    (tmp_path / "status" / "daily" / "2026-11-12.json").write_text("{not json")
    assert should_run(_utc("2026-11-12T08:00:00"), tmp_path)[0] is True


# ── dist retention ───────────────────────────────────────────────────────────

def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _days(start: date, count: int) -> list[str]:
    return [date.fromordinal(start.toordinal() + i).isoformat() for i in range(count)]


def test_prune_drops_old_dated_pages_and_keeps_everything_else(tmp_path):
    days = _days(date(2026, 8, 1), 100)          # newest 2026-11-08
    for d in days:
        _touch(tmp_path / "sections" / "markets" / f"{d}.html")
        _touch(tmp_path / f"{d}.html")
        _touch(tmp_path / f"{d}.md")
        _touch(tmp_path / "zips" / f"{d}.zip")
    keep = [_touch(tmp_path / "index.html"),
            _touch(tmp_path / "sections" / "markets" / "index.html"),
            _touch(tmp_path / "briefings" / "2026-08-01.html"),
            _touch(tmp_path / "weekly_briefings" / "2026-W31.html"),
            _touch(tmp_path / "status" / "daily" / "2026-08-01.json"),
            _touch(tmp_path / "live" / "index.html")]
    result = prune(tmp_path, keep_days=60)
    assert all(p.exists() for p in keep)
    cutoff = "2026-09-09"                          # 60 days before the newest
    for d in days:
        expected = d >= cutoff
        assert (tmp_path / "sections" / "markets" / f"{d}.html").exists() is expected, d
        # A daily page and its bundle go together: the page links to the zip.
        assert (tmp_path / f"{d}.html").exists() is expected, d
        assert (tmp_path / "zips" / f"{d}.zip").exists() is expected, d
    assert result["deleted"] == 39 * 4


def test_prune_keeps_the_seven_newest_pages_of_a_dormant_section(tmp_path):
    # site_builder links a section index to its newest seven days however old
    # they are (acled's newest page was five months old on 2026-11-11).
    old = _days(date(2026, 5, 20), 12)
    for d in old:
        _touch(tmp_path / "sections" / "acled" / f"{d}.html")
    _touch(tmp_path / "sections" / "markets" / "2026-11-11.html")
    prune(tmp_path, keep_days=60)
    left = sorted(p.name for p in (tmp_path / "sections" / "acled").iterdir())
    assert left == [f"{d}.html" for d in old[-7:]]


def test_prune_enforces_the_size_ceiling_oldest_day_first(tmp_path):
    days = _days(date(2026, 10, 1), 30)
    for d in days:
        _touch(tmp_path / "sections" / "state_news" / f"{d}.html", size=100_000)
    result = prune(tmp_path, keep_days=365, max_mb=1.0)   # 3 MB of pages, 1 MB allowed
    left = sorted(p.name[:10] for p in (tmp_path / "sections" / "state_news").iterdir())
    assert left == days[-10:]
    assert result["after"] <= 1_000_000


def test_prune_dry_run_deletes_nothing(tmp_path):
    for d in _days(date(2026, 1, 1), 200):
        _touch(tmp_path / "sections" / "markets" / f"{d}.html")
    result = prune(tmp_path, keep_days=30, dry_run=True)
    assert result["deleted"] > 0
    assert len(list((tmp_path / "sections" / "markets").iterdir())) == 200


# ── tracked alert issues ─────────────────────────────────────────────────────

class FakeGitHub:
    def __init__(self, issues=None):
        self.issues = list(issues or [])
        self.calls: list[tuple] = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if method == "GET":
            return [i for i in self.issues if i.get("state", "open") == "open"]
        if method == "POST" and path.endswith("/issues"):
            issue = {"number": 100 + len(self.issues), "title": body["title"],
                     "body": body["body"], "state": "open"}
            self.issues.append(issue)
            return issue
        if method == "PATCH":
            number = int(path.rsplit("/", 1)[1])
            for issue in self.issues:
                if issue["number"] == number:
                    issue.update(body)
            return {}
        return {}


def test_alert_opens_one_issue_then_comments_instead_of_duplicating():
    gh = FakeGitHub()
    number, created = alerting.raise_alert("o/r", "brief-missing", "no brief", "detail",
                                           owner="ian", transport=gh)
    assert created and gh.issues[0]["title"].startswith("[pipeline-alert:brief-missing]")
    assert gh.issues[0]["body"].startswith("@ian ")
    again, created_again = alerting.raise_alert("o/r", "brief-missing", "no brief", "detail",
                                                owner="ian", transport=gh)
    assert again == number and created_again is False and len(gh.issues) == 1
    assert gh.calls[-1][1].endswith(f"/issues/{number}/comments")
    assert "@ian" not in gh.calls[-1][2]["body"]      # repeats do not re-mention


def test_alert_keys_are_independent_and_pull_requests_are_never_matched():
    gh = FakeGitHub([{"number": 7, "title": "[pipeline-alert:brief-missing] in a PR",
                      "pull_request": {}, "state": "open"}])
    assert alerting.find_open("o/r", "brief-missing", gh) is None
    alerting.raise_alert("o/r", "site-stale", "stale", "", transport=gh)
    assert alerting.find_open("o/r", "brief-missing", gh) is None
    assert alerting.find_open("o/r", "site-stale", gh) is not None


def test_resolve_closes_the_open_issue_and_is_a_no_op_when_healthy():
    gh = FakeGitHub()
    assert alerting.resolve_alert("o/r", "brief-missing", transport=gh) is None
    number, _ = alerting.raise_alert("o/r", "brief-missing", "t", "m", transport=gh)
    assert alerting.resolve_alert("o/r", "brief-missing", "back", transport=gh) == number
    assert gh.issues[0]["state"] == "closed"
    assert alerting.find_open("o/r", "brief-missing", gh) is None


@pytest.mark.parametrize("repo,key", [("o/r", "Bad Key"), ("o/r", "../x"),
                                      ("not-a-repo", "ok"), ("o/r/../../x", "ok")])
def test_alert_rejects_values_that_would_reshape_the_api_path(repo, key):
    with pytest.raises(alerting.AlertError):
        alerting.find_open(repo, key, FakeGitHub())


def _cli(monkeypatch, *, pushover_ok: bool, issue_ok: bool) -> int:
    def fake_deliver(**_):
        if not pushover_ok:
            raise pushover_delivery.PushoverDeliveryError("no active devices to send to")
        return "req-1"

    def fake_env_transport():
        if not issue_ok:
            raise alerting.AlertError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")
        return "o/r", "ian", FakeGitHub()

    monkeypatch.setattr(pushover_delivery, "deliver", fake_deliver)
    monkeypatch.setattr(alerting, "env_transport", fake_env_transport)
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "t")
    return pushover_delivery.main(["--title", "t", "--message", "m",
                                   "--fallback-issue", "brief-missing"])


def test_dead_phone_channel_is_not_a_failure_when_the_issue_was_recorded(monkeypatch, capsys):
    assert _cli(monkeypatch, pushover_ok=False, issue_ok=True) == 0
    out = capsys.readouterr().out
    assert "::warning::Pushover did not deliver" in out and "alert issue #" in out


def test_alert_step_fails_only_when_both_channels_fail(monkeypatch):
    assert _cli(monkeypatch, pushover_ok=True, issue_ok=False) == 0
    assert _cli(monkeypatch, pushover_ok=True, issue_ok=True) == 0
    assert _cli(monkeypatch, pushover_ok=False, issue_ok=False) == 1


def test_without_the_flag_a_pushover_failure_still_fails(monkeypatch):
    def boom(**_):
        raise pushover_delivery.PushoverDeliveryError("no active devices to send to")
    monkeypatch.setattr(pushover_delivery, "deliver", boom)
    assert pushover_delivery.main(["--title", "t", "--message", "m"]) == 1


# ── routine pull request validator ───────────────────────────────────────────

TODAY = date(2026, 11, 12)
BRIEF = "# Morning brief - Thursday 12 November 2026\n\n" + "word " * 3000
WEEKLY = "# Weekly brief — 2026-W46 (8 November through 13 November)\n\n" + "word " * 1200


def _added(*names: str) -> list[dict]:
    return [{"filename": n, "status": "added"} for n in names]


def test_a_complete_daily_brief_merges():
    files = _added("briefings/2026-11-12.md", "briefings/2026-11-12-yield_curve.png",
                   "briefings/2026-11-12-events.geojson")
    assert vbp.classify(files, BRIEF, TODAY)[0] == "merge"


def test_title_may_follow_a_data_note_and_vary_in_case():
    text = "> **DATA NOTE:** bundle was late\n\n# Morning Brief - Thursday\n\n" + "w " * 3000
    assert vbp.classify(_added("briefings/2026-11-12.md"), text, TODAY)[0] == "merge"


def test_a_weekly_brief_for_this_or_last_week_merges():
    assert vbp.classify(_added("weekly_briefings/2026-W46.md"), WEEKLY, TODAY)[0] == "merge"
    assert vbp.classify(_added("weekly_briefings/2026-W45.md"), WEEKLY, TODAY)[0] == "merge"
    assert vbp.classify(_added("weekly_briefings/2026-W40.md"), WEEKLY, TODAY)[0] == "ignore"


def test_source_not_ready_records_are_closed():
    files = _added("status/source_not_ready_2026-11-12.txt")
    assert vbp.classify(files, None, TODAY)[0] == "close"


@pytest.mark.parametrize("files", [
    _added("briefings/2026-11-12.md", ".github/workflows/auto-merge-briefs.yml"),
    _added("briefings/2026-11-12.md", "tools/validate_brief_pr.py"),
    _added("briefings/2026-11-12.md", "briefings/2026-11-11-chart.png"),
    _added("briefings/2026-11-12.md", "briefings/2026-11-12-../../x.png"),
    _added("briefings/2026-11-12.md", "briefings/2026-11-12-page.html"),
    _added("briefings/2026-11-12.md", "weekly_briefings/2026-W46.md"),
    _added("briefings/2026-11-12-yield_curve.png"),
    _added("briefings/2026-10-01.md"),
    _added("briefings/2026-11-20.md"),
    [{"filename": "briefings/2026-11-12.md", "status": "modified"}],
    [{"filename": "briefings/2026-11-12.md", "status": "added"},
     {"filename": "briefings/2026-11-11.md", "status": "removed"}],
    _added("status/source_not_ready_2026-11-12.txt", "worldscope/brief.py"),
    [],
])
def test_anything_but_exactly_one_new_current_brief_is_left_for_a_person(files):
    assert vbp.classify(files, BRIEF, TODAY)[0] == "ignore"


@pytest.mark.parametrize("text", [None, "# Morning brief\n\ntoo short",
                                  "no title here\n\n" + "word " * 3000,
                                  "# Morning brief\n\n" + "w" * 700_000])
def test_a_brief_that_does_not_look_like_a_brief_is_not_merged(text):
    assert vbp.classify(_added("briefings/2026-11-12.md"), text, TODAY)[0] == "ignore"


# ── workflow invariants ──────────────────────────────────────────────────────

def _read(name: str) -> str:
    return (WF / name).read_text()


def test_every_alerting_workflow_has_the_issue_fallback_and_may_write_issues():
    for name in ("brief-deadman.yml", "watchdog-deadman.yml", "watchdog-alert.yml",
                 "site-freshness-watchdog.yml"):
        body = _read(name)
        assert "--fallback-issue " in body, name
        assert "issues: write" in body, name
        assert "GITHUB_TOKEN: ${{ github.token }}" in body, name


def test_brief_deadman_judges_state_not_a_fixed_utc_time():
    body = _read("brief-deadman.yml")
    assert "TZ=America/Chicago date +%-H" in body
    assert "fromJSON(steps.look.outputs.hour) >= 13" in body
    # Runs chained to a composer may close the alert but never raise it.
    assert "github.event_name != 'workflow_run'" in body
    assert "worldscope.alerting resolve --key brief-missing" in body


def test_daily_brief_is_gated_and_scheduled_more_than_once():
    body = _read("daily-brief.yml")
    assert body.count("- cron:") >= 6
    assert "worldscope.readiness should-run" in body
    assert "needs: gate" in body and "needs.gate.outputs.run == 'true'" in body
    # Retention runs before the readiness manifest and the commit.
    assert body.index("worldscope.dist_maintenance") \
        < body.index("worldscope.readiness publish-daily") < body.index("git add -f dist/")


def test_every_pages_deploy_retries_once_after_a_bounded_first_attempt():
    for name in ("daily-brief.yml", "live-map.yml", "render-briefings.yml"):
        body = _read(name)
        assert body.count("uses: actions/deploy-pages@v4") == 2, name
        assert "timeout: 300000" in body and "continue-on-error: true" in body, name
        assert "if: steps.first.outcome == 'failure'" in body, name


def test_compose_judges_the_producer_build_job_and_dispatches_render():
    body = _read("compose-brief.yml")
    assert 'select(.name == "build")' in body
    assert "github.event.workflow_run.conclusion == 'success'" not in body
    assert "claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in body
    assert "gh workflow run render-briefings.yml" in body
    assert "actions: write" in body


def test_auto_merge_never_runs_pull_request_code_or_interpolates_its_fields():
    body = _read("auto-merge-briefs.yml")
    assert "pull_request_target" in body
    assert "ref: main" in body
    assert "github.event.pull_request.head.sha" not in body
    assert "head.repo.full_name == github.repository" in body
    assert "startsWith(github.event.pull_request.head.ref, 'claude/')" in body
    runs = "\n".join(line for line in body.splitlines()
                     if "run:" in line or line.startswith("          "))
    assert "${{ github.event.pull_request" not in runs
    assert "gh workflow run render-briefings.yml" in body


# ── portfolio health ─────────────────────────────────────────────────────────

_pspec = importlib.util.spec_from_file_location(
    "check_portfolio", ROOT / "tools" / "check_portfolio.py")
cp = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(cp)

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
PAGE = b"<html>" + b"x" * 1000


def _runs(conclusion: str, updated: str) -> bytes:
    return json.dumps({"workflow_runs": [{"conclusion": conclusion, "updated_at": updated,
                                          "html_url": "https://example/run"}]}).encode()


def test_registry_is_valid_and_lists_each_repo_once():
    doc = cp.load_registry()
    repos = [s["repo"] for s in doc["sites"]]
    assert len(repos) == len(set(repos)) >= 40
    assert "worldscope" in repos and "ihelfrich.github.io" in repos


def test_site_check_retries_transient_errors_but_not_a_404():
    calls = []

    def flaky(url, headers):
        calls.append(url)
        return (502, b"") if len(calls) < 3 else (200, PAGE)
    assert cp.check_site({"repo": "r", "url": "https://x/"}, flaky, pause=0) is None
    assert len(calls) == 3

    calls.clear()
    def gone(url, headers):
        calls.append(url); return 404, b""
    assert "HTTP 404" in cp.check_site({"repo": "r", "url": "https://x/"}, gone, pause=0)
    assert len(calls) == 1


def test_an_empty_shell_served_as_200_is_reported():
    problem = cp.check_site({"repo": "r", "url": "https://x/"}, lambda u, h: (200, b"<html>"),
                            pause=0)
    assert problem and "bytes" in problem


def test_workflow_check_flags_failure_and_staleness_only():
    wf = {"file": "refresh.yml", "max_age_days": 3}
    ok = lambda u, h: (200, _runs("success", "2026-11-11T07:00:00Z"))
    failed = lambda u, h: (200, _runs("failure", "2026-11-11T07:00:00Z"))
    stale = lambda u, h: (200, _runs("success", "2026-10-01T07:00:00Z"))
    never = lambda u, h: (200, b'{"workflow_runs": []}')
    assert cp.check_workflow("o", "r", wf, NOW, ok) is None
    assert "last run failure" in cp.check_workflow("o", "r", wf, NOW, failed)
    assert "has not run for 42 days" in cp.check_workflow("o", "r", wf, NOW, stale)
    assert "never completed" in cp.check_workflow("o", "r", wf, NOW, never)


def test_run_collects_every_problem_across_the_registry():
    doc = {"owner": "o", "sites": [
        {"repo": "up", "url": "https://o.github.io/up/"},
        {"repo": "down", "url": "https://o.github.io/down/"},
        {"repo": "data", "url": "https://o.github.io/data/",
         "workflows": [{"file": "refresh.yml", "max_age_days": 3}]}]}

    def fetch(url, headers):
        if "api.github.com" in url:
            return 200, _runs("failure", "2026-11-12T07:00:00Z")
        return (404, b"") if "/down/" in url else (200, PAGE)
    failures = cp.run(doc, fetch=fetch, now=NOW, pause=0)
    assert len(failures) == 2
    assert failures[0].startswith("down: HTTP 404") and failures[1].startswith("data: refresh.yml")


def test_portfolio_workflow_raises_and_resolves_one_tracked_alert():
    body = _read("portfolio-health.yml")
    assert "alerting raise --key portfolio-health" in body
    assert "alerting resolve --key portfolio-health" in body
    assert "issues: write" in body
    assert "${{ steps.check.outputs.failures }}" not in "\n".join(
        line for line in body.splitlines() if line.strip().startswith(("python3", "--")))


def test_every_rebase_on_a_rejected_push_tolerates_a_dirty_tree():
    # A plain rebase refuses to start when the build left tracked files
    # modified; with `bash -e` the failed abort then killed the retry loop on
    # its first attempt (Daily briefing, 2026-11-11).
    seen = 0
    for path in sorted(WF.glob("*.yml")):
        for line in path.read_text().splitlines():
            code = line.strip()
            if code.startswith("#"):
                continue
            if code.startswith(("git rebase ", "git pull --rebase")) and "origin" in code:
                seen += 1
                assert "--autostash" in code, f"{path.name}: {code}"
    assert seen >= 6
