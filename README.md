# File Organizer

A Python command-line tool that automatically organizes files
into folders based on their file types.

## Features

- Preview mode
- Duplicate-file protection
- Undo previous organization runs
- Recursive folder organization
- Custom categories through JSON configuration
- Custom exclude patterns
- Protection for project/dependency folders
- Symlink protection
- Error handling
- Command-line and interactive modes
- Optional colored output

## Requirements

- Python 3.10+
- colorama (optional)

## Usage

Interactive:

python organizer.py

Preview:

python organizer.py "path/to/folder" --preview

Organize:

python organizer.py "path/to/folder" --organize

Recursive:

python organizer.py "path/to/folder" --organize --recursive

Undo:

python organizer.py "path/to/folder" --undo

## Safety

The program supports preview mode, duplicate protection,
undo logs, exclusion patterns, and protection for project folders.

## License

MIT
