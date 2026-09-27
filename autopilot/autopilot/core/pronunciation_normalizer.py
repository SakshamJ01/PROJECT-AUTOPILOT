"""Pronunciation Control & Text Normalization Layer — Phase 1.

Provides deterministic text normalization for TTS synthesis:
  1. Acronym Normalization (e.g. NASA -> NA-SA, ISRO -> ISS-RO, AI -> A-I, SQL -> S-Q-L)
  2. Number & Unit Formatting (e.g. 1,400,000,000 -> one point four billion, $50M -> fifty million dollars, 15°N -> fifteen degrees north, 2026 -> twenty twenty-six)
  3. Scientific & Chemical Formula Normalization (e.g. CO₂ -> C-O-two, H₂O -> H-two-O)
  4. Custom Pronunciation Dictionary overrides (from pronunciation_dict.json)

Keeps original narration text strictly separated from synthesized pronunciation text.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, List


# ---------------------------------------------------------------------------
# Number to Words Converter
# ---------------------------------------------------------------------------

ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen"
]
TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"
]
SCALES = ["", "thousand", "million", "billion", "trillion"]

DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"
}


def int_to_words(n: int) -> str:
    """Convert integer to English words."""
    if n == 0:
        return "zero"
    if n < 0:
        return "negative " + int_to_words(abs(n))

    def convert_hundreds(num: int) -> str:
        parts = []
        hundreds = num // 100
        rem = num % 100
        if hundreds > 0:
            parts.append(f"{ONES[hundreds]} hundred")
        if rem > 0:
            if rem < 20:
                parts.append(ONES[rem])
            else:
                tens_val = rem // 10
                units_val = rem % 10
                if units_val > 0:
                    parts.append(f"{TENS[tens_val]}-{ONES[units_val]}")
                else:
                    parts.append(TENS[tens_val])
        return " ".join(parts)

    chunks = []
    temp = n
    scale_idx = 0
    while temp > 0:
        chunk = temp % 1000
        if chunk != 0:
            chunk_str = convert_hundreds(chunk)
            if SCALES[scale_idx]:
                chunks.insert(0, f"{chunk_str} {SCALES[scale_idx]}")
            else:
                chunks.insert(0, chunk_str)
        temp //= 1000
        scale_idx += 1

    return " ".join(chunks)


def float_to_words(val: float, precision: int = 2) -> str:
    """Convert float value to English words with point notation."""
    int_part = int(val)
    dec_part = round(val - int_part, precision)
    if dec_part == 0:
        return int_to_words(int_part)
    dec_digits = str(dec_part).split(".")[1]
    dec_words = " ".join(DIGIT_WORDS.get(d, d) for d in dec_digits)
    return f"{int_to_words(int_part)} point {dec_words}"


def year_to_words(year_str: str) -> str:
    """Convert 4-digit year string (e.g. 2026 -> twenty twenty-six, 1999 -> nineteen ninety-nine)."""
    try:
        y = int(year_str)
    except ValueError:
        return year_str

    if 1000 <= y <= 9999:
        century = y // 100
        rest = y % 100
        if 2000 <= y <= 2009:
            return int_to_words(y)
        if rest == 0:
            return f"{int_to_words(century)} hundred"
        if rest < 10:
            return f"{int_to_words(century)} oh {int_to_words(rest)}"
        return f"{int_to_words(century)} {int_to_words(rest)}"
    return int_to_words(y)


# ---------------------------------------------------------------------------
# Pronunciation Normalizer
# ---------------------------------------------------------------------------

class PronunciationNormalizer:
    """Production text normalizer for Neural Voice Studio."""

    DEFAULT_DICT_PATH = Path(__file__).parent / "pronunciation_dict.json"

    # Pre-defined chemical formula mappings
    CHEMICAL_PATTERNS = {
        r"\bCO[2₂]\b": "C-O-two",
        r"\bH[2₂]O\b": "H-two-O",
        r"\bO[2₂]\b": "O-two",
        r"\bN[2₂]\b": "N-two",
        r"\bCH[4₄]\b": "C-H-four",
        r"\bNaCl\b": "N-a-C-l",
        r"\bSiO[2₂]\b": "S-i-O-two",
        r"\bCaCO[3₃]\b": "C-a-C-O-three",
        r"\bH[2₂]SO[4₄]\b": "H-two-S-O-four",
        r"\bNO[2₂]\b": "N-O-two",
    }

    # Compass directions
    DIRECTION_MAP = {
        "N": "north", "S": "south", "E": "east", "W": "west",
        "NE": "northeast", "NW": "northwest", "SE": "southeast", "SW": "southwest"
    }

    # Unit abbreviations
    UNIT_MAP = {
        "km/h": "kilometers per hour",
        "kmh": "kilometers per hour",
        "mph": "miles per hour",
        "km": "kilometers",
        "kg": "kilograms",
        "m": "meters",
        "cm": "centimeters",
        "mm": "millimeters",
        "ft": "feet",
        "in": "inches",
        "lb": "pounds",
        "lbs": "pounds",
        "gb": "gigabytes",
        "mb": "megabytes",
        "tb": "terabytes",
        "kb": "kilobytes",
        "hz": "hertz",
        "khz": "kilohertz",
        "mhz": "megahertz",
        "ghz": "gigahertz",
        "sec": "seconds",
        "s": "seconds",
        "min": "minutes",
        "hr": "hours",
        "hrs": "hours",
    }

    def __init__(self, custom_dict: Optional[Dict[str, str]] = None, dict_file: Optional[Path] = None):
        self.custom_dict: Dict[str, str] = {}
        # Load from disk if available
        file_to_load = dict_file or self.DEFAULT_DICT_PATH
        if file_to_load.exists():
            try:
                with open(file_to_load, "r", encoding="utf-8") as f:
                    self.custom_dict = json.load(f)
            except Exception:
                self.custom_dict = {}

        if custom_dict:
            self.custom_dict.update(custom_dict)

    def register_override(self, word: str, pronunciation: str) -> None:
        """Register a single pronunciation override."""
        self.custom_dict[word] = pronunciation

    def normalize(self, text: str) -> str:
        """Normalize narration text for TTS speech synthesis."""
        if not text or not text.strip():
            return ""

        res = text.strip()

        # Step 1: Chemical & Scientific Formulas
        res = self._normalize_chemicals(res)

        # Step 2: Custom Pronunciation Dictionary Overrides
        res = self._apply_custom_dict(res)

        # Step 3: Currencies & Financials ($50M, $1.4B, $100)
        res = self._normalize_currencies(res)

        # Step 4: Geographic Coordinates & Degrees (15°N, 45°W, 90°)
        res = self._normalize_coordinates(res)

        # Step 5: Percentages (25%, 0.5%)
        res = self._normalize_percentages(res)

        # Step 6: Numbers with Units (100km, 50m, 10kg, 500mph)
        res = self._normalize_units(res)

        # Step 7: Large numbers with commas or words (1,400,000,000, 2.5 million)
        res = self._normalize_large_numbers(res)

        # Step 8: Year dates (e.g. 2026, 1999)
        res = self._normalize_years(res)

        # Step 9: Standalone integers & decimals
        res = self._normalize_cardinal_numbers(res)

        # Step 10: Acronyms & Initialisms (NASA, ISRO, AI, SQL, etc.)
        res = self._normalize_acronyms(res)

        # Step 11: Cleanup whitespace
        res = re.sub(r"\s+", " ", res).strip()

        return res

    def _normalize_chemicals(self, text: str) -> str:
        for pattern, replacement in self.CHEMICAL_PATTERNS.items():
            text = re.sub(pattern, replacement, text)
        return text

    def _apply_custom_dict(self, text: str) -> str:
        for term, replacement in sorted(self.custom_dict.items(), key=lambda x: -len(x[0])):
            pattern = r"\b" + re.escape(term) + r"\b"
            text = re.sub(pattern, replacement, text)
        return text

    def _normalize_currencies(self, text: str) -> str:
        # Currency symbols map
        symbols = {
            "$": "dollars",
            "€": "euros",
            "£": "pounds",
            "₹": "rupees",
            "¥": "yen",
        }

        # Handle $50M / $1.5B / $500K / $100
        def repl_curr_suffix(match):
            sym = match.group(1)
            num_str = match.group(2).replace(",", "")
            suffix = (match.group(3) or "").upper()
            curr_name = symbols.get(sym, "dollars")

            try:
                num = float(num_str) if "." in num_str else int(num_str)
            except ValueError:
                return match.group(0)

            if suffix == "M":
                val_words = float_to_words(num) if isinstance(num, float) else int_to_words(num)
                return f"{val_words} million {curr_name}"
            elif suffix == "B":
                val_words = float_to_words(num) if isinstance(num, float) else int_to_words(num)
                return f"{val_words} billion {curr_name}"
            elif suffix == "K":
                val_words = float_to_words(num) if isinstance(num, float) else int_to_words(num)
                return f"{val_words} thousand {curr_name}"
            elif suffix == "T":
                val_words = float_to_words(num) if isinstance(num, float) else int_to_words(num)
                return f"{val_words} trillion {curr_name}"
            else:
                val_words = float_to_words(num) if isinstance(num, float) else int_to_words(num)
                return f"{val_words} {curr_name}"

        curr_pattern = r"([\$€£₹¥])(\d+(?:,\d{3})*(?:\.\d+)?)([mMbBkKtT])?\b"
        return re.sub(curr_pattern, repl_curr_suffix, text)


    def _normalize_coordinates(self, text: str) -> str:
        # e.g. 15°N, 45° 30' S, 90°
        def repl_coord(match):
            num = match.group(1)
            cardinal = match.group(2)
            num_words = int_to_words(int(num)) if num.isdigit() else num
            if cardinal:
                cardinal_word = self.DIRECTION_MAP.get(cardinal.upper(), cardinal)
                return f"{num_words} degrees {cardinal_word}"
            return f"{num_words} degrees"

        pattern = r"(\d+(?:\.\d+)?)\s*°\s*([NSEWnsew]|NE|NW|SE|SW)?"
        return re.sub(pattern, repl_coord, text)

    def _normalize_percentages(self, text: str) -> str:
        def repl_pct(match):
            num_str = match.group(1)
            if "." in num_str:
                words = float_to_words(float(num_str))
            else:
                words = int_to_words(int(num_str))
            return f"{words} percent"

        return re.sub(r"(\d+(?:\.\d+)?)\s*%", repl_pct, text)

    def _normalize_units(self, text: str) -> str:
        def repl_unit(match):
            num_str = match.group(1)
            unit_str = match.group(2).lower()
            if "." in num_str:
                num_words = float_to_words(float(num_str))
            else:
                num_words = int_to_words(int(num_str))
            unit_word = self.UNIT_MAP.get(unit_str, unit_str)
            return f"{num_words} {unit_word}"

        unit_keys = "|".join(re.escape(k) for k in sorted(self.UNIT_MAP.keys(), key=lambda x: -len(x)))
        pattern = rf"\b(\d+(?:\.\d+)?)\s*({unit_keys})\b"
        return re.sub(pattern, repl_unit, text, flags=re.IGNORECASE)

    def _normalize_large_numbers(self, text: str) -> str:
        # e.g. 1,400,000,000 -> one point four billion (or full words if exact)
        def repl_comma_num(match):
            raw = match.group(0).replace(",", "")
            try:
                val = int(raw)
                # Specific common scale abbreviations for concise speech
                if val >= 1_000_000_000 and val % 100_000_000 == 0:
                    dec = val / 1_000_000_000
                    if dec == int(dec):
                        return f"{int_to_words(int(dec))} billion"
                    return f"{float_to_words(dec)} billion"
                elif val >= 1_000_000 and val % 100_000 == 0:
                    dec = val / 1_000_000
                    if dec == int(dec):
                        return f"{int_to_words(int(dec))} million"
                    return f"{float_to_words(dec)} million"
                elif val >= 1_000 and val % 100 == 0:
                    dec = val / 1_000
                    if dec == int(dec):
                        return f"{int_to_words(int(dec))} thousand"
                    return f"{float_to_words(dec)} thousand"
                return int_to_words(val)
            except ValueError:
                return match.group(0)

        # Match numbers formatted with commas: 1,400,000,000
        text = re.sub(r"\b\d{1,3}(?:,\d{3})+\b", repl_comma_num, text)

        # Match 1.5 million, 2.4 billion, etc.
        def repl_scale_word(match):
            num_str = match.group(1)
            scale = match.group(2).lower()
            if "." in num_str:
                num_words = float_to_words(float(num_str))
            else:
                num_words = int_to_words(int(num_str))
            return f"{num_words} {scale}"

        text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(thousand|million|billion|trillion)\b", repl_scale_word, text, flags=re.IGNORECASE)
        return text

    def _normalize_years(self, text: str) -> str:
        # Match 4-digit years between 1800 and 2099 in context
        # e.g. "in 2026", "by 1999", "year 2000"
        def repl_year(match):
            prefix = match.group(1) or ""
            year = match.group(2)
            year_words = year_to_words(year)
            return f"{prefix}{year_words}"

        # Match years preceded by in, by, year, during, since, or standalone 4-digit 1800-2099
        pattern = r"\b(in |by |during |since |year |from )?((?:18|19|20)\d{2})\b"
        return re.sub(pattern, repl_year, text, flags=re.IGNORECASE)

    def _normalize_cardinal_numbers(self, text: str) -> str:
        # Decimals (e.g. 3.14)
        def repl_dec(match):
            num = float(match.group(0))
            return float_to_words(num)

        text = re.sub(r"\b\d+\.\d+\b", repl_dec, text)

        # Standalone integers (up to 999999)
        def repl_int(match):
            val = int(match.group(0))
            return int_to_words(val)

        text = re.sub(r"\b\d+\b", repl_int, text)
        return text

    def _normalize_acronyms(self, text: str) -> str:
        # Avoid common English uppercase words (e.g. I, A, AM, PM, OK)
        common_words = {"A", "I", "AN", "AM", "PM", "OK", "OR", "IF", "IN", "ON", "AT", "TO", "BY", "OF", "THE", "AND", "FOR"}

        def repl_acronym(match):
            word = match.group(0)
            if word in common_words or "-" in word:
                return word
            return "-".join(list(word))

        # Match standalone uppercase acronyms that are NOT already part of a hyphenated word
        return re.sub(r"(?<![A-Za-z\-])[A-Z]{2,5}(?![A-Za-z\-])", repl_acronym, text)

