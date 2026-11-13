"""Outbound policy (SSRF) and safe import of untrusted content."""

import stat
import zipfile
from pathlib import Path

import pytest

from app.security.archive import ImportLimits, UnsafeContentError, copy_tree, extract_zip
from app.security.ssrf import TargetNotAllowed, normalise_allow_entry, validate_subresource, validate_target

ALLOWED = ["127.0.0.1:8100"]


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://127.0.0.1:8100/", "gopher://127.0.0.1:8100/", "http://user:pw@127.0.0.1:8100/", "http:///nohost"],
)
def test_rejects_bad_schemes_credentials_and_missing_host(url: str) -> None:
    with pytest.raises(TargetNotAllowed):
        validate_target(url, ALLOWED)


def test_allowlisted_loopback_target_is_accepted() -> None:
    assert validate_target("http://127.0.0.1:8100/path", ALLOWED).port == 8100


@pytest.mark.parametrize("url", ["http://127.0.0.1:8101/", "http://10.0.0.5/", "http://192.168.1.10/", "http://[::1]:8100/"])
def test_private_addresses_need_an_exact_allowlist_entry(url: str) -> None:
    with pytest.raises(TargetNotAllowed):
        validate_target(url, ALLOWED)


def test_public_target_still_requires_authorisation() -> None:
    with pytest.raises(TargetNotAllowed, match="not an authorised target"):
        validate_target("http://93.184.216.34/", ALLOWED)


def test_metadata_endpoint_blocked_even_when_allowlisted() -> None:
    with pytest.raises(TargetNotAllowed, match="metadata"):
        validate_target("http://169.254.169.254/latest/meta-data/", ["169.254.169.254:80"])


def test_ipv4_mapped_ipv6_metadata_address_is_blocked() -> None:
    with pytest.raises(TargetNotAllowed, match="metadata"):
        validate_subresource("http://[::ffff:169.254.169.254]/", ["[::ffff:169.254.169.254]:80"])


def test_subresource_policy_allows_public_blocks_private() -> None:
    assert validate_subresource("https://93.184.216.34/lib.js", ALLOWED).host == "93.184.216.34"
    with pytest.raises(TargetNotAllowed, match="private"):
        validate_subresource("http://192.168.0.1/router", ALLOWED)


def test_allowlist_entries_are_normalised_and_validated() -> None:
    assert normalise_allow_entry(" HTTP://LocalHost:8100/x ") == "localhost:8100"
    assert normalise_allow_entry("https://example.com") == "example.com:443"
    with pytest.raises(ValueError):
        normalise_allow_entry("no-port")


def _zip(path: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, "/etc/passwd")
    return path


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "C:/evil.txt", "ok/con.txt"])
def test_zip_with_unsafe_paths_is_rejected(tmp_path: Path, name: str) -> None:
    archive = _zip(tmp_path / "a.zip", {name: b"x"})
    with pytest.raises(UnsafeContentError):
        extract_zip(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"ok.txt": b"x"}, symlink="link")
    with pytest.raises(UnsafeContentError, match="symbolic"):
        extract_zip(archive, tmp_path / "out")


def test_zip_bomb_is_stopped_by_ratio_and_size_limits(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (30 * 1024 * 1024)})
    with pytest.raises(UnsafeContentError, match="compression ratio"):
        extract_zip(archive, tmp_path / "out")
    with pytest.raises(UnsafeContentError, match="too large"):
        extract_zip(archive, tmp_path / "out2", ImportLimits(max_file_bytes=1024 * 1024, max_compression_ratio=10**9))


def test_zip_file_count_limit(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(20)})
    with pytest.raises(UnsafeContentError, match="too many files"):
        extract_zip(archive, tmp_path / "out", ImportLimits(max_files=10))


def test_zip_extracts_sources_but_never_secret_files(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "ok.zip", {"src/app.py": b"print(1)", ".env": b"KEY=1", ".env.example": b"KEY=", "key.pem": b"k"})
    summary = extract_zip(archive, tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").rglob("*") if p.is_file()) == [".env.example", "app.py"]
    assert sorted(summary.excluded_secret_files) == [".env", "key.pem"]
    assert summary.file_count == 2 and len(summary.content_hash) == 64


def test_copy_tree_skips_dependencies_and_secrets_and_is_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "src"
    (source / "node_modules" / "x").mkdir(parents=True)
    (source / "node_modules" / "x" / "index.js").write_text("x")
    (source / "main.py").write_text("print(1)")
    (source / ".env").write_text("SECRET=1")
    first = copy_tree(source, tmp_path / "one")
    second = copy_tree(source, tmp_path / "two")
    assert [p.name for p in (tmp_path / "one").rglob("*")] == ["main.py"]
    assert first.excluded_secret_files == [".env"] and first.content_hash == second.content_hash
