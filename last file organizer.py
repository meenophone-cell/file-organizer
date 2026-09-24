import argparse
import fnmatch
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# --------------------------------------------------------------------------
# Optional colour support
# --------------------------------------------------------------------------
# colorama is optional: without it the script simply prints plain text.
# Colours are also off when NO_COLOR is set or --no-color is given.

try:
    from colorama import Fore, Style, init

    init(autoreset=True)  # needed for Windows terminals
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

    class _NoColor:
        """Stand-in so Fore.GREEN, Style.DIM, ... all become empty strings."""

        def __getattr__(self, name: str) -> str:
            return ""

    Fore = Style = _NoColor()  # type: ignore[assignment]

USE_COLOR = COLORAMA_AVAILABLE and not os.environ.get("NO_COLOR")


def _paint(text: str, style: str) -> str:
    """Wrap text in a colour/style, or return it unchanged if colours are off."""
    if not USE_COLOR:
        return text
    return f"{style}{text}{Style.RESET_ALL}"


def green(text: str) -> str:
    return _paint(text, Fore.GREEN)


def yellow(text: str) -> str:
    return _paint(text, Fore.YELLOW)


def red(text: str) -> str:
    return _paint(text, Fore.RED)


def cyan(text: str) -> str:
    return _paint(text, Fore.CYAN)


def dim(text: str) -> str:
    return _paint(text, Style.DIM)


def bold(text: str) -> str:
    return _paint(text, Style.BRIGHT)


def make_output_safe() -> None:
    """
    Never crash on printing: file names with odd characters (or a console that
    can't show '→') are printed with replacement characters instead.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


# --------------------------------------------------------------------------
# Default settings
# --------------------------------------------------------------------------

DEFAULT_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "images": (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"),
    "documents": (".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"),
    "spreadsheets": (".xls", ".xlsx", ".csv"),
    "videos": (".mp4", ".mkv", ".mov", ".avi", ".webm"),
    "code": (".py", ".js", ".html", ".css", ".java", ".cpp", ".json"),
    "archives": (".zip", ".rar", ".7z", ".tar", ".gz"),
    "audio": (".mp3", ".wav", ".flac", ".m4a", ".ogg"),
}

FALLBACK_CATEGORY = "other"

# Starts with ".", so the organizer never moves it.
LOG_NAME = ".organizer_log.json"
CONFIG_NAME = "organizer_config.json"

# Never moved: system files, and downloads / documents that are still in use.
BUILTIN_EXCLUDE = [
    "desktop.ini", "thumbs.db", "ehthumbs.db",
    "*.crdownload", "*.part", "*.download", "*.opdownload",
    "~$*",  # Office lock files
]

# Recursive mode never goes into these (moving their files would break them).
PROTECTED_DIR_NAMES = {"node_modules", "__pycache__", "venv", "env", "site-packages"}
PROJECT_MARKERS = (".git", "package.json", "pyproject.toml")

# What classify_item() can decide about an item
PROCESS = "process"  # a file to organize
FOLDER = "folder"    # a normal folder (recursive mode may go inside)
SKIP = "skip"        # leave it alone

try:
    THIS_SCRIPT: Optional[Path] = Path(__file__).resolve()
except (NameError, OSError, RuntimeError):
    THIS_SCRIPT = None


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------

def is_valid_category_name(name: str) -> bool:
    """
    Category names become folder names, so they must be plain names:
    no slashes (would escape the folder), no '..', nothing hidden.
    """
    if not name or name.startswith("."):
        return False
    return not any(ch in name for ch in '/\\:*?"<>|')


def normalize_extension(ext: str) -> str:
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else f".{ext}"


def find_config() -> Optional[Path]:
    """Look for the config next to the script, then in the current directory."""
    candidates: List[Path] = []

    try:
        candidates.append(Path(__file__).resolve().parent / CONFIG_NAME)
    except (NameError, OSError, RuntimeError):
        pass

    try:
        candidates.append(Path.cwd() / CONFIG_NAME)
    except (OSError, RuntimeError):  # e.g. the current directory was deleted
        pass

    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue

    return None


def load_config() -> Tuple[Dict[str, Tuple[str, ...]], List[str]]:
    """
    Load categories and exclude patterns from organizer_config.json.

    Example file:
        {
          "categories": {"ebooks": [".epub", ".mobi"], "images": [".png"]},
          "exclude": ["*.tmp", "keep_*"]
        }
    Note: if "categories" is given, it REPLACES the built-in categories.
    """
    categories = DEFAULT_CATEGORIES.copy()
    exclude: List[str] = []

    config_path = find_config()
    if config_path is None:
        return categories, exclude

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(yellow(f"Warning: could not read config ({e}). Using defaults."))
        return categories, exclude

    if not isinstance(data, dict):
        print(yellow("Warning: config must be a JSON object. Using defaults."))
        return categories, exclude

    raw_categories = data.get("categories")
    if raw_categories is not None:
        if not isinstance(raw_categories, dict):
            print(yellow("Warning: 'categories' must be an object. Using defaults."))
        else:
            new_categories: Dict[str, Tuple[str, ...]] = {}

            for name, extensions in raw_categories.items():
                key = str(name).strip().lower()

                if not is_valid_category_name(key):
                    print(yellow(f"Warning: ignored invalid category name '{name}'."))
                    continue

                if not isinstance(extensions, list) or not all(
                    isinstance(ext, str) for ext in extensions
                ):
                    print(yellow(f"Warning: ignored '{name}' (needs a list of text)."))
                    continue

                new_categories[key] = tuple(
                    normalize_extension(ext) for ext in extensions if ext.strip()
                )

            if new_categories:
                categories = new_categories
            else:
                print(yellow("Warning: no valid categories in config. Using defaults."))

    raw_exclude = data.get("exclude")
    if raw_exclude is not None:
        if isinstance(raw_exclude, list):
            exclude = [str(p) for p in raw_exclude if str(p).strip()]
        else:
            print(yellow("Warning: 'exclude' must be a list. Ignored."))

    return categories, exclude


CATEGORIES, CONFIG_EXCLUDE = load_config()
CATEGORY_NAMES = set(CATEGORIES) | {FALLBACK_CATEGORY}
DEFAULT_EXCLUDE = BUILTIN_EXCLUDE + CONFIG_EXCLUDE


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def get_category(extension: str) -> str:
    """Return the category name for a given file extension."""
    extension = extension.lower()

    for category, extensions in CATEGORIES.items():
        if extension in extensions:
            return category

    return FALLBACK_CATEGORY


def is_category_name(name: str) -> bool:
    """Case-insensitive check (Windows/macOS treat 'Images' and 'images' alike)."""
    return name.lower() in CATEGORY_NAMES


def get_unique_destination(
    destination: Path,
    reserved: Optional[Set[Path]] = None
) -> Path:
    """
    If the target already exists (or was already picked for another file in
    this run), append _1, _2, ... until a free name is found.
    """
    def taken(path: Path) -> bool:
        return path.exists() or (reserved is not None and path in reserved)

    if not taken(destination):
        return destination

    stem = destination.stem
    suffix = destination.suffix
    parent = destination.parent
    counter = 1

    while True:
        new_destination = parent / f"{stem}_{counter}{suffix}"

        if not taken(new_destination):
            return new_destination

        counter += 1


def display_path(path: Path, folder: Path) -> str:
    """Path relative to `folder` for messages; falls back to the bare name."""
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return path.name


def is_inside(path: Path, folder: Path) -> bool:
    """
    True if `path` lives strictly inside `folder` (folder must be resolved).
    The folder itself does not count.
    """
    try:
        relative = path.resolve().relative_to(folder)
    except (ValueError, OSError, RuntimeError):
        return False

    return relative != Path(".")


def unsafe_folder_reason(folder: Path) -> Optional[str]:
    """Return a reason if this folder is too risky to organize, else None."""
    if folder.parent == folder:
        return "a drive / filesystem root"

    try:
        if folder == Path.home().resolve():
            return "your home folder"
    except (RuntimeError, OSError):
        pass

    return None


def has_project_marker(path: Path) -> bool:
    """True if the folder looks like a project root (git repo, node/python project)."""
    try:
        return any((path / marker).exists() for marker in PROJECT_MARKERS)
    except OSError:
        return True  # can't look inside: safest to assume it is


def is_protected_folder(path: Path) -> bool:
    """True for folders that recursive mode must not take apart."""
    return path.name.lower() in PROTECTED_DIR_NAMES or has_project_marker(path)


def resolve_folder(text: str) -> Optional[Path]:
    """Turn user input into an existing, resolved folder path (or None)."""
    # Drag & drop into a terminal often adds quotes around the path
    text = text.strip().strip("'\"")

    if not text:
        print(red("No folder entered."))
        return None

    try:
        folder = Path(text).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        print(red(f"Invalid path: {e}"))
        return None

    if not folder.is_dir():
        print(red("Folder not found or you don't have access to it."))
        return None

    return folder


def matches_exclude(name: str, patterns: List[str]) -> bool:
    """Case-insensitive wildcard matching (* and ?)."""
    name = name.lower()
    return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in patterns)


# --------------------------------------------------------------------------
# Moving files
# --------------------------------------------------------------------------

def move_file(
    source: Path,
    destination: Path,
    preview: bool = False,
    display_name: Optional[str] = None,
    reserved: Optional[Set[Path]] = None
) -> Tuple[bool, str, Optional[Path]]:
    """
    Move a file, or report what would happen in preview mode.
    `display_name` is what is shown in messages (e.g. 'sub/a.jpg').
    `reserved` holds destinations already picked in this run, so two files
    with the same name get different targets (in preview too).

    Returns:
        (success, message, final_destination)
    """
    name = display_name or source.name
    unique = get_unique_destination(destination, reserved)
    renamed = unique.name != source.name

    if preview:
        if reserved is not None:
            reserved.add(unique)

        if renamed:
            message = yellow(
                f"Would rename & move: {name} → {unique.parent.name}/{unique.name}"
            )
        else:
            message = yellow(f"Would move: {name} → {unique.parent.name}/")
        return True, message, unique

    try:
        shutil.move(str(source), str(unique))

        if reserved is not None:
            reserved.add(unique)

        if renamed:
            return True, green(f"Moved (renamed): {name} → {unique.name}"), unique

        return True, green(f"Moved: {name}"), unique

    except PermissionError:
        return False, red(f"Permission denied: {name}"), None

    except OSError as e:
        return False, red(f"Could not move {name}: {e}"), None


def prepare_category_folder(category_folder: Path, create: bool) -> Optional[str]:
    """
    Check (and, if `create`, create) the category folder.
    Returns an error message, or None if everything is fine.
    """
    if category_folder.is_symlink():
        return f"'{category_folder.name}' is a symlink, not a real folder"

    if category_folder.exists() and not category_folder.is_dir():
        return f"A file named '{category_folder.name}' is in the way"

    if create:
        try:
            category_folder.mkdir(exist_ok=True)
        except OSError as e:
            return f"Could not create folder '{category_folder.name}': {e}"

    return None


def classify_item(
    item: Path,
    root: Path,
    exclude_patterns: List[str]
) -> Tuple[str, Optional[str]]:
    """
    Decide once what to do with an item.

    Returns (status, note):
      - (PROCESS, None)   -> organize this file
      - (FOLDER, note)    -> a normal folder; `note` is what to print if
                             we do NOT go inside it
      - (SKIP, None)      -> skip silently
      - (SKIP, "text")    -> skip and print the note
    """
    try:
        # Hidden files/folders (.DS_Store, .git, our own log, ...)
        if item.name.startswith("."):
            return SKIP, None

        # The config file must never be moved (it may sit in this folder)
        if item.name.lower() == CONFIG_NAME:
            return SKIP, None

        if matches_exclude(item.name, exclude_patterns):
            return SKIP, dim(f"Skipped (excluded): {item.name}")

        # Symlinks: resolving a looping symlink can raise an error
        if item.is_symlink():
            return SKIP, dim(f"Skipped (symlink): {item.name}")

        # Never move this script itself
        if THIS_SCRIPT is not None and item.resolve() == THIS_SCRIPT:
            return SKIP, None

        if item.is_dir():
            # Our category folders live directly in the chosen folder.
            # A folder with the same name deeper down is the user's own.
            if item.parent == root and is_category_name(item.name):
                return SKIP, None
            return FOLDER, dim(f"Skipped folder: {item.name}")

        # Sockets, devices, etc.
        if not item.is_file():
            return SKIP, dim(f"Skipped (not a regular file): {item.name}")

    except (OSError, RuntimeError) as e:
        return SKIP, red(f"Skipped {item.name}: {e}")

    return PROCESS, None


# --------------------------------------------------------------------------
# Undo log
# --------------------------------------------------------------------------
# The log is a list of {"from", "to", "run"} entries. Paths are relative to the
# organized folder, so undo still works if the folder is renamed or moved.
# Each organize run adds its entries with its own "run" id, so several runs can
# be undone one after the other (newest first).

def load_log(log_path: Path, quiet: bool = False) -> Optional[List[Dict[str, str]]]:
    """Read and validate the undo log. Returns None if it is unusable."""
    def complain(message: str) -> None:
        if not quiet:
            print(red(message))

    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # ValueError covers bad JSON / encoding
        complain(f"Could not read the undo log: {e}")
        return None

    valid = isinstance(data, list) and all(
        isinstance(entry, dict)
        and isinstance(entry.get("from"), str)
        and isinstance(entry.get("to"), str)
        and (entry.get("run") is None or isinstance(entry.get("run"), str))
        for entry in data
    )

    if not valid:
        complain("The undo log has an unexpected format, so it can't be used.")
        return None

    return data


def write_log(log_path: Path, entries: List[Dict[str, str]]) -> None:
    """Write the log, or delete it if there is nothing left to undo."""
    if entries:
        log_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    else:
        log_path.unlink()


def save_log(folder: Path, moves: List[Dict[str, str]], run_id: str) -> None:
    """Add the moves of this run to the undo log (older runs are kept)."""
    if not moves:
        return

    log_path = folder / LOG_NAME
    existing: List[Dict[str, str]] = []

    try:
        if log_path.is_file():
            loaded = load_log(log_path, quiet=True)

            if loaded is None:
                # Unreadable log: keep a copy instead of overwriting it
                backup = folder / (LOG_NAME + ".bak")
                log_path.replace(backup)
                print(yellow(f"\nThe old undo log was unreadable; kept as {backup.name}"))
            else:
                existing = loaded

        entries = existing + [dict(move, run=run_id) for move in moves]
        write_log(log_path, entries)

    except OSError as e:
        print(red(f"\nWarning: could not save the undo log: {e}"))


# --------------------------------------------------------------------------
# Main operations
# --------------------------------------------------------------------------

def organize_folder(
    folder: Path,
    preview: bool = False,
    recursive: bool = False,
    exclude_patterns: Optional[List[str]] = None
) -> Dict[str, int]:
    """
    Organize the files in the given folder (and its subfolders if recursive).
    Everything ends up in category folders directly inside `folder`.

    Returns a summary dictionary:
    {category: count}
    """
    if exclude_patterns is None:
        exclude_patterns = DEFAULT_EXCLUDE

    summary: Dict[str, int] = defaultdict(int)
    moves: List[Dict[str, str]] = []
    reserved: Set[Path] = set()
    run_id = datetime.now().isoformat()

    # An explicit to-do list instead of recursion: very deep folder trees
    # can't hit Python's recursion limit.
    pending: List[Path] = [folder]

    try:
        while pending:
            current = pending.pop()

            try:
                # sorted() also takes a snapshot, so moving files can't
                # affect the loop, and the output order is stable.
                items = sorted(current.iterdir(), key=lambda p: p.name.lower())
            except OSError as e:
                print(red(f"Could not read {current}: {e}"))
                continue

            subfolders: List[Path] = []

            for item in items:
                status, note = classify_item(item, folder, exclude_patterns)

                if status == FOLDER:
                    if not recursive:
                        print(note)
                    elif is_protected_folder(item):
                        print(dim(f"Skipped project/dependency folder: {item.name}"))
                    else:
                        subfolders.append(item)
                    continue

                if status == SKIP:
                    if note:
                        print(note)
                    continue

                category = get_category(item.suffix)
                category_folder = folder / category
                relative = item.relative_to(folder).as_posix()

                error = prepare_category_folder(category_folder, create=not preview)
                if error:
                    print(red(f"Skipped {relative}: {error}"))
                    continue

                success, message, final_path = move_file(
                    item,
                    category_folder / item.name,
                    preview=preview,
                    display_name=relative,
                    reserved=reserved
                )

                print(message)

                # Only count successful operations
                if success:
                    summary[category] += 1

                    if not preview and final_path is not None:
                        moves.append({
                            "from": relative,
                            "to": final_path.relative_to(folder).as_posix(),
                        })

            # Keep alphabetical order: first subfolder is processed first
            pending.extend(reversed(subfolders))

    finally:
        # Saved even if the user presses Ctrl+C halfway through
        if not preview:
            save_log(folder, moves, run_id)

    return summary


def undo_last_run(folder: Path, preview: bool = False) -> None:
    """
    Move the files of the most recent organize run back where they were.
    Older runs stay in the log, so you can undo them one after another.
    In preview mode, only show what would be restored.
    """
    log_path = folder / LOG_NAME

    if not log_path.is_file():
        print(yellow("Nothing to undo: no log found in this folder."))
        return

    moves = load_log(log_path)
    if moves is None:
        return

    if not moves:
        print(yellow("The undo log is empty."))
        return

    # Entries without a "run" (older versions of this script) count as one run
    last_run = moves[-1].get("run")
    batch = [entry for entry in moves if entry.get("run") == last_run]
    older = [entry for entry in moves if entry.get("run") != last_run]

    restored = 0
    missing = 0
    rejected = 0
    failed_entries: List[Dict[str, str]] = []  # retryable: stay in the log
    touched_folders = set()

    print(cyan(f"\nLast run: {len(batch)} file(s)"))
    print()

    # Reverse order, so the last move is undone first
    for entry in reversed(batch):
        # Old logs may hold absolute paths; folder / absolute == absolute
        current = folder / entry["to"]
        original = folder / entry["from"]

        # Safety: never touch anything outside the chosen folder
        if not (is_inside(current, folder) and is_inside(original, folder)):
            print(red(f"Ignored (outside the folder): {entry['from']} → {entry['to']}"))
            rejected += 1
            continue

        if not current.exists():
            print(yellow(f"Missing: {display_path(current, folder)}"))
            missing += 1
            continue

        target = get_unique_destination(original)

        if preview:
            # "renamed" only if something already sits at the original spot
            if target != original:
                print(yellow(f"Would restore (renamed): {entry['from']} → {target.name}"))
            else:
                print(yellow(f"Would restore: {entry['from']}"))
            restored += 1
            continue

        try:
            # The original subfolder may have been deleted meanwhile
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(target))
            touched_folders.add(current.parent)
            restored += 1
            print(green(f"Restored: {display_path(target, folder)}"))
        except OSError as e:
            print(red(f"Could not restore {display_path(current, folder)}: {e}"))
            failed_entries.append(entry)

    unresolved = missing + rejected

    if preview:
        print(cyan(f"\nWould restore {restored} file(s), {unresolved} not found."))
        return

    # If nothing could be restored at all, the log is probably still needed
    # (wrong folder? files moved by hand?), so it is left untouched.
    if restored == 0 and not failed_entries:
        print(yellow(
            "\nNothing could be restored. The log was kept, in case this is "
            "the wrong folder.\nIf the files are gone for good, delete "
            f"{LOG_NAME} to reset it."
        ))
        return

    # Clean up empty category folders (rmdir refuses non-empty ones)
    for category_folder in touched_folders:
        if category_folder.parent == folder and is_category_name(category_folder.name):
            try:
                category_folder.rmdir()
            except OSError:
                pass

    # Keep older runs, plus the entries that failed here (original order),
    # so a retry doesn't see files that were already restored.
    failed_entries.reverse()
    remaining = older + failed_entries

    try:
        write_log(log_path, remaining)
    except OSError as e:
        print(red(f"\nWarning: could not update the undo log: {e}"))

    print(cyan(
        f"\nRestored {restored} file(s), {unresolved} not found, "
        f"{len(failed_entries)} failed."
    ))

    if failed_entries:
        print(yellow("The failed files are kept in the log, so you can run undo again."))

    older_runs = len({entry.get("run") for entry in older})
    if older_runs:
        print(cyan(f"{older_runs} earlier run(s) can still be undone."))


def print_summary(summary: Dict[str, int], preview: bool) -> None:
    """Print a summary of what happened."""
    if not summary:
        print(yellow("\nNo files were processed."))
        return

    action = "Would process" if preview else "Processed"
    print(cyan(f"\n=== {action} Summary ==="))

    total = 0

    for category, count in sorted(summary.items()):
        print(f"  {category:12} : {count}")
        total += count

    print(bold(f"  {'total':12} : {total}"))


def run_organize(
    folder: Path,
    preview: bool,
    assume_yes: bool = False,
    recursive: bool = False,
    extra_exclude: Optional[List[str]] = None,
    force: bool = False
) -> int:
    """Shared by interactive and command-line mode. Returns an exit code."""
    exclude = DEFAULT_EXCLUDE + (extra_exclude or [])

    if not preview:
        reason = unsafe_folder_reason(folder)

        if reason:
            print(red(f"Refusing to organize {reason}:\n{folder}"))
            print(yellow("Please choose a specific subfolder instead (e.g. Downloads)."))
            return 1

        if has_project_marker(folder) and not force:
            print(red(f"This looks like a project folder (git repo, package.json, ...):\n{folder}"))
            print(yellow("Organizing it would scatter the project's files. "
                         "Use --force if you really want that."))
            return 1

        if not assume_yes:
            scope = "everything, including ALL subfolders," if recursive else "everything"
            confirm = input(
                f"\nReally organize {scope} in:\n{folder}\n(y/n): "
            ).strip().lower()

            if confirm != "y":
                print(yellow("Cancelled."))
                return 0

    print()
    summary = organize_folder(
        folder,
        preview=preview,
        recursive=recursive,
        exclude_patterns=exclude
    )
    print_summary(summary, preview=preview)

    if not preview and summary:
        print(cyan("\nTip: run again and choose undo to reverse this."))

    return 0


# --------------------------------------------------------------------------
# Interactive + command-line entry points
# --------------------------------------------------------------------------

def interactive_mode() -> int:
    folder = resolve_folder(input("Enter the folder you want to organize: "))
    if folder is None:
        return 1

    while True:
        mode = input("Preview, organize or undo last run? (p/o/u): ").strip().lower()
        if mode in ("p", "o", "u"):
            break
        print(red("Invalid choice. Enter 'p' (preview), 'o' (organize) or 'u' (undo)."))

    if mode == "u":
        while True:
            undo_mode = input("Preview the undo or restore now? (p/r): ").strip().lower()
            if undo_mode in ("p", "r"):
                break
            print(red("Invalid choice. Enter 'p' for preview or 'r' to restore."))

        undo_last_run(folder, preview=(undo_mode == "p"))
        return 0

    answer = input("Include subfolders (recursive)? (y/n): ").strip().lower()

    return run_organize(folder, preview=(mode == "p"), recursive=(answer == "y"))


def main() -> int:
    global USE_COLOR

    make_output_safe()

    parser = argparse.ArgumentParser(
        description="Organize files in a folder by type (images, documents, etc.)"
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help="Path to the folder you want to organize"
    )

    # Only one action at a time: conflicting flags are rejected by argparse
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "-p", "--preview",
        action="store_true",
        help="Preview what would be moved (does not change anything)"
    )
    action.add_argument(
        "-o", "--organize",
        action="store_true",
        help="Organize the files"
    )
    action.add_argument(
        "-u", "--undo",
        action="store_true",
        help="Undo the last organize run"
    )
    action.add_argument(
        "--undo-preview",
        action="store_true",
        help="Preview what the undo would restore"
    )

    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Also organize files inside subfolders (with -p or -o)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the confirmation question when organizing (-o)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow organizing a project folder (git repo, package.json, ...)"
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Turn off coloured output (the NO_COLOR variable also works)"
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Skip names matching this wildcard, e.g. '*.tmp' (repeatable)"
    )

    args = parser.parse_args()

    if args.no_color:
        USE_COLOR = False

    has_action = any([args.preview, args.organize, args.undo, args.undo_preview])

    # No folder and no action → ask questions interactively
    if not args.folder and not has_action:
        return interactive_mode()

    if not args.folder:
        parser.error(f"a folder path is required, e.g. {parser.prog} ~/Downloads -p")

    if not has_action:
        parser.error("please specify an action: -p, -o, -u or --undo-preview")

    if args.recursive and not (args.preview or args.organize):
        parser.error("-r/--recursive only works together with -p or -o")

    folder = resolve_folder(args.folder)
    if folder is None:
        return 1

    if args.undo or args.undo_preview:
        undo_last_run(folder, preview=args.undo_preview)
        return 0

    return run_organize(
        folder,
        preview=args.preview,
        assume_yes=args.yes,
        recursive=args.recursive,
        extra_exclude=args.exclude,
        force=args.force
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(yellow("\nCancelled."))
        sys.exit(130)
    except EOFError:
        print(yellow("\nInput ended, cancelled."))
        sys.exit(1)
    except BrokenPipeError:
        # Output was piped into something that closed early (e.g. `| head`).
        # Silence Python's exit-time flush error instead of showing a traceback.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        sys.exit(1)
