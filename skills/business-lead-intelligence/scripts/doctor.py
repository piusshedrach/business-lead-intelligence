#!/usr/bin/env python3
"""Check that the lead-intelligence pipeline can run on this machine."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from bootstrap import DEFAULT_MANIFEST, find_installed_skill, load_manifest


def check_module(name: str, *, required: bool) -> tuple[bool, str]:
    spec = importlib.util.find_spec(name)
    if spec:
        return True, f"[ok] Python module: {name}"
    label = "[fail]" if required else "[warn]"
    return not required, f"{label} Python module: {name} is not installed"


def find_firefox() -> str | None:
    """Find Firefox on PATH or in the standard macOS/Windows install locations."""
    candidates: list[str | None] = [
        os.environ.get("FIREFOX_BIN"),
        shutil.which("firefox"),
        shutil.which("firefox-esr"),
    ]
    if sys.platform == "darwin":
        candidates.append("/Applications/Firefox.app/Contents/MacOS/firefox")
    elif sys.platform.startswith("win"):
        candidates.extend(
            [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ]
        )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def companion_skill_status() -> list[str]:
    """Report companion skills declared by dependencies.json."""
    messages: list[str] = []
    try:
        manifest = load_manifest(DEFAULT_MANIFEST)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"[warn] Could not read dependency manifest: {exc}"]

    required = []
    for group in manifest.get("skills") or []:
        required.extend(str(name) for name in group.get("skills") or [])
    optional = []
    for group in manifest.get("optional_skills") or []:
        optional.extend(str(name) for name in group.get("skills") or [])

    for name in dict.fromkeys(required):
        if find_installed_skill(name):
            messages.append(f"[ok] Companion skill: {name}")
        else:
            messages.append(
                f"[warn] Companion skill missing: {name} "
                "(run scripts/bootstrap.py)"
            )
    for name in dict.fromkeys(optional):
        if name in required:
            continue
        if find_installed_skill(name):
            messages.append(f"[ok] Optional companion skill: {name}")
        else:
            messages.append(
                f"[info] Optional companion skill not installed: {name}"
            )
    return messages


def browser_control_status() -> tuple[bool, bool, str]:
    """Return (installed, extension_connected, message)."""
    binary = shutil.which("browser-control")
    if not binary:
        return False, False, "[info] browser-control is not on PATH (optional if Selenium works)"
    try:
        proc = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return True, False, f"[warn] browser-control found but status failed: {exc}"
    if proc.returncode != 0:
        return True, False, "[warn] browser-control is installed but its relay is not running"
    try:
        status = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return True, False, f"[info] browser-control status: {proc.stdout.strip()[:300]}"
    extension = (status.get("extension") or {}).get("connected")
    if extension:
        return True, True, "[ok] browser-control extension is connected"
    return True, False, "[warn] browser-control is running but no browser tab is attached"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the lead-intelligence setup.")
    parser.add_argument(
        "--allow-no-driver",
        action="store_true",
        help="Do not fail when neither Browser Control nor Selenium/Firefox is available.",
    )
    args = parser.parse_args()

    print("Business Lead Intelligence — setup check")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")

    ok = True
    if sys.version_info < (3, 10):
        ok = False
        print("[fail] Python 3.10 or newer is required.")
    else:
        print("[ok] Python version is supported.")

    for module in ("requests", "bs4"):
        good, message = check_module(module, required=True)
        ok = ok and good
        print(message)
    good, message = check_module("selenium", required=False)
    print(message)

    print("")
    for message in companion_skill_status():
        print(message)

    print("")
    _bc_present, bc_connected, bc_message = browser_control_status()
    print(bc_message)

    firefox = find_firefox()
    selenium_ok = importlib.util.find_spec("selenium") is not None
    if selenium_ok and firefox:
        print(f"[ok] Selenium fallback available (Firefox: {firefox})")
        driver_ok = True
    else:
        driver_ok = False
        if not selenium_ok:
            print("[warn] Selenium is not installed, so the headless Firefox fallback is unavailable.")
            print('       Install it with: pip install -r "$SKILL_DIR/requirements.txt"')
        if not firefox:
            print("[warn] Firefox was not found.")
            if sys.platform == "darwin":
                print("       Install it with: brew install --cask firefox")
            elif sys.platform.startswith("win"):
                print("       Install it with: winget install Mozilla.Firefox")
            else:
                print("       Install it with: sudo apt install firefox  (Debian/Ubuntu)")
                print("       or: sudo dnf install firefox  (Fedora), sudo pacman -S firefox  (Arch)")
            print("       If Firefox is installed elsewhere, set FIREFOX_BIN to its executable.")
        print("       Or attach the Browser Control extension to a browser tab.")

    if not (bc_connected or driver_ok) and not args.allow_no_driver:
        ok = False

    out = Path("output")
    try:
        out.mkdir(exist_ok=True)
        print(f"[ok] Output directory: {out.resolve()}")
    except OSError as exc:
        ok = False
        print(f"[fail] Cannot create output directory: {exc}")

    print("")
    if ok:
        print("Ready.")
    else:
        print("Fix the failures above before running the pipeline.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
