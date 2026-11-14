"""Android detection from project files (no build, no execution)."""

import re
from pathlib import Path, PurePosixPath
from typing import Any

_APPLICATION_PLUGIN = re.compile(r"com\.android\.application|libs\.plugins\.android\.application")
_APPLICATION_ID = re.compile(r"applicationId\s*=?\s*[\"']([\w.]+)[\"']")
_SDK = {name: re.compile(rf"{name}(?:Version)?\s*=?\s*(\d+)") for name in ("minSdk", "targetSdk", "compileSdk")}
MAX_GRADLE_BYTES = 256 * 1024


def detect(files: list[tuple[str, Path, int]]) -> dict[str, Any]:
    """Return APK artifacts, Android source projects and Trusted Web Activity configurations."""
    relative = {rel for rel, _, _ in files}
    projects: list[dict[str, Any]] = []
    for rel, path, size in files:
        name = PurePosixPath(rel).name
        directory = PurePosixPath(rel).parent.as_posix().removeprefix(".")
        if name in {"build.gradle", "build.gradle.kts"} and size <= MAX_GRADLE_BYTES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not _APPLICATION_PLUGIN.search(text):
                continue
            parent = PurePosixPath(directory).parent.as_posix().removeprefix(".")
            kind = "native (Kotlin/Java)"
            if (
                f"{PurePosixPath(parent).parent.as_posix().removeprefix('.')}/pubspec.yaml".lstrip("/") in relative
                or "pubspec.yaml" in relative
            ):
                kind = "Flutter"
            elif any(r.endswith("package.json") and "react-native" in p.read_text(encoding="utf-8", errors="replace")[:20000]
                     for r, p, s in files if r.count("/") <= 1 and r.endswith("package.json") and s < 200_000):  # fmt: skip
                kind = "React Native"
            application_id = _APPLICATION_ID.search(text)
            projects.append({
                "kind": kind, "path": directory, "application_id": application_id.group(1) if application_id else None,
                **{key: (int(m.group(1)) if (m := pattern.search(text)) else None) for key, pattern in _SDK.items()},
                "has_instrumented_tests": any(r.startswith(f"{directory}/src/androidTest/") for r in relative),
                "has_unit_tests": any(r.startswith(f"{directory}/src/test/") for r in relative),
            })  # fmt: skip
    return {
        "apk_files": sorted(rel for rel in relative if rel.lower().endswith(".apk"))[:20],
        "aab_files": sorted(rel for rel in relative if rel.lower().endswith(".aab"))[:20],
        "source_projects": projects[:20],
        "twa_manifests": sorted(rel for rel in relative if PurePosixPath(rel).name == "twa-manifest.json")[:10],
    }
