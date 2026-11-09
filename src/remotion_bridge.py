"""Python -> Remotion köprüsü: sahne görsellerini, ses/altyazı verisini ve kanal temasını render_props.json olarak yazar,
`remotion render` ile sessiz bir MP4 üretir. Ses (mastering, müzik, efektler) video_editor.py içinde FFmpeg ile eklenir."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.audio_subtitle import AudioResult, _ass_escape, _is_emphasis, _karaoke_slices, _spread, probe_duration
from src.visuals import (
    BADGES, DEFAULT_SCENE_TYPE, DEFAULT_SFX_FOR_TYPE, EXTRA_LEVEL, FIRST_SCENE_SFX_FACTOR, NO_BADGE, SCENE_TYPES,
    SFX_DIR, SFX_FILES, SFX_CUES, SFX_LEVEL, impact_seconds,
)

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
        if not highlight:
            # edge-tts bazen bir blok için kelime başına zaman vermez (ör. rakamlar): kelimeler blok süresine harf
            # sayısıyla orantılı dağıtılır; böylece anahtar ifade ve altı çizilen kelimeler yine yaklaşık anda vurur
            slices = [(s, e) for s, e, _ in _spread(tokens, start, end)]
        words = [
            {"t": token, "s": round(s, 3), "e": round(e, 3), "emph": _is_emphasis(token)}
            for token, (s, e) in zip(tokens, slices)
        ]
        closing = audio.highlight_from is not None and start >= audio.highlight_from - 0.05
        cues.append({"start": round(start, 3), "end": round(end, 3), "words": words, "highlight": highlight, "closing": closing})
    return cues


def _scene_words(cues: list[dict[str, Any]], start_s: float, end_s: float) -> list[dict[str, Any]]:
    """Sahnenin süresi içinde okunan kelimeler (kanıt belgesi ve anahtar ifade eşleşmesi için)."""
    out: list[dict[str, Any]] = []
    for cue in cues:
        for word in cue["words"]:
            if start_s - 0.02 <= word["s"] < end_s - 0.02:
                out.append({"t": word["t"], "s": word["s"], "e": word["e"]})
    return out


def _bare(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def _find_phrase_times(words: list[dict[str, Any]], phrase: str) -> tuple[float, float] | None:
    """Anahtar ifadenin söylendiği (başlangıç, bitiş) saniyesi; bulunamazsa None."""
    target = [_bare(w) for w in phrase.split() if _bare(w)]
    bare = [_bare(w["t"]) for w in words]
    n = len(target)
    if not n:
        return None
    for i in range(len(bare) - n + 1):
        if bare[i:i + n] == target:
            return words[i]["s"], words[i + n - 1]["e"]
    return None


def _scene_specs(
    audio: AudioResult, names: list[str], cues: list[dict[str, Any]], min_frames: int = FPS,
) -> list[dict[str, Any]]:
    """Sahne başlangıçları ses sürelerinin kümülatif toplamından hesaplanır; yuvarlama kayması birikmez.
    Her sahneye görsel yönetmen alanları (tür, anahtar ifade zamanı, rozet) ve animasyonun çarpma karesi eklenir."""
    specs: list[dict[str, Any]] = []
    cumulative = 0.0
    start = 0
    for index, (name, duration) in enumerate(zip(names, audio.durations)):
        cumulative += duration
        end = max(int(round(cumulative * FPS)), start + min_frames)
        meta = audio.scenes[index] if index < len(audio.scenes) else {}
        scene_type = meta.get("scene_type") if meta.get("scene_type") in SCENE_TYPES else DEFAULT_SCENE_TYPE
        frames = end - start
        impact = int(round(impact_seconds(scene_type, index) * FPS))
        impact = max(6, min(impact, max(frames - 12, 6)))           # kısa sahnede de sahne bitmeden çarpsın
        start_s, end_s = start / FPS, end / FPS
        words = _scene_words(cues, start_s, end_s)
        key_phrase = str(meta.get("key_phrase") or "").strip()
        times = _find_phrase_times(words, key_phrase) if key_phrase else None
        badge = meta.get("floating_badge") if meta.get("floating_badge") in BADGES else NO_BADGE
        specs.append({
            "image": name, "start": start, "frames": frames,
            "type": scene_type, "impact": impact, "badge": badge,
            "keyPhrase": key_phrase,
            "keyStart": round(times[0], 3) if times else None,
            "keyEnd": round(times[1], 3) if times else None,
            "words": words if scene_type == "evidence_document" else [],
            "sfx": meta.get("sfx_cue") if meta.get("sfx_cue") in SFX_CUES else DEFAULT_SFX_FOR_TYPE[scene_type],
        })
        start = end
    return specs


def plan_sfx(scenes: list[dict[str, Any]], total_seconds: float) -> list[tuple[Path, float, float]]:
    """Her sahnenin sfx_cue sesini animasyonun ekrana çarptığı ana (sahne başı + impact karesi) yerleştirir.
    Kanıt belgelerinde ek olarak daktilo tıkları ve kalemin çizmeye başladığı an için fosforlu kalem sesi eklenir."""
    plan: list[tuple[Path, float, float]] = []

    def add(name: str, at: float, level: float) -> None:
        path = SFX_DIR / name
        if path.is_file() and 0 <= at < total_seconds - 0.4:
            plan.append((path, at, level))

    for index, scene in enumerate(scenes):
        factor = FIRST_SCENE_SFX_FACTOR if index == 0 else 1.0
        at = scene["start"] / FPS + scene["impact"] / FPS
        cue = scene["sfx"]
        add(SFX_FILES[cue], at, SFX_LEVEL[cue] * factor)
        if scene["type"] == "evidence_document":
            add("typewriter.mp3", at + 0.3, EXTRA_LEVEL["typewriter"] * factor)
            if scene["words"] and cue != "marker_scratch":
                add("marker.mp3", scene["words"][0]["s"] + 0.1, EXTRA_LEVEL["marker"] * factor)
    return plan


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
    cues = _build_cues(audio)
    scenes = _scene_specs(audio, names, cues)
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
        "cues": cues,
        "labels": [
            {"start": round(s, 3), "end": round(e, 3), "text": _ass_escape(text)} for s, e, text in audio.labels
        ],
        "ctaText": CTA_TEXT,
        "seed": random.randint(1, 10_000),
    }


def render_silent_video(
    images: list[Path], audio: AudioResult, style: dict[str, Any], effects: list[str], work_dir: Path,
) -> tuple[Path, int, list[tuple[Path, float, float]]]:
    """Remotion ile sessiz MP4 üretir; (dosya yolu, kare sayısı, ses efekti planı) döndürür. Hata: RemotionError."""
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
    return output, props["totalFrames"], plan_sfx(props["scenes"], props["totalFrames"] / FPS)
