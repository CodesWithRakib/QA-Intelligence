"""Locations of the project-local Phase 2 tools (installed by scripts/install-phase2-tools.ps1).

Nothing here is on PATH: every tool is resolved from the tools directory so that the platform never
depends on, or interferes with, software installed elsewhere on the machine.
"""

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT

ANDROID_API = "34"
AVD_NAME = "qai_api34"


def tools_dir() -> Path:
    return Path(os.environ.get("QAI_TOOLS_DIR") or REPO_ROOT / ".tools")


@dataclass(frozen=True)
class Tool:
    name: str
    path: Path
    purpose: str
    install_hint: str = "scripts\\install-phase2-tools.ps1"

    @property
    def available(self) -> bool:
        return self.path.is_file()


def _t(name: str, relative: str, purpose: str) -> Tool:
    return Tool(name, tools_dir() / relative, purpose)


def java() -> Tool:
    return _t("JDK 17", "jdk-17/bin/java.exe", "runs apksigner and the Android SDK tools")


def sdk_root() -> Path:
    return tools_dir() / "android-sdk"


def adb() -> Tool:
    return _t("adb", "android-sdk/platform-tools/adb.exe", "device communication")


def emulator() -> Tool:
    return _t("Android emulator", "android-sdk/emulator/emulator.exe", "virtual Android device")


def aapt2() -> Tool:
    return _t("aapt2", f"android-sdk/build-tools/{ANDROID_API}.0.0/aapt2.exe", "APK metadata and manifest extraction")


def apksigner() -> Tool:
    return _t("apksigner", f"android-sdk/build-tools/{ANDROID_API}.0.0/lib/apksigner.jar", "APK signature inspection")


def appium() -> Tool:
    return _t("Appium", "appium/node_modules/appium/build/lib/main.js", "Android UI automation server")


def lighthouse() -> Tool:
    return _t("Lighthouse", "lighthouse/node_modules/lighthouse/cli/index.js", "page performance audit")


def sample_apk() -> Tool:
    return _t("ApiDemos sample APK", "samples/ApiDemos-debug.apk", "harmless sample app for validating the Android engine")


def avd_home() -> Path:
    return tools_dir() / "avd"


def android_env() -> dict[str, str]:
    """Environment for Android SDK tools and Appium. Contains paths only, never secrets."""
    jdk = java().path.parents[1]
    env = {k: v for k, v in os.environ.items() if k.upper() in _PASSTHROUGH}
    env |= {
        "JAVA_HOME": str(jdk), "ANDROID_HOME": str(sdk_root()), "ANDROID_SDK_ROOT": str(sdk_root()),
        "ANDROID_AVD_HOME": str(avd_home()), "APPIUM_HOME": str(tools_dir() / "appium" / "home"),
        "PATH": os.pathsep.join([str(jdk / "bin"), str(sdk_root() / "platform-tools"), os.environ.get("PATH", "")]),
    }  # fmt: skip
    return env


# Only what child processes need to start on Windows; host secrets are not forwarded.
_PASSTHROUGH = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                "HOMEDRIVE", "HOMEPATH", "COMSPEC", "PATHEXT", "PROGRAMDATA", "PROGRAMFILES", "NUMBER_OF_PROCESSORS"}  # fmt: skip


def run(args: list[str | Path], *, timeout: int, env: dict[str, str] | None = None, cwd: Path | None = None,
        input_text: str | None = None) -> subprocess.CompletedProcess[str]:  # fmt: skip
    """Run a trusted local tool with a fixed argument vector (never through a shell)."""
    return subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        env=env or android_env(), cwd=cwd, input=input_text,
    )  # fmt: skip


@lru_cache
def node_binary() -> str | None:
    import shutil

    return shutil.which("node")
