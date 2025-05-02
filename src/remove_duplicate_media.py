#!/usr/bin/env python3
"""
====================

Recursively scan a directory for photos / videos, detect byte-for-byte
duplicates, and **delete** (or optionally move) the redundant copies.

READ ME FIRST
-------------------------------------------------------------------
* By default this script **only prints** what it would delete
  (`--dry-run`).  Add  `--delete`  to actually remove duplicates.
* The first file encountered in each duplicate set is kept; the rest
  are deleted / moved.
* Duplicate detection:
      1. group by file size
      2. SHA-256 hash of the *first* 1 MB  (fast pre-filter)
      3. full SHA-256 hash confirmation
* Multithreaded hashing (`-w / --workers`) for speed
-------------------------------------------------------------------

Usage examples
--------------
Dry run (safe):

    python deduplicate_media.py "G:/Photos"

Delete duplicates for real, using 8 worker threads:

    python deduplicate_media.py "G:/Photos" --delete -w 8

Move duplicates into a side folder instead of deleting:

    python deduplicate_media.py "G:/Photos" --delete --trash "G:/Duplicates"
"""

from __future__ import annotations
import argparse
import concurrent.futures as cf
import hashlib
import logging
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# ---------- tune here ---------------------------------------------------------
PHOTO_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".heic",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".cr2",
    ".nef",
}
VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".wmv",
    ".flv",
    ".mts",
    ".m2ts",
    ".3gp",
    ".hevc",
}
CHUNK_SIZE = 1 << 20  # 1 MiB
FAST_CHUNK = 1 << 20  # hash first 1 MiB for pre-filter
# -----------------------------------------------------------------------------


def iter_media(root: Path) -> List[Path]:
    wanted = PHOTO_EXTS | VIDEO_EXTS
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in wanted]


def sha256_of(path: Path, first_n: int | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        if first_n:
            h.update(fh.read(first_n))
        else:
            for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
                h.update(chunk)
    return h.hexdigest()


def group_by_size(files: List[Path]) -> Dict[int, List[Path]]:
    groups: Dict[int, List[Path]] = defaultdict(list)
    for f in files:
        try:
            groups[f.stat().st_size].append(f)
        except OSError:
            logging.warning(f"Cannot stat {f} – skipped")
    return {sz: lst for sz, lst in groups.items() if len(lst) > 1}


def compute_fast_hashes(group: List[Path]) -> Dict[str, List[Path]]:
    d: Dict[str, List[Path]] = defaultdict(list)
    for f in group:
        try:
            d[sha256_of(f, FAST_CHUNK)].append(f)
        except Exception as e:
            logging.warning(f"Hash failed for {f}: {e}")
    return {h: lst for h, lst in d.items() if len(lst) > 1}


def compute_full_hashes(files: List[Path], workers: int) -> Dict[str, List[Path]]:
    m: Dict[str, List[Path]] = defaultdict(list)
    with cf.ThreadPoolExecutor(workers) as ex:
        for f, digest in zip(files, ex.map(sha256_of, files)):
            m[digest].append(f)
    return {h: lst for h, lst in m.items() if len(lst) > 1}


def deduplicate(
    root: Path, dry: bool, trash: Path | None, workers: int
) -> Tuple[int, int]:
    files = iter_media(root)
    logging.info(f"Scanning {len(files):,} media files …")

    dup_sets: List[List[Path]] = []

    # 1) group by size
    for size_group in group_by_size(files).values():
        # 2) fast 1 MB hash
        for fast_group in compute_fast_hashes(size_group).values():
            # 3) full hash
            dup_sets.extend(compute_full_hashes(fast_group, workers).values())

    to_delete = sum(len(s) - 1 for s in dup_sets)
    logging.info(f"Found {len(dup_sets)} duplicate sets / {to_delete} files to remove.")

    removed = 0
    for dup in dup_sets:
        keeper, *clones = sorted(dup)  # keep first alphabetically
        for f in clones:
            if dry:
                logging.info(f"[dry-run] would delete {f}")
            else:
                try:
                    if trash:
                        trash.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), trash / f.name)
                    else:
                        f.unlink()
                    removed += 1
                    logging.debug(f"Removed {f}")
                except Exception as e:
                    logging.error(f"Failed to remove {f}: {e}")

    return len(dup_sets), removed


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="deduplicate_media",
        description="Remove duplicate photos/videos by content.",
    )
    ap.add_argument("root", type=Path, help="Folder to scan (recursive)")
    ap.add_argument(
        "-w",
        "--workers",
        type=int,
        default=4,
        help="Thread pool size for hashing (default 4)",
    )
    ap.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete/move duplicates (omit = dry run)",
    )
    ap.add_argument(
        "--trash", type=Path, help="Move duplicates here instead of deleting"
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose DEBUG log output"
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.trash and not args.delete:
        logging.error("--trash implies --delete; add the flag or omit --trash.")
        sys.exit(1)

    dup_sets, removed = deduplicate(
        args.root, dry=not args.delete, trash=args.trash, workers=args.workers
    )

    if args.delete:
        logging.info(f"✓ Done — removed {removed} files in {dup_sets} sets.")
    else:
        logging.info("✓ Dry-run complete — add --delete to remove them for real.")


if __name__ == "__main__":
    main()
