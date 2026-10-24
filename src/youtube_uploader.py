"""YouTube Data API v3 ile kanal bazlı OAuth2 yenilemeli, zamanlanabilir video yükleme."""
from __future__ import annotations

import logging
import math
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config_loader import get_youtube_credentials_env

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_UPLOAD_RETRIES = 8
MIN_LEAD_TIME = timedelta(minutes=15)  # publishAt şimdiden en az bu kadar ileride olmalı; değilse now + 15 dk
PAST_GRACE = timedelta(hours=6)        # cron gecikmesinde bu kadar geçmiş saat hâlâ "bugünün slotu" sayılır


def build_service(channel: dict[str, Any]):
    """Kanalın kendi CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN değerleriyle servis oluşturur."""
    env = get_youtube_credentials_env(channel)
    credentials = Credentials(
        token=None,
        refresh_token=env["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=env["client_id"],
        client_secret=env["client_secret"],
        scopes=SCOPES,
    )
    credentials.refresh(Request())
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def compute_publish_at(channel: dict[str, Any], slot: int, now: datetime | None = None) -> str:
    """publish_times_utc[slot] saatini UTC takvim gününe oturtur (ISO 8601, Z).

    Saat bugün henüz gelmediyse bugüne, 01:00 gibi gün dönümünden sonraki saatlerse ertesi güne yazılır.
    Cron gecikmesiyle hedef saat 6 saate kadar geride kaldıysa ertesi güne atlanmaz (o günün kendi videosuyla
    çakışmasın diye); hedef şimdiden en az 15 dk ileride değilse now + 15 dk kullanılır.
    """
    times = channel["publish_times_utc"]
    if not 0 <= slot < len(times):
        raise ValueError(f"{channel['id']}: geçersiz slot {slot}; 0-{len(times) - 1} arası olmalı")
    now = now or datetime.now(timezone.utc)
    hour, minute = (int(part) for part in times[slot].split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Saatin, [now - PAST_GRACE, now - PAST_GRACE + 24 sa) aralığına düşen tek oluşumunu seç
    days = math.ceil((now - PAST_GRACE - target) / timedelta(days=1))
    target += timedelta(days=days)
    earliest = now.replace(microsecond=0) + MIN_LEAD_TIME + timedelta(seconds=1)
    if target < earliest:
        log.warning(
            "%s: %s UTC yayın saati yakın/geçmiş; en erken uygun zamana (%s) çekildi",
            channel["id"], times[slot], earliest.strftime(ISO_FORMAT),
        )
        target = earliest
    return target.strftime(ISO_FORMAT)


def _valid_future_time(publish_at_utc: str | None) -> str | None:
    if not publish_at_utc:
        return None
    try:
        parsed = datetime.strptime(publish_at_utc, ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("Geçersiz publish_at '%s' (YYYY-MM-DDTHH:MM:SSZ bekleniyor); hemen yayınlanacak", publish_at_utc)
        return None
    if parsed <= datetime.now(timezone.utc) + timedelta(minutes=1):
        log.warning("publish_at geçmişte kaldı (%s); video hemen yayınlanacak", publish_at_utc)
        return None
    return publish_at_utc


def _resumable_upload(request) -> dict[str, Any]:
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log.info("Yükleniyor: %%%d", int(status.progress() * 100))
        except HttpError as exc:
            if exc.resp.status not in RETRIABLE_STATUS:
                raise
            error: Exception = exc
        except (ConnectionError, TimeoutError, OSError) as exc:
            error = exc
        else:
            continue
        retry += 1
        if retry > MAX_UPLOAD_RETRIES:
            raise RuntimeError(f"Yükleme {MAX_UPLOAD_RETRIES} yeniden denemeden sonra başarısız: {error}")
        delay = random.random() * 2 ** retry
        log.warning("Geçici yükleme hatası (%s); %.1f sn sonra yeniden denenecek", error, delay)
        time.sleep(delay)
    return response


def upload_video(
    channel: dict[str, Any], video_path: Path, script: dict[str, Any], publish_at_utc: str | None = None
) -> str:
    """Videoyu yükler ve YouTube video ID'sini döndürür."""
    service = build_service(channel)
    publish_at = _valid_future_time(publish_at_utc)

    status: dict[str, Any] = {
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    if publish_at:
        status.update({"privacyStatus": "private", "publishAt": publish_at})
    else:
        status["privacyStatus"] = "public"

    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": script["description"][:5000],
            "tags": script["tags"],
            "categoryId": str(channel["category_id"]),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": status,
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    log.info(
        "[%s] Yükleme başlıyor: %s (%s)",
        channel["id"], video_path.name, f"planlı {publish_at}" if publish_at else "hemen herkese açık",
    )
    response = _resumable_upload(request)
    video_id = response["id"]
    log.info("[%s] Yüklendi: https://youtube.com/shorts/%s", channel["id"], video_id)
    return video_id
