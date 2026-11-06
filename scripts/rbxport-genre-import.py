#!/usr/bin/env python3
"""Import the genre folders of a Down Low DJ USB stick into rbxport.

Reads `<stick>/Library/playlists.json` (written by Down Low DJ) and imports
every audio file in each `category: "genre"` folder with `rbxport-cli import`.
The CLI already provides everything this script relies on:

  - the playlist is named after the folder's basename and created at the
   playlist tree ROOT when it does not exist yet;
  - tracks already in the collection are skipped (`already in library`), but
    still placed in the genre playlist;
  - tracks already in the playlist are dropped by `Writer::add_tracks`.

Playlists are discovered from `metadata_path` (first path component), which is
the real folder name on the stick after sanitisation — safer than
`playlist_name`, which may differ (e.g. "GRG-How's" -> `GRG-How_s`).

Dry-run by default; pass --apply to touch the library.

Usage:
  rbxport-genre-import.py [--stick PATH] [--category cat] [--only name]...
                          [--no-analysis] [--dedupe] [--apply] [--list]

  --stick    USB mount point (default /run/media/<user>/KANGSTON)
  --category playlist category to import (default: genre)
  --only     restrict to matching folder names (substring, repeatable)
  --no-analysis  pass --no-analysis to rbxport-cli import
  --dedupe   run `rbxport-cli dedupe` after importing: removes duplicate
             rows (same size + length + title), keeping the copy with cues,
             then the one with waveform analysis
  --apply    actually write; without it the script only reports the plan
  --list     just print the discovered folders and audio counts
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus",
}
BIN_PATH = Path(__file__).resolve().parent.parent / "target/release/rbxport-cli"
FORK_ROOT = Path(__file__).resolve().parent.parent


def count_audio(folder: Path) -> int:
    n = 0
    for _dir, _dirs, files in os.walk(folder):
        n += sum(1 for f in files if Path(f).suffix.lower() in AUDIO_EXTENSIONS)
    return n


def discover(stick: Path, category: str) -> list[tuple[str, Path, int]]:
    """Return [(folder_name, folder_path, audio_count)] for plan-ordered discovery."""
    index = stick / "Library" / "playlists.json"
    if not index.is_file():
        sys.exit(f"error: {index} not found — is the right stick mounted under {stick}?")
    data = json.loads(index.read_text(encoding="utf-8"))
    found: list[tuple[str, Path, int]] = []
    for playlist in data.get("playlists", []):
        if playlist.get("category") != category:
            continue
        meta = playlist.get("metadata_path", "")
        name = meta.split("/")[0] if meta else ""
        if not name:
            print(f"warning: {playlist.get('playlist_name')} has no metadata_path; skipping", file=sys.stderr)
            continue
        folder = stick / "Library" / name
        if not folder.is_dir():
            print(f"warning: folder {folder} missing on stick; skipping", file=sys.stderr)
            continue
        found.append((name, folder, count_audio(folder)))
    return found


def ensure_binary() -> Path:
    if BIN_PATH.is_file():
        return BIN_PATH
    if shutil.which("cargo") is None:
        sys.exit("error: rbxport-cli is not built and cargo is missing")
    print("rbxport-cli not built yet — building (release)…", file=sys.stderr)
    subprocess.run(["cargo", "build", "--release", "--bin", "rbxport-cli"], cwd=FORK_ROOT, check=True)
    return BIN_PATH


def import_folder(cli: Path, folder: Path, no_analysis: bool) -> dict[str, int]:
    cmd = [str(cli), "import"]
    if no_analysis:
        cmd.append("--no-analysis")
    cmd.append(str(folder))
    proc = subprocess.run(cmd, text=True, capture_output=True)
    counts = {"added": 0, "existing": 0, "skipped": 0, "playlist_failed": 0, "analysis_failed": 0}
    for line in proc.stdout.splitlines():
        if line.startswith("added "):
            counts["added"] += 1
        elif line.startswith("already in library "):
            counts["existing"] += 1
    for line in proc.stdout.splitlines() + proc.stderr.splitlines():
        if line.startswith("skipped "):
            counts["skipped"] += 1
        elif line.startswith("playlist placement failed "):
            counts["playlist_failed"] += 1
        elif line.startswith("analysis failed "):
            counts["analysis_failed"] += 1
    if proc.returncode != 0:
        counts["import_failed"] = 1
        for line in (proc.stderr or "").splitlines():
            print(f"    {line}", file=sys.stderr)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_stick = "/run/media/" + os.environ.get("USER", "jonaxor") + "/KANGSTON"
    parser.add_argument("--stick", default=default_stick, help="USB mount point")
    parser.add_argument("--category", default="genre", help="playlists.json category to import")
    parser.add_argument("--only", action="append", default=[], help="only folders whose name contains this")
    parser.add_argument("--no-analysis", action="store_true", help="skip BPM/key analysis on new imports")
    parser.add_argument("--dedupe", action="store_true",
                        help="after importing, remove duplicate rows (keeps cues, then waveform analysis)")
    parser.add_argument("--apply", action="store_true", help="write to the library (default: dry run)")
    parser.add_argument("--list", action="store_true", help="print the plan and exit")
    args = parser.parse_args()

    stick = Path(args.stick)
    folders = discover(stick, args.category)
    if args.only:
        folders = [f for f in folders if any(pat in f[0] for pat in args.only)]
    if not folders:
        sys.exit(f"error: no '{args.category}' folders discovered under {stick}/Library")

    total = sum(f[2] for f in folders)
    print(f"{len(folders)} {args.category} folder(s), {total} audio file(s), stick={stick}")
    name_width = max(len(f[0]) for f in folders)
    for name, _folder, n in folders:
        print(f"  {name:{name_width}}  {n:5d}")

    if args.list:
        return
    if not args.apply:
        print("\ndry run — nothing was imported. Re-run with --apply to write.")
        if args.dedupe:
            cli = ensure_binary()
            print()
            subprocess.run([str(cli), "dedupe"])
        return

    cli = ensure_binary()
    grand = {"added": 0, "existing": 0, "skipped": 0, "playlist_failed": 0, "analysis_failed": 0, "import_failed": 0}
    failures = 0
    print()
    for name, folder, n in folders:
        counts = import_folder(cli, folder, args.no_analysis)
        for key in grand:
            grand[key] += counts.get(key, 0)
        failures += counts.get("playlist_failed", 0) + counts.get("import_failed", 0)
        note = []
        if counts.get("playlist_failed"):
            note.append(f"{counts['playlist_failed']} playlist-placement failures")
        if counts.get("import_failed"):
            note.append("import failed")
        print(f"  {name:{name_width}}  +{counts['added']:<5} ={counts['existing']:<5} x{counts['skipped']:<4}"
              f"  ({n} scanned){'  [' + '; '.join(note) + ']' if note else ''}")
        sys.stdout.flush()

    print(f"\ntotals: {grand['added']} added, {grand['existing']} already in library, "
          f"{grand['skipped']} skipped (refused), {grand['playlist_failed']} playlist failures, "
          f"{grand['analysis_failed']} analysis failures")

    if args.dedupe:
        print("\ndedupe — keeping the copy with cues, then waveform analysis:")
        if subprocess.run([str(cli), "dedupe", "--apply"]).returncode != 0:
            failures += 1

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
