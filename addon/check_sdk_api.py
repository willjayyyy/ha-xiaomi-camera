"""Verify that every vendor-SDK call the bridge makes actually exists.

Run during the image build, where the SDK is installed. It exists because a
misread method name once shipped: `MIoTClient.gen_auth_url` is the method
`gen_oauth_url_async` delegates to internally, not part of the client's own
surface. Nothing caught it -- the tests deliberately avoid importing the SDK,
linters cannot resolve attributes across a third-party package, and the failure
only surfaced when a user clicked the button.

The check is deliberately crude: it greps for attribute accesses on the objects
the bridge holds, then asks the real classes whether those attributes exist. It
cannot prove a call is correct, but it does catch a name that is simply not
there.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from miot.camera import MIoTCameraInstance
from miot.client import MIoTClient
from miot.cloud import MIoTHttpClient

#: Variable-name pattern -> the class that variable holds at runtime.
BINDINGS: list[tuple[str, type, str]] = [
    (r"\bclient\.(\w+)", MIoTClient, "MIoTClient"),
    (r"self\._client\.(\w+)", MIoTClient, "MIoTClient"),
    (r"\binstance\.(\w+)", MIoTCameraInstance, "MIoTCameraInstance"),
    (r"_http_client\.(\w+)", MIoTHttpClient, "MIoTHttpClient"),
]

#: Names that are ours, not the SDK's, and would otherwise be reported missing.
IGNORE = {
    "base_url",
    "async_health",
    "async_cameras",
    "async_snapshot",
    "async_set_power",
}


def attributes_of(cls: type) -> set[str]:
    """Public and private attributes, including annotation-only ones.

    Instance attributes assigned in ``__init__`` are invisible to ``hasattr``
    on the class, but the SDK annotates them at class level, so the annotations
    fill that gap.
    """
    names = set(dir(cls))
    for klass in cls.__mro__:
        names |= set(getattr(klass, "__annotations__", {}))
    return names


def main(argv: list[str]) -> int:
    # The directory is passed in rather than derived from __file__: this runs
    # both from the repository root and from inside an image where the two live
    # in unrelated places, and a wrong guess silently checks nothing.
    if len(argv) > 1:
        source_dir = Path(argv[1])
    else:
        source_dir = Path(__file__).parent / "rootfs" / "app" / "bridge"
    sources = list(source_dir.glob("*.py"))
    if not sources:
        print(f"no sources found under {source_dir}", file=sys.stderr)
        return 1

    failures: list[str] = []
    checked = 0
    for pattern, cls, label in BINDINGS:
        available = attributes_of(cls)
        used: set[str] = set()
        for path in sources:
            used |= set(re.findall(pattern, path.read_text()))
        used -= IGNORE
        checked += len(used)
        for name in sorted(used):
            if name not in available:
                failures.append(f"{label} has no attribute {name!r}")

    if failures:
        print("Vendor SDK API check failed:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"Vendor SDK API check passed ({checked} attributes verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
