"""Telifsiz (tamamen sentezlenmiş) sinematik ses efektlerini üretir.

Hiçbir örnek (sample) kullanılmaz: sesler numpy ile sıfırdan sentezlenir, FFmpeg ile mp3'e çevrilir.
    assets/sfx/whoosh.mp3     hızlı hava akımı (videonun ilk saniyesi)
    assets/sfx/sub_drop.mp3   derin sinematik bas darbesi (kanca sahnesinin sonu)
    assets/sfx/glitch.mp3     kısa dijital gerilim sesi (korku/gizem)

Kullanım:
    python scripts/generate_sfx.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SFX_DIR = ROOT_DIR / "assets" / "sfx"
SR = 44100
PEAK = 0.9


def _time(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def _normalize(stereo: np.ndarray, fade_out: float = 0.05) -> np.ndarray:
    stereo = stereo / max(np.abs(stereo).max(), 1e-9) * PEAK
    fade = int(SR * fade_out)
    stereo[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None]
    return stereo


def _sweeping_lowpass(noise: np.ndarray, cutoff: np.ndarray) -> np.ndarray:
    """Zamanla değişen kesim frekanslı tek kutuplu alçak geçiren filtre."""
    alpha = 1.0 - np.exp(-2 * np.pi * cutoff / SR)
    out = np.empty_like(noise)
    state = 0.0
    for i, (sample, a) in enumerate(zip(noise, alpha)):
        state += a * (sample - state)
        out[i] = state
    return out


def whoosh() -> np.ndarray:
    rng = np.random.default_rng(11)
    t = _time(0.9)
    progress = t / t[-1]
    noise = rng.standard_normal(t.size)
    cutoff = 300 + 5200 * np.sin(np.pi * progress) ** 1.5      # kesim önce yükselir, sonra düşer
    body = _sweeping_lowpass(noise, cutoff)
    body = body - _sweeping_lowpass(body, np.full(t.size, 180.0))  # alt gürültüyü ayıkla
    envelope = np.sin(np.pi * np.clip(progress, 0, 1)) ** 2.2
    mono = body * envelope
    pan = progress                                              # sesi soldan sağa süpürür
    return _normalize(np.stack([mono * (1 - 0.6 * pan), mono * (0.4 + 0.6 * pan)], axis=1))


def sub_drop() -> np.ndarray:
    rng = np.random.default_rng(12)
    t = _time(1.8)
    freq = 38 + 80 * np.exp(-t / 0.18)                          # 118 Hz'den 38 Hz'e düşen bas
    phase = 2 * np.pi * np.cumsum(freq) / SR
    amp = np.exp(-t / 0.55) * np.minimum(t / 0.004, 1.0)
    tone = np.sin(phase) * amp + 0.25 * np.sin(2 * phase) * np.exp(-t / 0.25)
    click = _sweeping_lowpass(rng.standard_normal(t.size), np.full(t.size, 900.0)) * np.exp(-t / 0.03) * 0.6
    mono = tone + click
    return _normalize(np.stack([mono, mono], axis=1), fade_out=0.15)


def glitch() -> np.ndarray:
    rng = np.random.default_rng(13)
    t = _time(0.55)
    step = int(SR * 0.035)
    mono = np.zeros(t.size)
    for start in range(0, t.size, step):
        end = min(start + step, t.size)
        n = end - start
        kind = rng.integers(0, 3)
        if kind == 0:     # kare dalga
            tone = np.sign(np.sin(2 * np.pi * rng.uniform(180, 1800) * np.arange(n) / SR))
        elif kind == 1:   # örnek tutmalı (bit-crush) gürültü
            hold = rng.integers(8, 40)
            tone = np.repeat(rng.standard_normal(n // hold + 1), hold)[:n]
        else:             # sessizlik boşluğu
            tone = np.zeros(n)
        mono[start:end] = tone * rng.uniform(0.5, 1.0)
    mono *= np.exp(-t / 0.35)
    shift = int(SR * 0.004)
    right = np.roll(mono, shift)                                # sağ kanal hafif gecikmeli
    return _normalize(np.stack([mono, right], axis=1), fade_out=0.04)


def write_mp3(stereo: np.ndarray, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "sfx.wav"
        with wave.open(str(wav_path), "wb") as fh:
            fh.setnchannels(2)
            fh.setsampwidth(2)
            fh.setframerate(SR)
            fh.writeframes(pcm.tobytes())
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "libmp3lame", "-b:a", "192k", str(dest)],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg hatası: {result.stderr.strip()[-600:]}")


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        return
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"FFmpeg bulunamadı ve static-ffmpeg yüklenemedi: {exc}")


def main() -> int:
    ensure_ffmpeg()
    for name, make in (("whoosh", whoosh), ("sub_drop", sub_drop), ("glitch", glitch)):
        dest = SFX_DIR / f"{name}.mp3"
        write_mp3(make(), dest)
        print(f"{dest.relative_to(ROOT_DIR)}  ({dest.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
