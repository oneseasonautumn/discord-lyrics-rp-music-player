#!/usr/bin/env python3
"""Auto-deploy script for Lyric Media Player.

Checks the environment, installs dependencies, configures the Discord
Application ID, verifies FFmpeg, and launches the app.

Usage:
    python deploy.py            # full setup + launch
    python deploy.py --check    # only check environment
    python deploy.py --no-run   # setup without launching
"""

import argparse
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path

from config_manager import load_config, save_config


MIN_PYTHON = (3, 10)
REQUIRED_PACKAGES = [
    ("PyQt6", "PyQt6"),
    ("mutagen", "mutagen"),
    ("pypresence", "pypresence"),
    ("requests", "requests"),
]

PROJECT_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
CONFIG_FILE = PROJECT_DIR / "config.json"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def log(msg: str, color: str = RESET) -> None:
    print(f"{color}{msg}{RESET}")


def ok(msg: str) -> None:
    log(f"  [OK]   {msg}", GREEN)


def fail(msg: str) -> None:
    log(f"  [FAIL] {msg}", RED)


def warn(msg: str) -> None:
    log(f"  [WARN] {msg}", YELLOW)


def info(msg: str) -> None:
    log(f"  [INFO] {msg}", CYAN)


def check_python_version() -> bool:
    log("\n[1/6] Checking Python version...", CYAN)
    current = sys.version_info[:2]
    if current >= MIN_PYTHON:
        ok(f"Python {current[0]}.{current[1]} detected")
        return True
    fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {current[0]}.{current[1]}")
    return False


def check_pip() -> bool:
    log("\n[2/6] Checking pip...", CYAN)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ok("pip is available")
        return True
    except subprocess.CalledProcessError:
        fail("pip is not available. Install it and rerun.")
        return False


def check_os() -> None:
    log("\n[3/6] Detecting operating system...", CYAN)
    system = platform.system()
    release = platform.release()
    ok(f"{system} {release} ({platform.machine()})")

    if system == "Linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            warn("No DISPLAY/WAYLAND_DISPLAY found. GUI may not start in this session.")
        try:
            result = subprocess.run(
                ["ldconfig", "-p"],
                capture_output=True,
                text=True,
                check=False,
            )
            if "libxcb" not in result.stdout:
                warn("libxcb may be missing. Install it for PyQt6 on Linux.")
        except FileNotFoundError:
            warn("ldconfig not found, skipping library check.")


def check_discord_running() -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            output = subprocess.check_output(
                ["tasklist"], text=True, stderr=subprocess.DEVNULL
            )
            return "Discord.exe" in output
        elif system == "Darwin":
            output = subprocess.check_output(
                ["pgrep", "-x", "Discord"], text=True, stderr=subprocess.DEVNULL
            )
            return bool(output.strip())
        else:
            output = subprocess.check_output(
                ["pgrep", "-f", "discord"], text=True, stderr=subprocess.DEVNULL
            )
            return bool(output.strip())
    except Exception:
        return False


def install_requirements() -> bool:
    log("\n[4/6] Installing Python dependencies...", CYAN)

    missing = [
        module
        for module, _ in REQUIRED_PACKAGES
        if importlib.util.find_spec(module) is None
    ]

    if not missing:
        ok("All dependencies already installed")
        return True

    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if REQUIREMENTS_FILE.exists():
        cmd += ["-r", str(REQUIREMENTS_FILE)]
    else:
        cmd += [pkg for _, pkg in REQUIRED_PACKAGES]

    try:
        subprocess.run(cmd, check=True)
        ok("Dependencies installed")
        return True
    except subprocess.CalledProcessError as exc:
        fail(f"pip install failed: {exc}")
        return False


def configure_discord_id() -> bool:
    log("\n[5/6] Configuring Discord Application ID...", CYAN)

    config = load_config()
    existing = config.get("discord_app_id") or os.environ.get("DISCORD_APP_ID")

    if existing and existing != "YOUR_APP_ID":
        ok(f"Discord App ID already configured: {existing[:6]}...{existing[-4:]}")
        return True

    info("You need a Discord Application ID to enable Rich Presence.")
    info("Get one at: https://discord.com/developers/applications")
    info("Press Enter to skip (RPC will be disabled).")

    try:
        user_input = input("  Enter Discord Application ID: ").strip()
    except (EOFError, KeyboardInterrupt):
        user_input = ""

    if not user_input:
        warn("No App ID provided, skipping RPC setup.")
        config["discord_app_id"] = ""
        save_config(config)
        return True

    if not user_input.isdigit() or len(user_input) < 17:
        fail("That doesn't look like a valid Discord App ID (should be 17-20 digits).")
        return False

    config["discord_app_id"] = user_input
    save_config(config)
    ok(f"Saved Discord App ID to {CONFIG_FILE.name}")

    if not check_discord_running():
        warn("Discord does not appear to be running. RPC will connect when it starts.")
    else:
        ok("Discord client is running")

    return True


def check_ffmpeg() -> None:
    """Informational only. Does not fail setup if FFmpeg is missing."""
    log("\n[6/6] Checking for FFmpeg (optional)...", CYAN)
    try:
        from ffmpeg_locator import find_noninteractive

        path = find_noninteractive()
        if path:
            ok(f"FFmpeg found: {path}")
        else:
            warn("FFmpeg not found in PATH or config.")
            info("The player will prompt you to locate it on first launch.")
            info("FFmpeg is optional, but some features may need it.")
    except Exception as exc:
        warn(f"FFmpeg check skipped: {exc}")


def launch_app() -> int:
    log("\nLaunching Lyric Media Player...", CYAN)
    main_file = PROJECT_DIR / "main.py"
    if not main_file.exists():
        fail("main.py not found. Are you in the project directory?")
        return 1
    try:
        return subprocess.call([sys.executable, str(main_file)])
    except KeyboardInterrupt:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-deploy Lyric Media Player")
    parser.add_argument("--check", action="store_true", help="Only check environment")
    parser.add_argument("--no-run", action="store_true", help="Setup without launching")
    args = parser.parse_args()

    log("=" * 60, CYAN)
    log("  Lyric Media Player — Auto Deploy", CYAN)
    log("=" * 60, CYAN)

    if not check_python_version():
        return 1
    if not check_pip():
        return 1

    check_os()

    if args.check:
        log("\nEnvironment check complete.", GREEN)
        return 0

    if not install_requirements():
        return 1
    if not configure_discord_id():
        return 1

    check_ffmpeg()

    if args.no_run:
        log("\nSetup complete. Run 'python main.py' to start.", GREEN)
        return 0

    return launch_app()


if __name__ == "__main__":
    sys.exit(main())