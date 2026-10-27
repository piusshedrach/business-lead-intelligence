#!/usr/bin/env python3
"""Install the Python packages, companion skills, and optional tools this skill needs.

Run this once from a writable workspace after installing the skill:

    cd ~/lead-runs
    python3 "$SKILL_DIR/scripts/bootstrap.py"

The script is intentionally dependency-free: it uses only the Python standard
library so it can run before requests, BeautifulSoup, or Selenium exist.

It is idempotent. It creates a workspace .venv when needed, installs the Python
requirements, installs the companion skills with `npx skills`, and installs the
optional Browser Control CLI when npm is available. System packages such as
Firefox are reported, not installed with sudo.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_MANIFEST = SKILL_DIR / "dependencies.json"
MIN_PYTHON = (3, 10)
MIN_NODE = (22, 20)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict:
    """Load and minimally validate the dependency manifest."""
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if not data.get("skills_cli"):
        raise ValueError(f"{path} is missing skills_cli")
    return data


def skill_groups(manifest: dict, *, include_optional: bool) -> list[dict]:
    groups = list(manifest.get("skills") or [])
    if include_optional:
        groups.extend(manifest.get("optional_skills") or [])
    return groups


def skill_names(manifest: dict, *, include_optional: bool = True) -> list[str]:
    names: list[str] = []
    for group in skill_groups(manifest, include_optional=include_optional):
        names.extend(str(name) for name in group.get("skills") or [])
    return names


def skill_roots(workspace: Path | None = None) -> list[Path]:
    """Directories OpenCode and the skills CLI use for installed skills."""
    workspace = workspace or Path.cwd()
    return [
        Path.home() / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".config" / "opencode" / "skills",
        workspace / ".agents" / "skills",
        workspace / ".opencode" / "skills",
    ]


def find_installed_skill(name: str, workspace: Path | None = None) -> Path | None:
    for root in skill_roots(workspace):
        candidate = root / name / "SKILL.md"
        if candidate.is_file():
            return candidate
    return None


def installed_skill_names(workspace: Path | None = None) -> set[str]:
    found: set[str] = set()
    for root in skill_roots(workspace):
        if not root.is_dir():
            continue
        for candidate in root.glob("*/SKILL.md"):
            found.add(candidate.parent.name)
    return found


def skills_command(
    group: dict,
    *,
    cli: str,
    agent: str,
    global_scope: bool,
) -> list[str]:
    """Build the npx skills command for one source repository group."""
    skills = [str(name) for name in group.get("skills") or []]
    if not group.get("source") or not skills:
        raise ValueError(f"Invalid skill group: {group!r}")
    command = [
        "npx",
        "-y",
        cli,
        "add",
        str(group["source"]),
        "--skill",
        *skills,
        "-a",
        agent,
        "--copy",
        "-y",
    ]
    if global_scope:
        command.append("-g")
    return command


def npm_install_command(tool: dict) -> list[str]:
    package = str(tool.get("package") or "")
    if not package:
        raise ValueError(f"Invalid npm tool: {tool!r}")
    return ["npm", "install", "-g", package]


def parse_version(value: str) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\.(\d+)", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def node_version() -> tuple[int, int] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        proc = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    return parse_version(proc.stdout.strip())


def run_command(
    command: list[str],
    *,
    cwd: Path,
    dry_run: bool,
    label: str,
) -> bool:
    printable = shlex.join(command)
    print(f"[bootstrap] {label}: {printable}")
    if dry_run:
        return True
    try:
        proc = subprocess.run(command, cwd=str(cwd), check=False)
    except FileNotFoundError:
        print(f"[fail] Command not found: {command[0]}")
        return False
    except OSError as exc:
        print(f"[fail] Could not run {command[0]}: {exc}")
        return False
    if proc.returncode != 0:
        print(f"[fail] {label} exited with status {proc.returncode}")
        return False
    return True


def workspace_python(workspace: Path) -> Path:
    if sys.platform.startswith("win"):
        return workspace / ".venv" / "Scripts" / "python.exe"
    return workspace / ".venv" / "bin" / "python"


def install_python(
    workspace: Path,
    requirements: Path,
    *,
    use_venv: bool,
    dry_run: bool,
) -> tuple[bool, Path]:
    """Create a workspace venv when needed and install requirements."""
    if use_venv:
        venv = workspace / ".venv"
        python = workspace_python(workspace)
        if not venv.exists():
            if not dry_run:
                workspace.mkdir(parents=True, exist_ok=True)
            if not run_command(
                [sys.executable, "-m", "venv", str(venv)],
                cwd=workspace,
                dry_run=dry_run,
                label="create virtual environment",
            ):
                return False, python
        install_python_exe = python
    else:
        install_python_exe = Path(sys.executable)

    if not requirements.is_file():
        print(f"[fail] Requirements file not found: {requirements}")
        return False, install_python_exe

    ok = run_command(
        [str(install_python_exe), "-m", "pip", "install", "-r", str(requirements)],
        cwd=workspace,
        dry_run=dry_run,
        label="install Python requirements",
    )
    return ok, install_python_exe


def install_skills(
    manifest: dict,
    workspace: Path,
    *,
    agent: str,
    global_scope: bool,
    include_optional: bool,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    """Install companion skills. Return (required_failures, optional_failures)."""
    required_failures: list[str] = []
    optional_failures: list[str] = []
    groups = list(manifest.get("skills") or [])
    optional_groups = list(manifest.get("optional_skills") or [])
    if include_optional:
        groups.extend(optional_groups)

    if not groups:
        return required_failures, optional_failures

    if not dry_run and not shutil.which("npx"):
        message = "npx not found; install Node.js 22.20+ to install companion skills"
        print(f"[fail] {message}")
        for group in manifest.get("skills") or []:
            required_failures.extend(str(name) for name in group.get("skills") or [])
        for group in optional_groups:
            optional_failures.extend(str(name) for name in group.get("skills") or [])
        return required_failures, optional_failures

    version = node_version()
    if not dry_run and version is not None and version < MIN_NODE:
        print(
            f"[warn] Node.js {version[0]}.{version[1]} is older than "
            f"{MIN_NODE[0]}.{MIN_NODE[1]}; npx skills may fail."
        )

    cli = str(manifest["skills_cli"])
    for group in groups:
        command = skills_command(
            group,
            cli=cli,
            agent=agent,
            global_scope=global_scope,
        )
        ok = run_command(
            command,
            cwd=workspace,
            dry_run=dry_run,
            label=f"install skills from {group['source']}",
        )
        if not ok:
            names = [str(name) for name in group.get("skills") or []]
            if group in optional_groups:
                optional_failures.extend(names)
            else:
                required_failures.extend(names)
    return required_failures, optional_failures


def install_npm_tools(
    manifest: dict,
    workspace: Path,
    *,
    dry_run: bool,
) -> list[str]:
    """Install optional npm tools. Return a list of failures."""
    failures: list[str] = []
    tools = list(manifest.get("npm_tools") or [])
    if not tools:
        return failures
    if not dry_run and not shutil.which("npm"):
        print("[warn] npm not found; skipping Browser Control CLI.")
        return [str(tool.get("package") or tool) for tool in tools]

    version = node_version()
    if not dry_run and version is not None and version < MIN_NODE:
        print(
            f"[warn] Node.js {version[0]}.{version[1]} is older than "
            f"{MIN_NODE[0]}.{MIN_NODE[1]}; Browser Control may not run."
        )

    for tool in tools:
        binary = str(tool.get("binary") or "")
        if binary and shutil.which(binary):
            print(f"[bootstrap] already installed: {binary}")
            continue
        ok = run_command(
            npm_install_command(tool),
            cwd=workspace,
            dry_run=dry_run,
            label=f"install {tool.get('package', 'npm tool')}",
        )
        if not ok:
            failures.append(str(tool.get("package") or tool))
    return failures


def print_next_steps(python: Path, workspace: Path) -> None:
    doctor = SKILL_DIR / "scripts" / "doctor.py"
    run_pipeline = SKILL_DIR / "scripts" / "run_pipeline.py"
    print("")
    print("[bootstrap] Next steps:")
    print(f'  "{python}" "{doctor}"')
    print(
        f'  "{python}" "{run_pipeline}" --business "dentists" '
        '--location "Lahore" --areas "Gulberg, DHA" --limit 25'
    )
    if sys.platform.startswith("win"):
        print("  If you plan to use the Selenium fallback, install Firefox:")
        print("    winget install Mozilla.Firefox")
    elif sys.platform == "darwin":
        print("  If you plan to use the Selenium fallback, install Firefox:")
        print("    brew install --cask firefox")
    else:
        print("  If you plan to use the Selenium fallback, install Firefox:")
        print("    sudo apt install firefox  # Debian/Ubuntu")
    print("")
    print(f"[bootstrap] Workspace: {workspace.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install business-lead-intelligence dependencies."
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Writable workspace for .venv and pipeline output (default: current directory).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to dependencies.json (default: the bundled manifest).",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent target for npx skills (default: the manifest value, usually opencode).",
    )
    parser.add_argument(
        "--project",
        action="store_true",
        help="Install companion skills into the current project instead of globally.",
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="Install Python packages into the current interpreter instead of .venv.",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Skip all companion-skill installs.",
    )
    parser.add_argument(
        "--no-optional",
        action="store_true",
        help="Skip optional companion skills such as browser-control.",
    )
    parser.add_argument(
        "--no-browser-control",
        action="store_true",
        help="Skip the optional Browser Control skill and npm CLI.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands without installing anything.",
    )
    args = parser.parse_args()

    if sys.version_info < MIN_PYTHON:
        print(f"[fail] Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.")
        raise SystemExit(1)

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[fail] Could not read dependency manifest: {exc}")
        raise SystemExit(1)

    workspace = args.workspace.expanduser().resolve()
    if workspace == SKILL_DIR or SKILL_DIR in workspace.parents:
        print(
            "[warn] The workspace is inside the skill install directory. "
            "Use a separate writable directory for runs."
        )
    if not args.dry_run:
        workspace.mkdir(parents=True, exist_ok=True)

    agent = args.agent or str(manifest.get("agent") or "opencode")
    requirements = SKILL_DIR / str((manifest.get("python") or {}).get("requirements") or "requirements.txt")

    print("[bootstrap] Business Lead Intelligence dependency setup")
    print(f"[bootstrap] Skill: {SKILL_DIR}")
    print(f"[bootstrap] Workspace: {workspace}")

    required_failures: list[str] = []
    optional_failures: list[str] = []

    python_ok, python = install_python(
        workspace,
        requirements,
        use_venv=not args.no_venv,
        dry_run=args.dry_run,
    )
    if not python_ok:
        required_failures.append("Python requirements")

    if not args.no_skills:
        skill_failures, optional_skill_failures = install_skills(
            manifest,
            workspace,
            agent=agent,
            global_scope=not args.project,
            include_optional=not args.no_optional and not args.no_browser_control,
            dry_run=args.dry_run,
        )
        required_failures.extend(skill_failures)
        optional_failures.extend(optional_skill_failures)

    if not args.no_browser_control and not args.no_optional:
        optional_failures.extend(
            install_npm_tools(manifest, workspace, dry_run=args.dry_run)
        )

    if not args.dry_run and not args.no_skills:
        installed = installed_skill_names(workspace)
        expected = skill_names(
            manifest,
            include_optional=not args.no_optional and not args.no_browser_control,
        )
        missing = [name for name in expected if name not in installed]
        if missing:
            print(f"[warn] Companion skills not found after install: {', '.join(missing)}")
            print("[warn] Check the npx skills output above, then run doctor.py.")
        else:
            print("[ok] Companion skills are present.")

    if optional_failures:
        print("")
        print("[warn] Optional dependencies were not installed:")
        for item in optional_failures:
            print(f"  - {item}")
        print("  The Selenium + Firefox fallback still works without Browser Control.")

    if required_failures:
        print("")
        print("[fail] Required dependencies were not installed:")
        for item in required_failures:
            print(f"  - {item}")
        print("Fix the failures above, then run this script again.")
        raise SystemExit(1)

    print("")
    print("[ok] Dependency setup complete.")
    print_next_steps(python, workspace)


if __name__ == "__main__":
    main()
