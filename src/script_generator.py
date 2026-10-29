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

from src.config_loader import MAX_HISTORY_TOPICS, get_gemini_api_key, get_themes, get_titles

log = logging.getLogger(__name__)

# Ücretsiz katmanda kota MODEL BAŞINA tutulur; bu yüzden birden çok model zinciri günlük kapasiteyi katlar.
# GEMINI_MODEL ile ana model değiştirilebilir. Günlük kotası dolan model o çalışmada bir daha denenmez.
# Sıra gerçek başarıya göre: 3.8 ve 3.7 son çalışmalarda hiç senaryo üretemedi (hep 503/504 ya da kota dolu),
# bu yüzden son çare olarak sona alındı.
MODEL_CHAIN = list(dict.fromkeys(
    [m for m in (
        os.environ.get("GEMINI_MODEL", "").strip(),
        "gemini-3.6-flash", "gemini-3-flash-preview", "gemini-3.5-flash", "gemini-2.5-flash",
        "gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash",
    ) if m]
))
OVERLOAD_LIMIT = 2              # bir model bu kadar kez pes ettirirse (503/504) çalışmanın geri kalanında atlanır
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
_overload_hits: dict[str, int] = {}   # modelin art arda "pes ettirdiği" sayı; başarıda sıfırlanır

# Başlık benzerliği kontrolü için anlamsız kelimeler ve eş adlar
_TITLE_STOPWORDS = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "this", "that", "it", "his", "her", "its"}
_FIGURE_ALIASES = {
    "heracles": ("hercules",), "kronos": ("cronus", "chronos"), "set": ("seth",), "valkyries": ("valkyrie",),
    "jormungandr": ("midgard serpent", "world serpent"), "ra": ("re",), "hel": ("helheim",),
}

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
MAX_IDENTITY_CHARS = 140       # sabit karakter/mekan tarifi başına üst sınır
MAX_LABELS, MAX_LABEL_CHARS = 3, 28
IMAGE_SAFETY_SUFFIX = "no text, no letters, no watermark, vertical 9:16 composition"


class Character(BaseModel):
    name: str = Field(description="Short unique name or label, e.g. 'the cat' or 'Thomas'")
    visual: str = Field(description="Fixed visual description, max 20 words: species/age/build, colors, markings, clothing, one distinguishing feature")


class Scene(BaseModel):
    narration: str = Field(description="1-2 sentences spoken in this scene")
    image_prompt: str = Field(description="Image prompt: action, framing, lighting only; do not redescribe the characters or the setting")
    characters_present: list[str] = Field(default_factory=list, description="Exact names from `characters` visible in this scene")
    show_setting: bool = Field(default=False, description="True if the main setting is visible in this scene")
    label: str = Field(default="", description="1-4 word on-screen time/place caption when the time or place changes, else empty")


class VideoScript(BaseModel):
    topic_summary: str
    title: str
    description: str
    tags: list[str]
    comment_question: str = Field(description="Short question for viewers, identical to the question closing the last scene")
    characters: list[Character] = Field(default_factory=list, description="0-3 recurring characters, animals or objects")
    setting: str = Field(default="", description="Fixed description of the main location, max 25 words")
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


POPULAR_CANDIDATES = 10


def _popular_block(channel: dict[str, Any]) -> str:
    """Kanalda tanınmış figür listesi varsa ana özneyi bunlardan seçtirir (obskür figürlere kaymayı önler)."""
    subjects = channel.get("popular_subjects") or []
    if not subjects:
        return ""
    shortlist = ", ".join(random.sample(subjects, min(POPULAR_CANDIDATES, len(subjects))))
    return (
        "\nWELL-KNOWN SUBJECTS ONLY: the central figure of the video must be one of these famous, instantly "
        f"recognisable figures (choose one that is not the central subject of a recent video): {shortlist}.\n"
        "The TITLE must contain the figure's name (for example 'Hades' or 'Thor'), never just 'The God Who...'. "
        "Never pick an obscure, minor or purely regional figure that a casual viewer would not know. "
        "Instead, make the story about the darkest, strangest or least-known secret, myth or consequence "
        "of that famous figure, so the title can name the figure the viewer already recognises.\n"
    )


def _build_prompt(channel: dict[str, Any], past_topics: list[str], direction: dict[str, str]) -> str:
    history_block = "\n".join(f"- {t}" for t in past_topics[-MAX_HISTORY_TOPICS:]) or "- (none yet)"
    recent_block = "\n".join(f"- {t}" for t in past_topics[-RECENT_SUBJECTS_SHOWN:]) or "- (none yet)"
    titles_block = "\n".join(f"- {t}" for t in get_titles(channel["id"])[-15:]) or "- (none yet)"
    return f"""You are a senior YouTube Shorts scriptwriter and retention strategist. Your only goal: the viewer cannot swipe away before the end, then wants to comment.

CHANNEL CONCEPT: {channel['concept']}
LANGUAGE: English

TASK: Invent ONE brand-new, highly viral topic for this channel and write a complete Shorts script.

TOPICS ALREADY USED (never repeat or closely paraphrase any of them):
{history_block}

SUBJECT VARIETY: the central subject of the new video (the person, object, place, creature, event or myth it is mainly about) must be completely different from the central subject of each of these recent videos, even if you would approach it from a new angle. If a recent video was about Voyager, do not write about Voyager; if one was about a vanished ship, do not write about another vanished ship:
{recent_block}

RECENT TITLES ON THIS CHANNEL (the new title must NOT reuse their wording, sentence structure or ending; a viewer scrolling the channel must see clearly different titles):
{titles_block}
{_popular_block(channel)}
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

STORY BIBLE (so the viewer sees the SAME characters and place in every scene):
- characters: 0 to 3 recurring characters, animals or key objects that appear in two or more scenes. Give each a short name and one fixed `visual` description (max 20 words: species/age/build, exact colors, markings, clothing, one distinguishing feature).
- setting: one fixed description (max 25 words) of the main location: architecture, weather, time of day, dominant colors.
- In each scene's image_prompt write ONLY the action, framing and lighting. Do NOT redescribe the characters or the setting there; instead list the exact names of everyone visible in `characters_present` and set `show_setting` to true when the location is visible. The code inserts the fixed descriptions into every image prompt word for word.
- For topics without recurring characters (space, abstract ideas) leave `characters` empty and use `setting` or a recurring key object for visual continuity.

ON-SCREEN LABELS:
- label: a 1 to 4 word caption shown on screen when the time or place changes, for example "Kyoto, 1185", "4 years later", "That night", "Deep below". Leave it as an empty string otherwise. Use at most 3 labels per video, never in scene 1 or the last scene.

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


def _title_words(title: str) -> list[str]:
    return _normalize(re.sub(r"#shorts", "", title, flags=re.IGNORECASE)).split()


def _check_title_variety(title: str, recent: list[str]) -> None:
    """Yeni başlık son başlıklara çok benziyorsa (aynı kelimeler, aynı bitiş, art arda aynı açılış) reddeder."""
    words = _title_words(title)
    keys = {w for w in words if w not in _TITLE_STOPWORDS}
    for age, old in enumerate(reversed(recent[-20:])):          # age 0 = en yeni başlık
        old_words = _title_words(old)
        old_keys = {w for w in old_words if w not in _TITLE_STOPWORDS}
        union = keys | old_keys
        if union and len(keys & old_keys) / len(union) >= 0.6:
            raise ValueError(f"Başlık son bir başlığa çok benziyor: '{old}'")
        if age < 12 and len(words) >= 4 and len(old_words) >= 4 and words[-4:] == old_words[-4:]:
            raise ValueError(f"Başlık, son başlıklardaki cümle bitişini tekrarlıyor: '{old}'")
        if age < 3 and len(words) >= 3 and words[:3] == old_words[:3]:
            raise ValueError(f"Başlık art arda aynı açılışla başlıyor: '{old}'")


def _check_famous_title(title: str, channel: dict[str, Any]) -> None:
    """Tanınmış figür listesi olan kanalda başlık bu figürlerden birinin adını içermelidir."""
    subjects = channel.get("popular_subjects") or []
    if not subjects:
        return
    padded = f" {_normalize(title)} "
    for subject in subjects:
        name = _normalize(subject)
        name = name[4:] if name.startswith("the ") else name
        for candidate in (name, *_FIGURE_ALIASES.get(name, ())):
            if f" {_normalize(candidate)} " in padded:
                return
    raise ValueError(f"Başlıkta tanınmış bir figürün adı yok: '{title}'")


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


def _trim(text: str, limit: int) -> str:
    text = _clean(text).rstrip(".,; ")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(".,; ")
    return text


def _finalize_image_prompt(prompt: str, style: str, identity: list[str] | None = None, setting: str = "") -> str:
    """Sabit karakter/mekan tarifleri en başa konur (görsel modeller baştaki kelimeleri daha çok dikkate alır)."""
    base = _clean(prompt).rstrip(".,; ")
    if not base:
        raise ValueError("Görsel prompt'u boş")
    base = _trim(base, MAX_BASE_IMAGE_PROMPT_CHARS if not identity and not setting else 260)
    parts = [*(identity or []), base]
    if setting:
        parts.append(setting)
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

    visuals = {_normalize(c.name): _trim(c.visual, MAX_IDENTITY_CHARS) for c in raw.characters[:3] if c.name.strip() and c.visual.strip()}
    setting = _trim(raw.setting, MAX_IDENTITY_CHARS) if raw.setting else ""
    base_seed = random.randint(1, 2_147_483_647)   # aynı hikayedeki karakterli sahneler aynı tohumla başlar
    labels_used = 0

    final_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        narration = _clean(scene.narration)
        if not narration:
            raise ValueError("Boş narration içeren sahne var")
        identity = [visuals[k] for k in (_normalize(n) for n in scene.characters_present) if k in visuals]
        used_setting = setting if scene.show_setting else ""
        label = _clean(scene.label)
        if not label or labels_used >= MAX_LABELS or index in (0, len(scenes) - 1) or len(label) > MAX_LABEL_CHARS:
            label = ""
        else:
            labels_used += 1
        entry: dict[str, Any] = {
            "narration": narration,
            "image_prompt": _finalize_image_prompt(
                scene.image_prompt, channel["visual_style_prompt"], identity, used_setting
            ),
            "label": label,
        }
        if identity or used_setting:
            entry["seed"] = base_seed
        final_scenes.append(entry)
    _check_hook(final_scenes[0]["narration"])
    question = _ensure_closing_question(final_scenes, raw.comment_question)

    total_words = sum(len(s["narration"].split()) for s in final_scenes)
    if not MIN_TOTAL_WORDS <= total_words <= MAX_TOTAL_WORDS:
        raise ValueError(
            f"Toplam kelime sayısı {total_words}; {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS} aralığı dışında"
        )

    title = _finalize_title(raw.title)
    _check_title_variety(title, get_titles(channel["id"]))
    _check_famous_title(title, channel)

    return {
        "topic_summary": topic,
        "title": title,
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
        if _overload_hits.get(model, 0) >= OVERLOAD_LIMIT:
            continue   # bu çalışmada sürekli 503/504 verdi; her videoda zaman kaybetmemek için atlanır
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
                _overload_hits.pop(model, None)
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
                        _overload_hits[model] = _overload_hits.get(model, 0) + 1
                        break
                    time.sleep(5 * 3 ** (transient_tries - 1))   # 5 sn, 15 sn
                else:
                    content_tries += 1
                    if content_tries >= CONTENT_TRIES:
                        break
        if requests_made >= MAX_REQUESTS:
            break
    raise RuntimeError(f"Senaryo üretilemedi ({requests_made} istek): {last_error}") from last_error
