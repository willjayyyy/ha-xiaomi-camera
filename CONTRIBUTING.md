# Contributing

Thanks for looking. A few conventions, and the reasoning behind them.

## Language

Code, comments, commit messages and pull requests are in **English**, so that
anyone can read them. User-facing text is **bilingual** — English and
Simplified Chinese — because most of these cameras are sold in China and most
of Home Assistant's users are not: `README.md` / `README.zh-CN.md`,
`addon/translations/`, `custom_components/xiaomi_camera/translations/`, and the
strings in `addon/rootfs/app/web/index.html`. A change that adds user-facing
text without both languages is incomplete.

## Branches

`main` is always releasable. Work happens on a branch and arrives through a
pull request.

Do **not** change the version on a feature branch. Versions move once, when a
release is being made — see below. Bumping it per change is how the add-on's
version and the integration's drifted apart during early development, and how
the container registry ended up with thirty tags nobody wanted.

## Tests

```
python -m pytest
```

The suite covers the pure logic — bitstream parsing, options validation,
credential redaction, the account page's guards — and deliberately avoids
mocking the vendor SDK. A mock of a closed-source library tests an assumption
about it, not its behaviour.

Some tests exist because the thing they check has shipped broken: that every
address the page requests is a route the add-on serves, that every name
imported between modules exists, that the two halves of the repository agree on
a version. They are cheap, and each one was written the day after it would have
helped.

Lint and formatting:

```
ruff check .
ruff format .
```

## Releasing

One version for the whole repository, in three places that different systems
read:

| Where | Read by |
|---|---|
| `addon/config.yaml` | Supervisor, and the build's idempotence check |
| `custom_components/xiaomi_camera/manifest.json` | Home Assistant |
| the git tag `vX.Y.Z` and its GitHub release | HACS |

To release: bump the first two, add an entry to `addon/CHANGELOG.md`, merge to
`main`. The workflow builds and publishes the images, then creates the release,
using that changelog entry as its notes. Nothing else is needed, and the
release is never created before the image exists — one that names a version
users cannot install is worse than none.

Changelog entries are for the person deciding whether to update. Describe what
changed for them, not which functions were touched.

## Where the surprises are

`docs/ARCHITECTURE.md` collects the things that cost a day to discover: why
this is two components rather than one, what the vendor SDK does that its
signatures do not suggest, and which failures are silent. Read it before
changing anything in `addon/rootfs/app/bridge/`.
