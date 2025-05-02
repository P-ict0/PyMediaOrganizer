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


# ---------- constants ---------------------------------------------------------
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
UNKNOWN_DIR = "_unknown"  # folder for files with no valid date
PROGRESS_EVERY = 1_000  # INFO message every N copies
MAX_DUPES = 1_000  # safety-valve for next_non_clashing_path
# ------------------------------------------------------------------------------

try:
    from PIL import Image, ExifTags
except ImportError:  # EXIF support optional
    Image = None

_EXIF_DT_TAG = None
if Image:
    for t_id, t_name in ExifTags.TAGS.items():
        if t_name == "DateTimeOriginal":
            _EXIF_DT_TAG = t_id
            break

_JSON_DUP_RE = re.compile(r"(.+?)\((\d+)\)$")  #  "image(1)"
_THIS_YEAR = datetime.now(timezone.utc).year
_YEAR_MIN, _YEAR_MAX = 1900, _THIS_YEAR + 2
_LOCK = threading.Lock()  # mkdir synchronisation
# ------------------------------------------------------------------------------


# ---- helpers -----------------------------------------------------------------
def sanitize_year(y: int | None) -> int | None:
    """Return y only if it is in a sane range; else None."""
    if y and _YEAR_MIN <= y <= _YEAR_MAX:
        return y
    return None


def _same_file(src: Path, dst: Path) -> bool:
    """
    Return True if *dst* exists and has the same file size as *src*.
    (Good enough for incremental backup; avoids hashing large files.)
    """
    try:
        return dst.exists() and src.stat().st_size == dst.stat().st_size
    except OSError:
        return False


def find_metadata_json(media: Path) -> Path | None:
    """Locate Google Photos side-car JSON for *media*, if present."""
    parent = media.parent

    # 1) normal "<file>.json" / ".JSON"
    for ext in (".json", ".JSON"):
        cand = parent / f"{media.name}{ext}"
        if cand.exists():
            return cand

    # 2) duplicate pattern "image(1).jpg" → "image.jpg(1).json"
    m = _JSON_DUP_RE.match(media.stem)
    if m:
        base, num = m.groups()
        base = base.rstrip()  # trim possible space before '('
        suffix = media.suffix
        variants = (
            f"{base}{suffix}({num})",
            f"{base}{suffix} ({num})",
        )
        for stem in variants:
            for ext in (".json", ".JSON"):
                cand = parent / f"{stem}{ext}"
                if cand.exists():
                    return cand
    return None


def year_from_google_json(jpath: Path) -> int | None:
    try:
        with jpath.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        ts = data.get("photoTakenTime", {}).get("timestamp") or data.get(
            "creationTime", {}
        ).get("timestamp")
        if ts:
            year = datetime.fromtimestamp(int(ts), tz=timezone.utc).year
            return sanitize_year(year)
    except Exception as e:
        logging.debug(f"Broken JSON in {jpath} ({e})")
    return None


def year_from_photo_exif(p: Path) -> int | None:
    if not Image:
        return None
    try:
        with Image.open(p) as im:
            exif = im._getexif() or {}
            raw = exif.get(_EXIF_DT_TAG)  # "YYYY:MM:DD …"
            if raw:
                year = int(raw.split(":")[0])
                return sanitize_year(year)
    except Exception as e:
        logging.debug(f"EXIF read failed in {p} ({e})")
    return None


def file_year(path: Path) -> int | None:
    """Return a sane year or None (→ '_unknown')."""
    meta = find_metadata_json(path)
    if meta:
        y = year_from_google_json(meta)
        if y:
            logging.debug(f"Year via JSON  : {y}  ({path.name})")
            return y

    if path.suffix.lower() in PHOTO_EXTS:
        y = year_from_photo_exif(path)
        if y:
            logging.debug(f"Year via EXIF  : {y}  ({path.name})")
            return y

    # fallback: filesystem mtime
    y = sanitize_year(datetime.fromtimestamp(path.stat().st_mtime).year)
    if y:
        logging.debug(f"Year via mtime  : {y}  ({path.name})")
    return y


def next_non_clashing_path(dest: Path, src: Path) -> Path | None:
    """
    * If an identical file already exists at *dest*, return **None**  → skip.
    * If a different file exists, append  ' (1)', ' (2)' …  until a free name
      is found.  Returns that new Path.
    * If *dest* is free, simply returns *dest*.
    """
    if not dest.exists():
        return dest

    if _same_file(src, dest):  # identical – nothing to do
        return None

    stem, suff = dest.stem, dest.suffix
    for i in range(1, MAX_DUPES + 1):
        cand = dest.with_name(f"{stem} ({i}){suff}")
        if not cand.exists():
            return cand
        if _same_file(src, cand):  # duplicate already copied earlier
            return None
    raise RuntimeError(f"Too many duplicates for {dest}")


# ---- worker -------------------------------------------------------------------
def copy_one(src: Path, dest_root: Path) -> bool:
    """
    Copy *src* into its year folder.
    Returns True if a copy was actually made or skipped because identical,
    False on genuine error.
    """
    try:
        year = file_year(src) or UNKNOWN_DIR
        year_dir = dest_root / str(year)
        with _LOCK:
            year_dir.mkdir(parents=True, exist_ok=True)

        # decide where (or whether) to copy
        target = next_non_clashing_path(year_dir / src.name, src)
        if target is None:  # identical already there
            logging.debug(f"Skip (identical): {src}")
            return True

        shutil.copy2(src, target)  # real copy
        logging.debug(f"Copied {src} → {target}")
        return True

    except Exception as exc:
        logging.error(f"    Failed to copy {src}  ({exc})")
        logging.debug("Traceback:", exc_info=True)
        return False


def gather_files(root: Path) -> list[Path]:
    logging.info("Scanning '%s' ...", root)
    exts = PHOTO_EXTS | VIDEO_EXTS
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]


# ---- main ---------------------------------------------------------------------
import os, signal, sys
from concurrent.futures import wait, FIRST_COMPLETED, ThreadPoolExecutor


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Copy photos/videos into YYYY folders "
        "(multithreaded, Google-Photos aware, robust)."
    )
    ap.add_argument("source_root", type=Path)
    ap.add_argument("dest_root", type=Path)
    ap.add_argument(
        "-w", "--workers", type=int, default=4, help="Thread-pool size (default 4)"
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
    logging.info(f"Found {total} media files; copying into '{args.dest_root}' …")

    copied = errors = 0
    futures: dict[cf.Future, Path] = {}
    # Use a thread-pool executor to copy files in parallel
    executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=args.workers)

    def _sigint_handler(signo, frame):
        logging.warning("    Ctrl+C detected — killing all workers…")
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
        os._exit(130)  # 130 = interrupted by Ctrl-C

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        for p in files:
            fut = executor.submit(copy_one, p, args.dest_root)
            futures[fut] = p

        pending = set(futures)
        i = 0
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done:
                i += 1
                try:
                    ok = fut.result()
                    copied += 1 if ok else 0
                    errors += 0 if ok else 1
                except Exception as e:
                    logging.error(f"    Task crashed: {e}")
                    errors += 1

                if i % PROGRESS_EVERY == 0 or i == total:
                    logging.info(f"Progress: {i} / {total} processed ({errors} errors)")

    finally:
        # In normal completion shut the pool down cleanly
        if executor:
            executor.shutdown(wait=True)

    logging.info(f"Finished — {copied} copied, {errors} errors.")


if __name__ == "__main__":
    main()
