# gen_manifest.py - Regenerates MANIFEST.txt for the firmware bundle.
# Version: 2.0.0
# Updated: 2026-JUL-19
# Author: Rick Jara
#
# Runs on your PC (CPython), NOT on the ESP32. It scans every file in the
# firmware folder (the parent of this dev/ folder), reads each file's version
# shorthand, and writes a readable MANIFEST.txt so the list never drifts.
#
#   Usage (from the dev/ folder):  python gen_manifest.py
#
# Preferred per-file shorthand (one comment line, anywhere near the top):
#     # @v <version> | <date> | <short description>
# e.g.  # @v 1.3.4 | 2026-07-19 | App entry: loop, sensors, uploaders
#
# JSON files use keys instead:  "version"/"_version", "updated"/"_updated", "_desc".
# Fallbacks (older files): code __version__ / class VERSION / a "Version:" comment.

import os
import re
import json
import datetime

# Firmware root is the parent of this dev/ folder.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = "MANIFEST.txt"

SKIP_NAMES = {OUTPUT, ".gitignore", ".firebaserc"}
SKIP_DIRS = {".git", "__pycache__"}

# Preferred: compact shorthand  # @v <ver> | <date> | <desc>
SHORT_RE = re.compile(r"@v\s+(\S+)\s*\|\s*([^|]+?)\s*\|\s*(.*)")
# Fallbacks.
CODE_VER_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')
CODE_DATE_RE = re.compile(r'__date__\s*=\s*["\']([^"\']+)["\']')
CLS_VER_RE = re.compile(r'\bVERSION\s*=\s*["\']([^"\']+)["\']')
CLS_DATE_RE = re.compile(r'\bDATE\s*=\s*["\']([^"\']+)["\']')
VER_RE = re.compile(r"Version:\s*([^\s#>]+)", re.IGNORECASE)
DATE_RE = re.compile(r"(?:Updated|Date):\s*(.+)", re.IGNORECASE)

KIND = {".py": "module", ".html": "web", ".json": "config", ".txt": "data", "": "data"}


def marker_from_text(path):
    """Return (version, date, description)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = "".join(f.readline() for _ in range(60))
    except OSError:
        return "-", "-", "-"

    m = SHORT_RE.search(head)
    if m:
        return m.group(1), m.group(2).strip(), m.group(3).replace("-->", "").strip()

    version = None
    for rx in (CODE_VER_RE, CLS_VER_RE, VER_RE):
        mm = rx.search(head)
        if mm:
            version = mm.group(1)
            break
    date = None
    for rx in (CODE_DATE_RE, CLS_DATE_RE):
        mm = rx.search(head)
        if mm:
            date = mm.group(1)
            break
    if date is None:
        mm = DATE_RE.search(head)
        if mm:
            date = mm.group(1).replace("-->", "").strip()
    return (version or "-", date or "-", "-")


def marker_from_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return "-", "-", "-"
    if not isinstance(obj, dict):
        return "-", "-", "-"
    ver = obj.get("version") or obj.get("_version") or "-"
    upd = obj.get("updated") or obj.get("_updated") or "-"
    desc = obj.get("_desc") or "-"
    return str(ver), str(upd), str(desc)


def collect():
    rows = []
    for name in sorted(os.listdir(HERE)):
        if name in SKIP_NAMES or name in SKIP_DIRS:
            continue
        full = os.path.join(HERE, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext == ".json":
            ver, upd, desc = marker_from_json(full)
        else:
            ver, upd, desc = marker_from_text(full)
        rows.append((name, KIND.get(ext, "other"), ver, upd, desc))
    return rows


def bundle_version(rows):
    for name, _, ver, _, _ in rows:
        if name == "main.py":
            return ver
    return "-"


def render(rows):
    w_file = max(len("File"), max(len(r[0]) for r in rows))
    w_kind = max(len("Type"), max(len(r[1]) for r in rows))
    w_ver = max(len("Version"), max(len(r[2]) for r in rows))
    w_date = max(len("Updated"), max(len(r[3]) for r in rows))
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("Outdoor Sensor Firmware - File Manifest")
    lines.append(f"Bundle version : {bundle_version(rows)}   (from main.py @v tag)")
    lines.append(f"Generated      : {now}   by gen_manifest.py")
    lines.append(f"Files          : {len(rows)}")
    lines.append("")
    header = (f"{'File':<{w_file}}  {'Type':<{w_kind}}  {'Version':<{w_ver}}  "
              f"{'Updated':<{w_date}}  Description")
    lines.append(header)
    lines.append("-" * len(header))
    for name, kind, ver, upd, desc in rows:
        lines.append(f"{name:<{w_file}}  {kind:<{w_kind}}  {ver:<{w_ver}}  "
                     f"{upd:<{w_date}}  {desc}")
    lines.append("")
    lines.append("Per-file shorthand:  # @v <version> | <date> | <description>")
    lines.append("Regenerate with:     python gen_manifest.py   (history in dev/CHANGELOG.md)")
    return "\n".join(lines) + "\n"


def main():
    rows = collect()
    text = render(rows)
    with open(os.path.join(HERE, OUTPUT), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"Wrote {OUTPUT} ({len(rows)} files).")


if __name__ == "__main__":
    main()
