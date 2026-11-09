"""Telifsiz (tamamen sentezlenmiş) sinematik ses efektlerini üretir.

Hiçbir örnek (sample) kullanılmaz: sesler numpy ile sıfırdan sentezlenir, FFmpeg ile mp3'e çevrilir.
    assets/sfx/whoosh.mp3     hızlı hava akımı (videonun ilk saniyesi)
    assets/sfx/sub_drop.mp3   derin sinematik bas darbesi (kanca sahnesinin sonu)
    assets/sfx/glitch.mp3     kısa dijital gerilim sesi (korku/gizem)
    assets/sfx/slam.mp3       dosyanın masaya çarpması (kanıt belgesi)
    assets/sfx/marker.mp3     fosforlu kalemle çizgi çekme sesi
    assets/sfx/typewriter.mp3 daktilo / kanıt klik sesi
    assets/sfx/shutter.mp3    fotoğraf makinesi deklanşörü
    assets/sfx/heartbeat.mp3  kalp atışı (lub-dub)
    assets/sfx/deep_boom.mp3  uzun, derin sinematik patlama

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


def _bandpass(noise: np.ndarray, low: float, high: float) -> np.ndarray:
    """Gürültüden [low, high] Hz bandını ayıklar (iki alçak geçiren filtrenin farkı)."""
    top = _sweeping_lowpass(noise, np.full(noise.size, float(high)))
    return top - _sweeping_lowpass(noise, np.full(noise.size, float(low)))


def _stereo(mono: np.ndarray, delay_ms: float = 3.0) -> np.ndarray:
    shift = int(SR * delay_ms / 1000)
    return np.stack([mono, np.roll(mono, shift)], axis=1)


def slam() -> np.ndarray:
    """Ağır bir dosyanın masaya çarpması: alçak thump + kağıt/ahşap şaklaması."""
    rng = np.random.default_rng(21)
    t = _time(0.8)
    freq = 46 + 70 * np.exp(-t / 0.05)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    thump = np.sin(phase) * np.exp(-t / 0.16) * np.minimum(t / 0.002, 1.0)
    slap = _bandpass(rng.standard_normal(t.size), 600, 4200) * np.exp(-t / 0.05)
    wood = np.sin(2 * np.pi * 180 * t) * np.exp(-t / 0.09) * 0.35
    rustle = _bandpass(rng.standard_normal(t.size), 2500, 9000) * np.exp(-t / 0.22) * 0.12
    return _normalize(_stereo(thump * 1.1 + slap * 0.9 + wood + rustle), fade_out=0.1)


def marker() -> np.ndarray:
    """Fosforlu kalemle üç hızlı çizgi: kağıda sürtünen gürültü + hafif gıcırtı."""
    rng = np.random.default_rng(22)
    t = _time(0.75)
    noise = _bandpass(rng.standard_normal(t.size), 1200, 6500)
    envelope = np.zeros(t.size)
    for start, length in ((0.02, 0.2), (0.25, 0.22), (0.5, 0.2)):        # üç kalem darbesi
        mask = (t >= start) & (t < start + length)
        local = (t[mask] - start) / length
        envelope[mask] = np.sin(np.pi * local) ** 0.6 * (0.85 + 0.15 * np.sin(2 * np.pi * 38 * t[mask]))
    squeak = np.sin(2 * np.pi * (2300 + 500 * np.sin(2 * np.pi * 5 * t)) * t) * envelope * 0.06
    return _normalize(_stereo(noise * envelope + squeak, 2.0), fade_out=0.06)


def typewriter() -> np.ndarray:
    """Daktilo tuşları: kısa keskin tıklamalar ve sonunda satır sonu zili."""
    rng = np.random.default_rng(23)
    t = _time(0.9)
    mono = np.zeros(t.size)
    for at in (0.0, 0.11, 0.19, 0.33, 0.41, 0.58):
        i = int(SR * at)
        n = int(SR * 0.05)
        seg = np.arange(n) / SR
        click = _bandpass(rng.standard_normal(n), 1500, 7000) * np.exp(-seg / 0.006)
        body = np.sin(2 * np.pi * rng.uniform(900, 1300) * seg) * np.exp(-seg / 0.012) * 0.5
        mono[i:i + n] += (click + body) * rng.uniform(0.7, 1.0)
    bell = np.sin(2 * np.pi * 2637 * t) * np.exp(-(t - 0.66).clip(0) / 0.12) * (t >= 0.66) * 0.22
    return _normalize(_stereo(mono + bell, 1.5), fade_out=0.08)


def shutter() -> np.ndarray:
    """Fotoğraf makinesi: iki mekanik tık (perde açılır, kapanır)."""
    rng = np.random.default_rng(24)
    t = _time(0.4)
    mono = np.zeros(t.size)
    for at, strength in ((0.0, 1.0), (0.075, 0.75)):
        i = int(SR * at)
        n = int(SR * 0.06)
        seg = np.arange(n) / SR
        burst = _bandpass(rng.standard_normal(n), 1800, 9000) * np.exp(-seg / 0.008)
        ring = np.sin(2 * np.pi * 2100 * seg) * np.exp(-seg / 0.02) * 0.4
        mono[i:i + n] += (burst + ring) * strength
    return _normalize(_stereo(mono, 1.0), fade_out=0.06)


def heartbeat() -> np.ndarray:
    """İki 'lub-dub' kalp atışı: alçak, yumuşak vuruşlar."""
    t = _time(1.7)
    mono = np.zeros(t.size)
    for at, strength in ((0.0, 1.0), (0.26, 0.7), (0.9, 0.85), (1.16, 0.6)):
        i = int(SR * at)
        n = int(SR * 0.4)
        seg = np.arange(n) / SR
        freq = 48 + 40 * np.exp(-seg / 0.04)
        phase = 2 * np.pi * np.cumsum(freq) / SR
        mono[i:i + n] += np.sin(phase) * np.exp(-seg / 0.09) * np.minimum(seg / 0.004, 1.0) * strength
    return _normalize(np.stack([mono, mono], axis=1), fade_out=0.1)


def deep_boom() -> np.ndarray:
    """Uzun, derin sinematik patlama: alçalan bas + yumuşak gürültü kuyruğu."""
    rng = np.random.default_rng(25)
    t = _time(2.6)
    freq = 28 + 75 * np.exp(-t / 0.3)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    tone = np.sin(phase) * np.exp(-t / 0.9) * np.minimum(t / 0.006, 1.0)
    tone += 0.3 * np.sin(2 * phase) * np.exp(-t / 0.35)
    rumble = _sweeping_lowpass(rng.standard_normal(t.size), np.full(t.size, 220.0)) * np.exp(-t / 0.7) * 0.9
    blast = _sweeping_lowpass(rng.standard_normal(t.size), np.full(t.size, 1500.0)) * np.exp(-t / 0.05) * 0.7
    return _normalize(np.stack([tone + rumble + blast, tone + np.roll(rumble, 180) + blast], axis=1), fade_out=0.3)


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
    effects = (
        ("whoosh", whoosh), ("sub_drop", sub_drop), ("glitch", glitch), ("slam", slam), ("marker", marker),
        ("typewriter", typewriter), ("shutter", shutter), ("heartbeat", heartbeat), ("deep_boom", deep_boom),
    )
    for name, make in effects:
        dest = SFX_DIR / f"{name}.mp3"
        write_mp3(make(), dest)
        print(f"{dest.relative_to(ROOT_DIR)}  ({dest.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
