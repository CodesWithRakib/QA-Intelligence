"""Safe materialisation of untrusted project content (ZIP archives and local folders).

Nothing here executes imported content. Defences: path traversal (zip-slip), absolute paths,
symlinks, decompression bombs (size, count and ratio limits measured on real bytes),
Windows reserved names, and exclusion of real secret files from the copied workspace.
"""

import hashlib
import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

EXCLUDED_DIRS = {
    "node_modules", ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".next", ".nuxt", "dist",
    "build", "target", ".gradle", ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "coverage", ".turbo", ".cache", "vendor", "obj",
}  # fmt: skip
_SECRET_FILE = re.compile(
    r"^(\.env(\..+)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)|\.npmrc|\.pypirc|\.netrc)$",
    re.IGNORECASE,
)
_SECRET_FILE_SAFE = re.compile(r"^\.env\.(example|sample|template|dist)$", re.IGNORECASE)
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)
_CHUNK = 1024 * 1024


class UnsafeContentError(Exception):
    """The archive or folder violates an import safety rule."""


@dataclass(frozen=True)
class ImportLimits:
    max_total_bytes: int = 300 * 1024 * 1024
    max_file_bytes: int = 50 * 1024 * 1024
    max_files: int = 20_000
    max_compression_ratio: int = 200


@dataclass
class ImportSummary:
    file_count: int = 0
    size_bytes: int = 0
    content_hash: str = ""
    excluded_secret_files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    _digests: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def finalise(self) -> "ImportSummary":
        tree = hashlib.sha256()
        for rel, digest in sorted(self._digests):
            tree.update(f"{rel}\0{digest}\n".encode())
        self.content_hash = tree.hexdigest()
        return self


def is_secret_file(name: str) -> bool:
    return bool(_SECRET_FILE.match(name)) and not _SECRET_FILE_SAFE.match(name)


def _safe_relative(name: str) -> PurePosixPath:
    normalised = name.replace("\\", "/")
    path = PurePosixPath(normalised)
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised) or ".." in path.parts:
        raise UnsafeContentError(f"unsafe path in archive: {name!r}")
    for part in path.parts:
        if _WINDOWS_RESERVED.match(part) or part.endswith((" ", ".")) or any(c in part for c in '<>:"|?*\0'):
            raise UnsafeContentError(f"unsupported file name in archive: {name!r}")
    return path


def _target(dest: Path, relative: PurePosixPath) -> Path:
    target = (dest / Path(*relative.parts)).resolve()
    if not target.is_relative_to(dest.resolve()):
        raise UnsafeContentError(f"path escapes the workspace: {relative}")
    return target


def _account(summary: ImportSummary, limits: ImportLimits, size: int) -> None:
    summary.file_count += 1
    summary.size_bytes += size
    if summary.file_count > limits.max_files:
        raise UnsafeContentError(f"too many files (limit {limits.max_files})")
    if summary.size_bytes > limits.max_total_bytes:
        raise UnsafeContentError(f"content exceeds {limits.max_total_bytes // (1024 * 1024)} MB limit")


def extract_zip(archive: Path, dest: Path, limits: ImportLimits | None = None) -> ImportSummary:
    """Extract an untrusted ZIP into ``dest`` (which must be empty), enforcing all limits."""
    limits = limits or ImportLimits()
    summary = ImportSummary()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        bundle = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise UnsafeContentError("not a valid ZIP archive") from exc
    with bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            relative = _safe_relative(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UnsafeContentError(f"symbolic links are not allowed: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise UnsafeContentError("encrypted archives are not supported")
            if any(part in EXCLUDED_DIRS for part in relative.parts[:-1]):
                continue
            if is_secret_file(relative.name):
                summary.excluded_secret_files.append(str(relative))
                continue
            target = _target(dest, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            written, digest = 0, hashlib.sha256()
            with bundle.open(info) as source, target.open("wb") as sink:
                while chunk := source.read(_CHUNK):
                    written += len(chunk)
                    if written > limits.max_file_bytes:
                        raise UnsafeContentError(f"file too large: {info.filename!r}")
                    if written > max(info.compress_size, 1) * limits.max_compression_ratio and written > _CHUNK:
                        raise UnsafeContentError(f"suspicious compression ratio: {info.filename!r}")
                    digest.update(chunk)
                    sink.write(chunk)
            _account(summary, limits, written)
            summary._digests.append((str(relative), digest.hexdigest()))
    if summary.file_count == 0:
        raise UnsafeContentError("archive contains no importable files")
    return summary.finalise()


def copy_tree(source: Path, dest: Path, limits: ImportLimits | None = None) -> ImportSummary:
    """Copy a local folder into ``dest`` without following symlinks or copying secret files."""
    limits = limits or ImportLimits()
    summary = ImportSummary()
    source = source.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not (root_path / d).is_symlink())
        for name in sorted(files):
            path = root_path / name
            relative = PurePosixPath(path.relative_to(source).as_posix())
            if path.is_symlink() or not path.is_file():
                summary.skipped.append(f"{relative} (link or special file)")
                continue
            if is_secret_file(name):
                summary.excluded_secret_files.append(str(relative))
                continue
            size = path.stat().st_size
            if size > limits.max_file_bytes:
                summary.skipped.append(f"{relative} (larger than per-file limit)")
                continue
            _account(summary, limits, size)
            target = _target(dest, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with path.open("rb") as reader, target.open("wb") as sink:
                while chunk := reader.read(_CHUNK):
                    digest.update(chunk)
                    sink.write(chunk)
            summary._digests.append((str(relative), digest.hexdigest()))
    if summary.file_count == 0:
        raise UnsafeContentError("folder contains no importable files")
    return summary.finalise()
