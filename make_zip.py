#!/usr/bin/env python3
"""Virtual Dowel - make_zip.py  v2.3  (2026-10-12)
Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Builds the zip for the shop PC:

    python3 make_zip.py              -> VirtualDowel-<VERSION>.zip next to this file
    python3 make_zip.py other.zip    -> that file

The zip has a clean top level - INSTALL.bat, README.md, LICENSE.txt, the docs folder - and the program in app.
The repository stays flat; only the zip has the app folder. Files go in byte for byte (CRLF kept).
Nothing generated, no tests, no plans, no agent files.
"""
import glob
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = ["INSTALL.bat", "README.md", "LICENSE.txt", "docs/guide.html"]
APP = ["vd_align.py", "vd_app.py", "vd_probe.sbp", "vd_selftest.sbp", "vd_align.bat"]


def members():
    """(file in the repository, name in the zip) for every file the shop gets."""
    top = TOP + sorted(os.path.relpath(p, HERE).replace(os.sep, "/")
                       for p in glob.glob(os.path.join(HERE, "docs", "example", "*")))
    return [(f, "VirtualDowel/" + f) for f in top] + [(f, "VirtualDowel/app/" + f) for f in APP]


def build(out=None):
    sys.path.insert(0, HERE)
    import vd_align
    out = out or os.path.join(HERE, f"VirtualDowel-{vd_align.VERSION}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, dst in members():
            z.write(os.path.join(HERE, src), dst)
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else None))
