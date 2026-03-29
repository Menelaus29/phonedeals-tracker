"""
matcher.py
──────────
Normalization and fuzzy matching engine.

Key design decisions:
  1. ALL text is lowercased and de-punctuated before any comparison.
  2. Abbreviations are expanded before comparison so "ip 15 pro" == "iphone 15 pro".
  3. Model NUMBERS / suffixes (e.g. "15 pro", "s24") are extracted from the watchlist
     query and must be found EXACTLY (word-boundary) in the listing title to prevent
     false positives like S24 matching S24+ or S24 Ultra.
  4. Fuzzy matching uses RapidFuzz token_set_ratio which is robust to word reordering
     and extra words in the title.
  5. Location normalization maps any common Vietnamese alias to a canonical city name.
"""

import re
import logging
from typing import Optional
from config import FUZZY_THRESHOLD

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

# ── Abbreviation expansion ────────────────────────────────────────────────────
# Applied to BOTH the query and the listing title.
# Order matters: longer patterns first to avoid partial replacements.
ABBREVIATIONS: list[tuple[re.Pattern, str]] = [
    # iPhone aliases
    (re.compile(r"\bip\b"), "iphone"),
    (re.compile(r"\biph\b"), "iphone"),
    (re.compile(r"\biphone\b"), "iphone"),  # idempotent
    # Samsung aliases
    (re.compile(r"\bss\b"), "samsung"),
    (re.compile(r"\bsamsung\b"), "samsung"),
    # Pro Max spacing
    (re.compile(r"\bpromax\b"), "pro max"),
    (re.compile(r"\bpro\s*max\b"), "pro max"),
    # Plus spacing
    (re.compile(r"\bplus\b"), "plus"),
    (re.compile(r"\bpro\b"), "pro"),
    # Ultra
    (re.compile(r"\bultra\b"), "ultra"),
    # Galaxy
    (re.compile(r"\bgalaxy\b"), "galaxy"),
    (re.compile(r"\bgt\b"), "galaxy"),  # common shorthand in VN listings
    # Xiaomi aliases
    (re.compile(r"\bxmr\b"), "xiaomi"),
    (re.compile(r"\bxmi\b"), "xiaomi"),
    # OPPO Find X -> keep as-is but normalise spacing
    (re.compile(r"\bfind\s*x(\d)"), r"find x\1"),
]


def _normalize_text(text: str) -> str:
    """
    Full normalization pipeline:
      1. Lowercase
      2. Convert '+' suffix to ' plus' BEFORE stripping punctuation, so
         's24+'  ->  's24 plus'  (not 's24', which would be indistinguishable)
      3. Strip remaining punctuation (keep letters, digits, spaces)
      4. Collapse whitespace
      5. Expand abbreviations
    """
    text = text.lower()
    # Convert trailing '+' on a model code (digit+) to the word 'plus'
    # so that s24+ -> s24 plus, 14+ -> 14 plus, etc.
    text = re.sub(r"(\d)\+", r"\1 plus", text)
    text = re.sub(r"[^\w\s]", " ", text)  # strip remaining punctuation
    text = re.sub(r"\s+", " ", text).strip()

    for pattern, replacement in ABBREVIATIONS:
        text = pattern.sub(replacement, text)

    # collapse again after expansions
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Location normalization ─────────────────────────────────────────────────────
_LOCATION_MAP: list[tuple[list[str], str]] = [
    (
        ["hn", "ha noi", "hanoi", "hà nội", "ha noi", "hànội"],
        "Hanoi",
    ),
    (
        ["hcm", "sg", "tphcm", "sai gon", "saigon", "ho chi minh", "hồ chí minh",
         "tp hcm", "tp.hcm", "tphcm", "hochiminhcity", "hcmc"],
        "Ho Chi Minh",
    ),
    (
        ["dn", "da nang", "danang", "đà nẵng"],
        "Da Nang",
    ),
    (
        ["hp", "hai phong", "haiphong", "hải phòng"],
        "Hai Phong",
    ),
    (
        ["ct", "can tho", "cantho", "cần thơ"],
        "Can Tho",
    ),
]


def normalize_location(raw: str) -> str:
    """
    Map any location alias to a canonical city name.
    If the alias is unrecognised, return the cleaned input as-is.
    """
    cleaned = re.sub(r"[^\w\s]", " ", raw.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    for aliases, canonical in _LOCATION_MAP:
        if cleaned in aliases:
            return canonical

    # Try fuzzy location matching as last resort
    from rapidfuzz import process as fz_process

    all_aliases = [(alias, canonical) for aliases, canonical in _LOCATION_MAP for alias in aliases]
    alias_strings = [a for a, _ in all_aliases]
    result = fz_process.extractOne(cleaned, alias_strings, scorer=fuzz.ratio, score_cutoff=80)
    if result:
        matched_alias = result[0]
        for alias, canonical in all_aliases:
            if alias == matched_alias:
                return canonical

    log.warning("Unknown location '%s' — using raw value.", raw)
    return raw.strip().title()


# ── Model number extraction ────────────────────────────────────────────────────
# Extracts sub-patterns that are model-specific identifiers: numbers, well-known
# suffixes, and letter+number combos (e.g. "s24", "15 pro", "find x7").
_MODEL_KEYWORD_RE = re.compile(
    r"""
    \b
    (
        \d{1,2}\s*(?:pro\s*max|pro|ultra|plus|\+)?  # digit-led: 15 pro max, 14, 13 mini
        | [a-z]\d{2,3}\s*(?:ultra|plus|\+|fe)?      # letter+digit: s24, s24 ultra, a54
        | find\s*x\d+                                # OPPO Find X7
    )
    \b
    """,
    re.VERBOSE,
)

# Characters that should be treated as word boundaries for suffix detection
_SUFFIX_BOUNDARY = re.compile(r"(\d)(\+)")  # turn "s24+" -> "s24 +" as separate tokens

# Suffixes that immediately follow a model keyword (with optional whitespace)
# and indicate a DIFFERENT, more-specific model variant.
# e.g. "s24 ultra", "s24+", "s24 fe", "s24 5g" are different from "s24".
_DIFFERENTIATING_SUFFIXES_RE = re.compile(
    r"^[\s]*(?:ultra|plus|fe|lite|mini|\+|5g)\b",
    re.IGNORECASE,
)


def extract_model_keywords(normalized_query: str) -> list[str]:
    """
    Extract the model-identifying tokens from a normalized watchlist query.
    Returns a list of strings that MUST all appear (as exact words) in the listing.
    """
    # Pre-process: split "s24+" into "s24 +" so regex word boundaries work
    q = _SUFFIX_BOUNDARY.sub(r"\1 \2", normalized_query)
    keywords = _MODEL_KEYWORD_RE.findall(q)
    return [kw.strip() for kw in keywords if kw.strip()]


def _keyword_present_exactly(keyword: str, listing_norm: str) -> bool:
    """
    Check that `keyword` appears in `listing_norm` as a standalone model token,
    NOT as part of a more specific variant.

    Rules:
    - "s24" must NOT match "s24+", "s24 ultra", "s24 fe" (different models).
    - "s24" MUST match "samsung galaxy s24 256gb cu" (standalone use).
    - "15 pro" must NOT match "15 pro max" (different model).
    - "15 pro" MUST match "iphone 15 pro 256gb fullbox".

    Strategy:
    - Find all word-boundary occurrences of `keyword`.
    - For each, inspect the text immediately following it.
    - Reject if followed immediately (no space) by alphanumerics → longer token.
    - Reject if followed (possibly with spaces) by a differentiating suffix word.
    - Accept if the keyword is genuinely standalone.
    """
    escaped = re.escape(keyword)
    pattern = re.compile(r"\b" + escaped + r"\b")

    for m in pattern.finditer(listing_norm):
        end = m.end()
        remainder = listing_norm[end:]

        # 1. Immediately followed by alphanumeric char (no gap) → longer token → reject
        if remainder and re.match(r"[a-z0-9+]", remainder):
            continue

        # 2. Followed (with optional whitespace) by a known differentiating suffix → reject
        #    e.g. " ultra", "+", " fe", " plus", " 5g"
        if _DIFFERENTIATING_SUFFIXES_RE.match(remainder):
            continue

        # 3. This occurrence is standalone — accept
        return True

    return False


# ── Main matching function ─────────────────────────────────────────────────────

def matches_watchlist_item(
    listing_title: str,
    listing_condition: str,
    listing_price: int,
    watchlist_item: dict,
    threshold: int,
) -> tuple[bool, float]:
    """
    Returns (is_match, pct_below_threshold).

    A listing is a match if ALL of the following:
      1. Condition is compatible (new/used/any).
      2. Price is below the threshold.
      3. Fuzzy similarity is >= FUZZY_THRESHOLD.
      4. All model keywords from the watchlist are found exactly in the listing title.
    """

    # 1. Condition gate
    required_condition = watchlist_item.get("condition", "any")
    if required_condition != "any":
        if listing_condition.lower() not in ("unknown",) and listing_condition.lower() != required_condition:
            return False, 0.0

    # 2. Price gate
    if listing_price >= watchlist_item["threshold"]:
        return False, 0.0
        
    if listing_price < watchlist_item.get("min_price", 0):
        return False, 0.0

    pct_below = (watchlist_item["threshold"] - listing_price) / watchlist_item["threshold"] * 100

    # 3. Normalize both sides
    norm_query = _normalize_text(watchlist_item["model"])
    norm_title = _normalize_text(listing_title)

    # 4. Fuzzy score gate
    score = fuzz.token_set_ratio(norm_query, norm_title)
    if score < FUZZY_THRESHOLD:
        return False, 0.0

    # 5. Model keyword hard-check
    keywords = extract_model_keywords(norm_query)
    for kw in keywords:
        if not _keyword_present_exactly(kw, norm_title):
            log.debug(
                "Rejected '%s': keyword '%s' not found exactly in '%s'",
                listing_title, kw, norm_title,
            )
            return False, 0.0

    return True, round(pct_below, 1)
