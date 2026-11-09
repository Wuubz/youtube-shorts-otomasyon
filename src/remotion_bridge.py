"""Python -> Remotion köprüsü: sahne görsellerini, ses/altyazı verisini ve kanal temasını render_props.json olarak yazar,
`remotion render` ile sessiz bir MP4 üretir. Ses (mastering, müzik, efektler) video_editor.py içinde FFmpeg ile eklenir."""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.audio_subtitle import AudioResult, _ass_escape, _is_emphasis, _karaoke_slices, probe_duration

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
ENTRY = "src/remotion/index.ts"
COMPOSITION = "ShortsComposition"
CLI_JS = ROOT_DIR / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
FPS = 30
SAFE_VIDEO_SECONDS = 57.9
RENDER_TIMEOUT_SECONDS = int(os.environ.get("REMOTION_TIMEOUT", "900"))   # takılan render sonsuza dek beklemesin
CTA_TEXT = "SUBSCRIBE"


class RemotionError(RuntimeError):
    """Remotion kurulu değil, render başarısız oldu ya da çıktı geçersiz."""


def remotion_available() -> bool:
    """Node.js ve npm bağımlılıkları (npm ci) kurulu mu?"""
    return bool(shutil.which("node")) and CLI_JS.is_file()


def _concurrency() -> int:
    env = os.environ.get("REMOTION_CONCURRENCY", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return max(1, min(os.cpu_count() or 2, 4))


def _bake_look(image: Path, dest: Path, style: dict[str, Any]) -> None:
    """Kanalın renk tonunu ve vinyetini görsele bir kez işler (FFmpeg, ~0.3 sn).

    Aynı efekti Remotion'da CSS filter ile her karede uygulamak render süresini ~3 kat uzatıyordu; durağan görselde
    bir kez yapmak görüntüyü aynı bırakır. Hata olursa görsel olduğu gibi kopyalanır (video yine üretilir)."""
    chain = ["scale=1080:1920:force_original_aspect_ratio=increase", "crop=1080:1920", "setsar=1", "format=gbrp"]
    if style.get("grade"):
        chain.append(str(style["grade"]))
    vignette = float(style.get("vignette", 0.0))
    if vignette > 0:
        chain.append(f"vignette=angle=PI*{0.07 + 0.26 * min(vignette, 1.0):.3f}")
    chain.append("format=yuvj420p")
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(image), "-vf", ",".join(chain), "-frames:v", "1", "-q:v", "2", str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dest.is_file():
        log.warning("Görsel rengi işlenemedi, özgün görsel kullanılıyor: %s", result.stderr.strip()[-300:])
        shutil.copyfile(image, dest)


def _build_cues(audio: AudioResult) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for cue in audio.cues:
        start, end, text, _ = cue
        tokens = _ass_escape(text).split()
        slices = _karaoke_slices(cue, len(tokens))
        highlight = len(slices) == len(tokens)
        if not highlight:                         # zamanlar uyuşmuyorsa kelimeler bloğun tamamı boyunca aynı kalır
            slices = [(start, end)] * len(tokens)
        words = [
            {"t": token, "s": round(s, 3), "e": round(e, 3), "emph": _is_emphasis(token)}
            for token, (s, e) in zip(tokens, slices)
        ]
        closing = audio.highlight_from is not None and start >= audio.highlight_from - 0.05
        cues.append({"start": round(start, 3), "end": round(end, 3), "words": words, "highlight": highlight, "closing": closing})
    return cues


def _scene_specs(audio: AudioResult, names: list[str], min_frames: int = FPS) -> list[dict[str, Any]]:
    """Sahne başlangıçları ses sürelerinin kümülatif toplamından hesaplanır; yuvarlama kayması birikmez."""
    specs: list[dict[str, Any]] = []
    cumulative = 0.0
    start = 0
    for index, (name, duration) in enumerate(zip(names, audio.durations)):
        cumulative += duration
        end = max(int(round(cumulative * FPS)), start + min_frames)
        specs.append({"image": name, "start": start, "frames": end - start})
        start = end
    return specs


def _pick_transitions(style: dict[str, Any], count: int) -> list[str]:
    pool = list(style.get("transitions") or ["dissolve"])
    picks: list[str] = []
    for _ in range(count):
        options = [t for t in pool if not picks or t != picks[-1]] or pool
        picks.append(random.choice(options))
    return picks


def build_props(
    audio: AudioResult, style: dict[str, Any], names: list[str], effects: list[str],
) -> dict[str, Any]:
    scenes = _scene_specs(audio, names)
    total_frames = min(scenes[-1]["start"] + scenes[-1]["frames"], int(SAFE_VIDEO_SECONDS * FPS))
    return {
        "channel_theme": style["name"],
        "fps": FPS,
        "totalFrames": total_frames,
        "scenes": scenes,
        "effects": effects,
        "transitions": _pick_transitions(style, max(len(scenes) - 1, 0)),
        "xfadeFrames": int(style["xfade_frames"]),
        "zoomMax": float(style["zoom_max"]),
        "hookZoom": float(style["hook_zoom"]),
        "cues": _build_cues(audio),
        "labels": [
            {"start": round(s, 3), "end": round(e, 3), "text": _ass_escape(text)} for s, e, text in audio.labels
        ],
        "ctaText": CTA_TEXT,
        "seed": random.randint(1, 10_000),
    }


def render_silent_video(
    images: list[Path], audio: AudioResult, style: dict[str, Any], effects: list[str], work_dir: Path,
) -> tuple[Path, int]:
    """Remotion ile sessiz MP4 üretir; (dosya yolu, kare sayısı) döndürür. Hata durumunda RemotionError fırlatır."""
    if not remotion_available():
        raise RemotionError("Node.js veya npm bağımlılıkları (npm ci) bulunamadı")

    work_dir.mkdir(parents=True, exist_ok=True)
    public_dir = work_dir / "public"
    public_dir.mkdir(exist_ok=True)
    names: list[str] = []
    for index, image in enumerate(images):
        name = f"scene_{index}.jpg"
        _bake_look(image, public_dir / name, style)
        names.append(name)

    props = build_props(audio, style, names, effects)
    props_path = work_dir / "render_props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    output = work_dir / "remotion_silent.mp4"

    cmd = [
        "node", str(CLI_JS), "render", ENTRY, COMPOSITION, str(output),
        f"--props={props_path}", f"--public-dir={public_dir}",
        f"--concurrency={_concurrency()}", "--muted", "--codec=h264", "--pixel-format=yuv420p",
        "--crf=23", "--x264-preset=veryfast", "--log=warn", "--overwrite",
    ]
    gl = os.environ.get("REMOTION_GL", "").strip()
    if gl:                                      # GPU'suz sunucuda sorun çıkarsa REMOTION_GL=swangle gibi denenebilir
        cmd.append(f"--gl={gl}")
    log.info("Remotion render başlıyor (%d kare, tema %s, %d paralel)", props["totalFrames"], style["name"], _concurrency())
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemotionError(f"Remotion render {RENDER_TIMEOUT_SECONDS} sn içinde bitmedi") from exc
    except OSError as exc:
        raise RemotionError(f"Remotion çalıştırılamadı: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-1500:]
        raise RemotionError(f"Remotion render hatası (kod {result.returncode}):\n{tail}")
    if not output.is_file() or output.stat().st_size < 50_000:
        raise RemotionError("Remotion çıktısı üretilmedi ya da çok küçük")

    seconds = probe_duration(output)
    expected = props["totalFrames"] / FPS
    if abs(seconds - expected) > 1.0:
        raise RemotionError(f"Remotion çıktısının süresi beklenenden farklı ({seconds:.1f} sn, beklenen {expected:.1f} sn)")
    size_mb = output.stat().st_size / 1_000_000
    log.info("Remotion render tamam: %.1f sn, %.1f MB", seconds, size_mb)
    return output, props["totalFrames"]
