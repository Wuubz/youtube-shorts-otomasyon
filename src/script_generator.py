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

from src.config_loader import (
    MAX_HISTORY_TOPICS, get_gemini_api_key, get_learned, get_themes, get_titles, get_video_count,
)
from src.visuals import (
    BADGES, DEFAULT_SCENE_TYPE, DEFAULT_SFX_FOR_TYPE, MAX_BADGES_PER_VIDEO, MAX_SPECIAL_SCENES, NO_BADGE, PLAIN_SCENE_TYPE,
    SCENE_TYPES, SFX_CUES,
)

log = logging.getLogger(__name__)

# Ücretsiz katmanda kota MODEL BAŞINA tutulur; bu yüzden birden çok model zinciri günlük kapasiteyi katlar.
# GEMINI_MODEL ile ana model değiştirilebilir. Günlük kotası dolan model o çalışmada bir daha denenmez.
# Sıra gerçek başarıya göre: 3.8 ve 3.7 son çalışmalarda hiç senaryo üretemedi (hep 503/504 ya da kota dolu),
# bu yüzden son çare olarak sona alındı.
MODEL_CHAIN = list(dict.fromkeys(
    [m for m in (
        os.environ.get("GEMINI_MODEL", "").strip(),
        "gemini-3.6-flash", "gemini-3-flash-preview", "gemini-3.5-flash",
        # lite modelleri: ayrı kota havuzu, 5-9 sn'de yanıt verir (gemini-2.5-flash artık yeni kullanıcılara kapalı: 404)
        "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest",
        "gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash",
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
# Uzunluk profilleri (channels.json `length_profile`). `target` prompt'a verilen hedef kelime aralığı (doğal konuşma
# hızında, atempo gerekmeden); `window` Gemini'nin kelime saymadaki küçük sapmalarını tolere eden doğrulama aralığı.
# "short": 25-32 sn, ~3 sn'lik sahneler (kaydırma oranını düşürmek için hızlı kesme denemesi).
LENGTH_PROFILES: dict[str, dict[str, Any]] = {
    "standard": {
        "scenes": "8 or 9", "target": (90, 115), "window": (85, 125), "edge": "6 to 12", "body": "10 to 14",
        "question": "6 to 14", "seconds": "42-50",
    },
    # Ölçüm: edge-tts + sahne arası duraklamalarla ~2 kelime/sn (81 kelime = 40,9 sn); takip çağrısı (~5 kelime) sonradan eklenir
    "short": {
        "scenes": "9 or 10", "target": (48, 58), "window": (42, 68), "edge": "5 to 8", "body": "4 to 7",
        "question": "5 to 9", "seconds": "26-32",
    },
}
DEFAULT_LENGTH_PROFILE = "standard"


def _profile(channel: dict[str, Any]) -> dict[str, Any]:
    return LENGTH_PROFILES[channel.get("length_profile", DEFAULT_LENGTH_PROFILE)]
MAX_BASE_IMAGE_PROMPT_CHARS = 450
MAX_SETTING_SCENES = 3         # sabit mekan en fazla bu kadar sahnede kullanılır
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
    scene_type: str = Field(default="", description="Motion-graphics layer for this scene: one of the allowed scene types")
    key_phrase: str = Field(default="", description="2-3 consecutive words copied EXACTLY from this scene's narration: the shock concept")
    sfx_cue: str = Field(default="", description="Sound effect fired when the scene's graphic lands: slam, marker_scratch, heartbeat, glitch, camera_shutter or deep_boom")
    floating_badge: str = Field(default="none", description="Icon floating on screen: warning, skull, galaxy, stoic_pillar, broken_heart or none")


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


def _merge_examples(static: list[str], learned: list[str]) -> list[str]:
    """Elle yazılmış örneklere öğrenilmiş başlıkları ekler. Statik girdiler notlu olabilir ('Başlık (aşırı kullanıldı)');
    bir statik girdinin başladığı öğrenilmiş başlık atlanır, böylece notla yasaklanan kalıp geri gelmez."""
    known = [_normalize(s) for s in static]
    extra = [t for t in learned if not any(k.startswith(_normalize(t)) for k in known)]
    return list(static) + extra


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
    learned = get_learned(channel["id"])
    proven = _merge_examples(channel.get("proven_examples") or [], learned.get("winners", []))
    weak = _merge_examples(channel.get("weak_examples") or [], learned.get("losers", []))
    if proven:
        lines.append("- Titles that performed BEST on this channel (copy their energy, not their topics): " + "; ".join(proven) + ".")
    if weak:
        lines.append("- Things that performed badly here (avoid them): " + "; ".join(weak) + ".")
    if "visual_mood" in direction:
        lines.append(
            f"- Visual palette: {direction['visual_mood']}. Use it ONLY as the color grade and lighting mood of the images. "
            "It is NOT a location or a subject: the place, people and objects in every image come from that scene's narration."
        )
    return "\n".join(lines) + "\n\n"


SCENE_TYPE_HELP = {
    "evidence_document": "a classified file / newspaper clipping slams onto a desk and the narrated sentence is underlined with a marker as it is spoken (use for facts, records, claims, proof)",
    "radar_hud": "a sci-fi radar / coordinates interface locks onto a target (use for detection, distance, scale, searching, tracking)",
    "mind_scan": "a neural-network / brain X-ray scan lights up (use for thoughts, emotions, manipulation, memory, behaviour)",
    "ancient_scroll": "an ancient parchment unrolls and a wax seal is stamped on it (use for myths, prophecy, oaths, old secrets, rules)",
    "dramatic_focus": "cinematic letterbox with a camera focus snapping onto the image (use for the hook, emotional peaks and the final question)",
}
BADGE_HELP = {
    "warning": "danger, a threat, a red flag",
    "skull": "death, doom, a deadly secret",
    "galaxy": "space, scale, the unknown cosmos",
    "stoic_pillar": "discipline, stoicism, strength, ancient wisdom",
    "broken_heart": "betrayal, loss, heartbreak, sacrifice",
}


def _allowed(channel: dict[str, Any], key: str, universe: tuple[str, ...]) -> list[str]:
    configured = [v for v in (channel.get(key) or []) if v in universe]
    return configured or list(universe)


def _visual_block(channel: dict[str, Any]) -> str:
    """Gemini'ye her sahne için Remotion'un çizeceği hareketli grafik katmanını seçtirir (görsel yönetmen)."""
    types = _allowed(channel, "scene_types", SCENE_TYPES)
    badges = _allowed(channel, "badges", BADGES)
    type_lines = "\n".join(f"  * {t}: {SCENE_TYPE_HELP[t]}" for t in types)
    badge_lines = "\n".join(f"  * {b}: {BADGE_HELP[b]}" for b in badges)
    return f"""
VISUAL DIRECTION (you are also the film director). The video must feel clean, calm and professional: a strong image, a simple caption, and a motion graphic ONLY at the few beats where it truly proves the point. Most scenes get no graphic at all, so choose carefully. For EVERY scene fill these fields:
- scene_type: the graphic layer that best proves what the narration says. At most {MAX_SPECIAL_SCENES} scenes of the whole video will keep their layer (the strongest beats in the middle of the story, never the first or the last scene), the others are shown as clean images. Allowed layers:
{type_lines}
- key_phrase: the 2 or 3 consecutive words of THIS scene's narration that carry the point (a number, a forbidden concept, the twist). Copy them EXACTLY as written in the narration, in the same order.
- sfx_cue: the soft sound that plays when the scene's graphic lands. One of: slam (files and facts), marker_scratch (underlining a claim), heartbeat (fear, tension, emotion), glitch (anomaly, corruption, digital), camera_shutter (a revealing moment), deep_boom (a huge reveal, doom).
- floating_badge: an icon for the single strongest emotional beat of the video (at most {MAX_BADGES_PER_VIDEO} scene), otherwise "none". Allowed icons:
{badge_lines}
  * none: no icon
"""


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


def _bible_block(channel: dict[str, Any]) -> str:
    """Hikaye kahramanlı kanallarda (mitoloji, korku, hayvan hikayeleri) sabit karakter; diğerlerinde her sahne farklı görsel."""
    if not channel.get("story_bible"):
        return (
            "NO RECURRING CHARACTER OR LOCATION: this channel explains real events and ideas, so every scene must show "
            "something DIFFERENT that illustrates its own sentence. Leave `characters` as an empty list and `setting` as "
            "an empty string, leave `characters_present` empty and `show_setting` false in every scene, and put everything "
            "the image needs (people, place, objects, action, framing, lighting) directly in that scene's image_prompt.\n"
        )
    return f"""STORY BIBLE (this story has a protagonist, so the viewer must recognise the same character):
- characters: 0 to 2 recurring characters, animals or key objects that appear in several scenes. Give each a short name and one fixed `visual` description (max 20 words: species/age/build, exact colors, markings, clothing, one distinguishing feature).
- setting: one fixed description (max 25 words) of the main location, used only in scenes that really take place there (at most {MAX_SETTING_SCENES} scenes); every other scene is set in a different place that fits its narration.
- In each scene's image_prompt write the action, framing, lighting and any NEW place or objects. Do NOT redescribe the characters or the setting there; list the exact names of everyone visible in `characters_present` and set `show_setting` to true only when the fixed location is visible. Scenes where the character is not on screen (a close-up of an object, a landscape, a crowd, a symbol) must not list them. The code inserts the fixed descriptions word for word.
"""


def _build_prompt(channel: dict[str, Any], past_topics: list[str], direction: dict[str, str]) -> str:
    history_block = "\n".join(f"- {t}" for t in past_topics[-MAX_HISTORY_TOPICS:]) or "- (none yet)"
    recent_block = "\n".join(f"- {t}" for t in past_topics[-RECENT_SUBJECTS_SHOWN:]) or "- (none yet)"
    titles_block = "\n".join(f"- {t}" for t in get_titles(channel["id"])[-15:]) or "- (none yet)"
    profile = _profile(channel)
    pace_rule = ""
    if channel.get("length_profile") == "short":
        pace_rule = (
            " FAST CUTS: every scene is ONE punchy beat (one short sentence, never a second clause) and the image changes "
            "every ~3 seconds, so cut every explanation to its sharpest words. No filler, no recap, no throat-clearing."
        )
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
- Scene 1 narration is {profile['edge']} words. It must open a curiosity loop the viewer can only close by watching to the end: state a shocking, impossible or contradictory fact, or a direct challenge, but WITHHOLD the explanation until the last third of the video.
- Start in the middle of the action with the most surprising thing. No greeting, no throat-clearing. Never begin with: "Did you know", "Imagine", "Have you ever", "In this video", "Today", "Welcome", "Let me tell you", "Ever wondered".
- Use at least one of: a precise number or date, a direct "you", a vivid forbidden or impossible concept, a belief of the viewer that you contradict.
- The hook must be honest: the video must actually deliver what the hook promises.

RETENTION STRUCTURE:
- Scene 2 raises the stakes or adds a second question. Scenes 3-6 escalate with micro-cliffhangers ("But that is not the strange part.", "And then it got worse."). Re-hook around scene 5 or 6 so a viewer who was about to leave stays. Reveal the payoff in the second-to-last scene.
- End scenes on tension that pulls into the next scene; do not wrap up early.

THE ENDING (last scene) = COMMENT BAIT:
- The last scene narration must be a short, specific question about THIS video's topic that is effortless to answer in a comment, ideally a choice between two options or a "Would you...?" opinion. {profile['question']} words, ends with a question mark. Never generic ("What do you think?"). Do NOT add a follow or subscribe line; the code appends it.
- Put exactly that question in the comment_question field as well.

SCRIPT RULES:
- Use {profile['scenes']} scenes. LENGTH IS STRICT: the total narration across all scenes must be between {profile['target'][0]} and {profile['target'][1]} words (never more). Scene 1 and the last scene are short ({profile['edge']} words); the other scenes are {profile['body']} words each. Count the words before answering. The voiceover is read at natural speed, so the video must finish in about {profile['seconds']} seconds without any speed-up.{pace_rule}
- Write like a gifted human storyteller or podcast host talking to one listener, NOT like an encyclopedia. Use natural pauses: commas, dashes and short punchy sentences ("Seven seconds. That's all it took."). Vary sentence length, use contractions and a warm conversational voice. Avoid lists of facts, stiff textbook phrasing and long sub-clauses.
- Original storytelling only. No real-person defamation, no graphic gore, no hate speech, no medical or political misinformation. Keep it advertiser-friendly.
- Do not use emojis, hashtags, stage directions or scene numbers inside narration.

{_bible_block(channel)}
ON-SCREEN LABELS:
- label: a 1 to 4 word caption shown on screen when the time or place changes, for example "Kyoto, 1185", "4 years later", "That night", "Deep below". Leave it as an empty string otherwise. Use at most 3 labels per video, never in scene 1 or the last scene.

IMAGE PROMPT RULES (one per scene, matching that scene's narration):
- THE IMAGES TELL THE STORY. Each image illustrates exactly what that scene's narration says, at the moment it is spoken, so a viewer who only watched the pictures would still follow the story and can easily picture it. Name the concrete things the sentence mentions: the people (how many, what they do), the place, the objects, the creature, the event. Example: "five strangers sit in a waiting room while grey smoke seeps from a wall vent" for a sentence about a smoke experiment, never a generic foggy road.
- EVERY scene shows a DIFFERENT subject, place or action from the previous scenes. Never reuse the same background, the same character pose or the same composition in two scenes. If the narration is abstract (a psychological rule, a feeling), dramatize it as a concrete situation a viewer can recognise (a person staring at a phone in an empty office, a crowd where one person looks away) or show the real object or place it refers to.
- Real events, people, places and dates in the narration must look like that era and place (clothing, architecture, technology).
- English, at most 60 words, highly descriptive: subject, setting, lighting, mood, camera angle.
- Scene 1's image is the thumbnail frame that stops the scroll: ONE bold focal subject that shows the shocking fact itself, strong contrast and rim light.
- Vary the shot type (extreme close-up, wide establishing shot, low angle, over-the-shoulder, aerial, macro); never the same framing in two consecutive scenes.
- Keep the main subject in the upper two thirds of the frame and the bottom third visually calm and darker, because subtitles are placed there.
- The last image should visually echo the closing question (a door left open, two paths, an unanswered choice).
- Vertical 9:16 composition. Absolutely NO text, letters, numbers, captions, logos or watermarks in the image.
- Keep one consistent color palette and lighting style across the video, but the content of each image must change.
- Motion graphics (documents, radar, scans, scrolls, icons) are drawn OVER a few scenes and the image is darkened there, so the image stays a strong readable scene, not a busy collage.
{_visual_block(channel)}
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
        if age < 8 and len(words) >= 4 and len(old_words) >= 4 and words[-4:] == old_words[-4:]:
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


def _finalize_description(description: str, question: str = "", pitch: str = "") -> str:
    text = (description or "").strip()
    if not text:
        raise ValueError("Açıklama boş")
    if "#shorts" not in text.lower():
        text = f"{text}\n\n#Shorts"
    header = []
    if question:
        header.append(f"💬 {question}")      # yorum sorusu açıklamanın ilk satırı
    if pitch:
        header.append(f"🔔 {pitch} - subscribe so you never miss one.")
    if header:
        text = "\n".join(header) + f"\n\n{text}"
    return text[:4900]


def _add_series_and_follow(final_scenes: list[dict[str, Any]], channel: dict[str, Any]) -> None:
    """Seri etiketini (ör. 'Rule #17') ilk sahnenin üst etiketine, takip çağrısını son sahnenin sesine ekler.

    Kelime sınırı denetiminden sonra çalışır: çağrı Gemini'nin kelime bütçesinden sayılmaz.
    """
    series_tag = channel.get("series_tag")
    if series_tag:
        final_scenes[0]["label"] = series_tag.format(n=get_video_count(channel["id"]) + 1)
    follow_lines = channel.get("follow_lines") or []
    if follow_lines:
        final_scenes[-1]["narration"] = f"{final_scenes[-1]['narration']} {random.choice(follow_lines)}"


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


def _bare_word(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def _strip_edges(word: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", word)


def _find_key_phrase(narration: str, phrase: str) -> str:
    """Anahtar ifadenin anlatım metninde ardışık kelimeler olarak geçtiğini doğrular. Bulunamazsa en güçlü tek kelimeyi seçer
    (rakam, yoksa en uzun anlamlı kelime): ifade ekranda, o kelime söylenirken vurulur."""
    words = [w for w in narration.split() if w]
    bare = [_bare_word(w) for w in words]
    target = [_bare_word(w) for w in phrase.split() if _bare_word(w)][:4]
    n = len(target)
    if n:
        for i in range(len(bare) - n + 1):
            if bare[i:i + n] == target:
                return " ".join(_strip_edges(w) for w in words[i:i + n])
    candidates = [w for w in words if _bare_word(w) and _bare_word(w) not in _TITLE_STOPWORDS]
    digits = [w for w in candidates if any(c.isdigit() for c in w)]
    if digits:
        return _strip_edges(digits[0])
    return _strip_edges(max(candidates, key=lambda w: len(_bare_word(w)))) if candidates else ""


def _resolve_visuals(
    scene: Scene, narration: str, index: int, count: int, channel: dict[str, Any], state: dict[str, Any],
) -> dict[str, Any]:
    """Gemini'nin görsel yönetmen alanlarını doğrular ve düzeltir. Geçersiz değer videoyu düşürmez; kanal için geçerli
    varsayılana çekilir (istek bütçesi boşa harcanmasın)."""
    types = _allowed(channel, "scene_types", SCENE_TYPES)
    badges = _allowed(channel, "badges", BADGES)

    scene_type = _clean(scene.scene_type).lower().replace(" ", "_")
    if scene_type not in types:
        scene_type = types[index % len(types)]
    if index in (0, count - 1) and DEFAULT_SCENE_TYPE in types:     # kanca ve kapanış: görselin kendisi öne çıkar
        scene_type = DEFAULT_SCENE_TYPE
    elif scene_type == state.get("prev_type") and len(types) > 1:   # art arda aynı katman yok
        scene_type = types[(types.index(scene_type) + 1) % len(types)]
    state["prev_type"] = scene_type

    cue = _clean(scene.sfx_cue).lower().replace(" ", "_").replace("-", "_")
    if cue not in SFX_CUES or (cue == "deep_boom" and state.get("boom_used")):
        cue = DEFAULT_SFX_FOR_TYPE[scene_type]
    if cue == "deep_boom":
        state["boom_used"] = True

    badge = _clean(scene.floating_badge).lower().replace(" ", "_")
    if badge not in badges or state.get("badges_used", 0) >= MAX_BADGES_PER_VIDEO:
        badge = NO_BADGE
    if badge != NO_BADGE:
        state["badges_used"] = state.get("badges_used", 0) + 1

    return {
        "scene_type": scene_type,
        "key_phrase": _find_key_phrase(narration, scene.key_phrase),
        "sfx_cue": cue,
        "floating_badge": badge,
    }


def _thin_visuals(scenes: list[dict[str, Any]]) -> None:
    """Sadelik kuralı: Gemini'nin seçtiği katmanlardan yalnızca en fazla MAX_SPECIAL_SCENES tanesi korunur (ortadaki
    sahneler, aralarında boşluk, her tür bir kez); diğer tüm sahneler grafiksiz 'plain' olur. Rozet de yalnızca
    korunan sahnelerden birinde kalır. Böylece her videoda aynı animasyonlar art arda tekrarlanmaz."""
    count = len(scenes)
    middle = range(1, count - 1)                                  # kanca ve kapanış sahnesi her zaman sade
    wanted = {i: scenes[i]["scene_type"] for i in middle if scenes[i]["scene_type"] in SCENE_TYPES}
    chosen: list[int] = []
    for target in (count * 0.34, count * 0.70)[:MAX_SPECIAL_SCENES]:
        pool = [
            i for i in wanted
            if i not in chosen
            and all(abs(i - c) > 1 and wanted[i] != wanted[c] for c in chosen)
        ]
        # dramatic_focus (sinema şeridi) yalnızca başka seçenek kalmadıysa kullanılır
        preferred = [i for i in pool if wanted[i] != DEFAULT_SCENE_TYPE] or pool
        if preferred:
            chosen.append(min(preferred, key=lambda i: (abs(i - target), random.random())))

    badge_kept = False
    for index, scene in enumerate(scenes):
        if index in chosen:
            if scene["floating_badge"] != NO_BADGE and not badge_kept:
                badge_kept = True
            else:
                scene["floating_badge"] = NO_BADGE
            continue
        scene.update(scene_type=PLAIN_SCENE_TYPE, key_phrase="", sfx_cue="", floating_badge=NO_BADGE)


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
    raw: VideoScript, channel: dict[str, Any], past_topics: list[str], direction: dict[str, str] | None = None,
    strict_titles: bool = True,
) -> dict[str, Any]:
    topic = _clean(raw.topic_summary)[:200]
    if not topic:
        raise ValueError("topic_summary boş")
    if _normalize(topic) in {_normalize(t) for t in past_topics}:
        raise ValueError(f"Konu daha önce işlenmiş: {topic}")

    scenes = raw.scenes[:MAX_SCENES]
    if len(scenes) < MIN_SCENES:
        raise ValueError(f"Yetersiz sahne sayısı: {len(scenes)} (en az {MIN_SCENES})")

    bible = bool(channel.get("story_bible"))
    visuals = (
        {_normalize(c.name): _trim(c.visual, MAX_IDENTITY_CHARS) for c in raw.characters[:2] if c.name.strip() and c.visual.strip()}
        if bible else {}
    )
    setting = _trim(raw.setting, MAX_IDENTITY_CHARS) if raw.setting and bible else ""
    setting_scenes = 0
    base_seed = random.randint(1, 2_147_483_647)   # aynı hikayedeki karakterli sahneler aynı tohumla başlar
    labels_used = 0
    visual_state: dict[str, Any] = {}

    final_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        narration = _clean(scene.narration)
        if not narration:
            raise ValueError("Boş narration içeren sahne var")
        identity = [visuals[k] for k in (_normalize(n) for n in scene.characters_present) if k in visuals]
        used_setting = setting if scene.show_setting and setting_scenes < MAX_SETTING_SCENES else ""
        setting_scenes += 1 if used_setting else 0
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
            **_resolve_visuals(scene, narration, index, len(scenes), channel, visual_state),
        }
        if identity or used_setting:
            entry["seed"] = base_seed
        final_scenes.append(entry)
    _thin_visuals(final_scenes)
    _check_hook(final_scenes[0]["narration"])
    question = _ensure_closing_question(final_scenes, raw.comment_question)

    total_words = sum(len(s["narration"].split()) for s in final_scenes)
    min_words, max_words = _profile(channel)["window"]
    if not min_words <= total_words <= max_words:
        raise ValueError(f"Toplam kelime sayısı {total_words}; {min_words}-{max_words} aralığı dışında")

    title = _finalize_title(raw.title)
    try:
        _check_title_variety(title, get_titles(channel["id"]))
        _check_famous_title(title, channel)
    except ValueError as exc:
        if strict_titles:
            raise
        # Başlık kalitesi kontrolü yüzünden kanal video kaçırmamalı: istek bütçesi yarılanınca uyarıyla kabul edilir
        log.warning("[%s] Başlık kontrolü gevşetildi, başlık kabul edildi: %s", channel["id"], exc)

    _add_series_and_follow(final_scenes, channel)
    return {
        "topic_summary": topic,
        "title": title,
        "description": _finalize_description(raw.description, question, channel.get("series_pitch", "")),
        "tags": _finalize_tags(raw.tags),
        "comment_question": question,
        "sub_theme": (direction or {}).get("sub_theme"),
        "scenes": final_scenes,
    }


def _classify_error(exc: Exception) -> tuple[str, float]:
    """Hatayı ('daily_quota' | 'rate_limit' | 'transient' | 'content', bekleme sn) olarak sınıflar."""
    text = str(exc)
    if "404" in text and "NOT_FOUND" in text:   # model kaldırılmış ya da yeni kullanıcılara kapalı: tekrar denemenin anlamı yok
        return "daily_quota", 0.0
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
                script = _finalize(
                    VideoScript.model_validate_json(response.text), channel, past_topics, direction,
                    strict_titles=requests_made <= MAX_REQUESTS // 2,
                )
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
