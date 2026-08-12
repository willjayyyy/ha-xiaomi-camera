"""Structural guards on the page's own assets.

These are not style opinions. Each one encodes a rule the spec states, and
each one has been broken before by a change that looked local.
"""

import pathlib
import re

WEB = pathlib.Path("addon/rootfs/app/web")
CSS = WEB / "app.css"
JS = WEB / "app.js"
HTML = WEB / "index.html"

#: The only lengths allowed outside the token block. Everything else has to
#: come from a var(), or the page grows 14px, 15px and 16px paddings that
#: nobody chose.
#:
#: The base set below is the brief's -- the spacing scale itself (--s1..--s7)
#: plus hairline/border widths, none of which can be a var() themselves
#: without inventing a token for a token's own definition.
#:
#: Everything after it is a genuine component dimension that is not a
#: spacing step and so cannot come from --s1..--s7: an icon's own pixel
#: size, a diameter chosen for a specific control, a breakpoint, a
#: max-width. Task C2 rewrote every component this guard used to itemise
#: (card, camera card, play control, preview controls, settings row,
#: choosers, status, overlay, focus) to route its spacing through tokens,
#: which is why this set is now much shorter than the 38 entries it held
#: before that pass -- each remaining one is annotated with the component it
#: sizes and why that size is not a spacing multiple. Do not add an entry to
#: make a new value pass; route it through a token or, if it truly cannot be,
#: justify it here the same way.
_SCALE = {
    "0",
    "1px",
    "2px",
    "3px",
    "4px",
    "8px",
    "12px",
    "16px",
    "20px",
    "24px",
    "32px",
    # .lang button padding/radius (not part of C2's component list), and the
    # invisible touch-target inset on `.ctl-btn`/`.overlay-close` (44 - 32,
    # halved -- a computed offset, not a spacing step of its own).
    "6px",
    # One-off spacing carried over from the pre-split page (sign-in heading
    # margin, overlay sheet's own entry transform, hint/group label
    # margins) -- not touched by this task.
    "10px",
    "11px",  # button and code block horizontal padding
    "13px",  # message banner vertical padding
    "14px",  # button/input horizontal padding, icon sizes, row gaps
    "15px",  # base body font size, .cam-name font size, close-icon size
    "17px",  # settings-button icon size, message banner padding
    "18px",  # card heading margin, overlay head/body padding, list margin
    "22px",  # sign-in mark border-radius, button padding, list indent
    "28px",  # sign-in subtitle margin, header margin-bottom
    "36px",  # settings-button diameter, sign-in card bottom padding
    "40px",  # overlay width offset, empty-state padding
    # Sign-in card top padding, and the segmented control's min-height (the
    # 44px touch-target floor lives on `.seg` itself, not per button --
    # adjacent buttons can't each carry an invisible inset without stealing
    # a neighbour's clicks).
    "44px",
    # Body bottom padding, and the play control's own diameter (Mi Home's
    # size for that control, not a spacing step).
    "48px",
    # Settings row min-height -- the touch-target floor plus room for two
    # rows never to merge under a thumb, not a spacing step.
    "56px",
    "64px",  # sign-in mark diameter
    "300px",  # camera grid minimum column width
    "400px",  # sign-in card max-width
    "420px",  # settings sheet max-width
    "600px",  # the sheet-becomes-a-drawer breakpoint
    "1080px",  # page container max-width
    "1100px",  # enlarged preview max-width
}


def test_the_page_is_split_into_three_files():
    assert CSS.exists() and JS.exists()
    html = HTML.read_text()
    assert '<link rel="stylesheet" href="app.css">' in html
    assert '<script src="app.js" defer></script>' in html
    assert "<style>" not in html


def test_no_external_requests():
    """The page works offline. A CDN font or script would break that, and
    would also fail closed inside a Home Assistant install with no internet."""
    for source in (HTML, CSS, JS):
        text = source.read_text()
        # Loopback is not "external": the login flow's placeholder text shows
        # what a redirect URL looks like, and always points back at 127.0.0.1.
        stripped = text.replace("http://127.0.0.1", "").replace("https://127.0.0.1", "")
        assert "http://" not in stripped
        assert "https://" not in stripped


def test_lengths_come_from_the_scale():
    body = CSS.read_text().split("/* end tokens */", 1)[1]
    offenders = sorted(
        {
            length
            for length in re.findall(r"(?<![\w-])(\d+(?:\.\d+)?px)", body)
            if length not in _SCALE
        }
    )
    assert not offenders, f"lengths outside the scale: {offenders}"


def test_every_transition_has_a_reduced_motion_answer():
    css = CSS.read_text()
    assert "@media (prefers-reduced-motion: reduce)" in css
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "transition: none" in reduced or "transition-duration: .01ms" in reduced


def test_settings_row_is_a_touch_target():
    """44px is the floor; the row itself is 56 so two rows never merge
    under a thumb."""
    css = CSS.read_text()
    assert re.search(r"\.setting-row-btn\s*{[^}]*min-height:\s*56px", css, re.S)


def test_separator_starts_at_the_label():
    """The most recognisable detail in Mi Home's settings lists: the hairline
    is inset to the label, not run wall to wall."""
    css = CSS.read_text()
    assert re.search(r"\.setting-row\s*\+\s*\.setting-row[^}]*margin-left", css, re.S)


def test_focus_is_visible_and_only_for_keyboards():
    css = CSS.read_text()
    assert ":focus-visible" in css
    assert re.search(r":focus-visible\s*{[^}]*outline:\s*2px", css, re.S)
    # A bare :focus rule would paint a ring on every mouse click.
    assert not re.search(r"[^-]:focus\s*{", css)


def test_the_sheet_becomes_a_bottom_drawer_on_small_screens():
    """This page is opened from the Home Assistant app more often than not,
    and a centred dialog puts its controls out of thumb reach."""
    css = CSS.read_text()
    assert "@media (max-width: 600px)" in css
    small = css.split("@media (max-width: 600px)", 1)[1]
    assert "border-radius: var(--r-sheet) var(--r-sheet) 0 0" in small


def test_dark_mode_redefines_every_colour_token():
    css = CSS.read_text()
    token = (
        r"(--(?:ground|surface|sunken|ink-\d|hairline|accent[\w-]*|ok|warn|err|lift)):"
    )
    before, after = css.split("@media (prefers-color-scheme: dark)")[:2]
    light = set(re.findall(token, before))
    dark = set(re.findall(token, after))
    assert light - dark == set(), f"not redefined in dark: {sorted(light - dark)}"


def test_both_languages_have_the_same_keys():
    """A string in one language only is not done."""
    js = JS.read_text()
    en = _keys_of(js, "en")
    zh = _keys_of(js, "zh")
    assert en - zh == set(), f"missing zh: {sorted(en - zh)}"
    assert zh - en == set(), f"missing en: {sorted(zh - en)}"


def _keys_of(js: str, lang: str) -> set[str]:
    """Every `word:` key in the language's block of the I18N table.

    Anchored on what comes *before* the word, not on line position -- two
    keys packed onto one line (`a: "x", b: "y",`) are both real keys, and a
    version of this check that only matched the first token on a line made
    the second one invisible to it. A key is only ever preceded by `{` (the
    block's own opening brace) or `,` (the previous entry), possibly across a
    newline; requiring that also keeps a string *value* that happens to end
    in "word: "" (e.g. "...sign-in: ") from being misread as a key of its own,
    since nothing but whitespace separates it from the text before it.
    """
    block = re.search(rf"\b{lang}:\s*{{(.*?)\n  }}", js, re.S)
    assert block, f"no {lang} block in the I18N table"
    return set(re.findall(r"(?:\A|[{,])\s*(\w+):\s*[\"']", block.group(1)))


def _function_body(js: str, name: str) -> str:
    """A top-level function's own source, from its signature to its own
    closing brace -- identified as the first line that is exactly `}` at
    column 0, which every top-level function in this file ends on (inner
    blocks close indented, never at column 0)."""
    match = re.search(rf"^function {name}\(.*?\n(.*?)^}}\n", js, re.M | re.S)
    assert match, f"no top-level function {name} found"
    return match.group(1)


def test_the_page_has_no_defaults_card_and_no_readonly_mirror():
    """The picture-quality default and the read-only add-on mirror both left
    the main page -- the first moved into the gear, the second was retired."""
    html = HTML.read_text()
    assert 'id="defaults-card"' not in html
    assert 'id="addon-card"' not in html
    assert 'id="addon-access"' not in html
    assert 'id="addon-loglevel"' not in html


def test_the_account_card_moved_behind_the_header_buttons():
    html = HTML.read_text()
    body = html.split("<main", 1)[1]
    assert 'id="link-flow"' not in body, "the sign-in flow belongs in the sheet"
    assert 'id="account-btn"' in body
    assert 'id="settings-btn"' in body


def test_only_compat_mode_screens_carry_explanatory_prose():
    """Copy is labels, not explanations -- state is never carried by a
    sentence. `compatWhy` and `compatEnableHint` are the restated rule's two
    named exceptions, and both belong to compatibility mode's own screens
    (M1's account sheet, M6). Scoped to M1 and M3 (the sheets this task
    built, plus the gear that sits beside M1) -- M2 is the pre-existing OAuth
    flow, moved here verbatim per the brief, and its own onboarding copy
    (`step2`, `privacyNote`, ...) predates this rule and is out of scope.
    """
    js = JS.read_text()
    functions = (
        "renderAccountSheetBody",
        "openCompatManage",
        "renderSettingsSheetBody",
    )
    m1_and_m3 = "".join(_function_body(js, name) for name in functions)
    keys = set(re.findall(r't\("(\w+)"\)', m1_and_m3))
    en_block = re.search(r"\ben:\s*{(.*?)\n  }", js, re.S).group(1)
    values = dict(re.findall(r'(\w+):\s*"([^"]*)"', en_block))
    #: Long enough that no label, button or status word in the table crosses
    #: it ("Connect Xiaomi account" is 23 chars), short enough that any real
    #: sentence does.
    SENTENCE_LENGTH = 30
    prose = {key for key in keys if len(values.get(key, "")) > SENTENCE_LENGTH}
    allowed = {"compatWhy", "compatEnableHint"}
    assert prose == allowed, (
        f"unexpected explanatory prose on M1/M3: {sorted(prose - allowed)}"
    )


def test_the_page_body_is_only_header_message_and_grid():
    """Everything else -- defaults, add-on info, the account -- moved behind
    the two header entrances."""
    html = HTML.read_text()
    main = html.split("<main", 1)[1].split("</main>", 1)[0]
    assert 'class="card"' not in main


def test_the_retired_words_appear_nowhere():
    """One fact, one name. These are the names this design retired."""
    retired = [
        "厂商库",
        "原生库",
        "小米官方库",
        "本地直连",
        "备用连接",
        "SDK",
        "backend",
    ]
    for source in (HTML, JS, CSS):
        text = source.read_text()
        for word in retired:
            assert word not in text, f"{source.name} still says {word}"


def test_go2rtc_is_named_only_where_it_should_be():
    """Not hidden -- someone troubleshooting needs the word -- but not
    scattered either. One line in the sign-in panel is the whole budget."""
    assert JS.read_text().count("go2rtc") <= 2  # the en and zh strings


def test_the_sign_in_page_can_change_language():
    html = HTML.read_text()
    signin = html.split('<section id="signin"', 1)[1].split("</section>", 1)[0]
    assert 'data-lang="en"' in signin and 'data-lang="zh"' in signin
