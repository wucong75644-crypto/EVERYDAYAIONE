from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from scripts.backfill_media_asset_urls import BackfillStats, backfill_value, strip_oss_process


def test_strip_oss_process_preserves_other_query_params():
    url = (
        "https://cdn.everydayai.com.cn/workspace/a.png"
        "?token=abc&x-oss-process=image/resize,w_360,m_lfit&v=1"
    )

    result = strip_oss_process(url)

    assert result == "https://cdn.everydayai.com.cn/workspace/a.png?token=abc&v=1"


def test_backfills_original_and_thumbnail_from_legacy_url():
    stats = BackfillStats()
    payload = [{"type": "image", "url": "https://cdn.everydayai.com.cn/workspace/a.png"}]

    result, changed = backfill_value(payload, stats)

    assert changed is True
    image = result[0]
    assert image["original_url"] == "https://cdn.everydayai.com.cn/workspace/a.png"
    assert image["thumbnail_url"].endswith("x-oss-process=image/resize,w_360,m_lfit")
    assert stats.original_added == 1
    assert stats.thumbnail_added == 1


def test_keeps_existing_thumbnail_url():
    stats = BackfillStats()
    payload = [{
        "type": "image",
        "url": "https://cdn.everydayai.com.cn/workspace/a.png",
        "original_url": "https://cdn.everydayai.com.cn/workspace/a.png",
        "thumbnail_url": "https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_120,m_lfit",
    }]

    result, changed = backfill_value(payload, stats)

    assert changed is False
    assert result[0]["thumbnail_url"].endswith("w_120,m_lfit")
    assert stats.thumbnail_added == 0


def test_normalizes_original_when_url_has_thumbnail_process():
    stats = BackfillStats()
    payload = [{
        "type": "image",
        "url": "https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_360,m_lfit",
    }]

    result, changed = backfill_value(payload, stats)

    assert changed is True
    image = result[0]
    assert image["original_url"] == "https://cdn.everydayai.com.cn/workspace/a.png"
    assert image["thumbnail_url"].startswith("https://cdn.everydayai.com.cn/workspace/a.png?")
