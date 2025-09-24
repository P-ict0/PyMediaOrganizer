# 📸 Media Toolkit

A tiny **two-script toolkit** for managing big photo / video collections:

| Script | What it does |
|--------|--------------|
| **`organize_media.py`** | Copies images & videos into year-based folders (`…/2023/…`). <br>• Reads Google-Photos JSON side-cars, EXIF and file-mtime to pick the right year.<br>• Incremental: skips files that are already present and identical. |
| **`remove_duplicate_media.py`** | Recursively finds **byte-for-byte duplicates** (even with different names) and removes, or moves, the redundant files. |

---

## 📖 Description

Keeping thousands of memories on an external SSD?  
These two scripts help you keep order **fast and safely**:

* **Organize** — walk any folder tree, discover each file’s year, copy it to an output tree grouped by year (`YYYY`). Existing identical files are skipped; name clashes become `name (1).jpg`, `name (2)…`.
* **Remove Duplicates** — detect identical media by *content* (size → fast 1 MiB hash → full SHA-256) and delete or quarantine the extras.

Both tools are multi-threaded, friendly to Ctrl-C and log what they’re doing.

---

## 🚀 Quick start

```bash
# 1) install dependencies (Pillow only needed for EXIF)
python -m pip install pillow

# 2) organise media from SSD to a neatly-dated structure
python organize_media.py "G:/Unorganized" "G:/Organized" -w 8

# 3) dry-run duplicate detection (safe, does not remove anything)
python remove_duplicate_media.py "G:/Organized"

# 4) really delete duplicates, keeping a trash folder
python deduplicate_media.py "G:/Organized" --delete --trash "G:/DupTrash" -w 8
```

## ✨ Features

- Google Photos aware  – reads *.json side-car timestamps (handles image.jpg(1).json, etc.).
- EXIF fallback  – DateTimeOriginal if JSON is absent.
- Filesystem fallback  – file-mtime when nothing else is available.
- Incremental  – identical files are skipped, so re-runs are fast.
- Multithreaded  – user-configurable worker count.
- Graceful / instant Ctrl-C  – kills or finishes workers as configured.
- Duplicate detection  – three-stage hash for speed (size ➜ 1 MiB ➜ full).
- Dry-run mode  – see what would happen before deleting anything.

## 📦 Installation

No package needed – clone the repo and run:
    
```bash
git clone https://github.com/P-ict0/PyMediaOrganizer.git
cd PyMediaOrganizer
python -m pip install pillow
```

## 📲 Usage

**Organize**
```bash
python organize_media.py <SOURCE_DIR> <DEST_DIR> [options]
```

**Example:**
```bash
python organize_media.py "G:/Camera Roll" "G:/Sorted" -w 8 -v
```

**Remove Duplicates**
```bash
python remove_duplicate_media.py <ROOT_DIR> [options]
```

Example (delete duplicates for real):
```bash
python remove_duplicate_media.py "G:/Sorted" --delete --trash "G:/DupTrash" -w 8
```

## ⚙ Options

| Flag(s) | Script | Default | Description |
|---------|--------|---------|-------------|
| `-w`, `--workers <N>` | **both** | `4` | Number of concurrent threads (copying / hashing). Increase for SSDs with many small files. |
| `-v`, `--verbose` | **both** | _off_ | Enable DEBUG-level per-file log output. |
| `--delete` | **remove_duplicate_media** | _dry-run_ | Actually delete or move duplicates (otherwise it just lists them). |
| `--trash <DIR>` | **remove_duplicate_media** | _none_ | Move duplicates into `<DIR>` instead of deleting.<br>Automatically implies `--delete`. |
