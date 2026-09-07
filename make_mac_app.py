#!/usr/bin/env python3
"""Build "Sitterwise Payroll.app" so payroll starts from the Dock.

    python3 make_mac_app.py

Makes a real Mac application next to this file. Double-click it, or keep it
in the Dock, and payroll opens in your browser - no Terminal window, no
command to remember.

Inside, it is the same app: the .app is a small wrapper that starts run.py.
Nothing is copied or duplicated, so the application keeps working as this
folder is updated. Move this folder and the application stops finding it -
run this again to point it at the new place.
"""
from __future__ import annotations

import math
import plistlib
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "Sitterwise Payroll.app"

# The Sitterwise palette, same as the app's own screens.
NAVY = (0x1B, 0x3A, 0x5C)
NAVY_DEEP = (0x12, 0x28, 0x3F)
CORAL = (0xF4, 0x8A, 0x91)
TEAL = (0x84, 0xD0, 0xD2)


# -- the icon ------------------------------------------------------------

def _png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    """Write RGBA pixels as a PNG, without needing anything installed."""
    rows = b"".join(b"\x00" + bytes(pixels[y * width * 4:(y + 1) * width * 4])
                    for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b""))


def _blend(under, over, alpha):
    return tuple(round(u + (o - u) * alpha) for u, o in zip(under, over))


def draw_icon(path: Path, size: int = 1024) -> None:
    """A navy squircle holding the coral dot from the app's masthead, over
    three lines that read as a pay stub rather than an anonymous shape."""
    pixels = bytearray(size * size * 4)
    half = size / 2
    radius = size * 0.46            # the squircle
    power = 5.0                     # close to Apple's corner shape
    edge = size * 0.0035            # how wide to fade an edge

    dot_cx, dot_cy, dot_r = half, size * 0.355, size * 0.093
    bar_h = size * 0.038
    bars = [(size * 0.545, size * 0.21),   # centre y, half-width
            (size * 0.650, size * 0.165),
            (size * 0.755, size * 0.115)]

    def soft(distance):
        return max(0.0, min(1.0, distance / edge + 0.5))

    for y in range(size):
        dy = y + 0.5 - half
        for x in range(size):
            dx = x + 0.5 - half
            f = (abs(dx) ** power + abs(dy) ** power) ** (1.0 / power)
            cover = soft(radius - f)
            if cover <= 0:
                continue

            colour = _blend(NAVY, NAVY_DEEP, (y / size) * 0.55)

            a = soft(dot_r - math.hypot(x + 0.5 - dot_cx, y + 0.5 - dot_cy))
            if a > 0:
                colour = _blend(colour, CORAL, a)

            for cy, hw in bars:
                ax = soft(hw - abs(dx))
                ay = soft(bar_h / 2 - abs(y + 0.5 - cy))
                a = min(ax, ay)
                if a > 0:
                    colour = _blend(colour, TEAL, a)

            i = (y * size + x) * 4
            pixels[i], pixels[i + 1], pixels[i + 2] = colour
            pixels[i + 3] = round(cover * 255)

    _png(path, size, size, pixels)


def build_icns(png: Path, into: Path) -> bool:
    """Turn the PNG into a Mac icon, if the Mac tools are here to do it."""
    if not (shutil.which("sips") and shutil.which("iconutil")):
        return False
    iconset = into.parent / "icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    try:
        for px in (16, 32, 64, 128, 256, 512, 1024):
            for name in (f"icon_{px}x{px}.png",
                         f"icon_{px // 2}x{px // 2}@2x.png" if px > 16 else None):
                if not name:
                    continue
                subprocess.run(["sips", "-z", str(px), str(px), str(png),
                                "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(into)],
                       check=True, capture_output=True)
        return into.exists()
    except (subprocess.SubprocessError, OSError):
        return False
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


# -- the application -----------------------------------------------------

LAUNCHER = '''#!/bin/bash
# Starts Sitterwise Payroll. Quitting this application stops it.

# Where the payroll code lives. If the application is still sitting inside
# that folder, work it out from here; otherwise fall back to where it was
# when this application was built.
HERE="$(cd "$(dirname "$0")/../../.." && pwd)"
BUILT_AT="__ROOT__"
if [ -f "$HERE/run.py" ]; then
  APP_ROOT="$HERE"
elif [ -f "$BUILT_AT/run.py" ]; then
  APP_ROOT="$BUILT_AT"
else
  osascript -e 'display alert "Sitterwise Payroll" message "The payroll folder has moved, so this application cannot find it. Open the folder and run make_mac_app.py again." as critical'
  exit 1
fi
cd "$APP_ROOT" || exit 1

PY=""
for candidate in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  osascript -e 'display alert "Sitterwise Payroll" message "Python 3 is not installed on this Mac. Install it from python.org and try again." as critical'
  exit 1
fi

if ! "$PY" -c "import openpyxl" >/dev/null 2>&1; then
  "$PY" -m pip install --quiet --user openpyxl >/dev/null 2>&1 || {
    osascript -e 'display alert "Sitterwise Payroll" message "Could not install what the app needs to read spreadsheets. In Terminal, run: python3 -m pip install openpyxl" as critical'
    exit 1
  }
fi

exec "$PY" run.py
'''

PLIST = {
    "CFBundleName": "Sitterwise Payroll",
    "CFBundleDisplayName": "Sitterwise Payroll",
    "CFBundleIdentifier": "net.sitterwise.payroll",
    "CFBundleExecutable": "Sitterwise Payroll",
    "CFBundleIconFile": "icon",
    "CFBundlePackageType": "APPL",
    "CFBundleVersion": "1.0",
    "CFBundleShortVersionString": "1.0",
    "LSMinimumSystemVersion": "10.15",
    "NSHighResolutionCapable": True,
}


def build() -> int:
    shutil.rmtree(APP, ignore_errors=True)
    macos = APP / "Contents" / "MacOS"
    resources = APP / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    (APP / "Contents" / "Info.plist").write_bytes(plistlib.dumps(PLIST))

    launcher = macos / "Sitterwise Payroll"
    launcher.write_text(LAUNCHER.replace("__ROOT__", str(ROOT)), encoding="utf-8")
    launcher.chmod(0o755)

    print("  Drawing the icon...")
    png = resources / "icon.png"
    draw_icon(png)
    if build_icns(png, resources / "icon.icns"):
        png.unlink(missing_ok=True)
        icon = "with its own icon"
    else:
        icon = ("without an icon - build it on a Mac to get one, or the "
                "generic application icon will show")

    print(f"\n  Made {APP.name}, {icon}.")
    print(f"  It is in {ROOT}\n")
    print("  Double-click it to run payroll. To keep it handy, drag it onto the")
    print("  Dock, or into your Applications folder - it keeps working from")
    print("  either, as long as this folder stays where it is.\n")
    return 0


if __name__ == "__main__":
    sys.exit(build())
