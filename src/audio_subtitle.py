"""edge-tts ile sahne sahne seslendirme, ffprobe ile süre ölçümü ve .ass altyazı üretimi."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import edge_tts

log = logging.getLogger(__name__)

TTS_ATTEMPTS = 3
SCENE_TAIL_PAD = 0.18          # her sahne sesinin sonuna eklenen doğal nefes boşluğu (sn); altyazı zamanlaması bu süreyi kapsar
MAX_TOTAL_AUDIO = 56.0         # video 58 sn'yi geçmesin diye üst sınır
MAX_SPEEDUP = 1.6
SILENT_SCENE_RANGE = (3.5, 5.4)  # voice_enabled=false iken sahne süresi (sn)
SUBTITLE_FONT = os.environ.get("SUBTITLE_FONT", "DejaVu Sans")
_BREAK_CHARS = (".", ",", "!", "?", ";", ":", "…")


@dataclass
class AudioResult:
    audio_path: Path            # birleştirilmiş seslendirme (.wav)
    ass_path: Path              # gömülecek altyazı
    durations: list[float]      # sahne başına süre (sn)
    total_duration: float


# --------------------------------------------------------------------------- #
# FFmpeg / ffprobe yardımcıları
# --------------------------------------------------------------------------- #
def probe_duration(path: Path) -> float:
    """ffprobe ile medya süresini saniye (float) olarak ölçer."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe süre ölçemedi ({path}): {result.stderr.strip()[-300:]}")
    return float(result.stdout.strip())


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg hatası: {result.stderr.strip()[-500:]}")


def _to_wav(src: Path, dest: Path) -> None:
    """mp3'ü wav'a çevirir ve sonuna kısa sessizlik ekler (wav süreleri kesin ölçülür)."""
    _ffmpeg(["-i", str(src), "-af", f"apad=pad_dur={SCENE_TAIL_PAD}", "-ar", "44100", "-ac", "2", str(dest)])


def _silence(dest: Path, seconds: float) -> None:
    _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds:.3f}", str(dest)])


def _speed_up(wav: Path, factor: float) -> None:
    tmp = wav.with_name(wav.stem + "_fast.wav")
    _ffmpeg(["-i", str(wav), "-filter:a", f"atempo={factor:.4f}", "-ar", "44100", "-ac", "2", str(tmp)])
    os.replace(tmp, wav)


def _concat_wavs(wavs: list[Path], dest: Path) -> None:
    list_file = dest.with_suffix(".txt")
    list_file.write_text("".join(f"file '{w.resolve().as_posix()}'\n" for w in wavs), encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(dest)])


# --------------------------------------------------------------------------- #
# edge-tts
# --------------------------------------------------------------------------- #
Event = tuple[float, float, str, str]  # (başlangıç sn, bitiş sn, metin, tür)


async def _synthesize(text: str, voice: str, rate: str, pitch: str, out_path: Path) -> list[Event]:
    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # eski edge-tts sürümlerinde boundary parametresi yok (varsayılan WordBoundary)
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

    events: list[Event] = []
    with open(out_path, "wb") as fh:
        async for chunk in communicate.stream():
            kind = chunk["type"]
            if kind == "audio":
                fh.write(chunk["data"])
            elif kind in ("WordBoundary", "SentenceBoundary"):
                start = chunk["offset"] / 10_000_000
                events.append((start, start + chunk["duration"] / 10_000_000, chunk["text"], kind))
    return events


async def _synthesize_all(
    texts: list[str], voice: str, rate: str, pitch: str, out_paths: list[Path]
) -> list[list[Event]]:
    all_events: list[list[Event]] = []
    for index, (text, path) in enumerate(zip(texts, out_paths)):
        last_error: Exception | None = None
        for attempt in range(1, TTS_ATTEMPTS + 1):
            try:
                events = await _synthesize(text, voice, rate, pitch, path)
                if path.stat().st_size < 1024:
                    raise RuntimeError("Üretilen ses dosyası boş")
                all_events.append(events)
                break
            except Exception as exc:
                last_error = exc
                log.warning("Sahne %d TTS denemesi %d/%d başarısız: %s", index, attempt, TTS_ATTEMPTS, exc)
                await asyncio.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Sahne {index} seslendirilemedi: {last_error}") from last_error
    return all_events


# --------------------------------------------------------------------------- #
# Altyazı
# --------------------------------------------------------------------------- #
def _spread(tokens: list[str], start: float, end: float) -> list[tuple[float, float, str]]:
    """Kelimeleri harf sayısına orantılı olarak [start, end] aralığına dağıtır."""
    if not tokens:
        return []
    weights = [max(len(t), 2) for t in tokens]
    total = sum(weights)
    cursor = start
    out: list[tuple[float, float, str]] = []
    for token, weight in zip(tokens, weights):
        step = (end - start) * weight / total
        out.append((cursor, cursor + step, token))
        cursor += step
    return out


def _word_entries(events: list[Event], text: str, duration: float) -> list[tuple[float, float, str]]:
    words = [e for e in events if e[3] == "WordBoundary"]
    tokens = text.split()
    if words:
        if len(words) == len(tokens):  # noktalama işaretli özgün kelimeleri koru
            return [(w[0], w[1], tok) for w, tok in zip(words, tokens)]
        return [(w[0], w[1], w[2]) for w in words]
    sentences = [e for e in events if e[3] == "SentenceBoundary"]
    if sentences:
        entries: list[tuple[float, float, str]] = []
        for start, end, sentence, _ in sentences:
            entries.extend(_spread(sentence.split(), start, end))
        return entries
    return _spread(tokens, 0.0, max(duration - SCENE_TAIL_PAD, 0.5))


def _chunk_entries(
    entries: list[tuple[float, float, str]], max_words: int = 3, max_chars: int = 20
) -> list[tuple[float, float, str]]:
    """Kelimeleri ekranda dinamik görünecek 1-3 kelimelik altyazı bloklarına böler."""
    groups: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    for entry in entries:
        current.append(entry)
        joined = " ".join(e[2] for e in current)
        if len(current) >= max_words or len(joined) >= max_chars or entry[2].endswith(_BREAK_CHARS):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [(g[0][0], g[-1][1], " ".join(e[2] for e in g)) for g in groups]


def _scene_cues(
    events: list[Event], text: str, duration: float, offset: float, speed: float
) -> list[tuple[float, float, str]]:
    entries = [(s / speed, e / speed, t) for s, e, t in _word_entries(events, text, duration)]
    chunks = _chunk_entries(entries)
    scene_end = offset + duration
    cues: list[tuple[float, float, str]] = []
    for i, (start, end, chunk_text) in enumerate(chunks):
        abs_start = min(offset + start, scene_end - 0.1)
        next_start = offset + chunks[i + 1][0] if i + 1 < len(chunks) else scene_end
        abs_end = max(offset + end, abs_start + 0.25)
        if next_start - abs_end < 0.35:  # küçük boşluklarda altyazı yanıp sönmesin
            abs_end = next_start
        cues.append((abs_start, min(abs_end, scene_end), chunk_text))
    return cues


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def _ass_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "").replace("\n", " ").upper()


def write_ass(cues: list[tuple[float, float, str]], dest: Path) -> None:
    """1080x1920 için orta-alt hizalı, kalın, siyah konturlu .ass dosyası yazar."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{SUBTITLE_FONT},84,&H0000FFFF,&H000000FF,&H00000000,&H96000000,"
        "-1,0,0,0,100,100,0,0,1,7,3,2,70,70,520,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for index, (start, end, text) in enumerate(cues):
        color = "" if index % 2 == 0 else r"\1c&H00FFFFFF&"  # sarı / beyaz dönüşümlü
        effect = r"{\fad(50,30)\fscx88\fscy88\t(0,120,\fscx100\fscy100)" + color + "}"
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{effect}{_ass_text(text)}\n"
        )
    dest.write_text(header + "".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Ana fonksiyon
# --------------------------------------------------------------------------- #
def _silent_duration(text: str) -> float:
    low, high = SILENT_SCENE_RANGE
    return min(max(len(text.split()) * 0.42, low), high)


def generate_audio_and_subtitles(
    scenes: list[dict[str, Any]], channel: dict[str, Any], work_dir: Path
) -> AudioResult:
    """Sahne seslerini üretir, ölçer, birleştirir ve kelime bazlı .ass altyazısını yazar."""
    work_dir.mkdir(parents=True, exist_ok=True)
    texts = [s["narration"] for s in scenes]
    voice_on = bool(channel.get("voice_enabled", True))

    if voice_on:
        raw_paths = [work_dir / f"scene_{i}.mp3" for i in range(len(texts))]
        all_events = asyncio.run(
            _synthesize_all(texts, channel["voice"], channel["rate"], channel["pitch"], raw_paths)
        )
    else:
        raw_paths = []
        all_events = [[] for _ in texts]

    wavs: list[Path] = []
    for i, text in enumerate(texts):
        wav = work_dir / f"scene_{i}.wav"
        if voice_on:
            _to_wav(raw_paths[i], wav)
        else:
            _silence(wav, _silent_duration(text))
        wavs.append(wav)

    durations = [probe_duration(w) for w in wavs]
    speed = 1.0
    if sum(durations) > MAX_TOTAL_AUDIO:
        speed = min(sum(durations) / MAX_TOTAL_AUDIO, MAX_SPEEDUP)
        log.warning("Toplam ses %.1f sn; %.2fx hızlandırılıyor", sum(durations), speed)
        for wav in wavs:
            _speed_up(wav, speed)
        durations = [probe_duration(w) for w in wavs]

    narration = work_dir / "narration.wav"
    _concat_wavs(wavs, narration)

    cues: list[tuple[float, float, str]] = []
    offset = 0.0
    for text, events, duration in zip(texts, all_events, durations):
        cues.extend(_scene_cues(events, text, duration, offset, speed))
        offset += duration

    ass_path = work_dir / "subtitles.ass"
    write_ass(cues, ass_path)
    total = sum(durations)
    log.info("Ses hazır: %d sahne, toplam %.1f sn, %d altyazı bloğu", len(durations), total, len(cues))
    return AudioResult(audio_path=narration, ass_path=ass_path, durations=durations, total_duration=total)
