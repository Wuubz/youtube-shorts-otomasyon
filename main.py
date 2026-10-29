"""6 kanallı YouTube Shorts otomasyonunun ana orkestratörü.

Örnekler:
    python main.py --channel ch1_cosmic_whatif --dry-run
    python main.py --all-enabled --schedule-slot 0
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.audio_subtitle import generate_audio_and_subtitles
from src.config_loader import (
    ROOT_DIR, ConfigError, add_topic, get_channel, get_enabled_channels, get_topics, require_env,
)
from src.image_generator import generate_images
from src.notifier import notify_failures, notify_uploaded, register_publish
from src.script_generator import generate_script
from src.state import is_done, mark_done, sync_state, today_utc
from src.styles import resolve_style
from src.video_editor import render_video
from src.youtube_uploader import compute_publish_at, slot_target, upload_video

TEMP_DIR = ROOT_DIR / "temp"
OUTPUT_DIR = ROOT_DIR / "output"
RETRY_WAIT_SECONDS = 15
UPLOAD_ATTEMPTS = 2
UPLOAD_RETRY_WAIT = 20
PASS_WAIT_SECONDS = 120   # başarısız kanallar için sonraki turdan önce bekleme

log = logging.getLogger("main")


def ensure_ffmpeg() -> bool:
    """ffmpeg/ffprobe PATH'te yoksa static-ffmpeg paketini yedek olarak devreye alır."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    try:
        import static_ffmpeg

        log.info("Sistemde FFmpeg bulunamadı; static-ffmpeg yedeği kullanılıyor (ilk çalıştırmada indirilir)...")
        static_ffmpeg.add_paths()
    except Exception as exc:
        log.error("static-ffmpeg yedeği devreye alınamadı: %s", exc)
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube Shorts otomasyonu")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--channel", help="Yalnızca bu kanalı çalıştırır (ör. ch1_cosmic_whatif)")
    target.add_argument("--all-enabled", action="store_true", help="channels.json'da enabled=true olan tüm kanalları çalıştırır")
    parser.add_argument(
        "--schedule-slot", type=int, default=None,
        help="publish_times_utc listesindeki sıra (0 veya 1); video private + publishAt ile o saate zamanlanır. Verilmezse hemen yayınlanır",
    )
    parser.add_argument("--dry-run", action="store_true", help="Videoyu üretir ama YouTube'a yüklemez")
    parser.add_argument(
        "--no-history", action="store_true",
        help="Üretilen konuyu data/history.json'a kaydetmez (varsayılan: dry-run dahil her üretimde kaydedilir)",
    )
    parser.add_argument("--retries", type=int, default=1, help="Görsel/ses/kurgu aşaması hatasında ek deneme sayısı (varsayılan 1)")
    parser.add_argument("--passes", type=int, default=2, help="Başarısız kanallar için toplam tur sayısı (varsayılan 2)")
    parser.add_argument(
        "--time-budget", type=float, default=0,
        help="Dakika cinsinden toplam süre bütçesi; dolunca yeni kanal başlatılmaz (0 = sınırsız)",
    )
    parser.add_argument("--force", action="store_true", help="Bu slot için bugün zaten üretilmiş kanalları da yeniden üretir")
    return parser.parse_args()


def produce_video(channel: dict[str, Any], script: dict[str, Any], run_dir: Path) -> Path:
    """Görseller -> ses/altyazı -> kurgu zincirini çalıştırır."""
    images = generate_images(script["scenes"], run_dir / "images")
    audio = generate_audio_and_subtitles(script["scenes"], channel, run_dir / "audio")
    return render_video(images, audio, channel["id"], run_dir / "video", OUTPUT_DIR, resolve_style(channel))


def _upload_with_retry(channel: dict[str, Any], video: Path, script: dict[str, Any], slot: int | None) -> tuple[str, str | None]:
    """Yükler ve (video_id, publish_at) döndürür. Yayın saati her denemede yükleme anına göre hesaplanır."""
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        publish_at = compute_publish_at(channel, slot) if slot is not None else None
        try:
            return upload_video(channel, video, script, publish_at), publish_at
        except Exception as exc:
            log.error("[%s] Yükleme denemesi %d/%d başarısız: %s", channel["id"], attempt, UPLOAD_ATTEMPTS, exc)
            if attempt == UPLOAD_ATTEMPTS:
                raise
            time.sleep(UPLOAD_RETRY_WAIT)
    raise RuntimeError("ulaşılamaz")


def run_channel(channel: dict[str, Any], args: argparse.Namespace, day: str) -> None:
    channel_id = channel["id"]
    require_env(channel, need_gemini=True, need_youtube=not args.dry_run)

    slot = args.schedule_slot if channel["schedule_mode"] == "scheduled" else None
    if slot is not None:  # geçersiz slot, video üretilmeden önce yakalansın
        compute_publish_at(channel, slot)

    run_dir = TEMP_DIR / f"{channel_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    try:
        # Senaryo tek sefer üretilir (kendi içinde model zinciri ve yeniden denemesi var); alttaki aşamalar
        # hata verirse Gemini'ye yeniden gidilmez.
        script = generate_script(channel, get_topics(channel_id))
        video: Path | None = None
        attempts = max(args.retries, 0) + 1
        for attempt in range(1, attempts + 1):
            try:
                video = produce_video(channel, script, run_dir / f"try{attempt}")
                break
            except Exception as exc:
                log.error("[%s] Üretim denemesi %d/%d başarısız: %s", channel_id, attempt, attempts, exc)
                if attempt == attempts:
                    raise
                time.sleep(RETRY_WAIT_SECONDS)

        if args.dry_run:
            metadata = video.with_suffix(".json")
            metadata.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("[%s] DRY-RUN: video kaydedildi, YouTube'a yüklenmedi: %s", channel_id, video)
        else:
            video_id, publish_at = _upload_with_retry(channel, video, script, slot)
            log.info("[%s] TAMAMLANDI: https://youtube.com/shorts/%s", channel_id, video_id)
            # Yükleme başarılı: buradan sonraki hiçbir hata kanalı "başarısız" saymamalı (yinelenen yükleme olmasın)
            try:
                if slot is not None:
                    mark_done(channel_id, slot, day)
                if not args.no_history:
                    add_topic(channel_id, script["topic_summary"], script.get("sub_theme"), script.get("title"))
                sync_state(f"{channel_id} yüklendi")
                notify_uploaded(channel, script, video_id, publish_at)
                register_publish(channel, script, video_id, publish_at)
            except Exception as exc:
                log.error("[%s] Yükleme sonrası kayıt/bildirim hatası (video yüklendi): %s", channel_id, exc)
            return

        if not args.no_history:
            add_topic(channel_id, script["topic_summary"], script.get("sub_theme"), script.get("title"))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def order_channels(channels: list[dict[str, Any]], slot: int | None) -> list[dict[str, Any]]:
    """Slot verilmişse en erken yayın saati olan kanal önce işlenir (geç kalan kanalın payı azalmasın)."""
    if slot is None:
        return channels
    now = datetime.now(timezone.utc)
    return sorted(channels, key=lambda c: slot_target(c, slot, now) if c["schedule_mode"] == "scheduled" else now)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    started = time.monotonic()

    if not ensure_ffmpeg():
        log.error("FFmpeg/ffprobe bulunamadı. FFmpeg'i kurun veya 'pip install static-ffmpeg' çalıştırın.")
        return 1

    try:
        channels = [get_channel(args.channel)] if args.channel else get_enabled_channels()
    except ConfigError as exc:
        log.error("Yapılandırma hatası: %s", exc)
        return 1
    if not channels:
        log.error("Çalıştırılacak aktif kanal yok (channels.json'da enabled=true yapın).")
        return 1

    day = today_utc()
    channels = order_channels(channels, args.schedule_slot)
    pending: list[dict[str, Any]] = []
    for channel in channels:
        if (
            args.schedule_slot is not None and not args.dry_run and not args.force
            and channel["schedule_mode"] == "scheduled" and is_done(channel["id"], args.schedule_slot, day)
        ):
            log.info("[%s] Slot %d bugün (%s) zaten üretilmiş; atlanıyor", channel["id"], args.schedule_slot, day)
            continue
        pending.append(channel)

    def over_budget() -> bool:
        return args.time_budget > 0 and (time.monotonic() - started) / 60 > args.time_budget

    succeeded: list[str] = []
    failed: dict[str, str] = {}
    try:
        for pass_no in range(1, max(args.passes, 1) + 1):
            if not pending:
                break
            if pass_no > 1:
                log.info("=== %d. tur: %d kanal yeniden denenecek (%d sn sonra) ===", pass_no, len(pending), PASS_WAIT_SECONDS)
                time.sleep(PASS_WAIT_SECONDS)
            still_failing: list[dict[str, Any]] = []
            for channel in pending:
                if over_budget():
                    failed[channel["id"]] = "süre bütçesi doldu, atlandı"
                    log.error("[%s] Süre bütçesi (%.0f dk) doldu; kanal atlandı", channel["id"], args.time_budget)
                    continue
                log.info("=== Kanal: %s ===", channel["id"])
                try:
                    run_channel(channel, args, day)
                    succeeded.append(channel["id"])
                    failed.pop(channel["id"], None)
                except Exception as exc:  # bir kanalın hatası diğer kanalları durdurmasın
                    log.exception("[%s] Kanal başarısız: %s", channel["id"], exc)
                    failed[channel["id"]] = str(exc)
                    still_failing.append(channel)
            pending = still_failing
    finally:
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    # Süre bütçesi nedeniyle hiç denenmeyen kanallar yalnızca 'failed' içinde kalır
    failed = {cid: reason for cid, reason in failed.items() if cid not in succeeded}
    log.info("ÖZET -> başarılı: %s | başarısız: %s", succeeded or "-", list(failed) or "-")
    if failed and not args.dry_run:
        notify_failures(failed, succeeded)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
