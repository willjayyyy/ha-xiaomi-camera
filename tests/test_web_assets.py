"""Structural guards on the page's assets.

The page is built by Vite + Svelte from `addon/web/src` into
`addon/rootfs/app/web`, which the bridge serves. Rules about what the
*components say and name* are checked against the source; rules about *what
the bridge serves* (three files, offline, the token scale) are checked
against the built output. They were one file before the Svelte migration
because the source was the output.

Each guard encodes a rule the spec states, and each has been broken before by
a change that looked local.
"""

import pathlib
import re

import pytest

SOURCE = pathlib.Path("addon/web/src")
#: The design tokens and component styles live in one source stylesheet; the
#: built `app.css` is this file carried through Vite (unminified), so the
#: token-scale and media-query rules are checked here -- where they run
#: without the output being built.
SRC_CSS = SOURCE / "global.css"
WEB = pathlib.Path("addon/rootfs/app/web")
JS = WEB / "app.js"
HTML = WEB / "index.html"
I18N = SOURCE / "lib/i18n.svelte.js"


def _require_built_output():
    """The output-level checks need the web UI built (the image build does it
    in Docker, CI's test job before pytest). Skipped otherwise, so a local
    `python -m pytest` runs the source checks without a build."""
    if not JS.exists():
        pytest.skip("web UI not built; run `cd addon/web && npm run build`")


#: Every source file under `addon/web/src`, for whole-page rules like the
#: retired vocabulary.
_SOURCES = [
    p
    for p in SOURCE.rglob("*")
    if p.is_file() and p.suffix in {".svelte", ".js", ".html", ".css"}
]

#: The only lengths allowed outside the token block. Everything else has to
#: come from a var(), or the page grows 14px, 15px and 16px paddings that
#: nobody chose. Same base set as before the migration -- the token scale and
#: the component dimensions that genuinely are not spacing steps.
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
    "6px",
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
    "44px",
    "48px",
    "56px",
    "64px",  # sign-in mark diameter
    "300px",  # camera grid minimum column width
    "400px",  # sign-in card max-width
    "420px",  # settings sheet max-width
    "600px",  # the sheet-becomes-a-drawer breakpoint
    "1080px",  # page container max-width
    "1100px",  # enlarged preview max-width
    "200px",  # preview-preference panel's own min-width, not a spacing step
}

#: The words this design retired, from `docs/superpowers/specs` -- one fact,
#: one name.
_RETIRED = [
    "厂商库",
    "原生库",
    "小米官方库",
    "本地直连",
    "备用连接",
    "SDK",
    "backend",
]


def _keys_of(lang: str) -> set[str]:
    """Every translation key in one language block of the I18N table."""
    block = re.search(rf"\b{lang}:\s*\{{(.*?)\n  \}},", I18N.read_text(), re.S)
    assert block, f"no {lang} block in the I18N table"
    return set(re.findall(r"(?:\A|[{,])\s*(\w+):\s*[\"']", block.group(1)))


def _en_values() -> dict[str, str]:
    block = re.search(r"\ben:\s*\{(.*?)\n  \},", I18N.read_text(), re.S)
    assert block, "no en block in the I18N table"
    return dict(re.findall(r'(\w+):\s*"([^"]*)"', block.group(1)))


# ---------------------------------------------------------------------------
# Source rules -- what the components say and name.
# ---------------------------------------------------------------------------


def test_both_languages_have_the_same_keys():
    """A string in one language only is not done."""
    en = _keys_of("en")
    zh = _keys_of("zh")
    assert en - zh == set(), f"missing zh: {sorted(en - zh)}"
    assert zh - en == set(), f"missing en: {sorted(zh - en)}"


def test_the_retired_words_appear_nowhere():
    for source in _SOURCES:
        text = source.read_text()
        for word in _RETIRED:
            assert word not in text, f"{source.name} still says {word}"


def test_go2rtc_is_not_named_in_user_facing_copy():
    """The service compatibility mode is built on is not named to users --
    the sign-in panel's credit line was removed, so the word now appears
    only in logs, docs and code comments."""
    values = " ".join(_en_values().values())
    assert "go2rtc" not in values


def test_only_compat_mode_screens_carry_explanatory_prose():
    """Copy is labels, not explanations. Two named exceptions, both on the
    account/compat/settings sheets: `compatCannotRemove` (M6's enabled half
    stating, in an actionable sentence, what is true now and what removes it)
    and `confirmUnlink` (the disconnect confirmation -- an error/help
    sentence, which the rule always permits)."""
    sheets = "".join(
        (SOURCE / "components" / name).read_text()
        for name in (
            "AccountSheet.svelte",
            "CompatManage.svelte",
            "SettingsSheet.svelte",
        )
    )
    keys = set(re.findall(r't\("(\w+)"\)', sheets))
    values = _en_values()
    #: Long enough that no label, button or status word crosses it.
    SENTENCE_LENGTH = 30
    prose = {key for key in keys if len(values.get(key, "")) > SENTENCE_LENGTH}
    allowed = {"compatCannotRemove", "confirmUnlink"}
    assert prose == allowed, (
        f"unexpected explanatory prose on M1/M3: {sorted(prose - allowed)}"
    )


def test_the_manage_screen_has_no_sign_in_form():
    """Signing in happens on the login panel; the management screen lists
    what uses the credential, and never asks for it."""
    manage = (SOURCE / "components" / "CompatManage.svelte").read_text()
    assert "password" not in manage


def test_no_delete_request_is_ever_sent_to_compat():
    """Removing the credential always answers 501 -- the page must never
    call it, not even from a button."""
    for source in _SOURCES:
        assert 'method: "DELETE"' not in source.read_text()
        assert '"/api/compat"' not in source.read_text()


def test_switching_connection_confirms_inside_the_sheet():
    """Never the browser's `confirm()` -- it cannot be laid out bilingually
    and cannot be tested."""
    sheet = (SOURCE / "components" / "CameraSheet.svelte").read_text()
    assert "pathSwitchWarning" in sheet
    assert "window.confirm" not in sheet
    assert "confirm(" not in sheet


def test_the_camera_sheet_has_no_preview_preferences():
    """Frame rate and detail float on the picture itself -- the sheet is only
    settings that change the camera for everyone."""
    sheet = (SOURCE / "components" / "CameraSheet.svelte").read_text()
    assert "prefFps" not in sheet
    assert "prefDetail" not in sheet


def test_the_connection_row_reads_the_backends_resolved_path():
    """`path_for` in the add-on is the one place this fact is decided -- the
    sheet must read `settings.path`, never reconstruct it from
    `override.path`."""
    sheet = (SOURCE / "components" / "CameraSheet.svelte").read_text()
    assert "settings?.path" in sheet
    assert "override.path" not in sheet


def test_a_successful_signin_switches_the_cameras_that_needed_compat():
    """Signing in is the decision to use compatibility mode: every camera
    with no usable path is switched, whichever entry the login came from --
    the account sheet and a camera's action button share one login, one
    result. The switch is reflected in the store immediately, so the card is
    playable the moment the login lands rather than after a slow refresh."""
    signed_in = (SOURCE / "components" / "CompatSignIn.svelte").read_text()
    assert 'path: "compat"' in signed_in
    assert "c.publishable" in signed_in
    assert "compat_ready: true" in signed_in


def test_compat_signin_posts_the_step_the_401_names():
    step = (SOURCE / "components" / "CompatSignIn.svelte").read_text()
    assert "/api/compat/signin" in step
    assert "409" in step and "compatSignInBusy" in step
    assert "captcha" in step and "verify_phone" in step and "verify_email" in step


def test_the_preview_chip_changes_only_this_viewers_own_picture():
    """Frame rate and detail are stored in this browser (via the prefs
    helpers, which use `localStorage`), never sent to the add-on -- the chip
    must not make a network call of its own."""
    chip = (SOURCE / "components" / "Chip.svelte").read_text()
    assert "prefsFor" in chip
    assert "api(" not in chip


def test_follow_default_rows_show_the_resolved_value():
    """A row following the default names the value the camera will actually
    get, even though no option is marked selected."""
    row = (SOURCE / "components" / "SettingRow.svelte").read_text()
    assert "resolvedValue" in row
    assert "valueLabel" in row


def test_blocker_has_a_third_case_for_an_unbuildable_stream():
    """A camera can have a resolved path and still have no picture -- the
    add-on's own `stream_error`, not a translation key, is what this case
    shows, and it only offers a way back to Xiaomi official when that model
    actually supports it (`support === "full"`, derived rather than read from
    a baked snapshot)."""
    card = (SOURCE / "components" / "CameraCard.svelte").read_text()
    assert "stream_error" in card
    assert 'camera.support === "full"' in card


def test_a_compat_sign_in_reaches_every_card_on_its_own():
    """The card's blocked state is a function of the global sign-in store,
    not of a snapshot baked into the camera data -- so the moment a sign-in
    lands, every card re-derives without a refresh."""
    card = (SOURCE / "components" / "CameraCard.svelte").read_text()
    assert "$addonInfo.compat_ready" in card


def test_the_chip_is_not_on_a_card_that_cannot_play():
    """The preview-preference chip only makes sense over a picture that can
    actually play -- a blocked card has no picture for it to change. (The
    playable branch no longer carries a "tap to view" line: the play button
    is the whole invitation.)"""
    card = (SOURCE / "components" / "CameraCard.svelte").read_text()
    markup = card.split("</script>", 1)[1]
    blocked, playable = markup.split('{:else if mode === "failed"}', 1)
    assert "<Chip" not in blocked
    assert "<Chip" in playable


def test_changing_a_preview_pref_restarts_a_running_preview_only():
    apply = (SOURCE / "components" / "CameraCard.svelte").read_text()
    assert 'mode !== "idle"' in apply or "mode" in apply


# ---------------------------------------------------------------------------
# Built-output rules -- what the bridge actually serves.
# ---------------------------------------------------------------------------


def test_the_page_is_split_into_three_files():
    _require_built_output()
    app_css = WEB / "app.css"
    assert app_css.exists() and JS.exists()
    html = HTML.read_text()
    assert 'href="./app.css"' in html or 'href="app.css"' in html
    assert 'src="./app.js"' in html or 'src="app.js"' in html
    assert "<style>" not in html


def test_no_external_requests():
    """The page works offline. A CDN font or script would break that, and
    would also fail closed inside a Home Assistant install with no internet.
    The Svelte runtime's own error-hint URLs and the XML namespace constants
    are string literals in the bundle that are never fetched -- ignored along
    with the loopback placeholder."""
    _require_built_output()
    for source in (HTML, WEB / "app.css", JS):
        text = source.read_text()
        for literal in (
            "http://127.0.0.1",
            "https://127.0.0.1",
            "https://svelte.dev/e/",
            "http://www.w3.org/1999/xhtml",
            "http://www.w3.org/2000/svg",
            "http://www.w3.org/1998/Math/MathML",
        ):
            text = text.replace(literal, "")
        assert "http://" not in text
        assert "https://" not in text


def test_lengths_come_from_the_scale():
    body = SRC_CSS.read_text().split("/* end tokens */", 1)[1]
    offenders = sorted(
        {
            length
            for length in re.findall(r"(?<![\w-])(\d+(?:\.\d+)?px)", body)
            if length not in _SCALE
        }
    )
    assert not offenders, f"lengths outside the scale: {offenders}"


def test_every_transition_has_a_reduced_motion_answer():
    css = SRC_CSS.read_text()
    assert "@media (prefers-reduced-motion: reduce)" in css
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "transition" in reduced


def test_settings_row_is_a_touch_target():
    css = SRC_CSS.read_text()
    assert re.search(r"\.setting-row-btn\s*{[^}]*min-height:\s*56px", css, re.S)


def test_separator_starts_at_the_label():
    css = SRC_CSS.read_text()
    assert re.search(r"\.setting-row\s*\+\s*\.setting-row[^}]*margin-left", css, re.S)


def test_focus_is_visible_and_only_for_keyboards():
    css = SRC_CSS.read_text()
    assert ":focus-visible" in css
    assert re.search(r":focus-visible\s*{[^}]*outline:\s*2px", css, re.S)
    assert not re.search(r"[^-]:focus\s*{", css)


def test_the_sheet_becomes_a_bottom_drawer_on_small_screens():
    css = SRC_CSS.read_text()
    assert "@media (max-width: 600px)" in css
    small = css.split("@media (max-width: 600px)", 1)[1]
    assert "border-radius: var(--r-sheet) var(--r-sheet) 0 0" in small


def test_dark_mode_redefines_every_colour_token():
    css = SRC_CSS.read_text()
    token = (
        r"(--(?:ground|surface|sunken|ink-\d|hairline|accent[\w-]*|ok|warn|err|lift)):"
    )
    before, after = css.split("@media (prefers-color-scheme: dark)")[:2]
    light = set(re.findall(token, before))
    dark = set(re.findall(token, after))
    assert light - dark == set(), f"not redefined in dark: {sorted(light - dark)}"
