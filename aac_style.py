"""Automatic AAC illustration style prompt builder (v15.24).

The ONE place the permanent illustration style lives. Nothing else in the
app duplicates these strings: the SD Turbo pipeline (aac_ai.sd_generate_png)
and the AI tab's Advanced section both read them from here.

A finished prompt is assembled in exactly this order:
    1. subject description — one concrete thing to draw
    2. permanent illustration style (below)
    3. composition and background instructions (below)
and the negative prompt travels separately to the image model.

v15.25: everything is distilled to fit SD Turbo's ~77-token text window.
The subject stays first (it always survives); the style keeps only the
highest-impact cues, and the negative keeps only the bans that matter,
front-loaded so a cut tail costs the least.

The picture itself is ONLY the illustration: no word label, no circular
tile border, no interface, no selection highlight, no card layout. AAC Tag
Studio adds those around the picture afterwards, so they must never be
asked of the image model (and the negative prompt forbids them too).

Subject descriptions come from the built-in dictionary first (fast, fully
offline). For a word the dictionary does not know, the caller may pass the
existing text model as `text_describer` (aac_ai.visual_subject); when the
mode is "dictionary" the model is never consulted, and when it is
unavailable the word itself is the fallback — never the internet.
"""

import re

STYLE_NAME = "AAC pastel"

# Permanent illustration style — the user's supplied style text, verbatim
# in substance (split here only so the composition block can follow it).
DEFAULT_STYLE_PROMPT = (
    "Soft pastel 3D cartoon AAC educational symbol, friendly, child-appropriate, "
    "instantly recognizable, rounded simplified forms, gentle soft shading, thin dark "
    "brown outlines, pastel palette of sage, butter yellow, peach, sky blue and lavender."
)

# Composition and background instructions — always last in the prompt.
DEFAULT_COMPOSITION = (
    "Single centered subject, plain warm ivory background, generous empty space, "
    "square composition, no text."
)

DEFAULT_NEGATIVE_PROMPT = (
    "photorealistic, hyperrealism, scary, aggressive, harsh shadows, "
    "neon colors, oversaturated colors, busy background, multiple objects, collage, "
    "circular frame, decorative border, button interface, text, letters, words, "
    "watermark, cropped subject, subject touching edges, blurry, low resolution, "
    "excessive texture, sharp angular shapes, overly complex details, distorted anatomy, "
    "messy linework, inconsistent style, flat monochrome silhouette"
)

SUBJECT_MODE_MODEL = "model"          # dictionary, then the text model when available
SUBJECT_MODE_DICTIONARY = "dictionary"  # dictionary only — never the text model
SUBJECT_MODES = (SUBJECT_MODE_MODEL, SUBJECT_MODE_DICTIONARY)


# v15.25: the 15.24 defaults were far longer than SD Turbo's text window
# (~77 tokens), so only their opening ever reached the model. The distilled
# defaults above are what ships now. These originals are kept for one reason:
# a saved 15.24 setting that is byte-identical to them was never a personal
# choice, so loading it upgrades it to the distilled default (see from_dict).
LEGACY_STYLE_PROMPT_1524 = (
    "Soft pastel 3D cartoon illustration for an AAC (Augmentative and Alternative "
    "Communication) educational picture symbol. Friendly, child-appropriate, instantly "
    "recognizable subject, rounded simplified forms, smooth sculpted appearance, subtle "
    "hand-drawn details, gentle soft shading, delicate highlights, soft ambient occlusion, "
    "thin-to-medium dark brown outlines, warm cream and ivory tones, muted pastel color "
    "palette of sage green, butter yellow, peach, sky blue and lavender. Slightly tactile "
    "illustrated finish, polished children's educational illustration, charming and "
    "approachable, high visual clarity, strong recognizable silhouette, balanced "
    "proportions, clean professional icon illustration."
)
LEGACY_NEGATIVE_PROMPT_1524 = (
    "photorealistic, realistic photography, hyperrealism, dark horror style, scary, "
    "aggressive, gritty, harsh shadows, high contrast, neon colors, oversaturated colors, "
    "busy background, detailed scenery, multiple unrelated objects, collage, multiple "
    "panels, circular frame, decorative border, badge, button interface, text, letters, "
    "words, captions, watermark, signature, logo, cropped subject, subject touching edges, "
    "cut off objects, blurry, low resolution, noisy image, excessive texture, sharp "
    "angular shapes, overly complex details, distorted anatomy, deformed objects, "
    "duplicate objects, messy linework, inconsistent illustration style, flat monochrome "
    "silhouette"
)

# SD Turbo's text encoder window. Prompts are assembled subject-first so the
# subject always survives; anything past this budget is cut from the tail.
MAX_PROMPT_TOKENS = 77


def estimate_tokens(text):
    """Rough CLIP-style token count for `text` (words, plus one extra for
    each long word that usually splits into subwords). Approximate — the
    real encoder differs — but good enough to check a prompt fits its budget."""
    words = re.findall(r"[A-Za-z0-9']+", str(text))
    return len(words) + sum(1 for w in words if len(w) > 7)


def _norm(word):
    """Dictionary key: lowercased, apostrophes dropped, spaces collapsed."""
    w = str(word).strip().lower().replace("\u2019", "").replace("'", "")
    return re.sub(r"\s+", " ", w)


# Built-in subject descriptions for common AAC vocabulary. One concrete,
# single-subject picture each; the style block (not the description) carries
# the look, so descriptions stay short and visual.
SUBJECT_DESCRIPTIONS = {
    # Cody's Maker words + everyday core vocabulary
    "now": "a round clock with bold hands",
    "dinner": "a dinner plate with chicken, carrots and potatoes",
    "stop": "a red octagonal stop sign",
    "listen": "a child listening carefully with one hand behind their ear",
    "next": "a bold green arrow pointing to the right",
    "play": "a child playing with colorful building blocks",
    "home": "a cozy little house with a red roof and a door",
    "alligator": "a friendly green alligator with a big smile",
    "i made a mistake": "a child looking sheepish next to a toppled block tower",
    "i need help": "a child raising one hand asking for help",
    "i need a break": "a child resting calmly on a soft chair",
    "i need space": "a child sitting alone peacefully with space around them",
    "again": "a child clapping again with a smile",
    # food
    "eat": "a child eating from a bowl with a spoon",
    "drink": "a child drinking from a cup",
    "apple": "a shiny red apple with a green leaf",
    "banana": "a curved yellow banana",
    "lunch": "a lunchbox with a sandwich and an apple",
    "breakfast": "a bowl of cereal with milk and a spoon",
    "snack": "a small bowl of crackers",
    "water": "a clear glass of water",
    "milk": "a glass of milk",
    "hungry": "a child holding their tummy, looking hungry",
    "thirsty": "a child drinking water, looking thirsty",
    # actions
    "run": "a child running happily",
    "jump": "a child jumping with both arms up",
    "read": "a child reading an open book",
    "sleep": "a child sleeping peacefully in bed",
    "go": "a green arrow pointing forward",
    "come": "a child waving someone over",
    "help": "a child helping a friend up",
    "look": "a child looking with wide curious eyes",
    "sit": "a child sitting on a chair",
    "stand": "a child standing tall",
    "walk": "a child walking",
    "sing": "a child singing happily with musical notes",
    "draw": "a child drawing a picture with crayons",
    "wash": "a child washing their hands with soap and water",
    # animals
    "dog": "a friendly brown dog wagging its tail",
    "cat": "a soft orange cat sitting",
    "bird": "a small blue bird perched on a branch",
    "fish": "a bright orange goldfish swimming",
    "bear": "a cuddly brown teddy bear",
    "lion": "a friendly lion with a big mane",
    "horse": "a gentle brown horse",
    "elephant": "a grey elephant with a long trunk",
    "monkey": "a playful brown monkey",
    "animals": "a friendly farm cow",
    # emotions
    "happy": "a smiling child's face, happy",
    "sad": "a child's face with a sad frown and a tear",
    "angry": "a child's face looking angry with crossed arms",
    "scared": "a frightened child's face with wide eyes",
    "excited": "a child jumping with both arms up and a big smile",
    "tired": "a sleepy child's face yawning",
    "silly": "a giggling child making a funny face",
    "calm": "a calm child with closed eyes and a gentle smile",
    "frustrated": "a child with a frustrated frown, hands on cheeks",
    "proud": "a proud child standing tall with a big smile",
    # objects and places
    "school": "a small school building with a flag",
    "bathroom": "a toilet and a sink",
    "book": "an open picture book",
    "ball": "a bright striped ball",
    "car": "a small red car",
    "bed": "a cozy bed with a pillow and blanket",
    "chair": "a simple wooden chair",
    "toy": "a colorful spinning top toy",
    "phone": "a simple smartphone",
    "computer": "a small laptop computer",
    "kitchen": "a kitchen counter with a stove and a pot",
    "outside": "a child playing outside in the sunshine",
    "inside": "a child playing inside a cozy room",
    # people
    "mom": "a kind mother smiling",
    "dad": "a kind father smiling",
    "friend": "a smiling child waving to a friend",
    "teacher": "a friendly teacher holding a book",
    "me": "a smiling child pointing to themselves",
    "you": "a smiling child pointing at you",
    # abstract and everyday concepts
    "more": "two apples next to one apple",
    "all done": "a child with hands up, all finished",
    "yes": "a smiling child nodding yes",
    "no": "a child shaking their head no",
    "please": "a child with hands together asking politely",
    "thank you": "a smiling child giving a thumbs up",
    "want": "a child reaching for a toy they want",
    "need": "a child pointing to what they need",
    "like": "a smiling child hugging a favorite toy",
    "dont like": "a child pushing something away with a frown",
    "big": "a very big ball next to a tiny ball",
    "little": "a tiny mouse",
    "hot": "a steaming hot bowl",
    "cold": "a child in a warm coat in the snow",
    "fast": "a child running very fast with speed lines",
    "slow": "a turtle walking slowly",
    "today": "a calendar page showing today",
    "tomorrow": "a calendar with an arrow to tomorrow",
    "sorry": "a child saying sorry with a sincere face",
}


class PromptResult:
    """One assembled prompt: positive + negative kept separate."""

    def __init__(self, word, subject, positive, negative, source):
        self.word = word
        self.subject = subject
        self.positive = positive
        self.negative = negative
        self.source = source  # dictionary | text_model | subject_given | word
        self.estimated_tokens = estimate_tokens(positive)
        self.estimated_negative_tokens = estimate_tokens(negative)

    def __repr__(self):
        return f"PromptResult(word={self.word!r}, source={self.source!r})"


class StyleConfig:
    """Editable illustration-style configuration (persisted in settings.json)."""

    def __init__(self, style_prompt=None, negative_prompt=None, subject_mode=None):
        self.style_prompt = style_prompt or DEFAULT_STYLE_PROMPT
        self.negative_prompt = negative_prompt if negative_prompt is not None else DEFAULT_NEGATIVE_PROMPT
        self.subject_mode = subject_mode if subject_mode in SUBJECT_MODES else SUBJECT_MODE_MODEL


    @classmethod
    def from_dict(cls, d):
        d = d or {}
        # v15.25 migration: a saved prompt identical to the 15.24 default was
        # never customized, so it upgrades to the distilled default instead
        # of silently keeping the over-long text.
        sp = d.get("style_prompt")
        if sp == LEGACY_STYLE_PROMPT_1524:
            sp = None
        neg = d.get("negative_prompt")
        if neg == LEGACY_NEGATIVE_PROMPT_1524:
            neg = None
        return cls(style_prompt=sp or None,
                   negative_prompt=neg,
                   subject_mode=d.get("subject_mode"))

    def reset(self):
        self.style_prompt = DEFAULT_STYLE_PROMPT
        self.negative_prompt = DEFAULT_NEGATIVE_PROMPT
        self.subject_mode = SUBJECT_MODE_MODEL


_CONFIG = StyleConfig()


def get_config():
    return _CONFIG


def set_config(config):
    global _CONFIG
    _CONFIG = config if isinstance(config, StyleConfig) else StyleConfig.from_dict(config)


def reset_config():
    _CONFIG.reset()
    return _CONFIG


def subject_description(word, text_describer=None, config=None):
    """One concrete description for `word`, and where it came from.

    Dictionary first; then the text model when the mode allows and a
    describer was supplied; otherwise the word itself. Never raises for an
    unknown word, and never needs the internet.
    """
    word = "" if word is None else str(word).strip()
    if not word:
        raise ValueError("No word given — type a word first.")
    cfg = config or _CONFIG
    hit = SUBJECT_DESCRIPTIONS.get(_norm(word))
    if hit:
        return hit, "dictionary"
    if cfg.subject_mode == SUBJECT_MODE_MODEL and text_describer is not None:
        try:
            desc = str(text_describer(word) or "").strip()
        except Exception:
            desc = ""
        if desc and desc.lower() != word.lower():
            return desc, "text_model"
    return word, "word"


def build_prompt(word, subject=None, config=None, text_describer=None):
    """Assemble the full prompt for `word`, in the required order:
    subject description, permanent style, composition/background. The
    negative prompt is returned separately, never mixed into the positive.
    An explicitly given `subject` is used verbatim (it already describes
    the picture); an empty word is rejected before anything is generated.
    """
    word = "" if word is None else str(word).strip()
    if not word:
        raise ValueError("No word given — type a word first.")
    cfg = config or _CONFIG
    if subject and str(subject).strip():
        desc, source = str(subject).strip(), "subject_given"
    else:
        desc, source = subject_description(word, text_describer=text_describer, config=cfg)
    positive = f"{desc}, {cfg.style_prompt} {DEFAULT_COMPOSITION}"
    return PromptResult(word, desc, positive, cfg.negative_prompt, source)
