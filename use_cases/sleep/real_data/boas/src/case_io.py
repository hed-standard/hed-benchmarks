"""Fresh private output directories for BOAS downstream analysis."""

import os
from pathlib import Path


def validate_destination(destination: Path, inputs: tuple[Path, ...]) -> None:
    """Require an unused directory separate from every input root.

    Parameters:
        destination: Non-existing output directory with an existing parent.
        inputs: Read-only source and upstream result directories.
    """
    resolved = destination.resolve()
    for root in inputs:
        source = root.resolve()
        if resolved.is_relative_to(source) or source.is_relative_to(resolved):
            raise ValueError("Output must be separate from every input directory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Output already exists; use a fresh directory")
    if not destination.parent.is_dir():
        raise ValueError("Use a fresh directory in an existing private parent")


def write_outputs(destination: Path, contents: dict[str, str | bytes]) -> None:
    """Render payloads before creating a fresh private directory and exclusive files.

    Parameters:
        destination: Validated non-existing directory; never reused.
        contents: Basename-keyed text or binary payloads. Text is UTF-8 with explicit LF bytes.

    Raises:
        OSError: A directory or file cannot be written; partial results are not a successful run.
        ValueError: A payload name is not a basename or its content type is unsupported.
    """
    rendered = {}
    for name, content in contents.items():
        if not isinstance(name, str) or name in ("", ".", "..") or "\x00" in name or Path(name).name != name:
            raise ValueError("Output names must be plain basenames")
        if not isinstance(content, (str, bytes)):
            raise ValueError("Output content must be text or bytes")
        rendered[name] = (
            content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8") if isinstance(content, str) else content
        )
    destination.mkdir(mode=0o700)
    for name, content in rendered.items():
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
