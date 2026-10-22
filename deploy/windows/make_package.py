"""Builds the Windows deployment package (a clean zip of the source + installer).

Usage:  python3 deploy/windows/make_package.py [output.zip]
Excludes local environments, build output, databases, keys and test data.
PowerShell/batch files get CRLF line endings for Windows editors.
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE_DIRS = {".venv", "venv", "node_modules", "dist", "keys", "protected_media", "__pycache__", ".git", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".sqlite3", ".pyc", ".pem", ".pfx", ".kbak", ".log"}
EXCLUDE_NAMES = {".env", ".DS_Store"}
CRLF = {".ps1", ".cmd", ".bat"}


def files():
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.suffix in EXCLUDE_SUFFIXES or path.name in EXCLUDE_NAMES:
            continue
        yield path, rel


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "Kantam-ERP-Windows.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, rel in files():
            data = path.read_bytes()
            if path.suffix in CRLF:
                text = data.decode("ascii")  # scripts must stay ASCII for Windows PowerShell 5.1
                data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")
            z.writestr(str(Path("Kantam-ERP-Windows") / rel).replace("\\", "/"), data)
            count += 1
    print(f"{out}  ({count} files, {out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
