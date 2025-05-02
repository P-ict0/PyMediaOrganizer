#!/usr/bin/env python3
import argparse
import concurrent.futures as cf
import itertools
import json
import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

# ---------- constants ------------------------------------------------
PHOTO_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".webp",
    ".bmp",
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
PROGRESS_EVERY = 1000  # INFO message every N copies
# --------------------------------------------------------------------------------

try:
    from PIL import Image, ExifTags
except ImportError:  # Pillow optional; JSON still works
    Image = None

# Map EXIF tag name → tag id
_EXIF_DT_TAG = None
if Image:
    for tag_id, tag_name in ExifTags.TAGS.items():
        if tag_name == "DateTimeOriginal":
            _EXIF_DT_TAG = tag_id
            break
# -------------------------------------------------------------------------------


# -------------------------------------------------------------------------------
# 1. JSON helpers
# -------------------------------------------------------------------------------
_JSON_DUP_RE = re.compile(r"(.+?)\((\d+)\)$")  #  e.g.  "image(1)"


def find_metadata_json(media: Path) -> Path | None:
    """
    Look in the same folder for the Google-Photos metadata of media.
    Covers:
        image.jpg      -> image.jpg.json
        image(1).jpg   -> image.jpg(1).json
        image (1).jpg  -> image.jpg (1).json
    Returns the Path if found, else None.
    """
    parent = media.parent

    # #1: straightforward "<file>.json"  -------------------------------
    cand = parent / f"{media.name}.json"
    if not cand.exists():
        cand = cand.with_suffix(".JSON")
    if cand.exists():
        return cand

    # #2: duplicate pattern "image(1).jpg" -> "image.jpg(1).json" -----
    m = _JSON_DUP_RE.match(media.stem)
    if m:
        base, num = m.groups()  # image , 1
        base = base.rstrip()  # remove trailing space (if any)
        # preserve any space that was inside the () in original stem
        suffix = media.suffix  # ".jpg"
        with_no_space = parent / f"{base}{suffix}({num}).json"
        with_space = parent / f"{base}{suffix} ({num}).json"
        for c in (
            with_no_space,
            with_space,
            with_no_space.with_suffix(".JSON"),
            with_space.with_suffix(".JSON"),
        ):
            if c.exists():
                return c

    return None


def year_from_google_json(json_path: Path) -> int | None:
    """
    Parse Google Photos JSON and return the taken year (int) if possible.
    Prefers photoTakenTime.timestamp; falls back to creationTime.timestamp.
    """
    try:
        with json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        ts = data.get("photoTakenTime", {}).get("timestamp") or data.get(
            "creationTime", {}
        ).get("timestamp")
        if ts:
            # use an *aware* datetime in UTC
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).year
    except Exception:
        pass
    return None


# -------------------------------------------------------------------------------
# 2. EXIF helper (same as before)
# -------------------------------------------------------------------------------
def year_from_photo_exif(path: Path) -> int | None:
    if not Image:
        return None
    try:
        with Image.open(path) as im:
            exif = im._getexif() or {}
            raw = exif.get(_EXIF_DT_TAG)  # "YYYY:MM:DD HH:MM:SS"
            if raw:
                return int(raw.split(":")[0])
    except Exception:
        pass
    return None


# -------------------------------------------------------------------------------
# 3. Unified year-detection
# -------------------------------------------------------------------------------
def file_year(path: Path) -> int:
    """
    Determine the year for *path* in this order:
      1. Google-Photos JSON side-car (if present)
      2. EXIF DateTimeOriginal  (photo only)
      3. Filesystem modification timestamp   (fallback)
    """
    meta_json = find_metadata_json(path)
    if meta_json:
        y = year_from_google_json(meta_json)
        if y:
            logging.debug(f"Year from JSON  ({meta_json.name}) -> {y}")
            return y

    if path.suffix.lower() in PHOTO_EXTS:
        y = year_from_photo_exif(path)
        if y:
            logging.debug(f"Year from EXIF        -> {y}  ({path.name})")
            return y

    y = datetime.fromtimestamp(path.stat().st_mtime).year
    logging.debug(f"Year from mtime       -> {y}  ({path.name})")
    return y


# -------------------------------------------------------------------------------
# 4. Copy helpers & worker
# -------------------------------------------------------------------------------
def next_non_clashing_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suff = dest.stem, dest.suffix
    for i in itertools.count(1):
        cand = dest.with_name(f"{stem} ({i}){suff}")
        if not cand.exists():
            return cand


def copy_one(src: Path, dest_root: Path, lock: threading.Lock):
    year = file_year(src)
    year_dir = dest_root / str(year)
    with lock:
        year_dir.mkdir(parents=True, exist_ok=True)

    dest_path = next_non_clashing_path(year_dir / src.name)
    shutil.copy2(src, dest_path)
    logging.debug(f"Copied {src} → {dest_path}")


def gather_files(root: Path) -> list[Path]:
    logging.info(f"Scanning '{root}' ...")
    wanted = PHOTO_EXTS | VIDEO_EXTS
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in wanted]


# -------------------------------------------------------------------------------
# 5. Main
# -------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Copy photos/videos into YYYY folders (multithreaded, Google-Photos aware)."
    )
    ap.add_argument("source_root", type=Path)
    ap.add_argument("dest_root", type=Path)
    ap.add_argument(
        "-w", "--workers", type=int, default=4, help="Thread pool size (default: 4)"
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show DEBUG-level per-file messages",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    files = gather_files(args.source_root)
    total = len(files)
    if total == 0:
        logging.info("No matching media files found — nothing to do.")
        return
    logging.info(f"Found {total} media files; copying into '{args.dest_root}' ...")

    lock = threading.Lock()
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for i, _ in enumerate(
            ex.map(lambda p: copy_one(p, args.dest_root, lock), files), 1
        ):
            if i % PROGRESS_EVERY == 0 or i == total:
                logging.info(f"Progress: {i} / {total} files copied")

    logging.info("✓ Finished — all files copied.")


if __name__ == "__main__":
    main()
