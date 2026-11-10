"""Every text and control color pair in the design tokens meets WCAG 2.2 AA in both themes."""

from __future__ import annotations

import itertools

import pytest

from src.desktop.marks import MISSING_BACKING_SELECTED_TOKEN, MISSING_BACKING_TOKEN, MISSING_TEXT_TOKEN
from src.desktop.theme import tokens

# WCAG 2.2 success criteria: 1.4.3 (text) and 1.4.11 (user interface parts and graphics).
AA_TEXT = 4.5
AA_NON_TEXT = 3.0

THEMES = ("light", "dark")
TEXT_INKS = ("ink", "ink_2", "ink_3")
SURFACES = ("paper", "sheet", "sheet_2", "sel")
SIGNALS = ("s_pip", "s_etco2", "s_hr", "s_spo2", "s_map")


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """Relative luminance of ``#rrggbb`` as WCAG 2.2 defines it."""
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(first: str, second: str) -> float:
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# (foreground token, background token, ratio the pair must reach)
PAIRS: list[tuple[str, str, float]] = [
    *[(ink, surface, AA_TEXT) for ink, surface in itertools.product(TEXT_INKS, SURFACES)],
    # Hover and pressed backgrounds of outlined and text buttons carry ink text.
    *[("ink", surface, AA_TEXT) for surface in ("rule_faint", "rule")],
    ("on_accent", "accent", AA_TEXT),
    ("on_accent", "accent_hover", AA_TEXT),
    # Links, citations and the 2 px focus ring are all the accent on paper or sheet.
    ("accent", "paper", AA_TEXT),
    ("accent", "sheet", AA_TEXT),
    ("accent_hover", "paper", AA_TEXT),
    # Accent text (links, citations) also sits on the selected row.
    ("accent", "sel", AA_TEXT),
    # Text on a missing-data cell sits on a backing of the row's own ground, never on the hatch itself.
    (MISSING_TEXT_TOKEN, MISSING_BACKING_TOKEN, AA_TEXT),
    (MISSING_TEXT_TOKEN, MISSING_BACKING_SELECTED_TOKEN, AA_TEXT),
    ("practice", "paper", AA_TEXT),
    ("rule_strong", "paper", AA_NON_TEXT),
    ("rule_strong", "sheet", AA_NON_TEXT),
    ("practice_bar", "paper", AA_NON_TEXT),
    *[(signal, "paper", AA_NON_TEXT) for signal in SIGNALS],
    # The case page: trace swatches in the inspector table sit on the side panel's sheet, and the cursor's rings,
    # the selected episode's outline, the minute slider's handle and the ruler's pointer are accent on paper or sheet.
    *[(signal, "sheet", AA_NON_TEXT) for signal in SIGNALS],
    ("accent", "sel", AA_NON_TEXT),
    ("ink_3", "sheet", AA_TEXT),
    ("ink_2", "paper", AA_TEXT),
    ("ink", "sel", AA_TEXT),
    ("ink_3", "sel", AA_TEXT),
]


def test_the_formula_matches_wcag_reference_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    # The usual textbook near-miss: #777 on white is 4.48:1 and fails AA for text.
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast("#767676", "#ffffff") >= AA_TEXT


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("foreground", "background", "needed"), PAIRS)
def test_color_pair_meets_aa(theme, foreground, background, needed):
    palette = tokens(theme)
    ratio = contrast(palette[foreground], palette[background])
    assert ratio >= needed, f"{theme}: {foreground} {palette[foreground]} on {background} {palette[background]} is {ratio:.2f}:1, needs {needed}:1"


@pytest.mark.parametrize("theme", THEMES)
def test_every_color_the_design_names_exists(theme):
    palette = tokens(theme)
    names = {
        "paper", "sheet", "sheet_2", "ink", "ink_2", "ink_3", "rule", "rule_faint", "rule_strong",
        "grid", "accent", "accent_hover", "on_accent", "sel", "practice", "practice_bar", "hatch",
        *SIGNALS,
    }
    assert names <= palette.keys()
    for name in names - {"hatch"}:
        assert len(palette[name]) == 7 and palette[name].startswith("#"), name
