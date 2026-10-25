"""Bu slot için bugün henüz video üretilmemiş aktif kanal sayısını GitHub Actions çıktısına yazar.

Yedek cron çalışmalarında her şey zaten tamamsa ağır adımlar (FFmpeg/pip kurulumu) atlanır.
Kullanım: python scripts/pending_check.py <slot>
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.state import is_done, today_utc  # noqa: E402


def main() -> None:
    slot = int(sys.argv[1])
    channels = json.loads((ROOT / "config" / "channels.json").read_text(encoding="utf-8"))["channels"]
    pending = [cid for cid, cfg in channels.items() if cfg.get("enabled") and not is_done(cid, slot, today_utc())]
    print(f"slot {slot} bekleyen kanallar: {pending or '-'}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"count={len(pending)}\n")


if __name__ == "__main__":
    main()
