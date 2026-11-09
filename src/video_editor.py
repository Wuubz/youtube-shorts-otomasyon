"""FFmpeg ile dinamik kamera hareketli, geçişli, altyazılı ve müzikli Shorts kurgusu."""
from __future__ import annotations

import logging
import os
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from src.audio_subtitle import AudioResult, probe_duration
from src.remotion_bridge import render_silent_video
from src.styles import resolve_style, usable_transitions

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT_DIR / "assets" / "music"
SFX_DIR = ROOT_DIR / "assets" / "sfx"

FPS = 30
WIDTH, HEIGHT = 1080, 1920
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
BAR_Y, BAR_HEIGHT = 96, 8            # ilerleme çubuğu (Shorts arayüzünün üstünde, altyazıdan uzak)


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


def _sfx_plan(durations: list[float], style: dict, total: float) -> list[tuple[Path, float, float]]:
    """Kanca ses efektleri: (dosya, başlangıç sn, ses düzeyi). Dosya yoksa o efekt sessizce atlanır."""
    # Açılıştaki whoosh kaldırıldı: kanca cümlesinin ilk kelimelerini bastırıp anlaşılmaz yapıyordu
    plan: list[tuple[Path, float, float]] = [
        (SFX_DIR / "sub_drop.mp3", 2.5, 0.40),      # kanca cümlesinin sonunda hafif bas darbesi
    ]
    if style.get("glitch") and durations and durations[0] > 1.5:   # korku: 2. sahneye geçerken dijital bozulma
        plan.append((SFX_DIR / "glitch.mp3", durations[0], 0.35))
    return [(path, at, level) for path, at, level in plan if path.is_file() and at < total - 0.5]


def _effect_sequence(effects: list[str], count: int) -> list[str]:
    """Kanalın kamera hareketlerini her videoda karıştırır; art arda aynı hareket gelmez."""
    sequence: list[str] = []
    while len(sequence) < count:
        bag = random.sample(effects, len(effects))
        if sequence and bag[0] == sequence[-1]:
            bag = bag[1:] + bag[:1]
        sequence.extend(bag)
    sequence = sequence[:count]
    sequence[0] = "zoom_in"            # açılış sahnesi: izleyiciyi yakalayan hızlı yaklaşma
    return sequence


def _motion_filter(effect: str, frames: int, style: dict, hook: bool) -> str:
    """Sahneye özel zoompan filtresi; yumuşak (ease-in-out) hareket tüm klip süresine yayılır."""
    ease = f"(1-cos(PI*on/{frames}))/2"
    zmax = style["hook_zoom"] if hook else style["zoom_max"]
    amp = zmax - 1.0
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if effect == "zoom_out":
        z, x, y = f"{zmax:.4f}-{amp:.4f}*{ease}", cx, cy
    elif effect == "push_right":
        z, x, y = f"1.16+0.06*{ease}", f"(iw-iw/zoom)*{ease}", cy
    elif effect == "push_left":
        z, x, y = f"1.16+0.06*{ease}", f"(iw-iw/zoom)*(1-{ease})", cy
    elif effect == "drift_up":
        z, x, y = f"1.16+0.06*{ease}", cx, f"(ih-ih/zoom)*(1-{ease})"
    else:  # zoom_in
        z, x, y = f"1+{amp:.4f}*{ease}", cx, cy
    shake = float(style.get("shake", 0.0))
    if shake > 0:  # el kamerası titremesi; kenar boşluğu için zoom en az 1.1
        z = f"max(1.1,{z})"
        x += f"+{shake:.1f}*sin(on*0.9)+{shake * 0.5:.1f}*sin(on*2.3)"
        y += f"+{shake:.1f}*cos(on*1.3)+{shake * 0.5:.1f}*cos(on*2.9)"
    return (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},setsar=1,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "scale=out_range=tv,format=yuv420p"  # JPEG full-range -> standart limited-range
    )


def _look_filter(style: dict) -> str:
    """Kanalın renk tonu, ışıma, vinyet, tanecik, titreme ve glitch efektleri (klip başına uygulanır)."""
    parts: list[str] = ["format=gbrp"]
    if style.get("grade"):
        parts.append(style["grade"])
    flicker = float(style.get("flicker", 0.0))
    if flicker > 0:
        parts.append(f"eq=brightness='{flicker:.3f}*sin(n*7.3)*sin(n*1.7)':eval=frame")
    chain = ",".join(parts)
    bloom = float(style.get("bloom", 0.0))
    if bloom > 0:
        chain += (
            f",split[o][b];[b]scale={WIDTH // 4}:{HEIGHT // 4},gblur=sigma=10,scale={WIDTH}:{HEIGHT}[bl];"
            f"[o][bl]blend=all_mode=screen:all_opacity={bloom:.2f}"
        )
    tail: list[str] = []
    vignette = float(style.get("vignette", 0.0))
    if vignette > 0:
        tail.append(f"vignette=angle=PI*{0.07 + 0.26 * min(vignette, 1.0):.3f}")
    if style.get("glitch"):
        tail.append("rgbashift=rh=14:bh=-14:gv=5:enable='lt(mod(n,73),3)'")
    grain = int(style.get("grain", 0))
    if grain > 0:
        tail.append(f"noise=alls={grain}:allf=t+u")
    tail.append("format=yuv420p")
    return chain + "," + ",".join(tail)


def _render_clips(
    images: list[Path], scene_frames: list[int], extend: int, work_dir: Path, style: dict
) -> list[Path]:
    """Her görseli kendi ses süresi (+ geçiş payı) kadar zoompan'lı ve stilli klibe çevirir."""
    effects = _effect_sequence(style["effects"], len(images))
    look = _look_filter(style)
    last = len(images) - 1

    def render_one(i: int) -> Path:
        frames = scene_frames[i] + (extend if i < last else 0)
        clip = work_dir / f"clip_{i}.mp4"
        _run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(images[i]),
            "-vf", _motion_filter(effects[i], frames, style, hook=(i == 0)) + "," + look,
            "-frames:v", str(frames), "-r", str(FPS),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
            str(clip),
        ])
        log.info("Klip %d hazır (%s, %d kare)", i, effects[i], frames)
        return clip

    # zoompan tek iş parçacıklıdır; klipler paralel üretilerek Actions dakikaları ve süre kısaltılır
    workers = max(1, min(os.cpu_count() or 1, 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(render_one, range(len(images))))


def _pick_transitions(style: dict, count: int) -> list[str]:
    pool = usable_transitions(style)
    picks: list[str] = []
    for _ in range(count):
        options = [t for t in pool if not picks or t != picks[-1]] or pool
        picks.append(random.choice(options))
    return picks


def _video_graph(
    n: int, scene_frames: list[int], use_xfade: bool, style: dict, bar_index: int | None, total: float,
    ass_name: str,
) -> list[str]:
    parts: list[str] = []
    if n == 1:
        previous = "[0:v]"
    elif not use_xfade:
        inputs = "".join(f"[{i}:v]" for i in range(n))
        parts.append(f"{inputs}concat=n={n}:v=1:a=0[cv]")
        previous = "[cv]"
    else:
        xfade_seconds = style["xfade_frames"] / FPS
        previous = "[0:v]"
        elapsed = 0
        for k, transition in enumerate(_pick_transitions(style, n - 1), start=1):
            elapsed += scene_frames[k - 1]
            label = f"[x{k}]"
            parts.append(
                f"{previous}[{k}:v]xfade=transition={transition}:duration={xfade_seconds:.3f}"
                f":offset={elapsed / FPS:.3f}{label}"
            )
            previous = label
    intro = style.get("intro", "none")
    if intro != "none":  # videonun ilk 0.3 sn'si renkten açılır
        parts.append(f"{previous}fade=t=in:st=0:d=0.3:color={'white' if intro == 'fadewhite' else 'black'}[iv]")
        previous = "[iv]"
    if bar_index is not None:  # üstte ilerleyen ince çubuk: izleyiciyi sonuna kadar tutan görsel ipucu
        parts.append(f"{previous}[{bar_index}:v]overlay=x='-w+w*t/{total:.3f}':y={BAR_Y}:shortest=1[bv]")
        previous = "[bv]"
    parts.append(f"{previous}ass={ass_name}[v]")
    return parts


def _audio_graph(
    voice_index: int, music_index: int | None, sfx: list[tuple[Path, float, float]], sfx_base: int, total: float,
) -> list[str]:
    """Seslendirme mastering'i + kısık müzik + ses efektlerini karıştırıp [a] etiketine bağlayan filtre parçaları."""
    graph: list[str] = []
    voice_chain = f"[{voice_index}:a]aresample=44100,{VOICE_MASTERING}"
    labels = ["[voice]"]
    if music_index is not None:
        fade_start = max(total - 2.0, 0.0)
        graph.append(
            f"[{music_index}:a]aresample=44100,volume={MUSIC_VOLUME},afade=t=in:d=1,"
            f"afade=t=out:st={fade_start:.2f}:d=2[bg]"
        )
        labels.append("[bg]")
    for k, (_, at, level) in enumerate(sfx):
        ms = round(at * 1000)
        # apad: efekt bitince girdi sessizlikle sürer, böylece amix kalan sesleri yükseltmez
        graph.append(f"[{sfx_base + k}:a]aresample=44100,volume={level},adelay={ms}|{ms},apad[s{k}]")
        labels.append(f"[s{k}]")
    if len(labels) == 1:
        graph.append(f"{voice_chain}[a]")
    else:
        graph.append(f"{voice_chain}[voice]")
        graph.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0,"
            f"volume={len(labels)},alimiter=limit=0.95[a]"
        )
    return graph


def _mux_audio(
    silent_video: Path, audio: AudioResult, music: Path | None, total: float, output: Path, style: dict,
    sfx: list[tuple[Path, float, float]] | None = None,
) -> None:
    """Remotion'un sessiz videosuna sesi ekler. Remotion JPEG kareler yüzünden tam aralıklı (yuvj420p) çıktı verdiği için
    görüntü standart limited-range yuv420p'ye çevrilerek yeniden kodlanır (eski FFmpeg yoluyla aynı biçim)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent_video.resolve()), "-i", str(audio.audio_path.resolve())]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
    if sfx is None:
        sfx = _sfx_plan(audio.durations, style, total)
    sfx_base = 2 + (1 if music else 0)
    for path, _, _ in sfx:
        cmd += ["-i", str(path.resolve())]
    graph = _audio_graph(1, 2 if music else None, sfx, sfx_base, total)
    cmd += [
        "-filter_complex", ";".join(graph),
        "-map", "0:v", "-map", "[a]",
        "-vf", "scale=out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{total:.3f}", str(output.resolve()),
    ]
    _run(cmd)


def _assemble(
    clips: list[Path], scene_frames: list[int], audio: AudioResult, music: Path | None,
    use_xfade: bool, total: float, output: Path, style: dict,
) -> None:
    n = len(clips)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for clip in clips:
        cmd += ["-i", str(clip.resolve())]
    cmd += ["-i", str(audio.audio_path.resolve())]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
    sfx = _sfx_plan(audio.durations, style, total)
    sfx_base = n + (2 if music else 1)
    for path, _, _ in sfx:
        cmd += ["-i", str(path.resolve())]
    bar_index: int | None = None
    if style.get("progress"):
        bar_index = sfx_base + len(sfx)
        cmd += ["-f", "lavfi", "-i", f"color=c={style['progress']}@0.9:s={WIDTH}x{BAR_HEIGHT}:r={FPS}:d={total + 1:.2f}"]

    graph = _video_graph(n, scene_frames, use_xfade, style, bar_index, total, audio.ass_path.name)
    graph += _audio_graph(n, n + 1 if music else None, sfx, sfx_base, total)

    cmd += [
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{total:.3f}", str(output.resolve()),
    ]
    # ass filtresine bağıl dosya adı verebilmek için altyazının bulunduğu klasörde çalıştırılır
    _run(cmd, cwd=audio.ass_path.parent)


def _plain(style: dict) -> dict:
    """Efektleri kapatılmış güvenli sürüm: stil bir filtre yüzünden çökerse video yine de üretilir."""
    plain = dict(style)
    plain.update(grade="", bloom=0.0, vignette=0.0, grain=0, flicker=0.0, shake=0.0, glitch=False)
    return plain


def _alert_fallback(channel_id: str, reason: str) -> None:
    """Remotion yedeğe düştüğünde (yalnızca GitHub Actions'ta) tek satırlık Telegram uyarısı gönderir."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    try:
        from src.notifier import send_message

        send_message(f"⚠️ {channel_id}: Remotion render başarısız, FFmpeg yedeğiyle üretildi.\n{reason.splitlines()[0][:200]}")
    except Exception as exc:  # uyarı hatası üretimi etkilemesin
        log.warning("Remotion yedek uyarısı gönderilemedi: %s", exc)


def _render_with_remotion(
    images: list[Path], audio: AudioResult, music: Path | None, total: float, output: Path, work_dir: Path, style: dict,
) -> None:
    effects = _effect_sequence(style["effects"], len(images))
    silent, _, sfx = render_silent_video(images, audio, style, effects, work_dir)
    _mux_audio(silent, audio, music, total, output, style, sfx)


def _render_with_ffmpeg(
    images: list[Path], audio: AudioResult, music: Path | None, total: float, output: Path, work_dir: Path, style: dict,
) -> None:
    scene_frames = [max(int(round(d * FPS)), FPS) for d in audio.durations]
    # 1) tam stil + geçişler  2) efektsiz + geçişler  3) efektsiz + düz birleştirme
    attempts = [(style, True), (_plain(style), True), (_plain(style), False)]
    for number, (attempt_style, use_xfade) in enumerate(attempts, start=1):
        try:
            extend = attempt_style["xfade_frames"] if use_xfade else 0
            clips = _render_clips(images, scene_frames, extend, work_dir, attempt_style)
            _assemble(clips, scene_frames, audio, music, use_xfade, total, output, attempt_style)
            return
        except RuntimeError as exc:
            if number == len(attempts):
                raise
            log.warning("Kurgu denemesi %d/%d başarısız, daha sade sürüme geçiliyor: %s", number, len(attempts), exc)


def render_video(
    images: list[Path], audio: AudioResult, channel_id: str, work_dir: Path, output_dir: Path,
    style: dict | None = None, renderer: str = "remotion",
) -> Path:
    """Görseller + ses + altyazı (+ müzik) ile son MP4 dosyasını üretir.

    renderer="remotion": hareketli grafikli kurgu (Node.js gerekir); herhangi bir hata olursa FFmpeg yoluna düşer.
    renderer="ffmpeg": eski zoompan/ASS kurgusu. SHORTS_RENDERER ortam değişkeni kanal ayarını ezer."""
    if len(images) != len(audio.durations):
        raise ValueError("Görsel sayısı ile sahne sesi sayısı eşleşmiyor")
    style = style or resolve_style({})
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    renderer = (os.environ.get("SHORTS_RENDERER") or renderer or "remotion").strip().lower()
    total = min(audio.total_duration, SAFE_VIDEO_SECONDS)
    music = pick_music(channel_id)
    log.info(
        "Stil: %s · kurgu: %s · müzik: %s", style["name"], renderer, music.name if music else "yok (yalnızca seslendirme)",
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"{channel_id}_{stamp}.mp4"

    done = False
    if renderer == "remotion":
        try:
            _render_with_remotion(images, audio, music, total, output, work_dir / "remotion", style)
            done = True
        except Exception as exc:   # Remotion yalnızca iyileştirmedir; video hiçbir koşulda kaçmasın
            log.warning("[%s] Remotion kurgusu başarısız, FFmpeg yedeğine geçiliyor: %s", channel_id, exc)
            _alert_fallback(channel_id, str(exc))
    if not done:
        _render_with_ffmpeg(images, audio, music, total, output, work_dir, style)

    duration = probe_duration(output)
    if duration > MAX_VIDEO_SECONDS:
        raise RuntimeError(f"Video süresi {duration:.1f} sn; 58 sn sınırını aşıyor")
    log.info("Video hazır: %s (%.1f sn)", output.name, duration)
    return output
