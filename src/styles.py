"""Kanal başına görsel kimlik: renk tonu, kamera hareketi, geçişler, altyazı biçimi ve efektler.

channels.json içindeki `style` alanı bir ön ayar adıdır (ör. "noir"); `style_overrides` ile tek tek alanlar ezilebilir.
Altyazı renkleri ASS biçimindedir: &HAABBGGRR (mavi-yeşil-kırmızı sırası).
"""
from __future__ import annotations

import copy
import logging
import re
import subprocess
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_STYLE = "cosmic"

# Her ön ayar tam bir sözlüktür; eksik anahtar bırakılmaz.
STYLES: dict[str, dict[str, Any]] = {
    # Uzay: akıcı, soğuk mavi-mor, yumuşak ışıma, uzun geçişler
    "cosmic": {
        "effects": ["zoom_in", "push_right", "zoom_out", "push_left", "drift_up"],
        "zoom_max": 1.28, "hook_zoom": 1.38, "shake": 0.0, "glitch": False, "flicker": 0.0,
        "grade": "eq=contrast=1.10:saturation=1.30:gamma=0.96,curves=b='0/0 0.5/0.56 1/1':r='0/0 0.5/0.47 1/1'",
        "vignette": 0.55, "grain": 4, "bloom": 0.22,
        "transitions": ["fadewhite", "dissolve", "circleopen", "smoothleft", "radial", "fadeblack"],
        "xfade_frames": 14, "intro": "fadewhite", "progress": "0x22D3EE",
        "caption": {
            "font": "DejaVu Sans", "size": 86, "bold": True, "uppercase": True,
            "colors": ["&H0000FFFF", "&H00FFFFFF"], "accent": "&H00FFD84A", "outline": "&H00501400",
            "outline_w": 7, "shadow": 3, "box": False, "margin_v": 520, "anim": "pop", "chunk": 3,
        },
    },
    # Karanlık tarih: sepya, yoğun tanecik, titreyen eski film hissi, yavaş hareket
    "chronicle": {
        "effects": ["push_left", "zoom_in", "drift_up", "zoom_out", "push_right"],
        "zoom_max": 1.22, "hook_zoom": 1.30, "shake": 0.0, "glitch": False, "flicker": 0.035,
        "grade": "eq=contrast=1.18:saturation=0.55:gamma=0.92,colorchannelmixer=rr=1.08:rg=0.12:rb=0.0:gr=0.06:gg=0.95:gb=0.0:br=0.0:bg=0.05:bb=0.72",
        "vignette": 0.85, "grain": 7, "bloom": 0.0,
        "transitions": ["fadeblack", "dissolve", "fadegrays", "wipeleft", "fadeblack"],
        "xfade_frames": 16, "intro": "fadeblack", "progress": "0xD6A24A",
        "caption": {
            "font": "DejaVu Serif", "size": 82, "bold": True, "uppercase": False,
            "colors": ["&H00E6F0F5", "&H0090C8F0"], "accent": "&H003C3CE6", "outline": "&H00101828",
            "outline_w": 6, "shadow": 4, "box": False, "margin_v": 500, "anim": "fade", "chunk": 3,
        },
    },
    # Psikoloji: yüksek kontrast noir, hızlı kesmeler, kutulu altyazı
    "noir": {
        "effects": ["zoom_in", "zoom_in", "push_right", "zoom_out", "push_left"],
        "zoom_max": 1.34, "hook_zoom": 1.45, "shake": 0.0, "glitch": False, "flicker": 0.0,
        "grade": "eq=contrast=1.30:saturation=0.35:brightness=-0.02:gamma=0.90,curves=r='0/0 0.4/0.33 1/1':b='0/0 0.4/0.38 1/1'",
        "vignette": 0.70, "grain": 6, "bloom": 0.0,
        "transitions": ["slideleft", "wipeleft", "slideup", "wiperight", "hblur", "slideright"],
        "xfade_frames": 6, "intro": "none", "progress": "0xEF4444",
        "caption": {
            "font": "Liberation Sans", "size": 80, "bold": True, "uppercase": True,
            "colors": ["&H00FFFFFF", "&H00FFFFFF"], "accent": "&H004444EF", "outline": "&H00000000",
            "outline_w": 18, "shadow": 0, "box": True, "margin_v": 540, "anim": "rise", "chunk": 3,
        },
    },
    # Mitoloji: altın tonlar, destansı yavaş yaklaşma, ışıma, daire geçişleri
    "epic": {
        "effects": ["zoom_in", "push_right", "zoom_in", "drift_up", "push_left", "zoom_out"],
        "zoom_max": 1.30, "hook_zoom": 1.40, "shake": 0.0, "glitch": False, "flicker": 0.0,
        "grade": "eq=contrast=1.14:saturation=1.22:gamma=0.95,curves=r='0/0 0.5/0.57 1/1':b='0/0 0.5/0.44 1/1'",
        "vignette": 0.50, "grain": 4, "bloom": 0.30,
        "transitions": ["circleopen", "radial", "fadewhite", "circleclose", "dissolve", "smoothup"],
        "xfade_frames": 14, "intro": "fadewhite", "progress": "0xF5B83D",
        "caption": {
            "font": "DejaVu Serif", "size": 88, "bold": True, "uppercase": True,
            "colors": ["&H0050C8FF", "&H00FFFFFF"], "accent": "&H003CA8FF", "outline": "&H00102850",
            "outline_w": 8, "shadow": 4, "box": False, "margin_v": 500, "anim": "pop", "chunk": 2,
        },
    },
    # Korku: soğuk yeşil-mavi, titrek el kamerası, glitch, yoğun tanecik
    "horror": {
        "effects": ["zoom_in", "drift_up", "push_left", "zoom_in", "push_right"],
        "zoom_max": 1.26, "hook_zoom": 1.36, "shake": 7.0, "glitch": True, "flicker": 0.06,
        "grade": "eq=contrast=1.22:saturation=0.45:brightness=-0.05:gamma=0.85,curves=g='0/0 0.5/0.54 1/1':r='0/0 0.5/0.44 1/1'",
        "vignette": 0.95, "grain": 8, "bloom": 0.0,
        "transitions": ["fadeblack", "pixelize", "hblur", "fadeblack", "distance", "fadegrays"],
        "xfade_frames": 8, "intro": "fadeblack", "progress": "0xB91C1C",
        "caption": {
            "font": "DejaVu Sans", "size": 84, "bold": True, "uppercase": True,
            "colors": ["&H00F0F0F0", "&H00F0F0F0"], "accent": "&H002020E0", "outline": "&H00000010",
            "outline_w": 9, "shadow": 5, "box": False, "margin_v": 480, "anim": "shake", "chunk": 2,
        },
    },
    # Sevimli hikayeler: sıcak, parlak, yumuşak ışıma, zıplayan altyazı
    "warm": {
        "effects": ["push_right", "zoom_in", "push_left", "zoom_out", "drift_up"],
        "zoom_max": 1.24, "hook_zoom": 1.34, "shake": 0.0, "glitch": False, "flicker": 0.0,
        "grade": "eq=contrast=1.06:saturation=1.38:brightness=0.03:gamma=1.04,curves=r='0/0 0.5/0.55 1/1':b='0/0 0.5/0.46 1/1'",
        "vignette": 0.30, "grain": 0, "bloom": 0.35,
        "transitions": ["smoothleft", "circleopen", "smoothright", "dissolve", "fade", "smoothup"],
        "xfade_frames": 12, "intro": "fade", "progress": "0xFB7185",
        "caption": {
            "font": "DejaVu Sans", "size": 90, "bold": True, "uppercase": False,
            "colors": ["&H00FFFFFF", "&H00B4B4FF", "&H0070F0FF"], "accent": "&H00B450FF", "outline": "&H00662D8A",
            "outline_w": 8, "shadow": 3, "box": False, "margin_v": 540, "anim": "bounce", "chunk": 3,
        },
    },
}

# Altyazıda vurgulanacak güç kelimeleri (rakamlar her zaman vurgulanır)
POWER_WORDS = {
    "never", "impossible", "secret", "secrets", "dead", "death", "killed", "only", "nobody", "everything",
    "truth", "lie", "lies", "wrong", "forbidden", "hidden", "lost", "warning", "trapped", "alone", "billion",
    "million", "thousand", "infinite", "forever", "scream", "darkness", "gods", "god",
}

ALL_TRANSITIONS_FALLBACK = ["fade", "dissolve", "slideleft", "slideright", "wipeleft", "wiperight"]


def resolve_style(channel: dict[str, Any]) -> dict[str, Any]:
    """Kanalın stil ön ayarını döndürür; `style_overrides` iç içe birleştirilir."""
    name = channel.get("style", DEFAULT_STYLE)
    if name not in STYLES:
        log.warning("Bilinmeyen stil '%s'; '%s' kullanılıyor", name, DEFAULT_STYLE)
        name = DEFAULT_STYLE
    style = copy.deepcopy(STYLES[name])
    for key, value in (channel.get("style_overrides") or {}).items():
        if isinstance(value, dict) and isinstance(style.get(key), dict):
            style[key].update(value)
        else:
            style[key] = value
    style["name"] = name
    return style


@lru_cache(maxsize=1)
def supported_transitions() -> frozenset[str]:
    """Kurulu FFmpeg'in desteklediği xfade geçişleri (eski sürümlerde bazı adlar yoktur)."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset(ALL_TRANSITIONS_FALLBACK)
    names = set(re.findall(r"^\s{4,}([a-z0-9]+)\s+\d+\s", out, flags=re.MULTILINE))
    return frozenset(names) or frozenset(ALL_TRANSITIONS_FALLBACK)


def usable_transitions(style: dict[str, Any]) -> list[str]:
    available = supported_transitions()
    chosen = [t for t in style["transitions"] if t in available]
    return chosen or [t for t in ALL_TRANSITIONS_FALLBACK if t in available] or ["fade"]
