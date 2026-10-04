"""Extension -> file-type category mapping for the treemap's colour legend."""

from __future__ import annotations

import os

CATEGORY_NAMES = (
    "Images",
    "Videos",
    "Audio",
    "Documents",
    "Archives",
    "Executables",
    "Code",
    "Other",
)
FOLDER_CATEGORY = "Folder"  # not a file category; used only for directory nodes

_EXTENSION_MAP: dict[str, str] = {
    # Images
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
    ".bmp": "Images", ".svg": "Images", ".webp": "Images", ".ico": "Images",
    ".tif": "Images", ".tiff": "Images", ".heic": "Images",
    # Videos
    ".mp4": "Videos", ".mkv": "Videos", ".avi": "Videos", ".mov": "Videos",
    ".wmv": "Videos", ".flv": "Videos", ".webm": "Videos", ".m4v": "Videos",
    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio",
    ".ogg": "Audio", ".wma": "Audio", ".m4a": "Audio",
    # Documents
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents", ".txt": "Documents",
    ".xls": "Documents", ".xlsx": "Documents", ".ppt": "Documents", ".pptx": "Documents",
    ".csv": "Documents", ".rtf": "Documents", ".md": "Documents", ".odt": "Documents",
    # Archives
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives", ".tar": "Archives",
    ".gz": "Archives", ".iso": "Archives", ".vhdx": "Archives", ".bz2": "Archives",
    # Executables
    ".exe": "Executables", ".msi": "Executables", ".dll": "Executables",
    ".bat": "Executables", ".cmd": "Executables", ".ps1": "Executables",
    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".java": "Code", ".cpp": "Code",
    ".c": "Code", ".h": "Code", ".cs": "Code", ".go": "Code", ".rs": "Code",
    ".html": "Code", ".css": "Code", ".json": "Code", ".xml": "Code", ".sql": "Code",
}


def category_for(path: str, is_dir: bool) -> str:
    """Return the treemap category for a file/folder. Folders always get FOLDER_CATEGORY."""
    if is_dir:
        return FOLDER_CATEGORY
    return _EXTENSION_MAP.get(os.path.splitext(path)[1].lower(), "Other")
