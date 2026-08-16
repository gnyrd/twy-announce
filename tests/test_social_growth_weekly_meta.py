from social_growth_weekly import (
    _ig_shortcode,
    _meta_cross_check,
    render_markdown,
    render_slack,
)


def _post(url, reach, likes=0):
    return {
        "platform_post_url": url,
        "class_name": "2026-06-02_prin-flow",
        "clip_name": "08_inspirational_score8_17s",
        "metrics": {"reach": reach, "likes": likes, "comments": 0, "saves": 0, "shares": 0},
    }


def _media(url, reach, likes=0):
    return {
        "permalink": url,
        "insights": {"reach": reach, "likes": likes, "comments": 0, "saved": 0, "shares": 0},
    }


def test_ig_shortcode():
    assert _ig_shortcode("https://www.instagram.com/reel/DcBUoGLASWS/") == "DcBUoGLASWS"
    assert _ig_shortcode("https://instagram.com/p/AbC123/?utm=x") == "AbC123"
    assert _ig_shortcode("") is None
    assert _ig_shortcode(None) is None


def test_cross_check_unavailable_none():
    result = _meta_cross_check([], None)
    assert result["status"] == "unavailable"
    assert result["reason"] == "meta insights not fetched"


def test_cross_check_unavailable_empty():
    result = _meta_cross_check([_post("https://www.instagram.com/reel/A/", 10)], [])
    assert result["status"] == "unavailable"
    assert result["reason"] == "no meta media returned"


def test_cross_check_matches_and_flags():
    posts = [
        _post("https://www.instagram.com/reel/AAA/", 48, likes=2),
        _post("https://www.instagram.com/reel/BBB/", 200, likes=5),
        _post("https://www.instagram.com/reel/CCC/", 30),
    ]
    media = [
        _media("https://www.instagram.com/reel/AAA/", 48, likes=2),
        _media("https://www.instagram.com/reel/BBB/", 50, likes=5),
    ]
    result = _meta_cross_check(posts, media)
    assert result["status"] == "ok"
    assert result["matched_count"] == 2
    assert result["unmatched_posts"] == 1
    assert result["agree_count"] == 1
    assert result["disagree_count"] == 1
    by_code = {row["shortcode"]: row for row in result["matched"]}
    assert by_code["AAA"]["reach_agrees"] is True
    assert by_code["AAA"]["reach_delta"] == 0
    assert by_code["BBB"]["reach_agrees"] is False
    assert by_code["BBB"]["reach_delta"] == 150


def _min_report(cross):
    delta = {"start": 0, "end": 0, "delta": 0}
    return {
        "week_start": "2026-08-10",
        "week_end": "2026-08-16",
        "snapshot_count": 3,
        "metrics": {
            "instagram_followers": delta,
            "email_subscribers": delta,
            "next_habit_registrations": delta,
            "landing_page": {
                "visitors": 0,
                "pageviews": 0,
                "habit_register_clicks": 0,
                "habit_signup_success": 0,
            },
        },
        "post_performance": {
            "posts_analyzed": 0,
            "posts": [],
            "totals": {"reach": 0, "growth_actions": 0},
            "top_by_reach": None,
        },
        "campaign_performance": {"variants": []},
        "recommendations": ["Do the thing."],
        "meta_cross_check": cross,
    }


def test_render_markdown_shows_cross_check():
    cross = {
        "status": "ok",
        "matched": [
            {
                "label": "Align and Reach",
                "reach_agrees": True,
                "reach_delta": 0,
                "zernio": {"reach": 48, "likes": 2},
                "meta": {"reach": 48, "likes": 2},
            }
        ],
        "matched_count": 1,
        "agree_count": 1,
        "disagree_count": 0,
        "unmatched_posts": 0,
    }
    markdown = render_markdown(_min_report(cross))
    assert "## Meta cross-check (Instagram)" in markdown
    assert "Zernio reach" in markdown
    assert "Align and Reach" in markdown


def test_render_markdown_unavailable():
    markdown = render_markdown(
        _min_report({"status": "unavailable", "reason": "meta insights not fetched", "matched": []})
    )
    assert "Unavailable this run" in markdown


def test_render_slack_shows_cross_check():
    cross = {
        "status": "ok",
        "matched": [],
        "matched_count": 3,
        "agree_count": 2,
        "disagree_count": 1,
        "unmatched_posts": 0,
    }
    slack = render_slack(_min_report(cross))
    assert "Meta cross-check:" in slack
