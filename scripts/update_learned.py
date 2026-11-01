"""Haftalık geri bildirim: kanalların gerçek izlenme verisinden en iyi/en kötü başlıkları çıkarıp data/learned.json'a yazar.

script_generator bu başlıkları bir sonraki videoların prompt'una "en iyi çalışanlar / kaçınılacaklar" olarak ekler.
Yalnızca okuma yapar: herkese açık video istatistikleri CH4 token'ı ile okunur (yeni yetki gerekmez).
Sonuç ayrıca Telegram'a özet olarak gönderilir.

Kullanım: python scripts/update_learned.py [--dry-run]
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config_loader import LEARNED_PATH, _atomic_write_json, load_channels  # noqa: E402
from src.notifier import send_message  # noqa: E402

log = logging.getLogger("update_learned")

API = "https://www.googleapis.com/youtube/v3/"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MIN_AGE = timedelta(hours=72)    # izlenmenin büyük kısmı ilk günlerde birikir; çok yeni videolar adil kıyaslanamaz
MIN_VIDEOS = 8                   # bundan azı varsa kanalın eski öğrenilmiş listesi korunur
MAX_VIDEOS = 150
LIST_SIZE = 5
MIN_WINNER_VIEWS = 50
SHORT_LIMIT_SECONDS = 36         # kısa/uzun sürüm denemesi için sınır
TIMEOUT = 30


def _access_token() -> str:
    names = ("YT_CH4_CLIENT_ID", "YT_CH4_CLIENT_SECRET", "YT_CH4_REFRESH_TOKEN")
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        raise RuntimeError(f"Eksik ortam değişkenleri: {', '.join(missing)}")
    response = requests.post(TOKEN_URL, data={
        "client_id": os.environ["YT_CH4_CLIENT_ID"], "client_secret": os.environ["YT_CH4_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_CH4_REFRESH_TOKEN"], "grant_type": "refresh_token",
    }, timeout=TIMEOUT)
    token = response.json().get("access_token") if response.ok else None
    if not token:
        raise RuntimeError(f"Erişim jetonu alınamadı: HTTP {response.status_code}")
    return token


def _get(path: str, token: str, **params: Any) -> dict[str, Any]:
    response = requests.get(API + path, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"{path}: HTTP {response.status_code} {response.text[:160]}")
    return response.json()


def _seconds(iso_duration: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not match:
        return 0
    hours, minutes, secs = (int(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + secs


def _fetch_videos(token: str, youtube_channel_id: str) -> list[dict[str, Any]]:
    info = _get("channels", token, part="contentDetails", id=youtube_channel_id)
    items = info.get("items") or []
    if not items:
        raise RuntimeError("kanal bulunamadı")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    page = None
    while len(ids) < MAX_VIDEOS:
        params = {"pageToken": page} if page else {}
        listing = _get("playlistItems", token, part="contentDetails", playlistId=uploads, maxResults=50, **params)
        ids += [i["contentDetails"]["videoId"] for i in listing.get("items", [])]
        page = listing.get("nextPageToken")
        if not page:
            break
    videos: list[dict[str, Any]] = []
    for start in range(0, len(ids), 50):
        batch = _get("videos", token, part="snippet,statistics,contentDetails", id=",".join(ids[start:start + 50]))
        for item in batch.get("items", []):
            videos.append({
                "title": re.sub(r"\s*#shorts\s*$", "", item["snippet"]["title"], flags=re.IGNORECASE).strip(),
                "published": datetime.fromisoformat(item["snippet"]["publishedAt"].replace("Z", "+00:00")),
                "views": int(item["statistics"].get("viewCount", 0)),
                "seconds": _seconds(item["contentDetails"].get("duration", "")),
            })
    return videos


def analyse(videos: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Yeterince yaşlanmış videolardan kazanan/kaybeden başlıkları ve kısa/uzun sürüm karşılaştırmasını çıkarır."""
    mature = [v for v in videos if now - v["published"] >= MIN_AGE]
    if len(mature) < MIN_VIDEOS:
        return None
    mid = median(v["views"] for v in mature)
    ranked = sorted(mature, key=lambda v: v["views"], reverse=True)
    threshold = max(MIN_WINNER_VIEWS, 2 * mid)
    winners = [v for v in ranked if v["views"] >= threshold][:LIST_SIZE]
    losers = [v for v in reversed(ranked) if v["views"] < mid][:LIST_SIZE]
    result: dict[str, Any] = {
        "updated": now.strftime("%Y-%m-%d"),
        "videos": len(mature),
        "median_views": mid,
        "best": {"title": ranked[0]["title"], "views": ranked[0]["views"]},
        "winners": [v["title"] for v in winners],
        "losers": [v["title"] for v in losers],
    }
    buckets = {
        "short": [v["views"] for v in mature if 0 < v["seconds"] <= SHORT_LIMIT_SECONDS],
        "long": [v["views"] for v in mature if v["seconds"] > SHORT_LIMIT_SECONDS],
    }
    result["by_length"] = {
        name: {"videos": len(views), "median_views": median(views)} for name, views in buckets.items() if views
    }
    return result


def _report(learned: dict[str, dict[str, Any]], names: dict[str, str]) -> str:
    lines = ["📊 <b>Haftalık performans özeti</b>", ""]
    for channel_id, entry in learned.items():
        lines.append(f"<b>{html.escape(names.get(channel_id, channel_id), quote=False)}</b> - {entry['videos']} video, medyan {entry['median_views']:g}")
        lines.append(f"   🏆 {html.escape(entry['best']['title'], quote=False)} ({entry['best']['views']})")
        by_length = entry.get("by_length", {})
        if len(by_length) == 2:
            lines.append(
                f"   ⏱ ≤{SHORT_LIMIT_SECONDS} sn: medyan {by_length['short']['median_views']:g} ({by_length['short']['videos']} video) "
                f"| uzun: medyan {by_length['long']['median_views']:g} ({by_length['long']['videos']} video)"
            )
    lines += ["", "En iyi/en kötü başlıklar bir sonraki videoların prompt'una eklendi."]
    return "\n".join(lines)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Haftalık izlenme verisinden öğrenilen başlık listelerini günceller")
    parser.add_argument("--dry-run", action="store_true", help="Sonucu yazdırır; dosyaya yazmaz, Telegram'a göndermez")
    args = parser.parse_args()

    channels = load_channels()
    try:
        token = _access_token()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    existing: dict[str, Any] = {}
    if LEARNED_PATH.exists():
        try:
            existing = json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("learned.json bozuk; sıfırdan yazılıyor")
    learned: dict[str, dict[str, Any]] = dict(existing)
    fresh: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for channel_id, cfg in channels.items():
        youtube_id = cfg.get("youtube_channel_id")
        if not cfg["enabled"] or not youtube_id:
            continue
        try:
            result = analyse(_fetch_videos(token, youtube_id), now)
        except (RuntimeError, requests.RequestException) as exc:
            log.error("[%s] istatistik okunamadı: %s", channel_id, exc)
            continue
        if result is None:
            log.info("[%s] yeterli olgun video yok (en az %d); önceki liste korunuyor", channel_id, MIN_VIDEOS)
            continue
        learned[channel_id] = fresh[channel_id] = result
        log.info("[%s] medyan %g | kazanan %d | kaybeden %d | %s", channel_id, result["median_views"],
                 len(result["winners"]), len(result["losers"]), result["by_length"])

    if not fresh:
        log.error("Hiçbir kanal güncellenemedi")
        return 1
    if args.dry_run:
        print(_report(fresh, {cid: c.get("display_name", cid) for cid, c in channels.items()}))
        return 0
    _atomic_write_json(LEARNED_PATH, learned)
    send_message(_report(fresh, {cid: c.get("display_name", cid) for cid, c in channels.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
