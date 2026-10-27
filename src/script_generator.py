"""Gemini ile özgün senaryo, sahne görsel prompt'ları ve SEO verisi üretir."""
from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.config_loader import MAX_HISTORY_TOPICS, get_gemini_api_key, get_themes

log = logging.getLogger(__name__)

# Ücretsiz katmanda kota MODEL BAŞINA tutulur; bu yüzden birden çok model zinciri günlük kapasiteyi katlar.
# GEMINI_MODEL ile ana model değiştirilebilir. Günlük kotası dolan model o çalışmada bir daha denenmez.
MODEL_CHAIN = list(dict.fromkeys(
    [m for m in (
        os.environ.get("GEMINI_MODEL", "").strip(),
        "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
        "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-flash-latest",
    ) if m]
))
MAX_REQUESTS = 16               # bir senaryo için toplam istek tavanı
TRANSIENT_TRIES = 2             # aynı modelde 503/bağlantı kopması için deneme
CONTENT_TRIES = 2               # aynı modelde geçersiz içerik (kelime sayısı, şema) için deneme
REQUEST_TIMEOUT_MS = 60_000     # takılan isteklerin dakikalarca beklemesini önler
TEMPERATURE = 0.95
_TRANSIENT_MARKERS = (
    "503", "500", "502", "504", "UNAVAILABLE", "INTERNAL", "DEADLINE", "disconnected", "timed out",
    "timeout", "ConnectError", "RemoteProtocol", "ReadError", "Connection",
)
_exhausted_models: set[str] = set()   # günlük kotası dolan modeller (süreç boyunca)

SHORTS_SUFFIX = " #Shorts"
MAX_TITLE_LENGTH = 70
MAX_TAGS_CHARS = 400
TAG_COUNT = 15
MIN_SCENES, MAX_SCENES = 8, 10
# Doğal konuşma hızında (1.0x, atempo gerekmeden) ~42-50 sn: prompt'a verilen hedef kelime aralığı
TARGET_MIN_WORDS, TARGET_MAX_WORDS = 90, 115
# Gemini kelime saymada küçük sapmalar yapabilir; bu pencerenin dışı yeniden denenir (en fazla ~54 sn)
MIN_TOTAL_WORDS, MAX_TOTAL_WORDS = 85, 125
MAX_BASE_IMAGE_PROMPT_CHARS = 450
IMAGE_SAFETY_SUFFIX = "no text, no letters, no watermark, vertical 9:16 composition"


class Scene(BaseModel):
    narration: str = Field(description="1-2 sentences spoken in this scene")
    image_prompt: str = Field(description="Detailed English text-to-image prompt for this scene")


class VideoScript(BaseModel):
    topic_summary: str
    title: str
    description: str
    tags: list[str]
    comment_question: str = Field(description="Short question for viewers, identical to the question closing the last scene")
    scenes: list[Scene]


# Aşırı kullanılmış, izleyiciyi hemen kaydıran açılış kalıpları: reddedilir ve yeniden denenir
BANNED_OPENINGS = (
    "did you know", "imagine", "have you ever", "in this video", "today", "welcome", "let me tell",
    "let's talk", "hey guys", "have you heard", "ever wondered", "what do you think",
)
RECENT_SUBJECTS_SHOWN = 25     # prompt'ta "bu konulardan farklı ana özne seç" diye gösterilen son konu sayısı


def _pick_theme(channel: dict[str, Any]) -> str | None:
    """Alt temayı rotasyonla seçer: hiç kullanılmamışlar önce, sonra en eski kullanılan (aynı tema art arda gelmez)."""
    themes = channel.get("sub_themes") or []
    if not themes:
        return None
    used = get_themes(channel["id"])
    last_index = {t: i for i, t in enumerate(used)}     # büyük = daha yeni
    ranked = sorted(themes, key=lambda t: (last_index.get(t, -1), random.random()))
    return ranked[0]


def _pick_direction(channel: dict[str, Any]) -> dict[str, str]:
    """Her video için alt tema (rotasyonla), kanca stili, başlık kalıbı ve görsel atmosfer seçer."""
    direction: dict[str, str] = {}
    theme = _pick_theme(channel)
    if theme:
        direction["sub_theme"] = theme
    for key, field in (
        ("hook_style", "hook_styles"), ("title_formula", "title_formulas"), ("visual_mood", "visual_moods"),
    ):
        options = channel.get(field) or []
        if options:
            direction[key] = random.choice(options)
    return direction


def _direction_block(direction: dict[str, str], channel: dict[str, Any]) -> str:
    if not direction:
        return ""
    lines = ["MANDATORY CREATIVE DIRECTION FOR THIS VIDEO (follow it exactly, do not pick a different angle):"]
    if "sub_theme" in direction:
        lines.append(
            f"- Sub-theme / story angle: {direction['sub_theme']}. Choose a specific, concrete subject "
            "inside this sub-theme that is NOT in the used-topics list."
        )
    if "hook_style" in direction:
        lines.append(f"- Hook style for scene 1: {direction['hook_style']}.")
    if "title_formula" in direction:
        lines.append(f"- Title pattern (adapt it to your topic, keep it truthful): {direction['title_formula']}.")
    proven, weak = channel.get("proven_examples") or [], channel.get("weak_examples") or []
    if proven:
        lines.append("- Titles that performed BEST on this channel (copy their energy, not their topics): " + "; ".join(proven) + ".")
    if weak:
        lines.append("- Things that performed badly here (avoid them): " + "; ".join(weak) + ".")
    if "visual_mood" in direction:
        lines.append(
            f"- Visual atmosphere: {direction['visual_mood']}. Every image prompt must express this "
            "atmosphere while staying consistent with the subject of its scene."
        )
    return "\n".join(lines) + "\n\n"


def _build_prompt(channel: dict[str, Any], past_topics: list[str], direction: dict[str, str]) -> str:
    history_block = "\n".join(f"- {t}" for t in past_topics[-MAX_HISTORY_TOPICS:]) or "- (none yet)"
    recent_block = "\n".join(f"- {t}" for t in past_topics[-RECENT_SUBJECTS_SHOWN:]) or "- (none yet)"
    return f"""You are a senior YouTube Shorts scriptwriter and retention strategist. Your only goal: the viewer cannot swipe away before the end, then wants to comment.

CHANNEL CONCEPT: {channel['concept']}
LANGUAGE: English

TASK: Invent ONE brand-new, highly viral topic for this channel and write a complete Shorts script.

TOPICS ALREADY USED (never repeat or closely paraphrase any of them):
{history_block}

SUBJECT VARIETY: the central subject of the new video (the person, object, place, creature, event or myth it is mainly about) must be completely different from the central subject of each of these recent videos, even if you would approach it from a new angle. If a recent video was about Voyager, do not write about Voyager; if one was about a vanished ship, do not write about another vanished ship:
{recent_block}

{_direction_block(direction, channel)}
THE HOOK (scene 1, the 3 seconds that decide everything):
- Scene 1 narration is 6 to 12 words. It must open a curiosity loop the viewer can only close by watching to the end: state a shocking, impossible or contradictory fact, or a direct challenge, but WITHHOLD the explanation until the last third of the video.
- Start in the middle of the action with the most surprising thing. No greeting, no throat-clearing. Never begin with: "Did you know", "Imagine", "Have you ever", "In this video", "Today", "Welcome", "Let me tell you", "Ever wondered".
- Use at least one of: a precise number or date, a direct "you", a vivid forbidden or impossible concept, a belief of the viewer that you contradict.
- The hook must be honest: the video must actually deliver what the hook promises.

RETENTION STRUCTURE:
- Scene 2 raises the stakes or adds a second question. Scenes 3-6 escalate with micro-cliffhangers ("But that is not the strange part.", "And then it got worse."). Re-hook around scene 5 or 6 so a viewer who was about to leave stays. Reveal the payoff in the second-to-last scene.
- End scenes on tension that pulls into the next scene; do not wrap up early.

THE ENDING (last scene) = COMMENT BAIT:
- The last scene narration must be a short, specific question about THIS video's topic that is effortless to answer in a comment, ideally a choice between two options or a "Would you...?" opinion. 6 to 14 words, ends with a question mark. Never generic ("What do you think?").
- Put exactly that question in the comment_question field as well.

SCRIPT RULES:
- Use 8 or 9 scenes. LENGTH IS STRICT: the total narration across all scenes must be between {TARGET_MIN_WORDS} and {TARGET_MAX_WORDS} words (never more). Scene 1 and the last scene are short (6-12 words); the other scenes are 10 to 14 words each. Count the words before answering. The voiceover is read at natural speed, so the video must finish in about 42-50 seconds without any speed-up.
- Write like a gifted human storyteller or podcast host talking to one listener, NOT like an encyclopedia. Use natural pauses: commas, dashes and short punchy sentences ("Seven seconds. That's all it took."). Vary sentence length, use contractions and a warm conversational voice. Avoid lists of facts, stiff textbook phrasing and long sub-clauses.
- Original storytelling only. No real-person defamation, no graphic gore, no hate speech, no medical or political misinformation. Keep it advertiser-friendly.
- Do not use emojis, hashtags, stage directions or scene numbers inside narration.

IMAGE PROMPT RULES (one per scene, matching that scene's narration):
- English, at most 60 words, highly descriptive: subject, setting, lighting, mood, camera angle. Show the exact concrete thing the narration mentions, never an abstract mood.
- Scene 1's image is the thumbnail frame that stops the scroll: ONE bold focal subject, extreme close-up or dramatic scale contrast, strong contrast and rim light, visually shocking or intriguing, showing the mystery or the emotional peak itself (not a generic landscape).
- Vary the shot type from scene to scene (extreme close-up, wide establishing shot, low angle, over-the-shoulder, aerial, macro); never use the same framing in two consecutive scenes.
- Keep the main subject in the upper two thirds of the frame and the bottom third visually calm and darker, because subtitles are placed there.
- The last image should visually echo the closing question (a door left open, two paths, an unanswered choice).
- Vertical 9:16 composition. Absolutely NO text, letters, numbers, captions, logos or watermarks in the image.
- Keep one consistent visual style across all scenes of the video.

SEO RULES:
- topic_summary: one short sentence (max 120 characters) that STARTS with the central subject (for example "Voyager 1: ...") so repeats can be detected later.
- title: curiosity-gap English title, at most 55 characters (the tag " #Shorts" is appended afterwards). Concrete and specific; no vague superlatives like "The Darkest ..." with no subject, and no vague "Why did this ...?" questions. The title must truthfully match the story the video actually tells.
- description: 2-3 SEO-friendly sentences followed by 5-8 relevant hashtags, including #Shorts. Do not include the comment question in it (it is added automatically).
- tags: exactly {TAG_COUNT} high-search-volume English keywords or short phrases, each at most 24 characters, no '#' symbol.

Return ONLY valid JSON that matches the provided schema."""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _finalize_title(title: str) -> str:
    base = re.sub(r"#shorts", "", _clean(title), flags=re.IGNORECASE).strip(" -|:–—")
    if not base:
        raise ValueError("Başlık boş")
    limit = MAX_TITLE_LENGTH - len(SHORTS_SUFFIX)
    if len(base) > limit:
        cut = base[:limit]
        base = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;:-–— ")
    return base + SHORTS_SUFFIX


def _finalize_description(description: str, question: str = "") -> str:
    text = (description or "").strip()
    if not text:
        raise ValueError("Açıklama boş")
    if "#shorts" not in text.lower():
        text = f"{text}\n\n#Shorts"
    if question:
        text = f"💬 {question}\n\n{text}"   # yorum sorusu açıklamanın ilk satırı
    return text[:4900]


def _finalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in tags:
        tag = _clean(raw.replace("#", ""))
        key = tag.lower()
        if not tag or len(tag) > 30 or key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
    cleaned = cleaned[:TAG_COUNT]
    if len(cleaned) < TAG_COUNT:
        raise ValueError(f"Yetersiz etiket sayısı: {len(cleaned)} (beklenen {TAG_COUNT})")
    # YouTube etiket limiti: toplam ~500; güvenli tarafta kalmak için 400 karakter
    while sum(len(t) + 1 for t in cleaned) > MAX_TAGS_CHARS and len(cleaned) > 10:
        cleaned.pop()
    if sum(len(t) + 1 for t in cleaned) > MAX_TAGS_CHARS:
        raise ValueError("Etiketlerin toplam uzunluğu 400 karakteri aşıyor")
    return cleaned


def _finalize_image_prompt(prompt: str, style: str) -> str:
    base = _clean(prompt).rstrip(".,; ")
    if not base:
        raise ValueError("Görsel prompt'u boş")
    if len(base) > MAX_BASE_IMAGE_PROMPT_CHARS:
        base = base[:MAX_BASE_IMAGE_PROMPT_CHARS].rsplit(" ", 1)[0].rstrip(".,; ")
    parts = [base]
    if "no watermark" not in base.lower():
        parts.append(IMAGE_SAFETY_SUFFIX)
    if style and style[:30].lower() not in base.lower():
        parts.append(style.strip())
    return ", ".join(parts)


def _check_hook(first_narration: str) -> None:
    opening = _normalize(first_narration)
    for banned in BANNED_OPENINGS:
        if opening.startswith(_normalize(banned)):
            raise ValueError(f"Zayıf açılış kalıbı: '{banned}' ile başlıyor")
    if len(first_narration.split()) > 16:
        raise ValueError("Açılış cümlesi çok uzun (kanca en fazla ~12 kelime olmalı)")


def _ensure_closing_question(final_scenes: list[dict[str, str]], question: str) -> str:
    """Son sahnenin bir soruyla bitmesini sağlar; soru yoksa yorum sorusu sahneye eklenir."""
    question = _clean(question)
    if not question.endswith("?") or not 3 <= len(question.split()) <= 20:
        raise ValueError(f"Geçersiz yorum sorusu: '{question}'")
    last = final_scenes[-1]["narration"]
    if not last.rstrip().endswith("?"):
        final_scenes[-1]["narration"] = f"{last} {question}"
    return question


def _finalize(
    raw: VideoScript, channel: dict[str, Any], past_topics: list[str], direction: dict[str, str] | None = None
) -> dict[str, Any]:
    topic = _clean(raw.topic_summary)[:200]
    if not topic:
        raise ValueError("topic_summary boş")
    if _normalize(topic) in {_normalize(t) for t in past_topics}:
        raise ValueError(f"Konu daha önce işlenmiş: {topic}")

    scenes = raw.scenes[:MAX_SCENES]
    if len(scenes) < MIN_SCENES:
        raise ValueError(f"Yetersiz sahne sayısı: {len(scenes)} (en az {MIN_SCENES})")

    final_scenes: list[dict[str, str]] = []
    for scene in scenes:
        narration = _clean(scene.narration)
        if not narration:
            raise ValueError("Boş narration içeren sahne var")
        final_scenes.append(
            {
                "narration": narration,
                "image_prompt": _finalize_image_prompt(scene.image_prompt, channel["visual_style_prompt"]),
            }
        )
    _check_hook(final_scenes[0]["narration"])
    question = _ensure_closing_question(final_scenes, raw.comment_question)

    total_words = sum(len(s["narration"].split()) for s in final_scenes)
    if not MIN_TOTAL_WORDS <= total_words <= MAX_TOTAL_WORDS:
        raise ValueError(
            f"Toplam kelime sayısı {total_words}; {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS} aralığı dışında"
        )

    return {
        "topic_summary": topic,
        "title": _finalize_title(raw.title),
        "description": _finalize_description(raw.description, question),
        "tags": _finalize_tags(raw.tags),
        "comment_question": question,
        "sub_theme": (direction or {}).get("sub_theme"),
        "scenes": final_scenes,
    }


def _classify_error(exc: Exception) -> tuple[str, float]:
    """Hatayı ('daily_quota' | 'rate_limit' | 'transient' | 'content', bekleme sn) olarak sınıflar."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        if "PerDay" in text or "per day" in text.lower():
            return "daily_quota", 0.0
        match = re.search(r"retryDelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", text)
        return "rate_limit", min(float(match.group(1)) if match else 30.0, 60.0)
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return "transient", 0.0
    return "content", 0.0


def generate_script(channel: dict[str, Any], past_topics: list[str]) -> dict[str, Any]:
    """Kanal için yeni bir senaryo üretir.

    Önce en iyi model denenir; 503/bağlantı hatasında aynı modelde kısa beklemeyle yeniden, günlük kotası
    dolmuşsa (429) ya da hata sürüyorsa zincirdeki sonraki modele geçilir. İstekler zaman aşımına sahiptir.
    """
    client = genai.Client(
        api_key=get_gemini_api_key(),
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=VideoScript,
        temperature=TEMPERATURE,
    )

    last_error: Exception | None = None
    requests_made = 0
    for model in MODEL_CHAIN:
        if model in _exhausted_models:
            continue
        transient_tries = content_tries = rate_tries = 0
        while requests_made < MAX_REQUESTS:
            requests_made += 1
            direction = _pick_direction(channel)  # içerik hatasında yeni deneme farklı yönle başlar
            prompt = _build_prompt(channel, past_topics, direction)
            log.info("[%s] Yaratıcı yön (%s): %s", channel["id"], model, direction or "(tanımsız)")
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=config)
                if not response.text:
                    raise RuntimeError("Gemini boş yanıt döndürdü (içerik filtrelenmiş olabilir)")
                script = _finalize(VideoScript.model_validate_json(response.text), channel, past_topics, direction)
                log.info(
                    "[%s] Senaryo hazır: '%s' (%d sahne, %s)",
                    channel["id"], script["title"], len(script["scenes"]), model,
                )
                return script
            except Exception as exc:  # API, ağ, şema ve doğrulama hataları sınıflandırılır
                last_error = exc
                kind, wait = _classify_error(exc)
                log.warning("[%s] Senaryo isteği (%s, %s) başarısız: %.220s", channel["id"], model, kind, exc)
                if kind == "daily_quota":
                    _exhausted_models.add(model)
                    break
                if kind == "rate_limit":
                    rate_tries += 1
                    if rate_tries > 2:
                        break
                    time.sleep(wait)
                elif kind == "transient":
                    transient_tries += 1
                    if transient_tries >= TRANSIENT_TRIES:
                        break
                    time.sleep(5 * 3 ** (transient_tries - 1))   # 5 sn, 15 sn
                else:
                    content_tries += 1
                    if content_tries >= CONTENT_TRIES:
                        break
        if requests_made >= MAX_REQUESTS:
            break
    raise RuntimeError(f"Senaryo üretilemedi ({requests_made} istek): {last_error}") from last_error
