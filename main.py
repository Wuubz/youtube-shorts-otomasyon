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
from src.script_generator import generate_script
from src.video_editor import render_video
from src.youtube_uploader import compute_publish_at, upload_video

TEMP_DIR = ROOT_DIR / "temp"
OUTPUT_DIR = ROOT_DIR / "output"
RETRY_WAIT_SECONDS = 15

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
    parser.add_argument("--retries", type=int, default=1, help="Üretim hattı hatasında ek deneme sayısı (varsayılan 1)")
    return parser.parse_args()


def produce_video(channel: dict[str, Any], run_dir: Path) -> tuple[dict[str, Any], Path]:
    """Senaryo -> görseller -> ses/altyazı -> kurgu zincirini çalıştırır."""
    channel_id = channel["id"]
    script = generate_script(channel, get_topics(channel_id))
    images = generate_images(script["scenes"], run_dir / "images")
    audio = generate_audio_and_subtitles(script["scenes"], channel, run_dir / "audio")
    video = render_video(images, audio, channel_id, run_dir / "video", OUTPUT_DIR)
    return script, video


def run_channel(channel: dict[str, Any], args: argparse.Namespace) -> None:
    channel_id = channel["id"]
    require_env(channel, need_gemini=True, need_youtube=not args.dry_run)

    scheduled = args.schedule_slot is not None and channel["schedule_mode"] == "scheduled"
    if scheduled:  # geçersiz slot, video üretilmeden önce yakalansın
        compute_publish_at(channel, args.schedule_slot)

    run_dir = TEMP_DIR / f"{channel_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    try:
        script, video = None, None
        attempts = max(args.retries, 0) + 1
        for attempt in range(1, attempts + 1):
            try:
                script, video = produce_video(channel, run_dir / f"try{attempt}")
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
            # Saat üretimden sonra hesaplanır: üretim süresi 15 dk'lık minimum payı tüketmesin
            publish_at = compute_publish_at(channel, args.schedule_slot) if scheduled else None
            video_id = upload_video(channel, video, script, publish_at)
            log.info("[%s] TAMAMLANDI: https://youtube.com/shorts/%s", channel_id, video_id)

        # Video başarıyla üretildi (ve dry-run değilse yüklendi): konu bir daha seçilmesin
        if not args.no_history:
            add_topic(channel_id, script["topic_summary"])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args = parse_args()

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

    succeeded: list[str] = []
    failed: list[str] = []
    try:
        for channel in channels:
            log.info("=== Kanal: %s ===", channel["id"])
            try:
                run_channel(channel, args)
                succeeded.append(channel["id"])
            except Exception as exc:  # bir kanalın hatası diğer kanalları durdurmasın
                log.exception("[%s] Kanal başarısız: %s", channel["id"], exc)
                failed.append(channel["id"])
    finally:
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    log.info("ÖZET -> başarılı: %s | başarısız: %s", succeeded or "-", failed or "-")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
