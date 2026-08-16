"""Headless Meta Graph API reader for TWY.

Reads Facebook Page and Instagram insights with the durable Page token in
META_PAGE_ACCESS_TOKEN (a non expiring token derived from a long lived user
token, stored in twy secrets). No browser, no Zernio.

Coverage, verified live 2026-08-15 against Graph v21:
  Instagram is fully covered. Account reach and per media reach, likes,
  comments, saved and shares all return, so Meta is authoritative for
  Instagram and replaces Zernio there.
  Facebook is partial. post_clicks and reactions return, but per post reach
  and impressions are deprecated in Graph v26, so this reader does not claim
  Facebook reach. Plausible measures Facebook link clicks at the destination.

Identifiers are Meta object ids, not paths, so they live here as named
constants rather than in twy_paths.
"""
import json
import os
import sys

import requests

GRAPH = "https://graph.facebook.com/v21.0"
PAGE_ID = "136254809853695"        # Tiffany Wood Yoga Facebook Page
IG_USER_ID = "17841402021315900"   # instagram @tiffanywoodyoga

MEDIA_METRICS = "reach,likes,comments,saved,shares,total_interactions"
FB_POST_METRICS = "post_clicks,post_reactions_by_type_total"


class MetaError(RuntimeError):
    """A Graph API error payload, surfaced instead of a silent empty result."""

    def __init__(self, error):
        super().__init__(error.get("message", "unknown Meta error"))
        self.error = error


def load_token():
    """Read META_PAGE_ACCESS_TOKEN through twy_paths, failing loudly if unset."""
    import twy_paths

    twy_paths.load_env()
    token = os.environ.get("META_PAGE_ACCESS_TOKEN")
    if not token:
        raise SystemExit("META_PAGE_ACCESS_TOKEN is not configured")
    return token


class MetaInsights:
    def __init__(self, token, *, page_id=PAGE_ID, ig_user_id=IG_USER_ID, timeout=20):
        self._token = token
        self._page = page_id
        self._ig = ig_user_id
        self._timeout = timeout

    def _get(self, path, **params):
        params["access_token"] = self._token
        response = requests.get(f"{GRAPH}/{path}", params=params, timeout=self._timeout)
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise MetaError(data["error"])
        return data

    @staticmethod
    def _first_value(entry):
        values = entry.get("values") or [{}]
        return values[0].get("value")

    def page_summary(self):
        """Page name, fan_count and the linked Instagram account."""
        return self._get(
            self._page,
            fields="name,fan_count,instagram_business_account{username,followers_count,media_count}",
        )

    def ig_account_reach(self, days=7):
        """Daily Instagram account reach, most recent last, trimmed to days."""
        result = self._get(f"{self._ig}/insights", metric="reach", period="day")
        series = self._first_value_series(result)
        return series[-days:]

    @staticmethod
    def _first_value_series(result):
        entry = (result.get("data") or [{}])[0]
        out = []
        for value in entry.get("values", []):
            end_time = value.get("end_time", "")
            out.append({"date": end_time[:10], "reach": value.get("value")})
        return out

    def ig_recent_media(self, limit=10):
        """Recent Instagram media, each with its own insight metrics attached."""
        media = self._get(
            f"{self._ig}/media",
            fields="id,media_type,timestamp,permalink,caption",
            limit=limit,
        )
        out = []
        for row in media.get("data", []):
            out.append(
                {
                    "id": row.get("id"),
                    "media_type": row.get("media_type"),
                    "timestamp": row.get("timestamp"),
                    "permalink": row.get("permalink"),
                    "insights": self._media_insights(row.get("id")),
                }
            )
        return out

    def _media_insights(self, media_id):
        # The full metric set is only valid for some media types. Fall back to
        # reach, which every media type supports, rather than losing the row.
        try:
            result = self._get(f"{media_id}/insights", metric=MEDIA_METRICS)
        except MetaError:
            result = self._get(f"{media_id}/insights", metric="reach")
        return {entry["name"]: self._first_value(entry) for entry in result.get("data", [])}

    def fb_recent_posts(self, limit=10):
        """Recent Facebook posts with post_clicks and reactions. No reach in v26."""
        posts = self._get(
            f"{self._page}/posts",
            fields="id,created_time,message,permalink_url",
            limit=limit,
        )
        out = []
        for row in posts.get("data", []):
            out.append(
                {
                    "id": row.get("id"),
                    "created_time": row.get("created_time"),
                    "permalink_url": row.get("permalink_url"),
                    "insights": self._fb_post_insights(row.get("id")),
                }
            )
        return out

    def _fb_post_insights(self, post_id):
        result = self._get(f"{post_id}/insights", metric=FB_POST_METRICS)
        return {entry["name"]: self._first_value(entry) for entry in result.get("data", [])}

    def summary(self, *, media_limit=10, reach_days=7):
        """One call that assembles the pieces a weekly report would consume."""
        page = self.page_summary()
        return {
            "page": {
                "name": page.get("name"),
                "fan_count": page.get("fan_count"),
            },
            "instagram": {
                "account": page.get("instagram_business_account"),
                "reach_daily": self.ig_account_reach(days=reach_days),
                "media": self.ig_recent_media(limit=media_limit),
            },
            "facebook": {
                "posts": self.fb_recent_posts(limit=media_limit),
                "note": "Facebook per post reach and impressions are deprecated in Graph v26",
            },
        }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    limit = 5
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    reader = MetaInsights(load_token())
    print(json.dumps(reader.summary(media_limit=limit), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
