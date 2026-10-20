"""Accessibility helpers for the Streamlit UI.

This module owns three things the rest of the UI depends on:

* a single :func:`heading` helper so the page keeps one predictable heading
  outline (Streamlit maps ``st.title``/``st.header``/``st.subheader`` to
  ``h1``/``h2``/``h3`` and plain markdown to ``h4`` and deeper);
* contrast arithmetic and a small stylesheet that lifts Streamlit's default
  palette above a 4.5:1 text contrast ratio in both the light and the dark
  theme (the stylesheet uses ``light-dark()`` because Streamlit sets
  ``color-scheme`` on its app container);
* escaping helpers so untrusted text cannot inject markup into a heading.

The colour values below were measured against Streamlit 1.66 in a headless
browser; see ``docs/accessibility.md`` for the method, the measurements and
the defects that could not be fixed inside the framework.  Nothing here is a
WCAG conformance claim.
"""

from __future__ import annotations

import re

MIN_CONTRAST = 4.5

# --- Streamlit 1.66 default palettes, measured in the browser -----------------
# Light theme
LIGHT_BACKGROUND = "#ffffff"
LIGHT_TEXT = "#31333f"
LIGHT_CODE_BACKGROUND = "#f8f9fb"
LIGHT_SUCCESS_BACKGROUND = "#e9f9ee"
LIGHT_SUCCESS_TEXT = "#158237"
# Dark theme
DARK_BACKGROUND = "#0e1117"
DARK_TEXT = "#fafafa"
DARK_CODE_BACKGROUND = "#1a1c24"
DARK_SUCCESS_BACKGROUND = "#173828"
DARK_SUCCESS_TEXT = "#5ce488"

# Accent colour Streamlit uses in both themes.
STREAMLIT_PRIMARY = "#ff4b4b"

# The JSON viewer (react-json-view) paints its tokens with inline styles, so
# they can only be corrected with `!important` rules that match the inline
# declaration.  Each entry is (inline declaration, colour, background).
JSON_TOKENS = (
    # light theme
    ("color: rgb(38, 139, 210)", "#268bd2", LIGHT_BACKGROUND),
    ("color: rgb(203, 75, 22)", "#cb4b16", LIGHT_BACKGROUND),
    ("color: rgb(42, 161, 152)", "#2aa198", LIGHT_BACKGROUND),
    ("color: rgb(133, 153, 0)", "#859900", LIGHT_BACKGROUND),
    ("color: rgb(108, 113, 196)", "#6c71c4", LIGHT_BACKGROUND),
    ("color: rgb(220, 50, 47)", "#dc322f", "#ebebeb"),
    # dark theme
    ("color: rgb(102, 217, 239)", "#66d9f9", DARK_BACKGROUND),
    ("color: rgb(204, 102, 51)", "#cc6633", DARK_BACKGROUND),
    ("color: rgb(253, 151, 31)", "#fd971f", DARK_BACKGROUND),
    ("color: rgb(161, 239, 228)", "#a1efe4", DARK_BACKGROUND),
    ("color: rgb(174, 129, 255)", "#ae81ff", DARK_BACKGROUND),
    ("color: rgb(244, 191, 117)", "#f4bf75", "#49483e"),
)

_CAPTION_MIN_OPACITY = 0.6  # Streamlit's own value for st.caption

_MARKDOWN_METACHARACTERS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>])")


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Return the 8-bit RGB channels of a ``#rgb``/``#rrggbb`` colour."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(channel * 2 for channel in text)
    if len(text) != 6:
        raise ValueError(f"Unsupported colour: {value!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"Unsupported colour: {value!r}") from exc


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of a hex colour."""
    channels = []
    for channel in hex_to_rgb(colour):
        ratio = channel / 255
        channels.append(ratio / 12.92 if ratio <= 0.03928 else ((ratio + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 - 21.0)."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def blend(foreground: str, background: str, alpha: float) -> str:
    """Composite ``foreground`` over ``background`` with the given alpha."""
    alpha = min(max(alpha, 0.0), 1.0)
    first = hex_to_rgb(foreground)
    second = hex_to_rgb(background)
    mixed = [round(f * alpha + b * (1 - alpha)) for f, b in zip(first, second, strict=True)]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def mix(colour: str, other: str, amount: float) -> str:
    """Mix ``amount`` of ``other`` into ``colour`` (0 keeps colour, 1 = other)."""
    amount = min(max(amount, 0.0), 1.0)
    first = hex_to_rgb(colour)
    second = hex_to_rgb(other)
    mixed = [round(f * (1 - amount) + s * amount) for f, s in zip(first, second, strict=True)]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def accessible_variant(
    colour: str,
    background: str,
    minimum: float = MIN_CONTRAST,
    towards: str | None = None,
) -> str:
    """Return ``colour`` adjusted until it reaches ``minimum`` against ``background``.

    The colour is moved away from the background (towards black on light
    backgrounds, towards white on dark ones) which keeps its hue family while
    raising the contrast ratio.  The smallest possible move is used, so the
    original colour is preserved as far as the ratio allows.
    """
    if contrast_ratio(colour, background) >= minimum:
        return colour.lower()
    target = towards
    if target is None:
        target = "#000000" if relative_luminance(background) > 0.5 else "#ffffff"
    low, high = 0.0, 1.0  # low = no movement, high = full movement towards target
    best = colour.lower()
    for _ in range(40):
        movement = (low + high) / 2
        candidate = mix(colour, target, movement)
        if contrast_ratio(candidate, background) >= minimum:
            best, high = candidate, movement
        else:
            low = movement
    # ``mix`` rounds to 8-bit channels, so re-check the rounded value and
    # move a little further from the background if rounding shaved the ratio.
    guard = 0
    while contrast_ratio(best, background) < minimum and guard < 50:
        best = mix(best, target, 0.05)
        guard += 1
    return best.lower()


def minimum_opacity(
    foreground: str,
    background: str,
    minimum: float = MIN_CONTRAST,
    start: float = _CAPTION_MIN_OPACITY,
) -> float:
    """Smallest opacity at which ``foreground`` over ``background`` passes.

    Streamlit renders captions at 60% opacity, which is 3.69:1 on the default
    white background.  This returns the smallest opacity that is still
    legible, so the muted look is kept as far as the ratio allows.
    """
    opacity = start
    while opacity <= 1.0:
        if contrast_ratio(blend(foreground, background, opacity), background) >= minimum:
            return round(opacity, 2)
        opacity = round(opacity + 0.01, 2)
    return 1.0


def escape_markdown(text: str) -> str:
    """Escape markdown metacharacters so untrusted text cannot alter markup."""
    return _MARKDOWN_METACHARACTERS.sub(r"\\\1", text)


def heading(st, text: str, level: int) -> None:
    """Render a heading at ``level`` (2 = section, 3 = subsection, 4+ = detail).

    Streamlit renders ``st.title`` as ``h1``, ``st.header`` as ``h2`` and
    ``st.subheader`` as ``h3``; levels below 4 are written as markdown so the
    text cannot inject markup of its own.
    """
    if level <= 1:
        st.title(text)
    elif level == 2:
        st.header(text)
    elif level == 3:
        st.subheader(text)
    else:
        st.markdown(f"{'#' * min(level, 6)} {escape_markdown(text)}")


def _pair(light: str, dark: str) -> str:
    """A CSS value that follows Streamlit's ``color-scheme`` on the app."""
    return f"light-dark({light}, {dark})"


def _multiselect_tag_rule() -> str:
    """Selected-value chips: white text on Streamlit's accent colour is 3.3:1."""
    background = accessible_variant(STREAMLIT_PRIMARY, "#ffffff")
    return (
        '[data-testid="stMultiSelectTagsContainer"] span[data-tag] '
        f"{{ background-color: {background}; }}"
    )


def _primary_button_rule() -> str:
    """Primary buttons: white text on Streamlit's accent colour is 3.3:1."""
    background = accessible_variant(STREAMLIT_PRIMARY, "#ffffff")
    return f'[data-testid="stBaseButton-primary"] {{ background-color: {background} !important; }}'


def _code_block_rule() -> str:
    """Make the code block scrollable with the keyboard.

    ``st.code`` renders a ``<pre>`` with ``overflow: auto`` and no tab stop,
    so the block could not be scrolled with the keyboard.  The inner ``pre``
    is made non-scrolling and the surrounding (already focusable) container
    becomes the scroll region instead.
    """
    light_background = LIGHT_CODE_BACKGROUND
    dark_background = DARK_CODE_BACKGROUND
    light_colour = LIGHT_TEXT
    dark_colour = DARK_TEXT
    return (
        '[data-testid="stCode"] { overflow: auto; '
        f"background-color: {light_background}; color: {light_colour}; "
        f"background-color: {_pair(light_background, dark_background)}; "
        f"color: {_pair(light_colour, dark_colour)}; }}"
        '[data-testid="stCode"] pre { overflow: visible; }'
    )


def _json_token_rules() -> list[str]:
    """Correct the JSON viewer's inline syntax colours."""
    rules = []
    for declaration, colour, background in JSON_TOKENS:
        if contrast_ratio(colour, background) >= MIN_CONTRAST:
            continue
        corrected = accessible_variant(colour, background)
        rules.append(
            f'[data-testid="stJson"] [style*="{declaration}"] '
            f"{{ color: {corrected} !important; opacity: 1 !important; }}"
        )
    return rules


def build_stylesheet() -> str:
    """Return the CSS overlay that corrects Streamlit's default palette.

    Only colours that Streamlit itself renders below a 4.5:1 text contrast
    ratio are corrected.  Each value is derived from the measured palette
    with :func:`accessible_variant`, so the generated stylesheet can be
    re-checked by the test suite.  ``light-dark()`` keeps the overlay correct
    in both the light and the dark theme: Streamlit sets ``color-scheme`` on
    its app container, and browsers without ``light-dark()`` fall back to the
    first (light) declaration.
    """
    caption_opacity = minimum_opacity(LIGHT_TEXT, LIGHT_BACKGROUND)
    success_light = accessible_variant(LIGHT_SUCCESS_TEXT, LIGHT_SUCCESS_BACKGROUND)
    success_dark = accessible_variant(DARK_SUCCESS_TEXT, DARK_SUCCESS_BACKGROUND)
    code_light = accessible_variant(STREAMLIT_PRIMARY, LIGHT_CODE_BACKGROUND)
    code_dark = accessible_variant(STREAMLIT_PRIMARY, DARK_CODE_BACKGROUND)
    slider_light = accessible_variant(STREAMLIT_PRIMARY, LIGHT_BACKGROUND)
    slider_dark = accessible_variant(STREAMLIT_PRIMARY, DARK_BACKGROUND)

    rules: list[str] = [
        "/* Streamlit renders selected-value chips with white text on the accent",
        "   colour (3.3:1). The chip background is darkened instead. */",
        _multiselect_tag_rule(),
        "/* Primary buttons use the same accent colour with white text. */",
        _primary_button_rule(),
        "/* The code block is scrollable with the keyboard. */",
        _code_block_rule(),
        "/* st.caption is rendered at 60% opacity (3.69:1 on white). */",
        f'[data-testid="stCaptionContainer"] {{ opacity: {caption_opacity}; }}',
        "/* st.success body text was 4.49:1 on the success background. */",
        (
            '[data-testid="stAlertContentSuccess"] p '
            f"{{ color: {success_light}; color: {_pair(success_light, success_dark)}; }}"
        ),
        "/* Markdown/code highlight colours in the syntax theme. */",
        (
            '[data-testid="stCode"] .token.title.important, '
            '[data-testid="stCode"] .token.table-header.important '
            f"{{ color: {code_light}; color: {_pair(code_light, code_dark)}; }}"
        ),
        "/* The slider's floating value label used the accent colour. */",
        (
            '[data-testid="stSliderThumbValue"] '
            f"{{ color: {slider_light}; color: {_pair(slider_light, slider_dark)}; }}"
        ),
        "/* The JSON viewer paints its tokens with inline styles. */",
        *_json_token_rules(),
    ]
    return "\n".join(rules)


def apply_accessibility(st) -> None:
    """Inject the stylesheet into the running app."""
    st.markdown(f"<style>{build_stylesheet()}</style>", unsafe_allow_html=True)
