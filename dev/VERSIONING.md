# Versioning shorthand

How versions are tracked in this firmware. One compact tag per file; the
manifest expands it into a table.

## The tag

Every source file carries **one** comment line — the `@v` tag — near the top:

```
# @v <version> | <date> | <short description>
```

Example:

```python
# @v 1.3.4 | 2026-07-19 | App entry: main loop, sensors, uploaders, web server
```

Rules:
- **version** — semver-ish, e.g. `1.3.4`. No spaces.
- **date** — the last-updated date, e.g. `2026-07-19`. May include a note in
  parentheses, e.g. `2021-03 (vendored)`.
- **description** — a short one-liner. Free text; everything after the 2nd `|`.
- Fields are separated by ` | ` (space-pipe-space).

JSON files can't have comments, so they use keys instead:

```json
"_version": "1.3.4",
"_updated": "2026-07-19",
"_desc": "Device + network + feature settings"
```

(`version.json` reuses its existing `version` / `updated` keys plus `_desc`.)

## The expanded manifest

`MANIFEST.txt` (at the firmware root) is generated from the tags — never edited
by hand. Regenerate it after any change:

```
cd dev
python gen_manifest.py
```

It lists every file as: `File · Type · Version · Updated · Description`, and
prints the bundle version (taken from `main.py`'s tag).

## Bumping a version

1. Edit the file.
2. Update that file's `@v` tag (version + date, and description if it changed).
3. If it's a code/behaviour change, add a note to `dev/CHANGELOG.md`.
4. If it changes the bundle, also bump `main.py`'s tag **and** `version.json`
   (`version`) so OTA sees the new build. `main.py` additionally keeps a runtime
   `__version__ = "..."` used by OTA — keep it equal to `main.py`'s `@v` version.
5. Run `python gen_manifest.py` to refresh `MANIFEST.txt`.

## Interaction with comment stripping

`dev/strip_comments.py` removes `#` comments from the firmware files, but it
**preserves** the `@v` tag (it skips any comment containing `@v`). So you can
strip comments freely without losing version tags. Run order after edits:

```
cd dev
python strip_comments.py   # optional: remove other comments
python gen_manifest.py     # refresh the manifest
```

## Fallbacks (older files)

If a file has no `@v` tag, the generator still tries, in order: a code
`__version__ = "..."`, a class `VERSION = "..."`, or a `# Version:` comment.
New files should just use the `@v` tag.

## Where things live

| Item | Location |
|------|----------|
| Per-file version | the `@v` tag in each file |
| Expanded list | `MANIFEST.txt` (firmware root, generated) |
| Change history | `dev/CHANGELOG.md` |
| Generator | `dev/gen_manifest.py` |
| Comment stripper | `dev/strip_comments.py` |
| This doc | `dev/VERSIONING.md` |
