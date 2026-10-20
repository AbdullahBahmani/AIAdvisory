"""The string layer of the product: catalogues, translator and formatters.

See :mod:`app.localization.engine` for the rules the layer follows and
``docs/localization.md`` for how to add a locale.
"""

from app.localization.engine import (
    CURRENCY_EXPONENTS,
    CURRENCY_SYMBOLS,
    DEFAULT_LOCALE,
    LOCALE_DIR,
    LOCALES,
    Catalogue,
    Locale,
    Translator,
    current,
    default_catalogue,
    format_currency,
    format_datetime,
    format_number,
    format_percent,
    install_labels,
    parse_timestamp,
    set_current,
    t,
    to_decimal,
)

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
