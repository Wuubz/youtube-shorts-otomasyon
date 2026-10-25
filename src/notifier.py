"""Telegram bildirimleri ve yayın zamanı kaydı (Cloudflare Worker). Tanımlı değilse sessizce atlanır."""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

TURKEY_TZ = timezone(timedelta(hours=3))
_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
REQUEST_TIMEOUT = 15


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    return bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"))


def send_message(text: str) -> bool:
    """Telegram'a HTML mesajı gönderir; hata verirse yalnızca loglar (üretim hattını asla durdurmaz)."""
    if not enabled():
        return False
    token = _env("TELEGRAM_BOT_TOKEN")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": _env("TELEGRAM_CHAT_ID"), "text": text, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            log.warning("Telegram mesajı gönderilemedi: HTTP %d %s", response.status_code, response.text[:150])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Telegram mesajı gönderilemedi: %s", str(exc).replace(token, "***"))
        return False


def format_turkey_time(iso_utc: str) -> str:
    moment = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TURKEY_TZ)
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment:%H:%M} TSİ"


def _display_name(channel: dict[str, Any]) -> str:
    return channel.get("display_name") or channel["id"]


def notify_uploaded(channel: dict[str, Any], script: dict[str, Any], video_id: str, publish_at: str | None) -> None:
    link = f"https://youtube.com/shorts/{video_id}"
    if publish_at:
        when = f"⏰ Yayın zamanı: <b>{format_turkey_time(publish_at)}</b>"
        head = "📤 Video yüklendi, yayın için planlandı"
    else:
        when = "⏰ Hemen yayında"
        head = "✅ Video yayında"
    send_message(
        f"{head}\n\n📺 <b>{html.escape(_display_name(channel))}</b>\n🎬 {html.escape(script['title'])}\n{when}\n🔗 {link}"
    )


def notify_failures(failed: dict[str, str], succeeded: list[str]) -> None:
    lines = ["⚠️ <b>Üretim sorunu</b>", ""]
    for channel_id, reason in failed.items():
        lines.append(f"❌ {html.escape(channel_id)}: {html.escape(reason[:160])}")
    if succeeded:
        lines.append("")
        lines.append("✅ Başarılı: " + ", ".join(html.escape(c) for c in succeeded))
    run_url = _run_url()
    if run_url:
        lines.append(f"\n🔎 {run_url}")
    send_message("\n".join(lines))


def _run_url() -> str:
    repo, run_id = _env("GITHUB_REPOSITORY"), _env("GITHUB_RUN_ID")
    return f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else ""


def register_publish(channel: dict[str, Any], script: dict[str, Any], video_id: str, publish_at: str | None) -> None:
    """Yayın saati geldiğinde 'yayında' bildirimi göndermesi için Worker'a kaydeder (en iyi çaba)."""
    base, secret = _env("WORKER_URL").rstrip("/"), _env("WORKER_SECRET")
    if not (base and secret and publish_at):
        return
    try:
        response = requests.post(
            f"{base}/register",
            headers={"Authorization": f"Bearer {secret}"},
            json={
                "channel_id": channel["id"],
                "name": _display_name(channel),
                "title": script["title"],
                "video_id": video_id,
                "publish_at": publish_at,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            log.warning("Yayın kaydı Worker'a iletilemedi: HTTP %d", response.status_code)
    except requests.RequestException as exc:
        log.warning("Yayın kaydı Worker'a iletilemedi: %s", exc)
