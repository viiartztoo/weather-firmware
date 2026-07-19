# strip_comments.py - Removes '#' comments from the firmware .py files.
# Runs on your PC (CPython), NOT the ESP32.
#
# It uses Python's tokenizer, so it only removes REAL comments - '#' inside a
# string (e.g. a CSS colour like "#888") is left untouched. Docstrings are
# kept. Blank runs are collapsed. The vendored PiicoDev drivers are skipped so
# their upstream licence/attribution headers stay intact.
#
#   Usage (from the dev/ folder):  python strip_comments.py
#
# History lives in dev/CHANGELOG.md; per-file versions stay as code
# (__version__ = "...") so they survive stripping and gen_manifest can read them.

import os
import io
import tokenize

# Firmware root is the parent of this dev/ folder.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files we never strip (third-party / not ours).
SKIP = {"PiicoDev_BME280.py", "PiicoDev_Unified.py"}


def strip_file(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.split("\n")

    # Collect comment token positions (1-based row, 0-based col).
    comment_positions = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            # Keep the "@v" version tag line; strip all other comments.
            if tok.type == tokenize.COMMENT and "@v" not in tok.string:
                comment_positions.append(tok.start)
    except (tokenize.TokenError, IndentationError):
        # Leave a file untouched rather than risk corrupting it.
        return 0

    # Truncate each line at the start of its comment.
    removed = 0
    for row, col in comment_positions:
        i = row - 1
        if 0 <= i < len(lines):
            lines[i] = lines[i][:col].rstrip()
            removed += 1

    # Collapse consecutive blank lines and trim leading blanks.
    out = []
    prev_blank = False
    for ln in lines:
        if ln.strip() == "":
            if prev_blank:
                continue
            prev_blank = True
            out.append("")
        else:
            prev_blank = False
            out.append(ln.rstrip())
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()

    result = "\n".join(out) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)
    return removed


def main():
    total = 0
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py") or name in SKIP:
            continue
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        n = strip_file(path)
        total += n
        print(f"{name:<24} {n} comments removed")
    print(f"\nDone. Removed {total} comments. Skipped: {', '.join(sorted(SKIP))}")


if __name__ == "__main__":
    main()
