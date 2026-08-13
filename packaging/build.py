#!/usr/bin/env python3
"""Build a native, self-contained desktop package for the current OS."""

import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    system = platform.system()
    if system not in {"Darwin", "Windows"}:
        print("Portable GUI builds are currently supported on macOS and Windows.")
        return 2

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "Conversation Reclaim",
    ]
    if system == "Windows":
        args.append("--onefile")
    else:
        args.extend([
            "--onedir",
            "--osx-bundle-identifier",
            "com.angelberrios.conversation-reclaim",
        ])
    args.append("gui.py")
    subprocess.run(args, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
