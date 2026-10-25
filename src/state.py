"""Slot başına tek üretim kaydı (yinelenen yükleme olmasın) ve GitHub Actions içinde durumu depoya yazma.

Yalnızca standart kütüphane kullanır; bağımlılık kurulmadan da çalışabilir (scripts/pending_check.py).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PUBLISH_LOG_PATH = ROOT_DIR / "data" / "publish_log.json"
STATE_FILES = ("data",)   # history.json + publish_log.json
KEEP_DAYS = 7
PUSH_ATTEMPTS = 4

log = logging.getLogger(__name__)


def today_utc(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def load_log() -> dict[str, dict[str, list[str]]]:
    if not PUBLISH_LOG_PATH.exists():
        return {}
    try:
        data = json.loads(PUBLISH_LOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("publish_log.json bozuk; boş kabul ediliyor")
        return {}
    return data if isinstance(data, dict) else {}


def is_done(channel_id: str, slot: int, day: str | None = None) -> bool:
    return channel_id in load_log().get(day or today_utc(), {}).get(str(slot), [])


def mark_done(channel_id: str, slot: int, day: str | None = None) -> None:
    data = load_log()
    done = data.setdefault(day or today_utc(), {}).setdefault(str(slot), [])
    if channel_id not in done:
        done.append(channel_id)
    for old in sorted(data)[:-KEEP_DAYS]:
        del data[old]
    PUBLISH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PUBLISH_LOG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, PUBLISH_LOG_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT_DIR, capture_output=True, text=True)


def sync_state(message: str) -> None:
    """GitHub Actions içinde history/publish_log değişikliklerini hemen commit + push eder.

    Runner sonradan çökse bile yüklenen videoların kaydı kaybolmaz. Yerelde hiçbir şey yapmaz.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    _git("add", *STATE_FILES)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return
    commit = _git("commit", "-m", f"chore: {message} [skip ci]")
    if commit.returncode != 0:
        log.warning("Durum commit'i başarısız: %s", commit.stderr.strip()[-300:])
        return
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        pull = _git("pull", "--rebase", "origin", branch)
        push = _git("push", "origin", f"HEAD:{branch}") if pull.returncode == 0 else pull
        if push.returncode == 0:
            return
        log.warning("Durum push denemesi %d/%d başarısız: %s", attempt, PUSH_ATTEMPTS, push.stderr.strip()[-300:])
        _git("rebase", "--abort")
        time.sleep(5 * attempt)
    log.error("Durum dosyaları depoya yazılamadı; iş akışı sonundaki commit adımı yeniden deneyecek")
