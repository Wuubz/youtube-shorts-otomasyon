"""Pollinations.ai (flux) ile sahne görsellerini indirir; başarısızlıkta FFmpeg yedeği üretir."""
from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

_QUERY = "?width=1080&height=1920&model=flux&nologo=true&seed={seed}"
# Anahtarsız ücretsiz uç nokta (IP başına 45 sn'de 1 görsel)
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}" + _QUERY
# API anahtarı (sk_...) yalnızca bu uç noktada geçerli; eski uç nokta anahtarı yok sayıp ücretsiz limite takılır
POLLINATIONS_KEY_URL = "https://gen.pollinations.ai/image/{prompt}" + _QUERY
WIDTH, HEIGHT = 1080, 1920
MIN_IMAGE_BYTES = 15 * 1024
MAX_ATTEMPTS = 4
MAX_PROMPT_CHARS = 900
REQUEST_TIMEOUT = (15, 150)  # (bağlantı, okuma) saniye
API_KEY_ENV = "POLLINATIONS_API_KEY"
MAX_RATE_WAIT_TOTAL = 300     # sahne başına limitte en fazla bu kadar saniye beklenir (deneme hakkından düşmez)
DEFAULT_RATE_WAIT = 47
USER_AGENT = "Mozilla/5.0 (compatible; ShortsAutomation/1.0)"
_RETRY_AFTER_RE = re.compile(r"Retry after (\d+)\s*s", re.IGNORECASE)


class AuthError(Exception):
    """POLLINATIONS_API_KEY reddedildi; anahtarsız (ücretsiz) moda dönülür."""


class RateLimitError(Exception):
    """Pollinations ücretsiz katman limiti; wait kadar saniye beklenmeli."""

    def __init__(self, wait: int):
        super().__init__(f"istek limiti, {wait} sn beklenecek")
        self.wait = wait

# (üst renk RGB, alt renk RGB) koyu sinematik gradyanlar
_GRADIENTS = [
    ((8, 12, 32), (48, 24, 86)),
    ((10, 10, 14), (64, 18, 24)),
    ((6, 20, 28), (20, 64, 78)),
    ((18, 10, 30), (84, 40, 52)),
    ((8, 8, 8), (44, 44, 60)),
]


def _detect_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _download(prompt: str, seed: int, dest_stem: Path, api_key: str | None = None) -> Path:
    template = POLLINATIONS_KEY_URL if api_key else POLLINATIONS_URL
    url = template.format(prompt=quote(prompt[:MAX_PROMPT_CHARS], safe=""), seed=seed)
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        url += f"&key={quote(api_key, safe='')}"
        headers["Authorization"] = f"Bearer {api_key}"
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
    if api_key and response.status_code in (401, 403):
        raise AuthError(f"HTTP {response.status_code}")
    if response.status_code in (402, 429):  # ücretsiz katman: IP başına 45 sn'de 1 görsel
        match = _RETRY_AFTER_RE.search(response.text)
        if api_key and not match and "free limit" not in response.text.lower():
            detail = re.sub(r"\s+", " ", response.text)[:160].replace(api_key, "***")
            raise AuthError(f"HTTP {response.status_code} (anahtar bakiyesi/yetkisi yetersiz): {detail}")
        raise RateLimitError(int(match.group(1)) + 2 if match else DEFAULT_RATE_WAIT)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    data = response.content
    extension = _detect_extension(data)
    if extension is None:
        raise ValueError("Yanıt geçerli bir görsel dosyası değil")
    if len(data) < MIN_IMAGE_BYTES:
        raise ValueError(f"Görsel çok küçük: {len(data)} bayt (en az {MIN_IMAGE_BYTES})")
    dest = dest_stem.with_suffix(extension)
    dest.write_bytes(data)
    return dest


def _fallback_gradient(dest: Path) -> Path:
    """Koyu sinematik gradyan arka plan üretir (indirme tamamen başarısız olursa)."""
    (r0, g0, b0), (r1, g1, b1) = random.choice(_GRADIENTS)
    gradient_filter = (
        f"format=gbrp,geq=r='lerp({r0},{r1},Y/H)':g='lerp({g0},{g1},Y/H)':b='lerp({b0},{b1},Y/H)'"
    )
    base = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi"]
    tail = ["-frames:v", "1", "-pix_fmt", "yuvj420p", "-q:v", "2", str(dest)]
    attempts = [
        base + ["-i", f"color=c=black:s={WIDTH}x{HEIGHT}:d=1:r=1", "-vf", gradient_filter] + tail,
        base + ["-i", f"color=c=0x{r0:02x}{g0:02x}{b0:02x}:s={WIDTH}x{HEIGHT}:d=1:r=1"] + tail,
    ]
    for cmd in attempts:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and dest.exists():
            return dest
        log.warning("Yedek gradyan komutu başarısız: %s", result.stderr.strip()[-300:])
    raise RuntimeError("Yedek arka plan üretilemedi (ffmpeg kurulu mu?)")


def generate_images(scenes: list[dict[str, str]], out_dir: Path) -> list[Path]:
    """Her sahne için bir görsel üretir ve dosya yollarını sahne sırasıyla döndürür.

    Her sahne için farklı seed ile 4 deneme yapılır; hepsi başarısızsa gradyan yedek kullanılır.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get(API_KEY_ENV, "").strip() or None
    log.info(
        "Pollinations modu: %s",
        "API anahtarı ile (gen.pollinations.ai, bekleme yok)" if api_key else "anahtarsız ücretsiz katman (45 sn/görsel)",
    )
    paths: list[Path] = []
    for index, scene in enumerate(scenes):
        stem = out_dir / f"scene_{index}"
        result: Path | None = None
        attempt = 0
        rate_waited = 0.0   # bu sahne için limitte toplam beklenen süre
        rate_streak = 0     # art arda limit yanıtı sayısı
        while attempt < MAX_ATTEMPTS and rate_waited <= MAX_RATE_WAIT_TOTAL:
            seed = random.randint(1, 2_147_483_647)
            try:
                result = _download(scene["image_prompt"], seed, stem, api_key)
                log.info("Sahne %d/%d görseli indirildi (seed %d)", index + 1, len(scenes), seed)
                break
            except RateLimitError as exc:
                rate_streak += 1
                # Sunucu çok kısa süre söylese de limit sürüyorsa bekleme giderek uzar (en fazla 40 sn)
                wait = max(exc.wait, min(5 * rate_streak, 40))
                rate_waited += wait
                log.info("Sahne %d: Pollinations ücretsiz limit, %d sn bekleniyor...", index + 1, wait)
                time.sleep(wait)
                continue
            except AuthError as exc:
                log.warning("Pollinations API anahtarı kullanılamadı (%s); anahtarsız ücretsiz moda geçiliyor", exc)
                api_key = None
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                attempt += 1
                reason = str(exc).replace(api_key, "***") if api_key else str(exc)  # URL'deki anahtarı loga sızdırma
                log.warning("Sahne %d görsel denemesi %d/%d başarısız: %s", index + 1, attempt, MAX_ATTEMPTS, reason)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(3 * attempt)
        if result is None:
            log.error("Sahne %d için tüm denemeler başarısız; yedek gradyan kullanılıyor", index + 1)
            result = _fallback_gradient(stem.with_suffix(".jpg"))
        paths.append(result)
    return paths
