"""Telifsiz (tamamen sentezlenmiş) sinematik ambiyans müziklerini üretir.

Hiçbir örnek (sample) veya harici kayıt kullanılmaz: tüm sesler numpy ile sıfırdan sentezlenir,
FFmpeg ile mp3'e çevrilir. Bu yüzden Content ID / telif riski yoktur.

Her kanal için 3 adet + 1 adet default olmak üzere 19 adet 65 saniyelik .mp3 üretir:
    assets/music/<channel_id>/*.mp3  ve  assets/music/default/default_ambient.mp3

Kullanım:
    python scripts/generate_music.py                 # hepsini üretir (aynı tohum = aynı çıktı)
    python scripts/generate_music.py --only ch5_midnight_horror
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT_DIR / "assets" / "music"

SR = 44100
DURATION = 65.0
N = int(SR * DURATION)
T = np.arange(N) / SR
TWO_PI = 2 * np.pi
# Müzik kurguda 0.10 ile çarpılıp seslendirmenin altına karıştırıldığı için yüksek seviyede masterlanır
TARGET_RMS = 0.20

Layer = tuple  # (sol, sağ, ağırlık[, normalize])
Track = tuple  # (sol, sağ, (reverb_sn, reverb_wet))


# --------------------------------------------------------------------------- #
# Sentez yapı taşları
# --------------------------------------------------------------------------- #
def mtof(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12)


def rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def unit_lfo(ts: np.ndarray, rate: float, phase: float = 0.0) -> np.ndarray:
    """0..1 arasında salınan yavaş LFO."""
    return 0.5 + 0.5 * np.sin(TWO_PI * rate * ts + phase)


def voice(ts, freq, harmonics, rolloff, phase=0.0, vib_depth=0.0, vib_rate=0.0) -> np.ndarray:
    """Toplamsal sentez: harmonikler 1/h^rolloff ile azalır (yüksek rolloff = yumuşak/sinüs benzeri)."""
    out = np.zeros_like(ts)
    limit = SR / 2 * 0.9
    for h in range(1, harmonics + 1):
        if freq * h >= limit:
            break
        arg = TWO_PI * freq * h * ts + phase * h
        if vib_depth:
            arg = arg + vib_depth * h * np.sin(TWO_PI * vib_rate * ts)
        out += np.sin(arg) / h ** rolloff
    return out


def pad(ts, midis, harmonics=6, rolloff=1.5, detune=7.0, lfo_rate=0.07, lfo_depth=0.35, seed=0):
    """Hafif detune'lu 3 ses × akor notaları; her nota kendi yavaş LFO'suyla salınır (stereo)."""
    r = rng(seed)
    left = np.zeros_like(ts)
    right = np.zeros_like(ts)
    for midi in midis:
        base = mtof(midi)
        note_l = np.zeros_like(ts)
        note_r = np.zeros_like(ts)
        for cents, pan in ((-detune, 0.75), (0.0, 0.5), (detune, 0.25)):
            v = voice(ts, base * 2 ** (cents / 1200), harmonics, rolloff, r.uniform(0, TWO_PI))
            note_l += v * pan
            note_r += v * (1 - pan)
        env = 1 - lfo_depth * unit_lfo(ts, lfo_rate * r.uniform(0.8, 1.25), r.uniform(0, TWO_PI))
        left += note_l * env
        right += note_r * env
    return left, right


def progression(chords, seed=0, **pad_kwargs):
    """Akorlar arasında yumuşak çapraz geçişli (equal-power) pad dizisi."""
    n = len(chords)
    seg = DURATION / n
    left = np.zeros(N)
    right = np.zeros(N)
    for i, chord in enumerate(chords):
        center = (i + 0.5) * seg
        i0, i1 = max(0, int((center - seg) * SR)), min(N, int((center + seg) * SR))
        ts = T[i0:i1]
        w = np.sin(0.5 * np.pi * np.clip(1 - np.abs(ts - center) / seg, 0, 1)) ** 2
        pl, pr = pad(ts, chord, seed=seed + i, **pad_kwargs)
        left[i0:i1] += pl * w
        right[i0:i1] += pr * w
    return left, right


def drone(midi, harmonics=3, rolloff=1.4, depth=0.3, lfo_rate=0.04, beat_cents=4.0, seed=0):
    """Sub-bas uğultusu: iki hafif detune'lu osilatör yavaş vuru (beating) yapar."""
    f = mtof(midi)
    r = rng(seed)
    a = voice(T, f, harmonics, rolloff)
    b = voice(T, f * 2 ** (beat_cents / 1200), harmonics, rolloff, 1.3)
    sig = (a + b) * 0.5 * (1 - depth * unit_lfo(T, lfo_rate, r.uniform(0, TWO_PI)))
    return sig, sig


def colored_noise(lo, hi, seed) -> np.ndarray:
    """Bant sınırlı gürültü (FFT maskesi ile highpass + lowpass), birim standart sapma."""
    x = rng(seed).standard_normal(N)
    spec = np.fft.rfft(x)
    fr = np.fft.rfftfreq(N, 1 / SR)
    mask = 1.0 / (1.0 + (fr / hi) ** 4)
    if lo > 0:
        mask = mask / (1.0 + (lo / np.maximum(fr, 1e-6)) ** 4)
    y = np.fft.irfft(spec * mask, N)
    return y / (np.std(y) + 1e-12)


def swelling_noise(lo, hi, rate, seed, floor=0.15):
    """Stereo, yavaşça kabarıp sönen bant gürültüsü (rüzgar / nefes)."""
    r = rng(seed)
    chans = []
    for k in range(2):
        env = floor + (1 - floor) * unit_lfo(T, rate * r.uniform(0.8, 1.25), r.uniform(0, TWO_PI)) ** 2
        chans.append(colored_noise(lo, hi, seed * 7 + k) * env)
    return chans[0], chans[1]


def pings(midis, count, decay, level, seed, partials=((1, 1.0), (2, 0.25))):
    """Seyrek, sönümlü notalar (parıltı / çan / piyano benzeri); rastgele zaman ve panorama."""
    r = rng(seed)
    left = np.zeros(N)
    right = np.zeros(N)
    for _ in range(count):
        start = int(r.uniform(1.5, DURATION - 9) * SR)
        freq = mtof(float(r.choice(midis)))
        length = min(N - start, int(SR * decay * 5))
        tt = np.arange(length) / SR
        env = np.exp(-tt / decay) * (1 - np.exp(-tt * 90))
        v = sum(a * np.sin(TWO_PI * freq * p * tt) for p, a in partials) * env * level * r.uniform(0.5, 1.0)
        pan = r.uniform(0.2, 0.8)
        left[start:start + length] += v * pan
        right[start:start + length] += v * (1 - pan)
    return left, right


def thuds(count, freq, decay, level, seed):
    """Seyrek derin vuruşlar (gerilim)."""
    r = rng(seed)
    out = np.zeros(N)
    for _ in range(count):
        start = int(r.uniform(4, DURATION - 8) * SR)
        length = min(N - start, int(SR * decay * 5))
        tt = np.arange(length) / SR
        f = freq * (1 + 0.5 * np.exp(-tt * 12))
        out[start:start + length] += np.sin(TWO_PI * np.cumsum(f) / SR) * np.exp(-tt / decay) * level
    return out, out.copy()


def crackle(rate, seed):
    """Vinil çıtırtısı: seyrek, rastgele genlikli kısa tıklamalar."""
    r = rng(seed)
    n = int(rate * DURATION)
    x = np.zeros(N)
    x[r.integers(0, N, n)] = r.choice([-1.0, 1.0], n) * r.uniform(0, 1, n) ** 3
    return x, np.roll(x, 37)


def pulse_bass(midi, bpm, decay):
    """Kalp atışı benzeri 'lub-dub' nabız bası."""
    f = mtof(midi)
    beat = 60.0 / bpm
    out = np.zeros(N)
    for offset, gain in ((0.0, 1.0), (0.3 * beat, 0.6)):
        since = np.mod(T - offset, beat)
        env = np.exp(-since / beat * decay) * (1 - np.exp(-since * 60))
        out += gain * env
    sig = (np.sin(TWO_PI * f * T) + 0.35 * np.sin(TWO_PI * 2 * f * T)) * out
    return sig, sig


def eerie_tone(f0, sweep, rate, seed):
    """Yavaşça kayan, arada kaybolan tekinsiz yüksek ton."""
    r = rng(seed)
    phase = TWO_PI * (f0 * T - sweep / (TWO_PI * rate) * np.cos(TWO_PI * rate * T))
    env = unit_lfo(T, 0.045, r.uniform(0, TWO_PI)) ** 3
    return np.sin(phase) * env, np.sin(phase * 1.004) * env


def layer_gain(left, right, weight, normalize=True) -> float:
    if not normalize:
        return weight
    return weight / (np.sqrt(np.mean(left ** 2 + right ** 2) / 2) + 1e-12)


def mix(layers) -> tuple[np.ndarray, np.ndarray]:
    left = np.zeros(N)
    right = np.zeros(N)
    for item in layers:
        l, r, weight = item[0], item[1], item[2]
        gain = layer_gain(l, r, weight, item[3] if len(item) > 3 else True)
        left += l * gain
        right += r * gain
    return left, right


def reverb(left, right, seconds, wet, seed):
    """FFT evrişimi ile gürültü tabanlı, karanlık (yüksek frekansı sönümlü) oda yankısı."""
    r = rng(seed)
    n_ir = int(SR * seconds)
    size = 1 << int(np.ceil(np.log2(N + n_ir)))
    t_ir = np.arange(n_ir) / SR
    fr_ir = np.fft.rfftfreq(n_ir, 1 / SR)
    outs = []
    for sig in (left, right):
        ir = r.standard_normal(n_ir) * np.exp(-t_ir * 6.9 / seconds)
        ir = np.fft.irfft(np.fft.rfft(ir) / (1 + (fr_ir / 4500) ** 2), n_ir)
        ir /= np.sqrt(np.sum(ir ** 2))
        y = np.fft.irfft(np.fft.rfft(sig, size) * np.fft.rfft(ir, size), size)[:N]
        y *= (np.std(sig) + 1e-12) / (np.std(y) + 1e-12)
        outs.append((1 - wet) * sig + wet * y)
    return outs[0], outs[1]


def master(left, right) -> np.ndarray:
    x = np.stack([left, right], axis=1)
    x = x - x.mean(axis=0, keepdims=True)
    x *= TARGET_RMS / np.sqrt(np.mean(x ** 2))
    return (np.tanh(x / 0.85) * 0.85).astype("<f4")


# --------------------------------------------------------------------------- #
# Kanal başına müzik tarifleri (her biri 3 varyasyon: v = 0, 1, 2)
# --------------------------------------------------------------------------- #
def build_cosmic(v: int) -> Track:
    """Derin uzay: sub-bas uğultusu + LFO'lu kozmik pad akorları + seyrek parıltılar."""
    roots = [26, 31, 33]
    chords = [
        [[50, 57, 64, 69], [48, 55, 62, 67], [46, 53, 60, 67], [45, 52, 59, 64]],
        [[55, 62, 66, 71], [53, 60, 64, 69], [55, 59, 66, 74], [52, 59, 64, 71]],
        [[57, 64, 67, 72], [53, 60, 65, 69], [55, 62, 67, 71], [52, 59, 64, 69]],
    ]
    sparkle_notes = [[86, 90, 93, 98], [88, 91, 95, 100], [84, 88, 91, 96]]
    sub = drone(roots[v], harmonics=3, rolloff=1.5, depth=0.35, seed=10 + v)
    pd = progression(chords[v], seed=100 + v, harmonics=5, rolloff=2.0, detune=8, lfo_rate=0.06 + 0.01 * v, lfo_depth=0.4)
    wind = swelling_noise(60, 500, 0.03, 20 + v)
    spark = pings(sparkle_notes[v], 9 + 2 * v, decay=3.5, level=0.35, seed=30 + v)
    left, right = mix([(*sub, 1.0), (*pd, 0.9), (*wind, 0.25), (*spark, 1.0, False)])
    return left, right, (6.0, 0.45)


def build_history(v: int) -> Track:
    """Gizemli minör doku + rüzgar + vinil çıtırtısı/hışırtısı."""
    chords = [
        [[45, 52, 57, 60, 64], [41, 48, 53, 57, 60], [43, 50, 55, 59, 62], [45, 52, 57, 60, 64]],
        [[38, 45, 50, 53, 57], [34, 41, 46, 50, 53], [36, 43, 48, 51, 55], [38, 45, 50, 53, 57]],
        [[40, 47, 52, 55, 59], [41, 48, 53, 57, 60], [38, 45, 50, 54, 57], [40, 47, 52, 55, 59]],
    ]
    pd = progression(chords[v], seed=200 + v, harmonics=9, rolloff=1.2, detune=9, lfo_rate=0.05, lfo_depth=0.45)
    low = drone([33, 26, 28][v], harmonics=4, rolloff=1.3, depth=0.3, seed=40 + v)
    wind = swelling_noise(250, 1600, 0.045 + 0.01 * v, 50 + v, floor=0.1)
    hiss = (colored_noise(2500, 9000, 60 + v), colored_noise(2500, 9000, 70 + v))
    pops = crackle(5 + 2 * v, 80 + v)
    left, right = mix([(*pd, 1.0), (*low, 0.8), (*wind, 0.55), (*hiss, 0.05), (*pops, 0.22, False)])
    return left, right, (3.0, 0.30)


def build_psych(v: int) -> Track:
    """Derin hipnotik synth pad + hafif nabız bası."""
    chords = [
        [[45, 52, 57, 60], [45, 52, 55, 60]],
        [[43, 50, 55, 58], [41, 48, 53, 57]],
        [[48, 55, 60, 63], [46, 53, 58, 62]],
    ]
    pd = progression(chords[v], seed=300 + v, harmonics=14, rolloff=1.4, detune=13, lfo_rate=0.12 - 0.02 * v, lfo_depth=0.5)
    pulse = pulse_bass([33, 31, 36][v], bpm=[56, 60, 52][v], decay=5.0)
    sub = drone([21, 19, 24][v], harmonics=2, rolloff=1.5, depth=0.25, seed=90 + v)
    air = swelling_noise(800, 3500, 0.06, 95 + v, floor=0.2)
    left, right = mix([(*pd, 1.0), (*pulse, 0.85), (*sub, 0.5), (*air, 0.12)])
    return left, right, (4.0, 0.30)


def build_myth(v: int) -> Track:
    """Zengin harmonikli, beşli aralıklarla yükselen epik sinematik drone (crescendo)."""
    start = [36, 38, 33][v]
    chords = [[r, r + 7, r + 12, r + 19, r + 24] for r in (start, start + 7, start + 14, start + 21)]
    pd = progression(chords, seed=400 + v, harmonics=14, rolloff=1.0, detune=6, lfo_rate=0.05, lfo_depth=0.3)
    sub = drone(start - 12, harmonics=3, rolloff=1.3, depth=0.2, seed=110 + v)
    breath = swelling_noise(300, 2200, 0.025, 120 + v, floor=0.3)
    crescendo = 0.4 + 0.6 * (T / DURATION) ** 1.3
    left, right = mix([(pd[0] * crescendo, pd[1] * crescendo, 1.0), (*sub, 0.9), (breath[0] * crescendo, breath[1] * crescendo, 0.3)])
    return left, right, (5.0, 0.40)


def build_horror(v: int) -> Track:
    """Tekinsiz uyumsuz (minör ikili + tritone) frekanslar, karanlık uğultu, seyrek vuruşlar."""
    root = [40, 37, 43][v]
    cluster = [root, root + 1, root + 6, root + 13]
    pd = pad(T, cluster, harmonics=7, rolloff=1.0, detune=22, lfo_rate=0.05, lfo_depth=0.55, seed=500 + v)
    rumble = (colored_noise(20, 140, 130 + v), colored_noise(20, 140, 140 + v))
    rumble = (rumble[0] * (0.3 + unit_lfo(T, 0.03, 1.0 + v)), rumble[1] * (0.3 + unit_lfo(T, 0.035, 2.0 + v)))
    tone = eerie_tone([2300, 1900, 2700][v], sweep=140, rate=0.031 + 0.004 * v, seed=150 + v)
    hits = thuds(5 + v, 48.0, 0.9, 0.6, 160 + v)
    left, right = mix([(*pd, 1.0), (*rumble, 0.8), (*tone, 0.04), (*hits, 1.0, False)])
    return left, right, (4.0, 0.35)


def build_universal(v: int) -> Track:
    """Sıcak, huzurlu majör/minör yedili pad + yumuşak çan/piyano notaları."""
    base_chords = [
        [[48, 55, 59, 64], [45, 52, 55, 60], [41, 48, 52, 57], [43, 50, 55, 59]],   # C majör
        [[53, 60, 64, 69], [50, 57, 60, 65], [46, 53, 57, 62], [48, 55, 60, 64]],   # F majör
        [[45, 52, 55, 60], [41, 48, 52, 57], [48, 55, 59, 64], [40, 47, 52, 55]],   # A minör
    ]
    scales = [[72, 74, 76, 79, 81, 84, 88], [77, 79, 81, 84, 86, 89, 93], [69, 72, 74, 76, 79, 81, 84]]
    pd = progression(base_chords[v], seed=600 + v, harmonics=3, rolloff=2.4, detune=5, lfo_rate=0.06, lfo_depth=0.3)
    keys = pings(scales[v], 22 + 2 * v, decay=1.8, level=0.33, seed=170 + v, partials=((1, 1.0), (2, 0.35), (3, 0.12)))
    bells = pings([s + 12 for s in scales[v]], 8, decay=3.0, level=0.18, seed=180 + v, partials=((1, 1.0), (2.76, 0.3), (5.4, 0.12)))
    left, right = mix([(*pd, 1.0), (*keys, 1.0, False), (*bells, 1.0, False)])
    return left, right, (4.5, 0.38)


def build_default(_: int = 0) -> Track:
    """Genel amaçlı karanlık sinematik ambiyans."""
    chords = [[50, 57, 65, 69], [46, 53, 62, 65], [48, 55, 64, 67], [45, 52, 60, 64]]
    sub = drone(26, harmonics=3, rolloff=1.4, depth=0.35, seed=700)
    pd = progression(chords, seed=710, harmonics=7, rolloff=1.6, detune=9, lfo_rate=0.055, lfo_depth=0.4)
    wind = swelling_noise(150, 1200, 0.035, 720, floor=0.15)
    hits = thuds(4, 52.0, 1.0, 0.45, 730)
    left, right = mix([(*sub, 1.0), (*pd, 0.9), (*wind, 0.3), (*hits, 1.0, False)])
    return left, right, (5.0, 0.40)


# (dosya adı, üretici, ek FFmpeg efekti)
TRACKS: dict[str, list[tuple[str, Callable[[], Track], str]]] = {
    "ch1_cosmic_whatif": [
        (f"cosmic_{i + 1}.mp3", (lambda i=i: build_cosmic(i)), "aecho=0.8:0.6:1100|1900:0.25|0.18") for i in range(3)
    ],
    "ch2_dark_history": [
        (f"history_{i + 1}.mp3", (lambda i=i: build_history(i)), "") for i in range(3)
    ],
    "ch3_dark_psychology": [
        (f"psych_{i + 1}.mp3", (lambda i=i: build_psych(i)), "chorus=0.6:0.9:55:0.4:0.25:2") for i in range(3)
    ],
    "ch4_epic_mythology": [
        (f"myth_{i + 1}.mp3", (lambda i=i: build_myth(i)), "aecho=0.8:0.7:800|1500:0.2|0.15") for i in range(3)
    ],
    "ch5_midnight_horror": [
        (f"horror_{i + 1}.mp3", (lambda i=i: build_horror(i)), "") for i in range(3)
    ],
    "ch6_universal_stories": [
        (f"universal_{i + 1}.mp3", (lambda i=i: build_universal(i)), "aecho=0.8:0.6:600|1200:0.18|0.12") for i in range(3)
    ],
    "default": [("default_ambient.mp3", build_default, "aecho=0.8:0.6:900:0.2")],
}


# --------------------------------------------------------------------------- #
# Çıktı
# --------------------------------------------------------------------------- #
def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        return
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"FFmpeg bulunamadı ve static-ffmpeg yüklenemedi: {exc}")
    if not shutil.which("ffmpeg"):
        sys.exit("FFmpeg bulunamadı. FFmpeg kurun veya 'pip install static-ffmpeg' çalıştırın.")


TARGET_LUFS = -16.0


def _ffmpeg_pcm(pcm: np.ndarray, filters: str, tail: list[str], loglevel: str) -> subprocess.CompletedProcess:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", loglevel,
        "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
        "-af", filters, "-t", f"{DURATION:.0f}", *tail,
    ]
    return subprocess.run(cmd, input=pcm.tobytes(), capture_output=True)


def encode_mp3(pcm: np.ndarray, out_path: Path, fx: str) -> None:
    """Ham stereo float32 PCM'i FFmpeg filtreleriyle (echo/highpass/lowpass/fade/limiter) mp3'e çevirir.

    Tüm parçalar -16 LUFS'a eşitlenir (iki geçiş): kanal değişse de müziğin duyulurluğu aynı kalır.
    """
    head = ([fx] if fx else []) + ["highpass=f=22", "lowpass=f=14000"]
    fades = ["afade=t=in:st=0:d=3", f"afade=t=out:st={DURATION - 6:.0f}:d=6"]

    probe = _ffmpeg_pcm(pcm, ",".join(head + fades + ["ebur128"]), ["-f", "null", "-"], "info")
    loudness = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", probe.stderr.decode(errors="replace"))
    gain_db = TARGET_LUFS - float(loudness[-1]) if loudness else 0.0

    chain = head + [f"volume={gain_db:.2f}dB"] + fades + ["alimiter=limit=0.9"]
    result = _ffmpeg_pcm(pcm, ",".join(chain), ["-c:a", "libmp3lame", "-b:a", "160k", str(out_path)], "error")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3 kodlama hatası: {result.stderr.decode(errors='replace')[-600:]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Telifsiz ambiyans müzikleri üretir.")
    parser.add_argument("--only", choices=sorted(TRACKS), help="Yalnızca bu klasörü üret")
    args = parser.parse_args()
    ensure_ffmpeg()

    produced = 0
    for folder, tracks in TRACKS.items():
        if args.only and folder != args.only:
            continue
        out_dir = MUSIC_DIR / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        for index, (filename, builder, fx) in enumerate(tracks):
            left, right, (rev_seconds, rev_wet) = builder()
            left, right = reverb(left, right, rev_seconds, rev_wet, seed=900 + index)
            out_path = out_dir / filename
            encode_mp3(master(left, right), out_path, fx)
            produced += 1
            print(f"[{produced:2d}] {out_path.relative_to(ROOT_DIR)}  ({out_path.stat().st_size / 1024:.0f} KB)", flush=True)
    print(f"\nTamam: {produced} müzik dosyası üretildi ({DURATION:.0f} sn, sözsüz, tamamen sentezlenmiş).")


if __name__ == "__main__":
    main()
