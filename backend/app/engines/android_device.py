"""Android device management: one emulator (or an explicitly authorised physical device) through adb.

Resource policy for a 16 GB host: at most one device session, the emulator is started headless with
2 GB RAM, and it is only started when enough host memory is free. A physical device is never used
unless the owner enabled it in the execution settings.
"""

import ctypes
import logging
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app import tooling
from app.config import get_settings
from app.engines.base import EngineContext, EvidenceDraft

log = logging.getLogger(__name__)
MIN_FREE_RAM_GB = 3.0
CACHE_KEY = "android_device"
_CRASH = re.compile(r"FATAL EXCEPTION|ANR in |Fatal signal \d+|has died|Force finishing activity")


class DeviceUnavailable(Exception):
    """No usable Android device; the message is the exact reason shown to the owner."""


@dataclass
class Device:
    serial: str
    is_emulator: bool
    started_by_platform: bool = False
    properties: dict[str, str] = field(default_factory=dict)

    def adb(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return tooling.run([tooling.adb().path, "-s", self.serial, *args], timeout=timeout)

    def shell(self, command: str, timeout: int = 60) -> str:
        return self.adb("shell", command, timeout=timeout).stdout

    def screenshot(self, ctx: EngineContext, label: str) -> EvidenceDraft | None:
        """PNG screenshot through adb (binary safe)."""
        completed = subprocess.run(
            [str(tooling.adb().path), "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=60, env=tooling.android_env(),
        )  # fmt: skip
        if completed.returncode != 0 or not completed.stdout.startswith(b"\x89PNG"):
            return None
        ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.evidence_dir / f"android-{uuid.uuid4().hex}.png"
        path.write_bytes(completed.stdout)
        return EvidenceDraft("screenshot", label, file=path)

    def clear_logcat(self) -> None:
        self.adb("logcat", "-c")
        self.adb("logcat", "-b", "crash", "-c")

    def crash_log(self, package: str) -> tuple[list[str], str]:
        """Return (crash indicator lines for the package, raw error log tail)."""
        crash_buffer = self.adb("logcat", "-b", "crash", "-d", "-v", "brief").stdout
        errors = self.adb("logcat", "-d", "-v", "brief", "*:E").stdout
        text = crash_buffer + "\n" + errors
        indicators = []
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if _CRASH.search(line) and (package in line or any(package in near for near in lines[index : index + 4])):
                indicators.append(line.strip()[:300])
        return sorted(set(indicators))[:20], text[-12_000:]

    def profile(self) -> dict[str, str]:
        """Device configuration that every measurement must be read against."""
        return {"serial": self.serial, "type": "emulator" if self.is_emulator else "physical device", **self.properties}


def free_ram_gb() -> float:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]  # fmt: skip

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0.0
    return status.ullAvailPhys / (1024**3)


def connected() -> list[tuple[str, str]]:
    """(serial, state) for every device adb can see."""
    if not tooling.adb().available:
        return []
    output = tooling.run([tooling.adb().path, "devices"], timeout=30).stdout
    return [(parts[0], parts[1]) for line in output.splitlines()[1:] if len(parts := line.split()) >= 2]


def environment_problems() -> list[str]:
    """Missing pieces for Android dynamic testing (empty = environment is complete)."""
    problems = [f"{tool.name} is not installed ({tool.install_hint})"
                for tool in (tooling.java(), tooling.adb(), tooling.emulator()) if not tool.available]  # fmt: skip
    if not (tooling.avd_home() / f"{tooling.AVD_NAME}.avd").is_dir():
        problems.append(f"emulator profile '{tooling.AVD_NAME}' does not exist ({tooling.adb().install_hint})")
    return problems


def _describe(device: Device) -> None:
    props = {"model": "ro.product.model", "android_release": "ro.build.version.release", "api_level": "ro.build.version.sdk",
             "abi": "ro.product.cpu.abi"}  # fmt: skip
    device.properties = {name: device.shell(f"getprop {prop}").strip() for name, prop in props.items()}
    device.properties["screen"] = device.shell("wm size").strip().rsplit(": ", 1)[-1]
    device.properties["density_dpi"] = device.shell("wm density").strip().rsplit(": ", 1)[-1]


def _start_emulator(boot_timeout_s: int) -> Device:
    problems = environment_problems()
    if problems:
        raise DeviceUnavailable("; ".join(problems))
    free = free_ram_gb()
    if free < MIN_FREE_RAM_GB:
        raise DeviceUnavailable(
            f"the emulator needs about {MIN_FREE_RAM_GB:.0f} GB of free RAM and only {free:.1f} GB is available; "
            "close other applications (browser, IDE) and run again"
        )
    log_path = get_settings().data_dir / "logs" / "emulator.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as sink:
        process = subprocess.Popen(  # noqa: S603 - fixed argv
            [str(tooling.emulator().path), "-avd", tooling.AVD_NAME, "-no-window", "-no-audio", "-no-boot-anim",
             "-no-snapshot-save", "-gpu", "swiftshader_indirect", "-memory", "2048", "-cores", "2", "-netfast"],
            stdout=sink, stderr=subprocess.STDOUT, env=tooling.android_env(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )  # fmt: skip
    deadline = time.monotonic() + boot_timeout_s
    serial = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
            raise DeviceUnavailable(f"the emulator exited during start-up (code {process.returncode}): {tail}")
        emulators = [s for s, state in connected() if s.startswith("emulator-") and state == "device"]
        if emulators:
            serial = emulators[0]
            booted = tooling.run([tooling.adb().path, "-s", serial, "shell", "getprop", "sys.boot_completed"], timeout=30)
            if booted.stdout.strip() == "1":
                break
        time.sleep(5)
    else:
        process.terminate()
        raise DeviceUnavailable(f"the emulator did not finish booting within {boot_timeout_s}s (see data/logs/emulator.log)")
    device = Device(serial, is_emulator=True, started_by_platform=True)
    for setting in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        device.shell(f"settings put global {setting} 0")
    device.shell("input keyevent 82")  # dismiss the lock screen
    return device


def acquire(ctx: EngineContext) -> Device:
    """Return the single device session of this run, starting the emulator when necessary."""
    cached = ctx.run_cache.get(CACHE_KEY)
    if isinstance(cached, Device):
        return cached
    if isinstance(cached, DeviceUnavailable):
        raise cached
    try:
        if not tooling.adb().available:
            raise DeviceUnavailable(f"adb is not installed ({tooling.adb().install_hint})")
        ready = [serial for serial, state in connected() if state == "device"]
        emulators = [s for s in ready if s.startswith("emulator-")]
        physical = [s for s in ready if not s.startswith("emulator-")]
        if emulators:
            device = Device(emulators[0], is_emulator=True)
        elif physical and ctx.settings.android_allow_physical_device:
            device = Device(physical[0], is_emulator=False)
        else:
            if physical:
                log.info("physical device connected but not authorised in execution settings; using the emulator")
            device = _start_emulator(ctx.settings.android_boot_timeout_s)
        _describe(device)
    except DeviceUnavailable as exc:
        ctx.run_cache[CACHE_KEY] = exc
        raise
    except (subprocess.TimeoutExpired, OSError) as exc:
        failure = DeviceUnavailable(f"adb did not respond: {exc}")
        ctx.run_cache[CACHE_KEY] = failure
        raise failure from exc
    ctx.run_cache[CACHE_KEY] = device
    return device


def release(ctx: EngineContext) -> dict[str, Any]:
    """Uninstall what the run installed and stop an emulator the platform started."""
    device = ctx.run_cache.pop(CACHE_KEY, None)
    report: dict[str, Any] = {}
    if not isinstance(device, Device):
        return report
    for package in ctx.run_cache.pop("android_installed", []):
        device.adb("uninstall", package, timeout=120)
        report.setdefault("uninstalled", []).append(package)
    if device.started_by_platform:
        device.adb("emu", "kill", timeout=60)
        report["emulator_stopped"] = True
    return report
