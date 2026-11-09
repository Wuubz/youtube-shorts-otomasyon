"""Sinematik görsel hikaye anlatımı sözleşmesi: sahne türleri, rozetler, ses efektleri ve zamanlamaları.

Gemini her sahne için `scene_type`, `key_phrase`, `sfx_cue` ve `floating_badge` üretir; Remotion bu değerlere göre
animasyon çizer, FFmpeg ses efektini animasyonun ekrana çarptığı anda (IMPACT) karıştırır.
"""
from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SFX_DIR = ROOT_DIR / "assets" / "sfx"

SCENE_TYPES = ("evidence_document", "radar_hud", "mind_scan", "ancient_scroll", "dramatic_focus")
DEFAULT_SCENE_TYPE = "dramatic_focus"
PLAIN_SCENE_TYPE = "plain"       # grafiksiz sahne: yalnızca görsel, kamera hareketi ve altyazı (videonun çoğu böyledir)

# Sadelik kuralı: bir videoda en fazla bu kadar sahne özel katman (belge, radar, tarama...) taşır; her tür en fazla bir kez.
MAX_SPECIAL_SCENES = 2

# Sahne geçişleri sakin tutulur (daire açılması, kayma, piksel gibi gösterişli geçişler kullanılmaz)
CALM_TRANSITIONS = ("dissolve", "dissolve", "dissolve", "fade", "fadeblack")

BADGES = ("warning", "skull", "galaxy", "stoic_pillar", "broken_heart")
NO_BADGE = "none"

# sfx_cue -> assets/sfx dosyası
SFX_FILES = {
    "slam": "slam.mp3",
    "marker_scratch": "marker.mp3",
    "heartbeat": "heartbeat.mp3",
    "glitch": "glitch.mp3",
    "camera_shutter": "shutter.mp3",
    "deep_boom": "deep_boom.mp3",
}
SFX_CUES = tuple(SFX_FILES)

# Gemini geçersiz/eksik değer verirse sahne türüne uygun varsayılan efekt
DEFAULT_SFX_FOR_TYPE = {
    "evidence_document": "slam",
    "radar_hud": "glitch",
    "mind_scan": "heartbeat",
    "ancient_scroll": "deep_boom",
    "dramatic_focus": "camera_shutter",
}

# Animasyonun ekrana "çarptığı" an (sahne başlangıcından saniye). Remotion bu kareye göre çizer, FFmpeg aynı ana efekt koyar.
IMPACT_SECONDS = {
    "evidence_document": 0.55,   # dosya masaya düşer
    "radar_hud": 0.60,           # hedef kilitlenir
    "mind_scan": 0.65,           # nöron dalgası başlar
    "ancient_scroll": 1.05,      # mühür basılır
    "dramatic_focus": 0.50,      # odak oturur
}
FIRST_SCENE_IMPACT = 1.4         # kanca cümlesinin ilk kelimeleri efektin altında kalmasın

# Ses seviyeleri (anlatıcı sesi 1.0): konuşmanın anlaşılırlığı için hepsi düşük tutulur
SFX_LEVEL = {
    "slam": 0.5, "marker_scratch": 0.3, "heartbeat": 0.5, "glitch": 0.35, "camera_shutter": 0.45, "deep_boom": 0.5,
}
EXTRA_LEVEL = {"typewriter": 0.22, "marker": 0.22}
FIRST_SCENE_SFX_FACTOR = 0.7
MAX_BADGES_PER_VIDEO = 1


def impact_seconds(scene_type: str, index: int) -> float:
    base = IMPACT_SECONDS.get(scene_type, IMPACT_SECONDS[DEFAULT_SCENE_TYPE])
    return max(base, FIRST_SCENE_IMPACT) if index == 0 else base
