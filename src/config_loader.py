"""Kanal yapılandırması, geçmiş konu hafızası ve ortam değişkeni doğrulaması."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "channels.json"
HISTORY_PATH = ROOT_DIR / "data" / "history.json"
MAX_HISTORY_TOPICS = 200
MAX_THEME_HISTORY = 60

load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger(__name__)

REQUIRED_CHANNEL_FIELDS = (
    "enabled",
    "concept",
    "language",
    "voice",
    "voice_enabled",
    "rate",
    "pitch",
    "category_id",
    "daily_target",
    "publish_times_utc",
    "schedule_mode",
    "env_prefix",
    "visual_style_prompt",
)
# Çeşitlilik için isteğe bağlı listeler: her videoda bunlardan rastgele biri seçilir
OPTIONAL_STRING_LISTS = (
    "sub_themes", "hook_styles", "visual_moods", "title_formulas", "proven_examples", "weak_examples",
    "popular_subjects",
)
VALID_SCHEDULE_MODES = ("scheduled", "immediate")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(Exception):
    """Yapılandırma, geçmiş dosyası veya ortam değişkeni hatası."""


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Dosya bulunamadı: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Geçersiz JSON ({path}): {exc}") from exc


def _atomic_write_json(path: Path, data: Any) -> None:
    """Yarım yazılmış dosya bırakmamak için geçici dosyaya yazıp yerine taşır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


# --------------------------------------------------------------------------- #
# Kanal yapılandırması
# --------------------------------------------------------------------------- #
def _validate_channel(channel_id: str, cfg: dict[str, Any]) -> None:
    missing = [f for f in REQUIRED_CHANNEL_FIELDS if f not in cfg]
    if missing:
        raise ConfigError(f"{channel_id}: eksik alanlar: {', '.join(missing)}")
    if cfg["schedule_mode"] not in VALID_SCHEDULE_MODES:
        raise ConfigError(
            f"{channel_id}: schedule_mode {VALID_SCHEDULE_MODES} değerlerinden biri olmalı"
        )
    times = cfg["publish_times_utc"]
    if not isinstance(times, list) or not times:
        raise ConfigError(f"{channel_id}: publish_times_utc boş olamaz")
    for value in times:
        if not isinstance(value, str) or not _TIME_RE.match(value):
            raise ConfigError(f"{channel_id}: geçersiz yayın saati '{value}' (HH:MM bekleniyor)")
    if cfg["daily_target"] != len(times):
        raise ConfigError(
            f"{channel_id}: daily_target ({cfg['daily_target']}) publish_times_utc eleman sayısına ({len(times)}) eşit olmalı"
        )
    if not str(cfg["env_prefix"]).strip():
        raise ConfigError(f"{channel_id}: env_prefix boş olamaz")
    for key in OPTIONAL_STRING_LISTS:
        values = cfg.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            raise ConfigError(f"{channel_id}: {key} boş olmayan metinlerden oluşan bir liste olmalı")


def load_channels() -> dict[str, dict[str, Any]]:
    """channels.json içindeki tüm kanalları {kanal_id: ayarlar} olarak döndürür."""
    raw = _read_json(CONFIG_PATH)
    channels = raw.get("channels")
    if not isinstance(channels, dict) or not channels:
        raise ConfigError("channels.json içinde 'channels' nesnesi bulunamadı")
    result: dict[str, dict[str, Any]] = {}
    for channel_id, cfg in channels.items():
        _validate_channel(channel_id, cfg)
        result[channel_id] = {**cfg, "id": channel_id}
    return result


def get_channel(channel_id: str) -> dict[str, Any]:
    channels = load_channels()
    if channel_id not in channels:
        raise ConfigError(f"Bilinmeyen kanal '{channel_id}'. Geçerli kanallar: {', '.join(channels)}")
    return channels[channel_id]


def get_enabled_channels() -> list[dict[str, Any]]:
    return [cfg for cfg in load_channels().values() if cfg["enabled"]]


# --------------------------------------------------------------------------- #
# Geçmiş konu hafızası
# --------------------------------------------------------------------------- #
def load_history() -> dict[str, dict[str, list[str]]]:
    """history.json dosyasını okur. Dosya yoksa boş yapı döner; bozuksa hata verir
    (böylece bozuk dosyanın üzerine yanlışlıkla yazılmaz)."""
    if not HISTORY_PATH.exists():
        return {}
    data = _read_json(HISTORY_PATH)
    if not isinstance(data, dict):
        raise ConfigError("history.json bir JSON nesnesi olmalı")
    return data


def save_history(history: dict[str, dict[str, list[str]]]) -> None:
    for entry in history.values():
        entry["topics"] = entry.get("topics", [])[-MAX_HISTORY_TOPICS:]
    _atomic_write_json(HISTORY_PATH, history)


def get_topics(channel_id: str) -> list[str]:
    entry = load_history().get(channel_id, {})
    return list(entry.get("topics", []))[-MAX_HISTORY_TOPICS:]


def get_themes(channel_id: str) -> list[str]:
    """Kanalda son kullanılan alt temalar (eskiden yeniye); alt tema rotasyonu için."""
    entry = load_history().get(channel_id, {})
    return list(entry.get("themes", []))[-MAX_THEME_HISTORY:]


def add_topic(channel_id: str, topic: str, theme: str | None = None) -> None:
    """Konuyu kanal geçmişine ekler; yalnızca son 200 konu saklanır."""
    topic = topic.strip()
    if not topic:
        return
    history = load_history()
    entry = history.setdefault(channel_id, {"topics": []})
    entry.setdefault("topics", []).append(topic)
    if theme:
        entry["themes"] = (entry.get("themes", []) + [theme])[-MAX_THEME_HISTORY:]
    save_history(history)
    log.info("[%s] Konu geçmişe eklendi: %s", channel_id, topic)


# --------------------------------------------------------------------------- #
# Ortam değişkenleri
# --------------------------------------------------------------------------- #
def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def get_gemini_api_key() -> str:
    key = _env("GEMINI_API_KEY")
    if not key:
        raise ConfigError("GEMINI_API_KEY ortam değişkeni tanımlı değil")
    return key


def youtube_env_names(channel: dict[str, Any]) -> dict[str, str]:
    prefix = channel["env_prefix"]
    return {
        "client_id": f"{prefix}_CLIENT_ID",
        "client_secret": f"{prefix}_CLIENT_SECRET",
        "refresh_token": f"{prefix}_REFRESH_TOKEN",
    }


def get_youtube_credentials_env(channel: dict[str, Any]) -> dict[str, str]:
    names = youtube_env_names(channel)
    missing = [n for n in names.values() if not _env(n)]
    if missing:
        raise ConfigError(f"{channel['id']}: eksik ortam değişkenleri: {', '.join(missing)}")
    return {key: _env(name) for key, name in names.items()}


def missing_env_vars(
    channel: dict[str, Any], need_gemini: bool = True, need_youtube: bool = True
) -> list[str]:
    missing: list[str] = []
    if need_gemini and not _env("GEMINI_API_KEY"):
        missing.append("GEMINI_API_KEY")
    if need_youtube:
        missing.extend(n for n in youtube_env_names(channel).values() if not _env(n))
    return missing


def require_env(channel: dict[str, Any], need_gemini: bool = True, need_youtube: bool = True) -> None:
    missing = missing_env_vars(channel, need_gemini, need_youtube)
    if missing:
        raise ConfigError(f"{channel['id']}: eksik ortam değişkenleri: {', '.join(missing)}")
