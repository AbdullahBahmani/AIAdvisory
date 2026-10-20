"""Locale-aware strings, numbers, currency, percentages and dates.

This package is the single string layer of the product.  It is deliberately
dependency-free: no gettext, no Babel, no i18n library.  It consists of

* a **catalogue**: one JSON file per locale under ``app/localization/locales``
  that maps a stable string id to the translated text.  ``en.json`` is the
  default locale and mirrors the English text that stays in the source code;
* a **translator**: resolves an id in the active locale, falls back to
  English when a translation is missing and *reports* the gap instead of
  swallowing it;
* **formatters**: numbers, currency, percentages and dates rendered with the
  conventions of the active locale.  Money is always handled with
  :class:`decimal.Decimal`; a float never touches a monetary value.

Two rules shape the whole layer:

* **Ids are stable.**  Strings are referenced by id (``"ui.intake.save"``),
  never by their English wording, so the English text can be edited without
  invalidating a translation.
* **Values are never translated.**  Policy terms, rule ids, recommendation
  category values, ISO-8601 timestamps and machine-readable field names are
  the contract of the system.  Only display labels are localized; the values
  themselves are reproduced exactly as recorded.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

LOCALE_DIR = Path(__file__).with_name("locales")
DEFAULT_LOCALE = "en"


@dataclass(frozen=True)
class Locale:
    """The conventions of one locale.

    The field names describe *display*, never stored data.  ``date_format``
    and ``time_format`` are :func:`time.strftime` patterns applied to a
    timestamp that is stored in ISO-8601 and is never rewritten.
    """

    code: str
    endonym: str
    decimal_separator: str = "."
    thousands_separator: str = ","
    group_size: int = 3
    currency_token: str = "code"  # "code" (NOK) or "symbol" (kr)
    currency_position: str = "after"  # "before" or "after"
    currency_separator: str = " "
    percent_separator: str = ""
    percent_decimals: int = 0
    date_format: str = "%Y-%m-%d"
    time_format: str = "%H:%M"


#: The supported locales.  English is the default and the fallback locale.
LOCALES: dict[str, Locale] = {
    "en": Locale(
        code="en",
        endonym="English",
        decimal_separator=".",
        thousands_separator=",",
        currency_token="code",
        currency_position="before",
        currency_separator=" ",
        percent_separator="",
        percent_decimals=0,
        date_format="%Y-%m-%d",
        time_format="%H:%M",
    ),
    "nb": Locale(
        code="nb",
        endonym="Norsk bokmål",
        decimal_separator=",",
        thousands_separator="\u00a0",
        currency_token="symbol",
        currency_position="after",
        currency_separator="\u00a0",
        percent_separator=" ",
        percent_decimals=0,
        date_format="%d.%m.%Y",
        time_format="%H:%M",
    ),
}

#: ISO 4217 minor units.  The number of decimals is a property of the
#: *currency*, not of the locale: NOK has two, JPY has none.
CURRENCY_EXPONENTS: dict[str, int] = {"JPY": 0, "KRW": 0}

#: The symbol used when a locale asks for symbols rather than ISO codes.
CURRENCY_SYMBOLS: dict[str, str] = {
    "NOK": "kr",
    "SEK": "kr",
    "DKK": "kr",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
}


class Catalogue:
    """The translatable strings of every locale, loaded from JSON files."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or LOCALE_DIR)
        self._texts: dict[str, dict[str, str]] = {}
        for path in sorted(self.directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Locale catalogue must be an object: {path}")
            self._texts[path.stem] = {str(key): str(value) for key, value in data.items()}

    @property
    def locales(self) -> list[str]:
        return sorted(self._texts)

    def ids(self, locale: str) -> set[str]:
        return set(self._texts.get(locale, {}))

    def text(self, locale: str, string_id: str) -> str | None:
        return self._texts.get(locale, {}).get(string_id)

    def has(self, locale: str, string_id: str) -> bool:
        return string_id in self._texts.get(locale, {})


def default_catalogue() -> Catalogue:
    """Return the catalogue shipped with the application."""
    return Catalogue()


class Translator:
    """Resolves string ids and formats values for one locale."""

    def __init__(self, locale: str = DEFAULT_LOCALE, catalogue: Catalogue | None = None) -> None:
        if locale not in LOCALES:
            raise ValueError(f"Unsupported locale: {locale!r}")
        self.catalogue = catalogue or default_catalogue()
        self._locale = locale
        self._missing: set[str] = set()
        self._unknown: set[str] = set()

    @property
    def locale_code(self) -> str:
        return self._locale

    @property
    def locale(self) -> Locale:
        return LOCALES[self._locale]

    def text(self, string_id: str, default: str | None = None) -> str:
        """Return the text of ``string_id`` in the active locale.

        A missing translation falls back to English.  The fallback is
        recorded in :attr:`missing` so an incomplete catalogue is visible
        instead of silently swallowed.  An id that is unknown in every locale
        is recorded in :attr:`unknown` and rendered as the id itself.
        """
        value = self.catalogue.text(self._locale, string_id)
        if value is not None:
            return value
        fallback = self.catalogue.text(DEFAULT_LOCALE, string_id)
        if fallback is None:
            self._unknown.add(string_id)
            return default if default is not None else string_id
        self._missing.add(string_id)
        return fallback

    def t(self, string_id: str, default: str | None = None, **params: object) -> str:
        """Translate ``string_id`` and interpolate ``params`` into it."""
        text = self.text(string_id, default)
        if not params:
            return text
        return text.format(**params)

    @property
    def missing(self) -> set[str]:
        """Ids that are translated nowhere but in the default locale."""
        return set(self._missing)

    @property
    def unknown(self) -> set[str]:
        """Ids that are defined in no catalogue at all."""
        return set(self._unknown)

    def missing_translations(self) -> list[str]:
        """Ids of the default catalogue that the active locale does not translate."""
        english = self.catalogue.ids(DEFAULT_LOCALE)
        return sorted(english - self.catalogue.ids(self._locale))

    def missing_count(self) -> int:
        """How many strings of the default catalogue the active locale lacks."""
        return len(self.missing_translations())

    # --- formatting -----------------------------------------------------------

    def number(self, value: object, decimals: int | None = None) -> str:
        """Format a number with the separators of the active locale."""
        return format_number(value, decimals, self.locale)

    def currency(self, amount: Decimal, currency: str = "NOK") -> str:
        """Format a monetary amount exactly, without ever using a float."""
        return format_currency(amount, currency, self.locale)

    def percent(self, value: object, decimals: int | None = None) -> str:
        """Format a ratio (0.21) as a percentage (21 %) of the active locale."""
        return format_percent(value, decimals, self.locale)

    def datetime(self, value: str | datetime) -> str:
        """Format an ISO-8601 timestamp for display; the stored value is unchanged."""
        return format_datetime(value, self.locale)

    def date(self, value: str | datetime) -> str:
        moment = parse_timestamp(value)
        if moment is None:
            return str(value)
        return moment.strftime(self.locale.date_format)

    def time(self, value: str | datetime) -> str:
        moment = parse_timestamp(value)
        if moment is None:
            return str(value)
        return moment.strftime(self.locale.time_format)


def to_decimal(value: object) -> Decimal:
    """Convert a value to :class:`~decimal.Decimal` without float rounding."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("A boolean is not a number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # ``str`` of a float is its shortest round-trip representation, so
        # this keeps the decimal the user meant without binary noise.
        return Decimal(str(value))
    return Decimal(str(value).strip())


def format_number(value: object, decimals: int | None, locale: Locale) -> str:
    """Format a number with the separators and precision of ``locale``."""
    number = to_decimal(value)
    if decimals is not None:
        number = number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    text = format(abs(number), "f")
    integer, _, fraction = text.partition(".")
    sign = "-" if number < 0 else ""
    grouped = _group(integer, locale.thousands_separator, locale.group_size)
    if fraction:
        return f"{sign}{grouped}{locale.decimal_separator}{fraction}"
    return f"{sign}{grouped}"


def format_currency(amount: Decimal, currency: str, locale: Locale) -> str:
    """Format a monetary amount with the currency's own minor units.

    The amount is quantized with :class:`~decimal.Decimal`, never converted
    to a float, so ``Decimal("1200.5")`` formats as exactly ``1200.50`` for
    NOK and as ``1200`` for JPY.
    """
    code = str(currency).strip().upper() or "NOK"
    exponent = CURRENCY_EXPONENTS.get(code, 2)
    number = to_decimal(amount).quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)
    text = format_number(number, None, locale)
    if locale.currency_token == "symbol":
        token = CURRENCY_SYMBOLS.get(code, code)
    else:
        token = code
    if locale.currency_position == "before":
        return f"{token}{locale.currency_separator}{text}"
    return f"{text}{locale.currency_separator}{token}"


def format_percent(value: object, decimals: int | None, locale: Locale) -> str:
    """Format a ratio as a percentage of the active locale.

    The rounding is half-even, the same rule Python's ``"{:.0%}"`` format
    uses, so a localized percentage and a formatted one never disagree.
    """
    ratio = to_decimal(value) * 100
    if decimals is None:
        decimals = locale.percent_decimals
    ratio = ratio.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    return f"{format_number(ratio, None, locale)}{locale.percent_separator}%"


def parse_timestamp(value: str | datetime) -> datetime | None:
    """Parse an ISO-8601 timestamp; the stored representation is never rewritten."""
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_datetime(value: str | datetime, locale: Locale) -> str:
    """Render an ISO-8601 timestamp with the date and time patterns of ``locale``."""
    moment = parse_timestamp(value)
    if moment is None:
        return str(value)
    return f"{moment.strftime(locale.date_format)} {moment.strftime(locale.time_format)}"


def _group(digits: str, separator: str, size: int) -> str:
    """Group the integer digits of a number with ``separator``."""
    if not separator or size < 1:
        return digits
    parts = []
    while len(digits) > size:
        parts.append(digits[-size:])
        digits = digits[:-size]
    parts.append(digits)
    return separator.join(reversed(parts))


# --- the active translator ----------------------------------------------------

_current: ContextVar[Translator | None] = ContextVar("localization_translator", default=None)
_original_labels: dict[str, str] = {}


def set_current(translator: Translator) -> None:
    """Make ``translator`` the active one for the current context."""
    _current.set(translator)


def current() -> Translator:
    """Return the active translator (English by default)."""
    translator = _current.get()
    if translator is None:
        translator = Translator(DEFAULT_LOCALE)
        _current.set(translator)
    return translator


def t(string_id: str, default: str | None = None, **params: object) -> str:
    """Translate ``string_id`` in the active locale.

    The English text stays at the call site as the default, so a missing
    translation can never render an empty label; the catalogue in
    ``locales/en.json`` mirrors it and is checked by the test suite.
    """
    return current().t(string_id, default, **params)


def install_labels(translator: Translator, modules: Iterable[object]) -> None:
    """Replace the ``LABEL_*`` constants of ``modules`` with localized text.

    The accessibility audit reads widget labels from the source, so the
    labels are module constants.  Their catalogue id is the qualified
    constant name (``ui.streamlit_app.LABEL_ASSESSMENT_TITLE``), which keeps
    the id and the source in step.
    """
    for module in modules:
        for name, value in list(vars(module).items()):
            if not name.startswith("LABEL_") or not isinstance(value, str):
                continue
            key = f"{module.__name__}.{name}"
            original = _original_labels.setdefault(key, value)
            setattr(module, name, translator.text(key, original))


__all__ = [
    "CURRENCY_EXPONENTS",
    "CURRENCY_SYMBOLS",
    "DEFAULT_LOCALE",
    "LOCALES",
    "LOCALE_DIR",
    "Catalogue",
    "Locale",
    "Translator",
    "current",
    "default_catalogue",
    "format_currency",
    "format_datetime",
    "format_number",
    "format_percent",
    "install_labels",
    "parse_timestamp",
    "set_current",
    "t",
    "to_decimal",
]
