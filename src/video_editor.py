"""FFmpeg ile dinamik kamera hareketli, geçişli, altyazılı ve müzikli Shorts kurgusu."""
from __future__ import annotations

import logging
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.audio_subtitle import AudioResult, probe_duration

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT_DIR / "assets" / "music"

FPS = 30
WIDTH, HEIGHT = 1080, 1920
XFADE_FRAMES = 9                      # 0.3 sn geçiş
XFADE_SECONDS = XFADE_FRAMES / FPS
MAX_VIDEO_SECONDS = 58.0
SAFE_VIDEO_SECONDS = 57.9
MUSIC_VOLUME = 0.10
# Anlatıcı sesine hafif "podcast stüdyo mikrofonu" mastering'i: düşük kesim, sıcaklık (160 Hz), netlik (3.2 kHz), yumuşak kompresyon
VOICE_MASTERING = (
    "highpass=f=75,"
    "equalizer=f=160:width_type=q:w=1.2:g=2.0,"
    "equalizer=f=3200:width_type=q:w=1.5:g=1.2,"
    "acompressor=threshold=-18dB:ratio=2:attack=20:release=200:makeup=1.5dB"
)
EFFECTS = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
TRANSITIONS = ["fade", "dissolve", "slideleft", "slideright", "wipeleft", "wiperight"]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg hatası:\n{result.stderr.strip()[-1200:]}")


def pick_music(channel_id: str) -> Path | None:
    """assets/music/{kanal}/ ya da assets/music/default/ içinden rastgele bir .mp3 seçer."""
    for folder in (MUSIC_DIR / channel_id, MUSIC_DIR / "default"):
        if folder.is_dir():
            tracks = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp3")
            if tracks:
                return random.choice(tracks)
    return None


def _effect_sequence(count: int) -> list[str]:
    """Her videoda kamera hareketlerini rastgele karıştırır; art arda aynı hareket gelmez."""
    sequence: list[str] = []
    while len(sequence) < count:
        bag = random.sample(EFFECTS, len(EFFECTS))
        if sequence and bag[0] == sequence[-1]:
            bag = bag[1:] + bag[:1]
        sequence.extend(bag)
    return sequence[:count]


def _motion_filter(effect: str, frames: int) -> str:
    """Sahneye özel zoompan filtresi; hareket tüm klip süresine yayılır (sarsıntısız)."""
    if effect == "zoom_in":
        step = 0.22 / frames
        z, x, y = f"min(zoom+{step:.6f},1.25)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif effect == "zoom_out":
        step = 0.24 / frames
        z, x, y = f"if(lte(zoom,1.0),1.25,max(1.001,zoom-{step:.6f}))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif effect == "pan_right":
        z, x, y = "1.2", f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
    else:  # pan_left
        z, x, y = "1.2", f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"
    return (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},setsar=1,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "scale=out_range=tv,format=yuv420p"  # JPEG full-range -> standart limited-range
    )


def _render_clips(
    images: list[Path], scene_frames: list[int], extend: int, work_dir: Path
) -> list[Path]:
    """Her görseli kendi ses süresi (+ geçiş payı) kadar zoompan'lı klibe çevirir."""
    effects = _effect_sequence(len(images))
    clips: list[Path] = []
    last = len(images) - 1
    for i, image in enumerate(images):
        frames = scene_frames[i] + (extend if i < last else 0)
        effect = effects[i]
        clip = work_dir / f"clip_{i}.mp4"
        _run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(image),
            "-vf", _motion_filter(effect, frames),
            "-frames:v", str(frames), "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
            str(clip),
        ])
        log.info("Klip %d hazır (%s, %d kare)", i, effect, frames)
        clips.append(clip)
    return clips


def _video_graph(n: int, scene_frames: list[int], use_xfade: bool, ass_name: str) -> list[str]:
    if n == 1:
        return [f"[0:v]ass={ass_name}[v]"]
    if not use_xfade:
        inputs = "".join(f"[{i}:v]" for i in range(n))
        return [f"{inputs}concat=n={n}:v=1:a=0[cv]", f"[cv]ass={ass_name}[v]"]
    parts: list[str] = []
    previous = "[0:v]"
    elapsed = 0
    for k in range(1, n):
        elapsed += scene_frames[k - 1]
        label = f"[x{k}]"
        transition = random.choice(TRANSITIONS)
        parts.append(
            f"{previous}[{k}:v]xfade=transition={transition}:duration={XFADE_SECONDS:.3f}"
            f":offset={elapsed / FPS:.3f}{label}"
        )
        previous = label
    parts.append(f"{previous}ass={ass_name}[v]")
    return parts


def _assemble(
    clips: list[Path], scene_frames: list[int], audio: AudioResult, music: Path | None,
    use_xfade: bool, total: float, output: Path,
) -> None:
    n = len(clips)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for clip in clips:
        cmd += ["-i", str(clip.resolve())]
    cmd += ["-i", str(audio.audio_path.resolve())]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]

    graph = _video_graph(n, scene_frames, use_xfade, audio.ass_path.name)
    if music:
        fade_start = max(total - 2.0, 0.0)
        graph += [
            f"[{n}:a]aresample=44100,{VOICE_MASTERING}[voice]",
            f"[{n + 1}:a]aresample=44100,volume={MUSIC_VOLUME},afade=t=in:d=1,"
            f"afade=t=out:st={fade_start:.2f}:d=2[bg]",
            "[voice][bg]amix=inputs=2:duration=first:dropout_transition=0,volume=2,alimiter=limit=0.95[a]",
        ]
    else:
        graph.append(f"[{n}:a]aresample=44100,{VOICE_MASTERING}[a]")

    cmd += [
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{total:.3f}", str(output.resolve()),
    ]
    # ass filtresine bağıl dosya adı verebilmek için altyazının bulunduğu klasörde çalıştırılır
    _run(cmd, cwd=audio.ass_path.parent)


def render_video(
    images: list[Path], audio: AudioResult, channel_id: str, work_dir: Path, output_dir: Path
) -> Path:
    """Görseller + ses + altyazı (+ müzik) ile son MP4 dosyasını üretir."""
    if len(images) != len(audio.durations):
        raise ValueError("Görsel sayısı ile sahne sesi sayısı eşleşmiyor")
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_frames = [max(int(round(d * FPS)), FPS) for d in audio.durations]
    total = min(audio.total_duration, SAFE_VIDEO_SECONDS)
    music = pick_music(channel_id)
    log.info("Müzik: %s", music.name if music else "yok (yalnızca seslendirme)")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"{channel_id}_{stamp}.mp4"

    try:
        clips = _render_clips(images, scene_frames, XFADE_FRAMES, work_dir)
        _assemble(clips, scene_frames, audio, music, True, total, output)
    except RuntimeError as exc:
        log.warning("Geçişli kurgu başarısız, düz birleştirmeye geçiliyor: %s", exc)
        clips = _render_clips(images, scene_frames, 0, work_dir)
        _assemble(clips, scene_frames, audio, music, False, total, output)

    duration = probe_duration(output)
    if duration > MAX_VIDEO_SECONDS:
        raise RuntimeError(f"Video süresi {duration:.1f} sn; 58 sn sınırını aşıyor")
    log.info("Video hazır: %s (%.1f sn)", output.name, duration)
    return output
