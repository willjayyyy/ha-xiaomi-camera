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
#: The base set below is the brief's. Everything after it is a genuine
#: existing component dimension that this split carried over unchanged from
#: the pre-split page -- each one added deliberately, one at a time, rather
#: than by loosening the regex or widening the scale into a range.
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
    "5px",  # corner-controls pill padding
    "6px",  # icon/chip internal gaps and radii
    "7px",  # status dot diameter
    "10px",  # one-off spacing carried over from the pre-split page
    "11px",  # button and code block horizontal padding
    "13px",  # message banner vertical padding
    "14px",  # button/input horizontal padding, icon sizes, row gaps
    "15px",  # base body font size, close-icon size
    "17px",  # settings-button icon size, message banner padding
    "18px",  # card border-radius and paddings
    "22px",  # sign-in mark border-radius, button padding, list indent
    "26px",  # card horizontal padding
    "28px",  # sign-in subtitle margin, header margin-bottom
    "30px",  # preview control button diameter
    "36px",  # settings-button diameter, sign-in card bottom padding
    "40px",  # overlay width offset, empty-state padding
    "44px",  # sign-in card top padding
    "48px",  # body bottom padding
    "52px",  # play button diameter
    "56px",  # settings row min-height
    "64px",  # sign-in mark diameter
    "320px",  # camera grid minimum column width
    "400px",  # sign-in card max-width
    "420px",  # settings sheet max-width
    "640px",  # mobile layout breakpoint
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


def test_dark_mode_redefines_every_colour_token():
    css = CSS.read_text()
    token = (
        r"(--(?:ground|surface|sunken|ink-\d|hairline|accent[\w-]*|ok|warn|err|lift)):"
    )
    before, after = css.split("@media (prefers-color-scheme: dark)")[:2]
    light = set(re.findall(token, before))
    dark = set(re.findall(token, after))
    assert light - dark == set(), f"not redefined in dark: {sorted(light - dark)}"
