"""
mediacloud.py — MediaCloud Online News Archive queries, watch-area-driven.

MediaCloud's index covers ~60K news sources globally and is far broader
than GDELT in some regions (Africa, South Asia, Latin America). The v4
API is at https://search.mediacloud.org/ — register for an API key, set
MEDIACLOUD_API_KEY in the env.

For each watch area with `keywords`, we issue a SearchApi.story_list
query joining the keywords with OR. Results carry full text URLs, source
domains, publication dates, and language. We don't request full text
(privacy + bandwidth); the brief routine follows up via WebFetch on
items of interest.

We bound the query budget at 8 areas per run, in priority order, with a
per-area cap of 50 stories. This keeps a single run under 30 seconds
even on a slow link.

Falls back gracefully if the `mediacloud` package or the API key is
missing.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any

from ..lib.watchareas import load_watch_areas
from . import Section

try:
    import mediacloud.api as _mc_api
except ImportError:
    _mc_api = None  # type: ignore


class MediaCloudSection(Section):
    id = "mediacloud"
    title = "MediaCloud — global news index (watch-area filtered)"
    emoji = "📰"

    PULL_TIMEOUT_S = 90
    MAX_AREAS = 8
    PER_AREA = 50
    DAYS_BACK = 2

    def pull(self) -> list[dict]:
        api_key = os.environ.get("MEDIACLOUD_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"[{self.id}] MEDIACLOUD_API_KEY not set — section cannot pull"
            )
        if _mc_api is None:
            raise RuntimeError(
                f"[{self.id}] mediacloud package not installed — pip install mediacloud"
            )
        try:
            search = _mc_api.SearchApi(api_key)
        except Exception as e:
            raise RuntimeError(
                f"[{self.id}] mediacloud SearchApi() failed: {type(e).__name__}: {e}"
            ) from e
        areas = load_watch_areas()
        prio_rank = {"high": 0, "normal": 1, "low": 2}
        areas.sort(key=lambda a: prio_rank.get(a.priority, 1))
        end = _dt.date.today()
        start = end - _dt.timedelta(days=self.DAYS_BACK)
        out: list[dict] = []
        seen_urls: set[str] = set()
        failures: list[str] = []
        attempted = 0
        for area in areas[: self.MAX_AREAS]:
            if not area.keywords:
                continue
            # Build query: top ~8 distinctive keywords joined by OR
            kws = sorted([k for k in area.keywords if k], key=len, reverse=True)[:8]
            if not kws:
                continue
            query = " OR ".join(f'"{k}"' for k in kws)
            attempted += 1
            try:
                token: Any = None
                pulled = 0
                while pulled < self.PER_AREA:
                    page, token = search.story_list(
                        query,
                        start_date=start,
                        end_date=end,
                        pagination_token=token,
                    )
                    if not page:
                        break
                    for st in page:
                        url = st.get("url") or ""
                        if not url or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        pub = st.get("publish_date") or ""
                        date_str = str(pub)[:10] if pub else ""
                        out.append({
                            "id": f"mc-{st.get('id') or hash(url) & 0xFFFFFFFF:x}",
                            "date": date_str,
                            "title": f"[{area.name}] {st.get('title', '(no title)')}",
                            "url": url,
                            "summary": f"{st.get('media_name','')} · {st.get('language','')}",
                            "country": "",
                            "domain": st.get("media_name", ""),
                            "language": st.get("language", ""),
                            "topics": area.topics,
                            "watch_areas": [area.name],
                            "_source": self.id,
                        })
                        pulled += 1
                        if pulled >= self.PER_AREA:
                            break
                    if not token:
                        break
            except Exception as exc:
                failures.append(f"{area.name}:{type(exc).__name__}: {exc}")
                continue

        # Loud-failure invariant: if we tried areas and none yielded,
        # that's an upstream problem worth surfacing — silent zero-record
        # "success" hid MediaCloud being broken for weeks.
        if attempted and not out and failures:
            raise RuntimeError(
                f"[{self.id}] All {len(failures)} MediaCloud area queries failed; "
                f"first: {failures[0]}"
            )
        if failures:
            print(f"[{self.id}] {len(failures)}/{attempted} area queries failed: "
                  + "; ".join(failures[:4])
                  + (f" (+{len(failures)-4} more)" if len(failures) > 4 else ""))
        out.sort(key=lambda it: it.get("date", ""), reverse=True)
        return out
