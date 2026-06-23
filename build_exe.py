"""
Build EBO Trend Builder into a standalone .exe using PyInstaller.

Usage:
    python build_exe.py

Output:
    dist/EBO_Trend_Builder.exe   (single-file executable)
"""

import subprocess
import sys
import os
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "ebo_trend_builder.py")
DIST_DIR = os.path.join(SCRIPT_DIR, "dist")
BUILD_DIR = os.path.join(SCRIPT_DIR, "build")
SPEC_FILE = os.path.join(SCRIPT_DIR, "ebo_trend_builder.spec")


def main():
    print("=" * 60)
    print("  EBO Trend Builder — PyInstaller Build")
    print("=" * 60)

    # Check requirements
    try:
        import PyInstaller  # noqa: F401
        print("[✓] PyInstaller found")
    except ImportError:
        print("[✗] PyInstaller not installed. Installing...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"]
        )
        print("[✓] PyInstaller installed")

    # Clean previous builds
    for path in [DIST_DIR, BUILD_DIR, SPEC_FILE]:
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"  Removed directory: {path}")
            else:
                os.remove(path)
                print(f"  Removed file: {path}")

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",           # Single executable
        "--console",           # Show console window (CLI + debug)
        "--name", "EBO_Trend_Builder",
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--clean",
        "--noconfirm",
    ]

    # Add data files (reference XML)
    ref_xml = os.path.join(SCRIPT_DIR, "TrendExportExample_ToolBuild.xml")
    if os.path.exists(ref_xml):
        cmd.extend(["--add-data", f"{ref_xml};."])
        print(f"[✓] Reference XML found: {os.path.basename(ref_xml)}")
    else:
        print(f"[!] Reference XML not found (optional): {ref_xml}")

    cmd.append(MAIN_SCRIPT)

    print(f"\n  Source: {os.path.basename(MAIN_SCRIPT)}")
    print(f"  Output: {os.path.join(DIST_DIR, 'EBO_Trend_Builder.exe')}")
    print("\n  Building...\n")

    try:
        subprocess.check_call(cmd)
        exe_path = os.path.join(DIST_DIR, "EBO_Trend_Builder.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"\n{'=' * 60}")
            print(f"  ✓ Build complete!")
            print(f"  Output: {exe_path}")
            print(f"  Size:   {size_mb:.1f} MB")
            print(f"{'=' * 60}")
        else:
            print(f"\n[✗] Build completed but exe not found at {exe_path}")
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n[✗] Build failed with exit code {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
