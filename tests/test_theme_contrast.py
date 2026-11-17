"""Every text and control color pair in the design tokens meets WCAG 2.2 AA in both themes."""

from __future__ import annotations

import itertools
import re

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
    # The Cases page: the summary charts' bars and ticks and the score bars in the table are ink_2 on paper (or on
    # the selected row), the median line is ink, and the page's text sits on paper, the selected row or the preview's
    # sheet. The traces in the preview are signal hues on sheet, checked above.
    ("ink_2", "paper", AA_NON_TEXT),
    ("ink_2", "sel", AA_NON_TEXT),
    ("ink", "paper", AA_NON_TEXT),
    ("ink_3", "paper", AA_NON_TEXT),
    # The rules drawer: its text sits on the sheet, or on the selected tint for the current rule (all three inks are
    # text pairs above). The rulers' limit marks are ink, their axis ink_3, the focus ring and the current-rule bar accent.
    ("ink", "sheet", AA_NON_TEXT),
    ("ink_3", "sheet", AA_NON_TEXT),
    ("ink_2", "sheet", AA_NON_TEXT),
    ("accent", "sheet", AA_NON_TEXT),
    ("accent", "sel", AA_NON_TEXT),
    # Medication and vitals: the colour swatch beside a vital's name in the table also sits on the selected row, and
    # the cursor's tag is on_accent on accent (above). WHO checklist: the tick on a done line is paper on ink_2; the
    # index card is the sheet with ink, ink_3 and rule_strong on it (above); a line the card leaves out is ink_3 on paper.
    *[(signal, "sel", AA_NON_TEXT) for signal in SIGNALS],
    ("paper", "ink_2", AA_NON_TEXT),
    # First-time setup's step list: in the light theme it is ink with paper text (rule for the quieter lines); in the
    # dark theme it is sheet_2 with ink text, ink_3 for the quieter lines, and the current number is sheet_2 on ink.
    ("paper", "ink", AA_TEXT),
    ("rule", "ink", AA_TEXT),
    ("sheet_2", "ink", AA_TEXT),
    ("ink_3", "sheet_2", AA_NON_TEXT),
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
def test_the_inspector_ground_and_every_swatch_colour_are_checked_as_pairs(theme):
    """The case page's inspector paints trace swatches on its own ground; each swatch colour must be a pair above."""
    from src.desktop.charts import _SIGNAL_INFO
    from src.desktop.theme import _build_stylesheet

    palette = tokens(theme)
    ground = re.search(r"QFrame#caseInspector \{\s*background:\s*(#[0-9A-Fa-f]{6});", _build_stylesheet(palette))
    assert ground, "the inspector no longer has one plain background in the stylesheet"
    assert ground.group(1).lower() == palette["sheet"].lower(), "the inspector ground changed: update these pairs"
    for token in sorted({info.token for info in _SIGNAL_INFO.values()}):
        needed = AA_TEXT if token in TEXT_INKS else AA_NON_TEXT
        assert (token, "sheet", needed) in PAIRS, f"{token} on the inspector's sheet has no contrast check"


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


@pytest.mark.parametrize("theme", THEMES)
def test_the_cases_preview_ground_is_the_sheet_so_its_text_pairs_are_the_checked_ones(theme):
    from src.desktop.theme import _build_stylesheet

    palette = tokens(theme)
    ground = re.search(r"QFrame#casePreview \{\s*background:\s*(#[0-9A-Fa-f]{6});", _build_stylesheet(palette))
    assert ground, "the cases preview no longer has one plain background in the stylesheet"
    assert ground.group(1).lower() == palette["sheet"].lower(), "the preview ground changed: update these pairs"
    for ink in TEXT_INKS:
        assert (ink, "sheet", AA_TEXT) in PAIRS


def test_every_token_the_cases_page_draws_text_in_is_checked_on_each_ground_it_sits_on():
    """The table's text tokens sit on paper and on the selected row; each is a text pair above."""
    import pandas as pd

    from src.desktop.cases_overview import TABLE_COLUMNS, _CasesDelegate, _CasesModel

    model = _CasesModel()
    model.set_frame(pd.DataFrame({"PID": ["A", "B"], **{c.key: [0, 1] for c in TABLE_COLUMNS if c.key != "PID"}}))
    tokens_used = {_CasesDelegate.text_token(model.index(row, col)) for row in range(2) for col in range(len(TABLE_COLUMNS))}
    assert tokens_used == {"ink", "ink_2", "ink_3"}
    for token in tokens_used:
        for ground in ("paper", "sel"):
            assert (token, ground, AA_TEXT) in PAIRS, (token, ground)


@pytest.mark.parametrize("theme", THEMES)
def test_the_handoff_side_panel_ground_is_the_sheet_and_its_boxes_are_paper(theme):
    """The Handoff page's colours are the tokens already checked: the panel on the sheet, its boxes on paper."""
    from src.desktop.theme import _build_stylesheet

    palette = tokens(theme)
    sheet = _build_stylesheet(palette)
    ground = re.search(r"QFrame#handoffSide \{\s*background:\s*(#[0-9A-Fa-f]{6});", sheet)
    assert ground, "the handoff side panel no longer has one plain background in the stylesheet"
    assert ground.group(1).lower() == palette["sheet"].lower(), "the panel ground changed: update these pairs"
    boxes = re.search(r"QFrame#handoffSide QPlainTextEdit \{\s*background:\s*(#[0-9A-Fa-f]{6});", sheet)
    assert boxes and boxes.group(1).lower() == palette["paper"].lower(), "the boxes' ground changed: update these pairs"
    for ink in TEXT_INKS:
        assert (ink, "sheet", AA_TEXT) in PAIRS and (ink, "paper", AA_TEXT) in PAIRS
    for pair in (("accent", "sheet", AA_TEXT), ("on_accent", "accent", AA_TEXT), ("accent", "paper", AA_TEXT),
                 ("rule_strong", "paper", AA_NON_TEXT), ("ink", "sel", AA_TEXT)):
        assert pair in PAIRS, pair


@pytest.mark.parametrize("theme", THEMES)
def test_the_rules_drawer_ground_is_the_sheet_and_the_current_rule_sits_on_the_selected_tint(theme):
    """The drawer's colours are the tokens already checked: text on the sheet, the current rule's text on sel."""
    from src.desktop.theme import _build_stylesheet

    palette = tokens(theme)
    sheet = _build_stylesheet(palette)
    ground = re.search(r"QFrame#rulesDrawer \{\s*background:\s*(#[0-9A-Fa-f]{6});", sheet)
    assert ground and ground.group(1).lower() == palette["sheet"].lower(), "the drawer ground changed: update these pairs"
    tint = re.search(r'QFrame#ruleRow\[highlight="true"\] \{\s*background:\s*(#[0-9A-Fa-f]{6});', sheet)
    assert tint and tint.group(1).lower() == palette["sel"].lower(), "the current rule's tint changed: update these pairs"
    bar = re.search(r'QFrame#ruleRow\[highlight="true"\] \{[^}]*border-left:\s*3px solid (#[0-9A-Fa-f]{6})', sheet)
    assert bar and bar.group(1).lower() == palette["accent"].lower()
    for ink in TEXT_INKS:
        assert (ink, "sheet", AA_TEXT) in PAIRS and (ink, "sel", AA_TEXT) in PAIRS
    for pair in (("accent", "sheet", AA_TEXT), ("accent", "sel", AA_NON_TEXT), ("ink", "sheet", AA_NON_TEXT)):
        assert pair in PAIRS, pair


@pytest.mark.parametrize("theme", THEMES)
def test_the_who_index_card_is_the_sheet_with_a_strong_edge_and_its_lines_are_ink(theme):
    """The WHO page's colours are tokens already checked: the card on the sheet, its edge, its lines and its tick."""
    from src.desktop.theme import _build_stylesheet

    palette = tokens(theme)
    sheet = _build_stylesheet(palette)
    card = re.search(r"QFrame#indexCard \{\s*background:\s*(#[0-9A-Fa-f]{6});\s*border:\s*1px solid\s*(#[0-9A-Fa-f]{6});", sheet)
    assert card, "the index card no longer has one plain background and edge in the stylesheet"
    assert card.group(1).lower() == palette["sheet"].lower() and card.group(2).lower() == palette["rule_strong"].lower()
    key = re.search(r"QLabel#cardKey \{\s*color:\s*(#[0-9A-Fa-f]{6});", sheet)
    value = re.search(r"QLabel#cardValue \{\s*color:\s*(#[0-9A-Fa-f]{6});", sheet)
    assert key and key.group(1).lower() == palette["ink_3"].lower()
    assert value and value.group(1).lower() == palette["ink"].lower()
    for pair in (("ink_3", "sheet", AA_TEXT), ("ink", "sheet", AA_TEXT), ("rule_strong", "sheet", AA_NON_TEXT),
                 ("paper", "ink_2", AA_NON_TEXT), ("ink_3", "paper", AA_TEXT)):
        assert pair in PAIRS, pair
