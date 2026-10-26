#!/usr/bin/env python3
"""Check that the lead-intelligence pipeline can run on this machine."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


def check_module(name: str, *, required: bool) -> tuple[bool, str]:
    spec = importlib.util.find_spec(name)
    if spec:
        return True, f"[ok] Python module: {name}"
    label = "[fail]" if required else "[warn]"
    return not required, f"{label} Python module: {name} is not installed"


def browser_control_status() -> tuple[bool, str]:
    binary = shutil.which("browser-control")
    if not binary:
        return False, "[info] browser-control is not on PATH (optional if Selenium works)"
    try:
        proc = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return True, f"[warn] browser-control found but status failed: {exc}"
    if proc.returncode != 0:
        return True, "[warn] browser-control is installed but its relay is not running"
    try:
        status = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return True, f"[info] browser-control status: {proc.stdout.strip()[:300]}"
    extension = (status.get("extension") or {}).get("connected")
    if extension:
        return True, "[ok] browser-control extension is connected"
    return True, "[warn] browser-control is running but no browser tab is attached"


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

    bc_ok, bc_message = browser_control_status()
    print(bc_message)

    firefox = shutil.which("firefox") or shutil.which("firefox-esr")
    selenium_ok = importlib.util.find_spec("selenium") is not None
    if selenium_ok and firefox:
        print(f"[ok] Selenium fallback available (Firefox: {firefox})")
        driver_ok = True
    else:
        driver_ok = False
        if not selenium_ok:
            print("[warn] Selenium is not installed, so the headless Firefox fallback is unavailable.")
        if not firefox:
            print("[warn] Firefox was not found on PATH.")
        print("       Install selenium and Firefox, or attach the Browser Control extension.")

    if not (bc_ok or driver_ok) and not args.allow_no_driver:
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
