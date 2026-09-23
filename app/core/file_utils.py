"""
Filename, extension, size and MIME type helpers.

Split out of app.core.utils, which still re-exports every name here.
"""

import os


def get_file_extension(filename: str) -> str:
    """
    Get the extension of a file.

    Args:
        filename: The filename

    Returns:
        str: The file extension
    """
    return os.path.splitext(filename)[1].lower()


def is_image_file(filename: str) -> bool:
    """
    Check if a file is an image.

    Args:
        filename: The filename

    Returns:
        bool: True if the file is an image
    """
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}
    return get_file_extension(filename) in image_extensions


def is_video_file(filename: str) -> bool:
    """
    Check if a file is a video.

    Args:
        filename: The filename

    Returns:
        bool: True if the file is a video
    """
    video_extensions = {".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm"}
    return get_file_extension(filename) in video_extensions


def is_audio_file(filename: str) -> bool:
    """
    Check if a file is an audio file.

    Args:
        filename: The filename

    Returns:
        bool: True if the file is an audio file
    """
    audio_extensions = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}
    return get_file_extension(filename) in audio_extensions


def get_file_size_str(size_bytes: int) -> str:
    """
    Get a human-readable file size string.

    Args:
        size_bytes: The file size in bytes

    Returns:
        str: Human-readable file size
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def get_mime_type(filename: str) -> str:
    """
    Get the MIME type of a file.

    Args:
        filename: The filename

    Returns:
        str: The MIME type
    """
    import mimetypes

    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"
