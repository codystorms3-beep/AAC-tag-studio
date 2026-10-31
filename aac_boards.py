"""AAC boards, category colours, topic packs, offline pack (v2 experimental).
Uses only packages already in requirements (matplotlib, Pillow, numpy).
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # v14.8: PDFs are built on a worker thread - never use a GUI (Tk) backend there
from matplotlib.backends.backend_pdf import PdfPages as _RawPdfPages
from matplotlib import pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

from aac_common import APP_VERSION, ICON_DIR, log
import aac_core as core


class PdfPages:
    """v14.8: drop-in PdfPages that writes to a temporary file and only swaps it
    into place when every page was saved. A failed or cancelled export can no
    longer leave a half-written (corrupt) PDF, or destroy a good one of the
    same name. A locked target (PDF open in a viewer) gives a plain message."""

    def __init__(self, path, *a, **k):
        self._final = Path(path)
        self._tmp = self._final.with_name(f".{self._final.stem}.{os.getpid()}.part.pdf")
        self._final.parent.mkdir(parents=True, exist_ok=True)
        self._pdf = _RawPdfPages(self._tmp, *a, **k)

    def savefig(self, *a, **k):
        return self._pdf.savefig(*a, **k)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._pdf.close()
        except Exception:
            if exc_type is None:
                raise
        if exc_type is not None:
            try:
                plt.close("all")   # figures left open by a failed page
            except Exception:
                pass
            try:
                self._tmp.unlink()
            except OSError:
                pass
            return False
        try:
            os.replace(self._tmp, self._final)
        except PermissionError:
            try:
                self._tmp.unlink()
            except OSError:
                pass
            raise PermissionError(
                f"Can't save '{self._final.name}' - it is open in another program "
                "(a PDF viewer?). Close it, or pick a different file name, and try again.")
        return False


# Fitzgerald-key style category colours (body tint suggestions)
CATEGORIES = {
    "People": "#F5E663",       # yellow
    "Actions": "#7BC67E",      # green
    "Nouns": "#F4A261",        # orange
    "Animals": "#C68B59",      # warm brown — v15.64: its own theme, not Nouns
    "Descriptors": "#6EC6FF",  # blue
    "Social": "#F2A7C3",       # pink
    "Little words": "#D0D0D0", # grey
    "Places": "#C3B1E1",       # purple
    "Feelings": "#FF8A80",     # coral
    "Uncategorized": "#F3E6DC",
}

# word (lowercase) -> category name
WORD_CATEGORY = {
    # people
    "i": "People", "me": "People", "my": "People", "you": "People", "he": "People",
    "she": "People", "we": "People", "they": "People", "mom": "People", "mum": "People",
    "mother": "People", "dad": "People", "father": "People", "friend": "People",
    "teacher": "People", "brother": "People", "sister": "People", "baby": "People",
    "boy": "People", "girl": "People", "man": "People", "woman": "People",
    "doctor": "People", "nurse": "People", "grandma": "People", "grandpa": "People",
    # actions
    "go": "Actions", "stop": "Actions", "come": "Actions", "wait": "Actions",
    "look": "Actions", "listen": "Actions", "see": "Actions", "play": "Actions",
    "work": "Actions", "eat": "Actions", "drink": "Actions", "sleep": "Actions",
    "wash": "Actions", "open": "Actions", "close": "Actions", "give": "Actions",
    "take": "Actions", "put": "Actions", "get": "Actions", "make": "Actions",
    "read": "Actions", "write": "Actions", "draw": "Actions", "sing": "Actions",
    "dance": "Actions", "run": "Actions", "walk": "Actions", "sit": "Actions",
    "stand": "Actions", "jump": "Actions", "help": "Actions", "want": "Actions",
    "need": "Actions", "like": "Actions", "share": "Actions",
    "choose": "Actions", "find": "Actions", "show": "Actions", "tell": "Actions",
    "ask": "Actions", "start": "Actions", "finish": "Actions", "finished": "Actions",
    # nouns
    "food": "Nouns", "water": "Nouns", "juice": "Nouns", "milk": "Nouns",
    "cookie": "Nouns", "apple": "Nouns", "banana": "Nouns", "pizza": "Nouns",
    "book": "Nouns", "toy": "Nouns", "ball": "Nouns", "phone": "Nouns",
    "computer": "Nouns", "tv": "Nouns", "music": "Nouns", "game": "Nouns",
    "car": "Nouns", "bus": "Nouns", "bike": "Nouns", "dog": "Animals", "cat": "Animals",
    "shirt": "Nouns", "shoes": "Nouns", "hat": "Nouns", "bag": "Nouns", "key": "Nouns",
    # descriptors
    "happy": "Feelings", "sad": "Feelings", "angry": "Feelings", "scared": "Feelings",
    "tired": "Feelings", "sick": "Feelings", "hurt": "Feelings", "excited": "Feelings",
    "bored": "Feelings", "calm": "Feelings", "good": "Descriptors", "bad": "Descriptors",
    "hot": "Descriptors", "cold": "Descriptors", "more": "Descriptors", "big": "Descriptors",
    "little": "Descriptors", "loud": "Descriptors", "quiet": "Descriptors",
    # social
    "yes": "Social", "no": "Social", "please": "Social", "thank you": "Social",
    "thanks": "Social", "sorry": "Social", "hello": "Social", "goodbye": "Social",
    "hi": "Social", "bye": "Social", "help please": "Social", "break": "Social",
    "all done": "Social", "done": "Social",
    # little words
    "and": "Little words", "the": "Little words", "a": "Little words", "to": "Little words",
    "in": "Little words", "on": "Little words", "it": "Little words", "this": "Little words",
    "that": "Little words", "what": "Little words", "where": "Little words",
    "when": "Little words", "who": "Little words", "why": "Little words", "how": "Little words",
    "don't": "Little words", "not": "Little words", "now": "Little words", "later": "Little words",
    "again": "Little words", "then": "Little words",
    # places
    "home": "Places", "school": "Places", "bathroom": "Places", "outside": "Places",
    "inside": "Places", "kitchen": "Places", "bedroom": "Places", "park": "Places",
    "store": "Places", "hospital": "Places", "library": "Places",
    "playground": "Places", "classroom": "Places", "office": "Places",
    "church": "Places", "zoo": "Places", "beach": "Places", "pool": "Places",
    "restaurant": "Places", "cafe": "Places", "garden": "Places",
    # more people
    "person": "People", "people": "People", "kid": "People", "child": "People",
    "children": "People", "family": "People", "uncle": "People", "aunt": "People",
    "cousin": "People", "neighbor": "People", "neighbour": "People",
    "therapist": "People", "aide": "People", "helper": "People",
    # more actions
    "push": "Actions", "pull": "Actions", "turn": "Actions", "move": "Actions",
    "bring": "Actions", "carry": "Actions", "hold": "Actions", "drop": "Actions",
    "throw": "Actions", "catch": "Actions", "kick": "Actions", "point": "Actions",
    "touch": "Actions", "feel": "Actions", "smell": "Actions", "taste": "Actions",
    "hear": "Actions", "speak": "Actions", "talk": "Actions", "call": "Actions",
    "cook": "Actions", "brush": "Actions", "comb": "Actions",
    "dress": "Actions", "wear": "Actions", "buy": "Actions", "pay": "Actions",
    "count": "Actions", "color": "Actions", "colour": "Actions", "paint": "Actions",
    "build": "Actions", "fix": "Actions", "cut": "Actions", "paste": "Actions",
    "swipe": "Actions", "tap": "Actions", "click": "Actions", "scroll": "Actions",
    "try": "Actions", "practice": "Actions", "learn": "Actions", "teach": "Actions",
    "remember": "Actions", "forget": "Actions", "think": "Actions", "know": "Actions",
    "understand": "Actions", "believe": "Actions", "hope": "Actions",
    "rest": "Actions", "relax": "Actions", "breathe": "Actions",
    # more nouns / things
    "table": "Nouns", "chair": "Nouns", "bed": "Nouns", "door": "Nouns",
    "window": "Nouns", "cup": "Nouns", "plate": "Nouns", "spoon": "Nouns",
    "fork": "Nouns", "knife": "Nouns", "bowl": "Nouns", "bottle": "Nouns",
    "pencil": "Nouns", "paper": "Nouns", "crayon": "Nouns", "marker": "Nouns",
    "scissors": "Nouns", "glue": "Nouns", "backpack": "Nouns", "coat": "Nouns",
    "pants": "Nouns", "socks": "Nouns", "jacket": "Nouns", "glasses": "Nouns",
    "money": "Nouns", "card": "Nouns", "ticket": "Nouns", "map": "Nouns",
    "clock": "Nouns", "watch": "Nouns", "lamp": "Nouns",
    "camera": "Nouns", "tablet": "Nouns", "ipad": "Nouns", "headphones": "Nouns",
    "animal": "Animals", "bird": "Animals", "fish": "Animals", "horse": "Animals",
    "cow": "Animals", "pig": "Animals", "chicken": "Animals", "duck": "Animals",
    "tree": "Nouns", "flower": "Nouns", "grass": "Nouns", "sun": "Nouns",
    "moon": "Nouns", "star": "Nouns", "cloud": "Nouns", "rain": "Nouns",
    "snow": "Nouns", "wind": "Nouns", "fire": "Nouns",
    "sandwich": "Nouns", "cheese": "Nouns", "bread": "Nouns", "egg": "Nouns",
    "meat": "Nouns", "soup": "Nouns", "salad": "Nouns", "cake": "Nouns",
    "candy": "Nouns", "chips": "Nouns", "ice cream": "Nouns", "yogurt": "Nouns",
    "cereal": "Nouns", "pasta": "Nouns", "rice": "Nouns", "vegetable": "Nouns",
    "fruit": "Nouns", "snack": "Nouns", "lunch": "Nouns", "breakfast": "Nouns",
    "dinner": "Nouns", "supper": "Nouns",
    "medicine": "Nouns", "bandaid": "Nouns", "band-aid": "Nouns", "soap": "Nouns",
    "towel": "Nouns", "toothbrush": "Nouns", "toothpaste": "Nouns",
    "toilet": "Nouns", "sink": "Nouns", "shower": "Nouns", "bathtub": "Nouns",
    # feelings
    "worried": "Feelings", "nervous": "Feelings", "frustrated": "Feelings",
    "confused": "Feelings", "proud": "Feelings", "shy": "Feelings",
    "lonely": "Feelings", "silly": "Feelings", "surprised": "Feelings",
    "mad": "Feelings", "upset": "Feelings", "glad": "Feelings",
    "love": "Feelings", "hate": "Feelings", "miss": "Feelings",
    "pain": "Feelings", "ouch": "Feelings", "comfortable": "Feelings",
    "uncomfortable": "Feelings", "hungry": "Feelings", "thirsty": "Feelings",
    # descriptors
    "fast": "Descriptors", "slow": "Descriptors", "hard": "Descriptors",
    "soft": "Descriptors", "heavy": "Descriptors", "light": "Descriptors",
    "full": "Descriptors", "empty": "Descriptors", "dirty": "Descriptors",
    "clean": "Descriptors", "wet": "Descriptors", "dry": "Descriptors",
    "new": "Descriptors", "old": "Descriptors", "same": "Descriptors",
    "different": "Descriptors", "next": "Descriptors", "last": "Descriptors",
    "first": "Descriptors", "second": "Descriptors", "third": "Descriptors",
    "many": "Descriptors", "few": "Descriptors", "some": "Descriptors",
    "all": "Descriptors", "none": "Descriptors", "enough": "Descriptors",
    "too much": "Descriptors", "too little": "Descriptors",
    "red": "Descriptors", "blue": "Descriptors", "green": "Descriptors",
    "yellow": "Descriptors", "orange": "Descriptors", "purple": "Descriptors",
    "pink": "Descriptors", "black": "Descriptors", "white": "Descriptors",
    "brown": "Descriptors", "gray": "Descriptors", "grey": "Descriptors",
    # social / phrases
    "excuse me": "Social", "you're welcome": "Social", "welcome": "Social",
    "good morning": "Social", "good night": "Social", "good afternoon": "Social",
    "see you": "Social", "I want": "Social", "I need": "Social",
    "my turn": "Social", "your turn": "Social", "I need space": "Social",
    "too loud": "Social", "all done": "Social", "not yet": "Social",
    "maybe": "Social", "okay": "Social", "ok": "Social", "sure": "Social",
    # little words / time
    "before": "Little words", "after": "Little words", "during": "Little words",
    "with": "Little words", "without": "Little words", "for": "Little words",
    "from": "Little words", "of": "Little words", "about": "Little words",
    "because": "Little words", "if": "Little words", "or": "Little words",
    "but": "Little words", "so": "Little words", "can": "Little words",
    "will": "Little words", "would": "Little words", "should": "Little words",
    "could": "Little words", "must": "Little words", "may": "Little words",
    "today": "Little words", "tomorrow": "Little words", "yesterday": "Little words",
    "morning": "Little words", "afternoon": "Little words", "evening": "Little words",
    "night": "Little words", "always": "Little words", "never": "Little words",
    "sometimes": "Little words", "soon": "Little words", "ready": "Little words",
    # animals — v15.64: its own theme (was Nouns / Uncategorized / heuristic misfiles)
    "bear": "Animals", "lion": "Animals", "tiger": "Animals", "elephant": "Animals",
    "monkey": "Animals", "rabbit": "Animals", "bunny": "Animals", "mouse": "Animals",
    "rat": "Animals", "snake": "Animals", "frog": "Animals", "toad": "Animals",
    "butterfly": "Animals", "bee": "Animals", "ant": "Animals", "spider": "Animals",
    "bug": "Animals", "beetle": "Animals", "firefly": "Animals", "caterpillar": "Animals",
    "ladybug": "Animals", "dragonfly": "Animals", "grasshopper": "Animals",
    "cricket": "Animals", "cockroach": "Animals", "termite": "Animals",
    "flea": "Animals", "tick": "Animals", "mosquito": "Animals", "fly": "Animals",
    "moth": "Animals", "wasp": "Animals", "hornet": "Animals", "mantis": "Animals",
    "cicada": "Animals", "worm": "Animals", "snail": "Animals",
    "whale": "Animals", "shark": "Animals", "dolphin": "Animals", "penguin": "Animals",
    "octopus": "Animals", "crab": "Animals", "seal": "Animals", "otter": "Animals",
    "walrus": "Animals", "manatee": "Animals", "orca": "Animals", "narwhal": "Animals",
    "beluga": "Animals", "seahorse": "Animals", "jellyfish": "Animals",
    "starfish": "Animals", "eel": "Animals", "trout": "Animals", "salmon": "Animals",
    "bass": "Animals", "carp": "Animals", "tuna": "Animals", "cod": "Animals",
    "squid": "Animals", "lobster": "Animals", "shrimp": "Animals", "clam": "Animals",
    "oyster": "Animals", "stingray": "Animals", "axolotl": "Animals",
    "catfish": "Animals", "goldfish": "Animals", "clownfish": "Animals",
    "swordfish": "Animals", "pufferfish": "Animals", "angelfish": "Animals",
    "owl": "Animals", "fox": "Animals", "wolf": "Animals", "deer": "Animals",
    "zebra": "Animals", "giraffe": "Animals", "kangaroo": "Animals", "koala": "Animals",
    "panda": "Animals", "turtle": "Animals", "lizard": "Animals", "gecko": "Animals",
    "chameleon": "Animals", "iguana": "Animals", "tortoise": "Animals",
    "newt": "Animals", "salamander": "Animals", "crocodile": "Animals",
    "alligator": "Animals", "sheep": "Animals", "goat": "Animals",
    "donkey": "Animals", "goose": "Animals", "turkey": "Animals", "llama": "Animals",
    "alpaca": "Animals", "mule": "Animals", "pony": "Animals", "camel": "Animals",
    "bison": "Animals", "buffalo": "Animals", "yak": "Animals", "elk": "Animals",
    "moose": "Animals", "squirrel": "Animals", "raccoon": "Animals", "skunk": "Animals",
    "badger": "Animals", "beaver": "Animals", "weasel": "Animals", "stoat": "Animals",
    "mole": "Animals", "bat": "Animals", "hamster": "Animals", "gerbil": "Animals",
    "ferret": "Animals", "hedgehog": "Animals", "chinchilla": "Animals",
    "parrot": "Animals", "peacock": "Animals", "flamingo": "Animals",
    "eagle": "Animals", "hawk": "Animals", "falcon": "Animals", "crow": "Animals",
    "raven": "Animals", "robin": "Animals", "sparrow": "Animals", "pigeon": "Animals",
    "dove": "Animals", "swan": "Animals", "pelican": "Animals", "stork": "Animals",
    "heron": "Animals", "ostrich": "Animals", "emu": "Animals", "kiwi": "Animals",
    "toucan": "Animals", "vulture": "Animals", "condor": "Animals",
    "bluebird": "Animals", "blackbird": "Animals", "hummingbird": "Animals",
    "mockingbird": "Animals", "songbird": "Animals", "seabird": "Animals",
    "leopard": "Animals", "cheetah": "Animals", "jaguar": "Animals",
    "gorilla": "Animals", "chimpanzee": "Animals", "baboon": "Animals",
    "orangutan": "Animals", "gibbon": "Animals", "lemur": "Animals",
    "sloth": "Animals", "anteater": "Animals", "armadillo": "Animals",
    "porcupine": "Animals", "tapir": "Animals", "capybara": "Animals",
    "rhinoceros": "Animals", "rhino": "Animals", "hippopotamus": "Animals",
    "hippo": "Animals", "coyote": "Animals", "lynx": "Animals", "bobcat": "Animals",
    "cougar": "Animals", "panther": "Animals", "hyena": "Animals", "jackal": "Animals",
    "wombat": "Animals", "platypus": "Animals", "echidna": "Animals", "quokka": "Animals",
    "kitten": "Animals", "puppy": "Animals", "calf": "Animals", "lamb": "Animals",
    "foal": "Animals", "chick": "Animals", "duckling": "Animals", "cub": "Animals",
    "pup": "Animals", "kit": "Animals", "tadpole": "Animals",
    # everyday nouns that were sitting in Uncategorized (incl. heuristic misfiles)
    "grape": "Nouns", "strawberry": "Nouns", "carrot": "Nouns", "potato": "Nouns",
    "tomato": "Nouns", "train": "Nouns", "truck": "Nouns", "boat": "Nouns",
    "plane": "Nouns", "bicycle": "Nouns", "motorcycle": "Nouns",
    "ambulance": "Nouns", "helicopter": "Nouns", "arm": "Nouns", "leg": "Nouns",
    "hand": "Nouns", "foot": "Nouns", "feet": "Nouns", "head": "Nouns",
    "eye": "Nouns", "ear": "Nouns", "nose": "Nouns", "mouth": "Nouns",
    "tooth": "Nouns", "teeth": "Nouns", "hair": "Nouns", "face": "Nouns",
    "finger": "Nouns", "toe": "Nouns", "shoe": "Nouns", "sock": "Nouns",
    "gloves": "Nouns", "boots": "Nouns", "block": "Nouns", "doll": "Nouns",
    "blanket": "Nouns", "pillow": "Nouns", "sky": "Nouns", "leaf": "Nouns",
    "leaves": "Nouns", "rock": "Nouns", "sand": "Nouns", "mountain": "Nouns",
    "lake": "Nouns", "ocean": "Nouns", "sea": "Nouns", "baseball": "Nouns",
    "glass": "Nouns", "house": "Nouns", "river": "Nouns",
    # multi-word phrases: the full phrase beats token order (sea turtle
    # is a turtle, not the sea; baseball bat is equipment, not a bat)
    "sea turtle": "Animals", "sea lion": "Animals", "polar bear": "Animals",
    "black bear": "Animals", "brown bear": "Animals", "grizzly bear": "Animals",
    "tropical fish": "Animals", "guinea pig": "Animals", "tree frog": "Animals",
    "bullfrog": "Animals", "baseball bat": "Nouns",
}


TOPIC_PACKS = {
    "Core 40": [
        "I", "you", "want", "need", "like", "don't", "help", "more", "finished",
        "yes", "no", "please", "thank you", "go", "stop", "come", "wait", "look",
        "play", "eat", "drink", "sleep", "open", "close", "give", "get",
        "happy", "sad", "angry", "tired", "home", "school", "bathroom",
        "mom", "dad", "friend", "food", "water", "now", "again",
    ],
    "Feelings": [
        "happy", "sad", "angry", "scared", "tired", "sick", "hurt", "excited",
        "bored", "calm", "worried", "proud", "confused", "surprised",
        "good", "bad", "I feel", "help", "break",
    ],
    "Classroom": [
        "I", "want", "help", "look", "listen", "sit", "stand", "wait", "stop",
        "go", "finished", "more", "please", "thank you", "teacher", "friend",
        "book", "write", "read", "draw", "play", "work", "bathroom", "drink",
        "quiet", "loud", "break",
    ],
    "Mealtime": [
        "eat", "drink", "hungry", "thirsty", "more", "finished", "please",
        "thank you", "I want", "water", "juice", "milk", "cookie", "apple",
        "banana", "pizza", "sandwich", "food", "hot", "cold", "good", "bad",
    ],
    "Playground": [
        "play", "run", "jump", "climb", "ball", "friend", "go", "stop", "wait",
        "my turn", "your turn", "help", "outside", "swing", "slide", "happy",
        "tired", "again", "finished",
    ],
    "Body & pain": [
        "hurt", "pain", "sick", "head", "hand", "foot", "stomach", "ear",
        "eye", "mouth", "hot", "cold", "tired", "help", "doctor", "medicine",
        "I need", "break",
    ],
    "Safety & break": [
        "stop", "help", "no", "wait", "break", "too loud", "I need space",
        "calm", "safe", "danger", "go home", "bathroom", "finished", "please",
    ],
    "Animals & phrases": [
        "I want", "I don't want", "I see", "I don't know", "I made a mistake",
        "I don't", "animals", "monkey", "alligator", "elephant", "lion",
        "flamingo", "hippo", "zebra", "giraffe", "rhinoceros",
        "tiger", "panda", "gorilla", "shark", "bird",
        "jellyfish", "tropical fish", "eagle", "goat",
    ],
}

SIZE_PRESETS = {
    "Keyring (30 mm)": 30.0,
    "Small tag (35 mm)": 35.0,
    "Standard (40 mm)": 40.0,
    "Desk (50 mm)": 50.0,
    "Board tile (55 mm)": 55.0,
    "Large (60 mm)": 60.0,
}


AI_CATEGORIES = {}  # word (lowercase) -> category, filled by the AI features / saved settings

# Suffix / pattern hints for words not in the tables (best-effort only)
_ACTION_SUFFIXES = (
    "ing", "ed", "ate", "ize", "ise", "ify", "en",
)
_FEELING_WORDS = {
    "happy", "sad", "angry", "mad", "scared", "afraid", "worried", "nervous",
    "tired", "sick", "hurt", "excited", "bored", "calm", "proud", "shy",
    "lonely", "silly", "surprised", "upset", "glad", "frustrated", "confused",
}
_PLACE_WORDS = {
    "home", "school", "bathroom", "kitchen", "bedroom", "outside", "inside",
    "park", "store", "hospital", "library", "playground", "classroom", "office",
}


def guess_category(word: str) -> str:
    """Heuristic category when the word is not in WORD_CATEGORY / AI_CATEGORIES."""
    w = (word or "").lower().strip()
    if not w:
        return "Uncategorized"
    # multi-word social phrases
    if any(x in w for x in ("please", "thank", "sorry", "hello", "goodbye", "excuse")):
        return "Social"
    if w in _FEELING_WORDS or w.endswith("y") and w[:-1] in _FEELING_WORDS:
        return "Feelings"
    if w in _PLACE_WORDS or w.endswith("room") or w.endswith("yard") or w.endswith("ground"):
        return "Places"
    # pronouns / people-ish
    if w in {"i", "me", "my", "mine", "you", "your", "he", "she", "we", "they",
             "him", "her", "us", "them", "someone", "anyone", "everyone"}:
        return "People"
    if w.endswith("er") and len(w) > 4 and w not in {"water", "paper", "other", "under", "over"}:
        # teacher, helper, runner… often people/roles
        if any(w.endswith(s) for s in ("er", "or", "ist")):
            return "People"
    # verb-ish
    if any(w.endswith(s) for s in _ACTION_SUFFIXES) and len(w) > 4:
        return "Actions"
    # colors / size adjectives often end common patterns
    if w in {"red", "blue", "green", "yellow", "big", "little", "small", "hot", "cold"}:
        return "Descriptors"
    # question words
    if w in {"what", "where", "when", "who", "why", "how", "which"}:
        return "Little words"
    return "Uncategorized"


def _singulars(w):
    """Plural → singular candidates, tried only when the singular is a
    known word (so glass never becomes glas, this never becomes thi)."""
    out = []
    if len(w) > 4 and w.endswith("ies"):
        out.append(w[:-3] + "y")
    if len(w) > 3 and w.endswith("es"):
        out.append(w[:-2])
    if len(w) > 2 and w.endswith("s"):
        out.append(w[:-1])
    return out


def _table_lookup(w):
    """AI override → built-in table, with singular fallback (dogs→dog,
    foxes→fox, puppies→puppy). Returns None when nothing matches."""
    if w in AI_CATEGORIES:
        return AI_CATEGORIES[w]
    if w in WORD_CATEGORY:
        return WORD_CATEGORY[w]
    for sing in _singulars(w):
        if sing in AI_CATEGORIES:
            return AI_CATEGORIES[sing]
        if sing in WORD_CATEGORY:
            return WORD_CATEGORY[sing]
    return None


def prune_stale_ai_categories():
    """v15.64 upgrade helper: drop remembered categories that only ever
    came from the old heuristic guess and are now contradicted by the
    built-in table — e.g. tiger remembered as People (the -er rule)
    when the table now says Animals. A stored category that differs
    from the heuristic (a deliberate user override) is kept untouched."""
    for w in list(AI_CATEGORIES):
        cat = AI_CATEGORIES[w]
        table_only = WORD_CATEGORY.get(w)
        if table_only is None:
            for sing in _singulars(w):
                if sing in WORD_CATEGORY:
                    table_only = WORD_CATEGORY[sing]
                    break
        if table_only is None or table_only == cat:
            continue
        if cat == guess_category(w):
            del AI_CATEGORIES[w]
            continue
        # v15.64: the ten animals that moved Nouns → Animals keep their
        # old remembered Nouns unless the user deliberately overrode
        # them to something else (anything but Nouns is kept).
        flipped = {"animal", "bird", "cat", "chicken", "cow", "dog",
                   "duck", "fish", "horse", "pig"}
        if (table_only == "Animals" and cat == "Nouns"
                and (w in flipped or any(s in flipped for s in _singulars(w)))):
            del AI_CATEGORIES[w]


def category_for(word: str) -> str:
    """Best category for a word: AI override → built-in table → singular
    / token fallback → heuristic guess."""
    w = word.lower().strip()
    if not w:
        return "Uncategorized"
    hit = _table_lookup(w)
    if hit:
        return hit
    # try significant tokens of a multi-word phrase
    parts = [p for p in w.replace("-", " ").split() if len(p) > 1]
    for p in parts:
        hit = _table_lookup(p)
        if hit:
            return hit
    return guess_category(w)


def assign_category(word: str, category: str | None = None) -> str:
    """Remember a category for a word (AI or user). Returns the category used."""
    w = (word or "").lower().strip()
    if not w:
        return "Uncategorized"
    cat = category if category in CATEGORIES else category_for(word)
    AI_CATEGORIES[w] = cat
    return cat


def category_color(word: str) -> str:
    return CATEGORIES.get(category_for(word), CATEGORIES["Uncategorized"])


def _board_icon_path(word, overrides=None, variants=None, search_as=None, local_only=True):
    """Resolve the icon for a board card, honouring the user's picture choices
    from the Words tab (custom uploads, picture variants, "search as" phrases).

    local_only=True keeps this safe for UI-thread previews (no network);
    exporters running in background threads may pass local_only=False.
    """
    ov = (overrides or {}).get(word)
    if ov and Path(ov).exists():
        return Path(ov)
    var = (variants or {}).get(word, 0)
    lookup = ((search_as or {}).get(word) or word).strip() or word
    o = core.Opts(icon_source=core.SOURCES[1] if local_only else core.SOURCES[0])
    path, _ = core.find_icon(lookup, o, None, var)
    return path


def _load_icon_rgba(word: str, size_px: int = 120, overrides=None, variants=None,
                    search_as=None, local_only=True):
    p = _board_icon_path(word, overrides=overrides, variants=variants,
                         search_as=search_as, local_only=local_only)
    if not p:
        return None
    try:
        im = Image.open(p).convert("RGBA")
        im.thumbnail((size_px, size_px), Image.Resampling.LANCZOS)
        return im
    except Exception as e:
        log.debug("Board icon load failed for %s: %s", word, e)
        return None


# PECS printable card layouts: cards_per_page -> (cols, rows) on US Letter
PECS_LAYOUTS = {
    "4 per page (2×2)": (2, 2),
    "6 per page (2×3)": (2, 3),
    "8 per page (2×4)": (2, 4),
    "9 per page (3×3)": (3, 3),
    "12 per page (3×4)": (3, 4),
    "16 per page (4×4)": (4, 4),
}

_LAYOUT_RE = re.compile(r"^(\d+) per page \((\d+)\u00d7(\d+)\)$")


def layout_name(cols, rows):
    return f"{cols * rows} per page ({cols}\u00d7{rows})"


def ensure_layout(cols, rows):
    """v14.9: make sure a cols x rows page layout exists (imported boards can be any size,
    e.g. 8 x 6) and return its name."""
    cols, rows = max(1, int(cols)), max(1, int(rows))
    name = layout_name(cols, rows)
    PECS_LAYOUTS.setdefault(name, (cols, rows))
    return name


def register_layout_name(name):
    """Re-create an imported-board layout from its saved name (settings remember it)."""
    m = _LAYOUT_RE.match(str(name or ""))
    if m and int(m.group(2)) * int(m.group(3)) == int(m.group(1)):
        ensure_layout(int(m.group(2)), int(m.group(3)))
        return True
    return False


# Typical PECS card size on paper (inches) — squares fit letter with margins
PECS_CARD_IN = {
    (2, 2): 3.5,
    (2, 3): 3.2,
    (2, 4): 2.4,
    (3, 3): 2.4,
    (3, 4): 2.2,
    (4, 4): 1.9,
}


def _fit_fontsize(text, base_fs, max_width_in, min_fs=6.0):
    """Shrink base_fs (points) until `text` in bold fits max_width_in inches.

    Measured with matplotlib's own text metrics, so what fits here is what
    fits on the printed card (the auto-fit half of the Word size slider).
    """
    try:
        from matplotlib.font_manager import FontProperties
        from matplotlib.textpath import TextPath
        fp = FontProperties(weight="bold")
        fs = float(base_fs)
        while fs > min_fs:
            w_pt = TextPath((0, 0), str(text), size=fs, prop=fp).get_extents().width
            if w_pt <= max_width_in * 72.0:
                break
            fs -= 0.5
        return fs
    except Exception:
        return float(base_fs)


def uses_opensymbols_credit(words, overrides):
    """True when any word's chosen picture comes from OpenSymbols (their
    CC licences need a credit line on the printed page)."""
    ov = overrides or {}
    for w in words:
        pth = ov.get(w)
        if pth and "_os_" in Path(pth).name:
            return True
    return False


def pecs_page_layout(page_words, *, layout_name="8 per page (2\u00d74)", paper="letter",
                     page_index=0, page_count=1, first_index=0,
                     use_category_colors=True, tile_color="#FFFFFF", tile_colors=None,
                     show_labels=True, show_pictures=True,
                     word_scale=1.0, pic_scale=1.0, pic_y=0.35,
                     pic_scale_map=None, pic_y_map=None, word_y=0.12,
                     hide_labels_for=None):
    """v15.4: THE card-page layout \u2014 the single source of truth shared by
    the PDF Studio preview canvas and the printed export, so what you see
    is exactly what prints.

    Geometry is in inches with the origin at the page's TOP-left and y
    growing DOWN (the preview's convention): pic_y is the icon centre as
    a fraction of card height measured from the card's TOP, matching the
    sliders and the canvas drag handles. (Before v15.4 the export read
    pic_y from the BOTTOM \u2014 printed icons sat mirrored vs the preview.)

    Returns a dict: page size, grid metrics, one slot per grid cell
    (slots past the word list have word=None), and the footer text.
    Each filled slot carries its card rect, face colour, the icon rect
    (or None), the icon load size, the label (centre + fitted size) and
    the word-only fallback size \u2014 renderers just draw what they're given.
    """
    cols, rows = PECS_LAYOUTS.get(layout_name, (2, 4))
    per_page = cols * rows
    card_in = PECS_CARD_IN.get((cols, rows), 2.0)
    if str(paper).lower() == "a4":
        page_w, page_h = 8.27, 11.69
    else:
        page_w, page_h = 8.5, 11.0
    margin = 0.45
    gap = 0.12
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin - 0.35  # room for the footer
    cell_w = (usable_w - (cols - 1) * gap) / cols
    cell_h = (usable_h - (rows - 1) * gap) / rows
    side = min(cell_w, cell_h, card_in)
    total_w = cols * side + (cols - 1) * gap
    total_h = rows * side + (rows - 1) * gap
    origin_x = (page_w - total_w) / 2
    origin_y = (page_h - total_h) / 2 + 0.1  # top-origin (print sits the grid a touch low)
    tc = tile_colors or {}
    hide = hide_labels_for or set()
    slots = []
    for j in range(per_page):
        r, c = divmod(j, cols)
        x = origin_x + c * (side + gap)
        y = origin_y + r * (side + gap)
        gi = first_index + j
        word = page_words[j] if j < len(page_words) else None
        slot = {"index": gi, "slot": j, "word": word, "blank": False,
                "x_in": x, "y_in": y, "side_in": side, "color": tile_color,
                "icon_rect": None, "icon_size_px": 0, "label": None,
                "big_word_fs_pt": None}
        if word is None:
            slots.append(slot)
            continue
        blank = not str(word).strip()
        slot["blank"] = blank
        # per-slot colour (str index) wins; legacy word-keyed colours next;
        # then category colour (if on); else the default tile colour
        slot["color"] = (tc.get(str(gi)) or tc.get(gi) or tc.get(word)
                         or (category_color(word) if use_category_colors and not blank
                             else tile_color))
        if blank:
            slots.append(slot)
            continue
        wl = show_labels and word not in hide
        w_ps, w_py = pic_scale, pic_y
        if pic_scale_map:
            try:
                w_ps = float(pic_scale_map.get(word, pic_scale))
            except Exception:
                w_ps = pic_scale
        if pic_y_map:
            try:
                w_py = float(pic_y_map.get(word, pic_y))
            except Exception:
                w_py = pic_y
        if show_pictures:
            pad_f = 0.10
            if wl:
                half_f = 0.28 * w_ps
                top_e = max(0.04, w_py - half_f)
                bot_e = min(0.78, w_py + half_f)
            else:
                half_f = 0.36 * w_ps
                top_e = max(0.06, w_py - half_f)
                bot_e = min(0.94, w_py + half_f)
            half_w_f = min(0.5 - pad_f, (1 - 2 * pad_f) * 0.5 * w_ps)
            slot["icon_rect"] = (x + side * (0.5 - half_w_f), y + side * top_e,
                                 side * 2 * half_w_f, side * (bot_e - top_e))
            slot["icon_size_px"] = int(side * 110 * max(0.5, w_ps))
        if wl:
            slot["label"] = {
                "cx_in": x + side / 2, "cy_in": y + side * (1 - word_y),
                "fs_pt": _fit_fontsize(word, max(9, min(17, int(side * 6.2))) * word_scale,
                                       side * 0.86),
                "max_w_in": side * 0.86}
        base = max(14, min(28, int(side * 9))) if (not show_pictures or wl) else 10
        slot["big_word_fs_pt"] = _fit_fontsize(word, base * word_scale, side * 0.9)
        slots.append(slot)
    footer = (f"PECS cards \u00b7 {layout_name} \u00b7 page {page_index + 1}/{page_count}"
              " \u00b7 cut on dashed lines")
    return {"page_w_in": page_w, "page_h_in": page_h, "cols": cols, "rows": rows,
            "per_page": per_page, "side_in": side, "gap_in": gap,
            "slots": slots, "footer": footer, "footer_y_in": page_h - 0.28}


def pecs_document_layout(words, *, overrides=None, **kw):
    """Lay out a whole document: pecs_page_layout per page, with the
    OpenSymbols credit appended to every footer when it applies."""
    cols, rows = PECS_LAYOUTS.get(kw.get("layout_name", "8 per page (2\u00d74)"), (2, 4))
    per_page = cols * rows
    chunks = [words[i:i + per_page] for i in range(0, len(words), per_page)] or [[]]
    os_credit = uses_opensymbols_credit(words, overrides)
    pages = []
    for pi, chunk in enumerate(chunks):
        page = pecs_page_layout(chunk, page_index=pi, page_count=len(chunks),
                                first_index=pi * per_page, **kw)
        if os_credit:
            page["footer"] += ("\nSome pictures via OpenSymbols (Mulberry CC BY-SA"
                               " \u00b7 Sclera CC BY-NC \u00b7 Tawasol CC BY-SA)")
        pages.append(page)
    return {"pages": pages, "page_w_in": pages[0]["page_w_in"],
            "page_h_in": pages[0]["page_h_in"], "os_credit": os_credit}


def export_pecs_cards_pdf(
    words,
    out_path: Path,
    layout_name: str = "8 per page (2×4)",
    use_category_colors: bool = True,
    tile_color: str = "#FFFFFF",
    tile_colors: dict = None,
    show_labels: bool = True,
    show_pictures: bool = True,
    cut_guides: bool = True,
    paper: str = "letter",
    overrides=None,
    variants=None,
    search_as=None,
    hide_labels_for=None,
    word_scale: float = 1.0,
    pic_scale: float = 1.0,
    pic_y: float = 0.35,
    pic_scale_map: dict = None,
    pic_y_map: dict = None,
    word_y: float = 0.12,
):
    """Printable PECS-style picture cards on letter/A4 paper.

    Same words + icons as the 3D tags, but laid out as cut-apart cards
    (4 / 6 / 8 / 9 / 12 / 16 per page). Multiple pages if needed.
    overrides/variants/search_as carry the Words-tab picture choices through.
    pic_scale grows/shrinks icons; pic_y is the vertical centre of the icon
    band as a fraction of card height (smaller = higher on the card).
    """
    out_path = Path(out_path)
    # v14.3: empty-string entries are intentional BLANK cards (PDF Studio
    # placeholders that hold a slot's position) \u2014 keep them so every other
    # card stays in its slot; they print as empty cards.
    words = [str(w) if w is not None else "" for w in words]
    if not words or not any(w.strip() for w in words):
        raise ValueError("No words for PECS cards")

    # v15.4: all geometry comes from the shared layout (pecs_page_layout),
    # the same numbers the PDF Studio preview draws \u2014 one layout brain.
    doc = pecs_document_layout(
        words, layout_name=layout_name, paper=paper,
        use_category_colors=use_category_colors, tile_color=tile_color,
        tile_colors=tile_colors, show_labels=show_labels,
        show_pictures=show_pictures, word_scale=word_scale,
        pic_scale=pic_scale, pic_y=pic_y, pic_scale_map=pic_scale_map,
        pic_y_map=pic_y_map, word_y=word_y, hide_labels_for=hide_labels_for,
        overrides=overrides)
    page_w, page_h = doc["page_w_in"], doc["page_h_in"]

    def _up(top_in, height_in=0.0):
        """Layout inches (top-origin) \u2192 matplotlib y-up bottom edge."""
        return page_h - top_in - height_in

    with PdfPages(out_path) as pdf:
        for page in doc["pages"]:
            fig, ax = plt.subplots(figsize=(page_w, page_h))
            ax.set_xlim(0, page_w)
            ax.set_ylim(0, page_h)
            ax.set_aspect("equal")
            ax.axis("off")
            for slot in page["slots"]:
                if slot["word"] is None:
                    continue
                x, y, side = slot["x_in"], slot["y_in"], slot["side_in"]
                y_up = _up(y, side)
                # card body
                ax.add_patch(FancyBboxPatch(
                    (x, y_up), side, side,
                    boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=slot["color"], edgecolor="#222222", linewidth=1.6,
                    zorder=1))
                # cut guide (dashed outer square)
                if cut_guides:
                    ax.add_patch(Rectangle(
                        (x - 0.02, y_up - 0.02), side + 0.04, side + 0.04,
                        fill=False, edgecolor="#999999", linewidth=0.6,
                        linestyle=(0, (2, 2)), zorder=0))
                if slot["blank"]:
                    continue
                icon = None
                if slot["icon_rect"] is not None:
                    icon = _load_icon_rgba(slot["word"], size_px=slot["icon_size_px"],
                                           overrides=overrides, variants=variants,
                                           search_as=search_as, local_only=False)
                big_word = False
                if icon is not None:
                    ix, iy, iw, ih = slot["icon_rect"]
                    ax.imshow(icon, extent=(ix, ix + iw, _up(iy, ih), _up(iy)),
                              zorder=2, aspect="equal")
                else:
                    # No picture: the card is the WORD itself, big and centered
                    if slot["big_word_fs_pt"]:
                        ax.text(x + side / 2, _up(y + side / 2), slot["word"],
                                ha="center", va="center",
                                fontsize=slot["big_word_fs_pt"],
                                fontweight="bold", zorder=3)
                    big_word = True
                lab = slot["label"]
                if lab is not None and not big_word:
                    ax.text(lab["cx_in"], _up(lab["cy_in"]), slot["word"],
                            ha="center", va="center", fontsize=lab["fs_pt"],
                            fontweight="bold", zorder=3, clip_on=True)
            # footer
            ax.text(page_w / 2, 0.28, page["footer"],
                    ha="center", va="center", fontsize=8, color="#666666")
            fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
            pdf.savefig(fig, dpi=160)
            plt.close(fig)

    log.info("PECS cards PDF \u2192 %s (%d cards, %d page(s), %s)",
             out_path, len(words), len(doc["pages"]), layout_name)
    return out_path


def export_choice_board_pdf(words, out_path: Path, title="Choice board", cols=2,
                            overrides=None, variants=None, search_as=None):
    """PDF grid of words + icons for laminating (PECS / choice making)."""
    out_path = Path(out_path)
    words = [w for w in words if w.strip()]
    if not words:
        raise ValueError("No words for choice board")
    cols = max(1, min(4, int(cols)))
    rows = (len(words) + cols - 1) // cols
    fig_w, fig_h = 8.5, max(5.0, 1.2 + rows * 2.4)
    with PdfPages(out_path) as pdf:
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        ax.set_xlim(0, cols)
        ax.set_ylim(0, rows)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(title, fontsize=16, pad=12, fontweight="bold")
        for i, w in enumerate(words):
            r, c = divmod(i, cols)
            y = rows - 1 - r
            color = category_color(w)
            ax.add_patch(FancyBboxPatch(
                (c + 0.06, y + 0.08), 0.88, 0.84,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color, edgecolor="#333", linewidth=1.5, alpha=0.92))
            icon = _load_icon_rgba(w, 100, overrides=overrides, variants=variants,
                                   search_as=search_as, local_only=False)
            if icon is not None:
                ax.imshow(icon, extent=(c + 0.28, c + 0.72, y + 0.38, y + 0.82), zorder=2)
            ax.text(c + 0.5, y + 0.18, w, ha="center", va="center",
                    fontsize=13, fontweight="bold", zorder=3)
        fig.tight_layout()
        pdf.savefig(fig, dpi=150)
        plt.close(fig)
    log.info("Choice board PDF → %s (%d words)", out_path, len(words))
    return out_path


def export_first_then_pdf(first_word: str, then_word: str, out_path: Path,
                          title="First → Then", overrides=None, variants=None, search_as=None):
    """Classic First-Then visual support PDF."""
    out_path = Path(out_path)
    with PdfPages(out_path) as pdf:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 5)
        ax.axis("off")
        ax.set_title(title, fontsize=18, fontweight="bold", pad=10)
        boxes = [
            (0.5, 0.6, 4.0, 3.6, "FIRST", first_word, "#B3E5FC"),
            (5.5, 0.6, 4.0, 3.6, "THEN", then_word, "#C8E6C9"),
        ]
        for x, y, w, h, label, word, col in boxes:
            ax.add_patch(FancyBboxPatch(
                (x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.15",
                facecolor=col, edgecolor="#222", linewidth=2))
            ax.text(x + w / 2, y + h - 0.45, label, ha="center", va="center",
                    fontsize=14, fontweight="bold", color="#333")
            icon = _load_icon_rgba(word, 140, overrides=overrides, variants=variants,
                                   search_as=search_as, local_only=False)
            if icon is not None:
                ax.imshow(icon, extent=(x + 1.1, x + w - 1.1, y + 1.0, y + h - 1.0), zorder=2)
            ax.text(x + w / 2, y + 0.45, word, ha="center", va="center",
                    fontsize=13, fontweight="bold")
        # arrow
        ax.annotate("", xy=(5.35, 2.4), xytext=(4.65, 2.4),
                    arrowprops=dict(arrowstyle="->", color="#333", lw=2.5))
        fig.tight_layout()
        pdf.savefig(fig, dpi=150)
        plt.close(fig)
    log.info("First-Then PDF → %s (%s → %s)", out_path, first_word, then_word)
    return out_path


def export_schedule_pdf(words, out_path: Path, title="Visual schedule",
                        overrides=None, variants=None, search_as=None):
    """Horizontal or wrapped schedule strip for routines."""
    out_path = Path(out_path)
    words = [w for w in words if w.strip()]
    if not words:
        raise ValueError("No words for schedule")
    n = len(words)
    fig_w = max(8.5, min(14, 1.2 + n * 1.6))
    with PdfPages(out_path) as pdf:
        fig, ax = plt.subplots(figsize=(fig_w, 3.8))
        ax.set_xlim(0, n)
        ax.set_ylim(0, 2.2)
        ax.axis("off")
        ax.set_title(title, fontsize=16, fontweight="bold")
        for i, w in enumerate(words):
            color = category_color(w)
            ax.add_patch(FancyBboxPatch(
                (i + 0.08, 0.25), 0.84, 1.7,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color, edgecolor="#333", linewidth=1.4))
            ax.text(i + 0.5, 1.85, str(i + 1), ha="center", va="center",
                    fontsize=10, color="#555")
            icon = _load_icon_rgba(w, 90, overrides=overrides, variants=variants,
                                   search_as=search_as, local_only=False)
            if icon is not None:
                ax.imshow(icon, extent=(i + 0.22, i + 0.78, 0.7, 1.55), zorder=2)
            ax.text(i + 0.5, 0.42, w, ha="center", va="center",
                    fontsize=11, fontweight="bold")
            if i < n - 1:
                ax.annotate("", xy=(i + 1.02, 1.1), xytext=(i + 0.98, 1.1),
                            arrowprops=dict(arrowstyle="->", color="#666", lw=1.5))
        fig.tight_layout()
        pdf.savefig(fig, dpi=150)
        plt.close(fig)
    log.info("Schedule PDF → %s (%d steps)", out_path, n)
    return out_path


def export_vocab_csv(words, out_path: Path):
    """CSV for import into other AAC apps."""
    out_path = Path(out_path)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        wri = csv.writer(f)
        wri.writerow(["word", "category", "color"])
        for word in words:
            wri.writerow([word, category_for(word), category_color(word)])
    return out_path


def build_offline_pack(words, out_dir: Path, o, overrides=None, variants=None,
                       search_as=None, progress=None, make_3d=True,
                       first=None, then=None):
    """One-shot offline AAC pack: icons, optional 3D tags, PDFs, CSV, manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    words = [w for w in words if w.strip()]
    overrides, variants = overrides or {}, variants or {}
    search_as = search_as or {}
    notes = []
    files = []

    # 1) icons
    if progress:
        progress(0.05, "Caching icons…")
    pref = core.prefetch_icons(words, o, overrides, variants, search_as,
                               progress=lambda f, t: progress(0.05 + 0.35 * f, t) if progress else None)
    notes.append(f"Icons cached: {len(pref['ok'])} ok, {len(pref['failed'])} failed")

    # 2) 3D tags (optional)
    if make_3d and words:
        if progress:
            progress(0.42, "Building 3D tags…")
        # colour each word by category when body is default
        rep = core.export_words(
            words, out_dir / "tags", o, overrides, variants,
            stl=True, threemf=True, plate=False,
            progress=lambda f, t: progress(0.42 + 0.35 * f, t) if progress else None,
            search_as=search_as,
            category_colors=True,
        )
        files.extend(rep.get("files", []))
        notes.extend(rep.get("notes", []))
        if rep.get("failed"):
            notes.append("3D failures: " + ", ".join(w for w, _ in rep["failed"]))

    # 3) PDFs
    if progress:
        progress(0.80, "Writing board PDFs…")
    if words:
        try:
            p = export_pecs_cards_pdf(words, out_dir / "pecs_cards_8up.pdf",
                                      layout_name="8 per page (2×4)",
                                      use_category_colors=True, show_labels=True,
                                      overrides=overrides, variants=variants, search_as=search_as)
            files.append(p)
        except Exception as e:
            log.warning("PECS cards PDF failed: %s", e)
            notes.append(f"PECS PDF failed: {e}")
        try:
            p = export_choice_board_pdf(words[:12], out_dir / "choice_board.pdf",
                                        title="Choice board", cols=3 if len(words) > 4 else 2,
                                        overrides=overrides, variants=variants, search_as=search_as)
            files.append(p)
        except Exception as e:
            log.warning("Choice board PDF failed: %s", e)
            notes.append(f"Choice board PDF failed: {e}")
        try:
            fw = first or (words[0] if words else "work")
            tw = then or (words[1] if len(words) > 1 else "play")
            p = export_first_then_pdf(fw, tw, out_dir / "first_then.pdf",
                                      overrides=overrides, variants=variants, search_as=search_as)
            files.append(p)
        except Exception as e:
            log.warning("First-Then PDF failed: %s", e)
        try:
            p = export_schedule_pdf(words[:8], out_dir / "schedule.pdf",
                                    title="Visual schedule",
                                    overrides=overrides, variants=variants, search_as=search_as)
            files.append(p)
        except Exception as e:
            log.warning("Schedule PDF failed: %s", e)

    # 4) CSV + manifest
    if progress:
        progress(0.92, "Writing vocab list…")
    csv_path = export_vocab_csv(words, out_dir / "vocabulary.csv")
    files.append(csv_path)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "version": APP_VERSION,
        "words": words,
        "categories": {w: category_for(w) for w in words},
        "files": [str(f.name) if isinstance(f, Path) else str(f) for f in files],
        "notes": notes,
        "icons_dir": str(ICON_DIR),
    }
    man_path = out_dir / "pack_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    files.append(man_path)
    if progress:
        progress(1.0, "Offline pack ready")
    log.info("Offline pack → %s (%d files)", out_dir, len(files))
    return {"files": files, "notes": notes, "dir": out_dir}


# ============================================================ v3.6.0-exp: more boards


CORE_BOARD_SIZES = {
    "24 core words (6×4)": (6, 4),
    "36 core words (6×6)": (6, 6),
    "60 core words (10×6)": (10, 6),
}


