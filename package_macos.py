#!/usr/bin/env python3
"""Build a portable Mac app from code only, excluding all payroll data.

Run with a universal2 Python and the pinned packaging dependencies installed.
Set SITTERWISE_CODESIGN_IDENTITY to a Developer ID Application identity to sign.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("SITTERWISE_MAC_BUILD_DIR",
                         str(Path(tempfile.gettempdir()) / "sitterwise-payroll-macos")))
VERSION = "1.1.2"


def main():
    OUT.mkdir(exist_ok=True)
    assets = OUT / "assets"
    assets.mkdir(exist_ok=True)
    fingerprint = hashlib.sha256()
    sources = [*sorted((ROOT / "payroll").glob("*.py")), *sorted((ROOT / "web").glob("*")),
               ROOT / "desktop_main.py", ROOT / "run.py", ROOT / "rules.json", ROOT / "onpay_mapping.json"]
    for source in sources:
        fingerprint.update(source.relative_to(ROOT).as_posix().encode())
        fingerprint.update(source.read_bytes())
    build = {"version": VERSION, "build": VERSION + "-" + fingerprint.hexdigest()[:12]}
    (assets / "build.json").write_text(json.dumps(build))
    licenses = assets / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(Path(sysconfig.get_path("stdlib")) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    for package in ("openpyxl", "et-xmlfile", "pypdf", "pyinstaller", "defusedxml"):
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if file.name.upper().startswith(("LICEN", "COPYING")) and ".dist-info" in str(file):
                shutil.copyfile(dist.locate_file(file), licenses / (package + "-" + file.name))
    from make_mac_app import draw_icon, build_icns
    png = assets / "icon.png"
    draw_icon(png)
    icon = assets / "icon.icns"
    if not build_icns(png, icon):
        raise RuntimeError("The Mac icon could not be built.")
    # Package only explicit application assets. data/, tests/, exports and .git
    # are never inputs to the distributable bundle.
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--windowed", "--onedir", "--target-arch", "universal2",
            "--name", "Sitterwise Payroll", "--osx-bundle-identifier", "net.sitterwise.payroll",
            "--icon", str(icon), "--distpath", str(OUT / "dist"),
            "--workpath", str(OUT / "work"), "--specpath", str(OUT),
            "--add-data", str(ROOT / "web") + ":web",
            "--add-data", str(ROOT / "rules.json") + ":.",
            "--add-data", str(ROOT / "onpay_mapping.json") + ":.",
            "--add-data", str(assets / "build.json") + ":.",
            "--add-data", str(licenses) + ":licenses",
            "--hidden-import", "pypdf", "--hidden-import", "defusedxml.ElementTree", "--exclude-module", "tkinter",
            "--exclude-module", "numpy", "--exclude-module", "PIL"]
    identity = os.environ.get("SITTERWISE_CODESIGN_IDENTITY")
    if identity:
        args += ["--codesign-identity", identity]
    args += [str(ROOT / "desktop_main.py")]
    subprocess.run(args, cwd=ROOT, check=True)
    app = OUT / "dist" / "Sitterwise Payroll.app"
    plist = app / "Contents" / "Info.plist"
    with plist.open("rb") as f:
        info = plistlib.load(f)
    info.update(CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION,
                LSUIElement=True, LSMinimumSystemVersion="11.0",
                NSHighResolutionCapable=True)
    with plist.open("wb") as f:
        plistlib.dump(info, f)
    # Changing Info.plist invalidates the outer signature; renew it last.
    command = ["/usr/bin/codesign", "--force", "--sign", identity or "-"]
    if identity:
        command += ["--options", "runtime", "--timestamp"]
    subprocess.run(command + [str(app)], check=True)
    print(json.dumps({**build, "app": str(app), "developer_id_signed": bool(identity)}))


if __name__ == "__main__":
    main()
