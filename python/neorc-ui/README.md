# neorc-ui

The built web UI of the neorc manager, as a Python distribution: static
assets and one function, `neorc_ui.static_dir()`, that says where they are.
`neorc manager start` serves them at `/ui/`; `pip install 'neorc[manager]'`
brings this package with it.

It has no dependencies and nothing to run. The assets are built from
`ts/neorc-ui` in the repository: by CI for a release, or by the wheel build
when they are missing, which needs Node.js. A wheel built from the sdist
needs none, since the sdist carries them.

In a checkout, `uv sync` installs this package editable with no assets;
`static_dir()` then raises an error naming the fix. Python work never needs
them: the manager's tests mount a stand-in directory.

See the repository's [README](https://github.com/open-shipyard/neorc) and
[CHANGELOG](https://github.com/open-shipyard/neorc/blob/main/CHANGELOG.md).
