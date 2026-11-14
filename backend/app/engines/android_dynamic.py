"""Android dynamic testing on one emulator or authorised device.

adb drives installation, launch, lifecycle, logs and measurements; Appium (UiAutomator2) drives the
UI exploration. Every measurement is reported together with the device profile it was taken on.
The APK is untrusted: it only ever runs inside the emulator/device, never on the host.
"""

import json
import logging
import re
import socket
import subprocess
import time
import xml.etree.ElementTree as ET  # noqa: S405 - parsing UI dumps produced by the platform's own device
from typing import Any

import httpx

from app import tooling
from app.config import get_settings
from app.domain import Category, Classification, ResultStatus, Severity
from app.engines import android_device, android_static
from app.engines.android_device import Device, DeviceUnavailable
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft

log = logging.getLogger(__name__)
APPIUM_KEY = "appium_server"
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_TOTAL_TIME = re.compile(r"TotalTime:\s*(\d+)")
_PSS = re.compile(r"TOTAL(?: PSS)?:?\s+(\d+)")
_JANK = re.compile(r"Janky frames:\s*(\d+)\s*\(([\d.]+)%\)")
_FRAMES = re.compile(r"Total frames rendered:\s*(\d+)")
MIN_TOUCH_TARGET_DP = 48
ANR_DIALOG_TEXT = "isn't responding"
# Items of list containers receive clicks through their parent, so they are interactive without clickable="true".
LIST_CONTAINERS = ("android.widget.ListView", "android.widget.GridView", "androidx.recyclerview.widget.RecyclerView")
INTERACTIVE_XPATH = "//*[@clickable='true'] | " + " | ".join(f"//{name}/*" for name in LIST_CONTAINERS)
COLD_START_SLOW_MS = 5000


def _finding(key: str, severity: Severity, title: str, description: str, info: android_static.ApkInfo, device: Device,
             evidence: list[EvidenceDraft], remediation: str, category: Category = Category.ANDROID,
             classification: Classification = Classification.CONFIRMED, **facts: Any) -> FindingDraft:  # fmt: skip
    return FindingDraft(
        key=f"apk-dynamic:{info.package}:{key}", category=category, severity=severity, title=title,
        description=f"{description}\n\nDevice: {json.dumps(device.profile())}", source_tool="android-dynamic",
        affected_component=f"{info.package} {info.version_name}".strip(), confidence=0.85, remediation=remediation,
        reproduction_steps=[f"Install the APK on a device matching: {device.profile()}.", f"Launch {info.package}.",
                            "Repeat the action described and watch `adb logcat`."],
        facts={"kind": "android_dynamic", "check": key, "device": device.profile(), **facts},
        evidence=evidence, classification=classification,
    )  # fmt: skip


def _prepare(ctx: EngineContext) -> tuple[Device, android_static.ApkInfo] | CaseOutcome:
    """Device ready and the app installed (once per run)."""
    apk = android_static.find_apk(ctx.workspace)
    if apk is None:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "the project contains no APK")
    try:
        info = ctx.run_cache.get("apk_info") or android_static.analyse(apk)
    except android_static.ApkAnalysisError as exc:
        return CaseOutcome.blocked(str(exc))
    ctx.run_cache["apk_info"] = info
    try:
        device = android_device.acquire(ctx)
    except DeviceUnavailable as exc:
        return CaseOutcome.blocked(f"no Android device: {exc}")
    installed = ctx.run_cache.setdefault("android_installed", [])
    if info.package not in installed:
        result = device.adb("install", "-r", "-t", "-g", str(apk), timeout=300)
        if "Success" not in result.stdout:
            return CaseOutcome.blocked(f"the APK could not be installed: {(result.stdout + result.stderr).strip()[-300:]}")
        installed.append(info.package)
    return device, info


def _component(info: android_static.ApkInfo) -> str:
    activity = info.launch_activity
    return f"{info.package}/{activity}" if activity else info.package


def _launch(device: Device, info: android_static.ApkInfo, *, cold: bool) -> tuple[int | None, str]:
    """Start the launcher activity and wait for it. Returns (TotalTime ms, raw output)."""
    if cold:
        device.shell(f"am force-stop {info.package}")
    if info.launch_activity:
        output = device.shell(f"am start -W -n {_component(info)}", timeout=120)
    else:
        output = device.shell(f"monkey -p {info.package} -c android.intent.category.LAUNCHER 1", timeout=120)
        time.sleep(3)
    match = _TOTAL_TIME.search(output)
    return (int(match.group(1)) if match else None), output


def _foreground(device: Device) -> str:
    focus = device.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    return focus.strip()[:300]


def _alive(device: Device, package: str) -> bool:
    return bool(device.shell(f"pidof {package}").strip())


def _crash_outcome(device: Device, info: android_static.ApkInfo, ctx: EngineContext, check: str, action: str,
                   evidence: list[EvidenceDraft], details: dict[str, Any]) -> CaseOutcome | None:  # fmt: skip
    """Shared crash/ANR verdict from logcat after an action."""
    indicators, raw = device.crash_log(info.package)
    evidence.append(EvidenceDraft("log", "logcat (errors and crash buffer)", raw))
    if not indicators:
        return None
    shot = device.screenshot(ctx, f"after crash: {action}")
    if shot:
        evidence.append(shot)
    details["crash_indicators"] = indicators
    return CaseOutcome(
        ResultStatus.FAILED, f"crash or ANR detected while: {action}", details, evidence,
        [_finding(f"crash:{check}", Severity.HIGH, f"App crashes or stops responding: {action}",
                  "logcat reported:\n" + "\n".join(f"- {line}" for line in indicators), info, device, evidence,
                  "Read the stack trace in the attached logcat and fix the unhandled exception.", action=action)],
    )  # fmt: skip


def _install_launch(ctx: EngineContext, device: Device, info: android_static.ApkInfo) -> CaseOutcome:
    device.clear_logcat()
    total, output = _launch(device, info, cold=True)
    time.sleep(2)
    evidence: list[EvidenceDraft] = [EvidenceDraft("tool_output", "am start output", output)]
    shot = device.screenshot(ctx, f"{info.package} after launch")
    if shot:
        evidence.append(shot)
    details = {"device": device.profile(), "package": info.package, "launch_time_ms": total, "foreground": _foreground(device)}
    if crashed := _crash_outcome(device, info, ctx, "launch", "launching the app", evidence, details):
        return crashed
    if not _alive(device, info.package):
        return CaseOutcome(
            ResultStatus.FAILED, "the app process is not running after launch", details, evidence,
            [_finding("not-running", Severity.HIGH, "App does not stay running after launch",
                      "No process for the package exists a few seconds after launch.", info, device, evidence,
                      "Check the launcher activity and start-up code.")],
        )  # fmt: skip
    return CaseOutcome(ResultStatus.PASSED, f"installed and launched ({total} ms reported by am start)", details, evidence)


# ---------------------------------------------------------------- Appium


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _appium_url(ctx: EngineContext) -> str:
    """Start the local Appium server once per run (bound to 127.0.0.1)."""
    cached = ctx.run_cache.get(APPIUM_KEY)
    if cached:
        return str(cached["url"])
    node = tooling.node_binary()
    if not (tooling.appium().available and node):
        raise DeviceUnavailable(f"Appium is not installed ({tooling.appium().install_hint})")
    port = _free_port()
    log_path = get_settings().data_dir / "logs" / "appium.log"
    sink = log_path.open("ab")
    process = subprocess.Popen(  # noqa: S603 - fixed argv
        [node, str(tooling.appium().path), "--address", "127.0.0.1", "--port", str(port), "--log-level", "warn"],
        stdout=sink, stderr=subprocess.STDOUT, env=tooling.android_env(), cwd=tooling.appium().path.parents[3],
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DeviceUnavailable(f"the Appium server exited (code {process.returncode}); see data/logs/appium.log")
        try:
            if httpx.get(f"{url}/status", timeout=3, trust_env=False).status_code == 200:
                ctx.run_cache[APPIUM_KEY] = {"url": url, "process": process, "log": sink}
                return url
        except httpx.HTTPError:
            time.sleep(2)
    process.terminate()
    raise DeviceUnavailable("the Appium server did not become ready within 90s")


def stop_appium(ctx: EngineContext) -> None:
    server = ctx.run_cache.pop(APPIUM_KEY, None)
    if server:
        server["process"].terminate()
        server["log"].close()


def _ui_exploration(ctx: EngineContext, device: Device, info: android_static.ApkInfo) -> CaseOutcome:
    """Bounded exploration: tap the clickable elements of the first screen, return, and watch for crashes."""
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.common.exceptions import WebDriverException
    except ImportError:
        return CaseOutcome.blocked("the Appium Python client is not installed in the backend environment")
    try:
        url = _appium_url(ctx)
    except DeviceUnavailable as exc:
        return CaseOutcome.blocked(str(exc))
    options = UiAutomator2Options()
    options.load_capabilities({
        "platformName": "Android", "appium:automationName": "UiAutomator2", "appium:udid": device.serial,
        "appium:appPackage": info.package, "appium:appActivity": info.launch_activity or None, "appium:noReset": True,
        "appium:autoGrantPermissions": True, "appium:newCommandTimeout": 180, "appium:disableWindowAnimation": True,
            # Generous timeouts: an emulator on a memory-constrained host installs and starts slowly.
            "appium:adbExecTimeout": 180000,
            "appium:uiautomator2ServerInstallTimeout": 180000,
            "appium:uiautomator2ServerLaunchTimeout": 180000,
            "appium:androidInstallTimeout": 180000,
    })  # fmt: skip
    device.clear_logcat()
    evidence: list[EvidenceDraft] = []
    actions: list[dict[str, Any]] = []
    screens: set[str] = set()
    anr_dialog_seen = False
    try:
        driver = webdriver.Remote(url, options=options)
    except WebDriverException as exc:
        return CaseOutcome.blocked(f"Appium could not start a UiAutomator2 session: {str(exc)[:300]}")
    try:
        driver.implicitly_wait(2)
        # A system "isn't responding" dialog is an ANR. Choose "Wait" (never "Close app") and remember it.
        hierarchy = driver.page_source
        for _ in range(3):
            if ANR_DIALOG_TEXT not in hierarchy:
                break
            anr_dialog_seen = True
            for button in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Wait")'):
                button.click()
            time.sleep(5)
            hierarchy = driver.page_source
        screens.add(str(driver.current_activity))
        evidence.append(EvidenceDraft("tool_output", "UI hierarchy of the first screen", hierarchy[:50_000]))
        ctx.run_cache["android_hierarchies"] = [hierarchy]
        first = device.screenshot(ctx, "first screen")
        if first:
            evidence.append(first)
        labels = []
        for element in driver.find_elements(AppiumBy.XPATH, INTERACTIVE_XPATH):
            if element.get_attribute("package") != info.package:
                continue  # system UI (dialogs, status bar) is not part of the app under test
            label = str(element.get_attribute("text") or element.get_attribute("content-desc") or "").strip()
            if label and label not in labels:
                labels.append(label)
        for label in labels[: ctx.settings.android_explore_actions]:
            action: dict[str, Any] = {"tap": label}
            try:
                target = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                                             f'new UiSelector().textContains("{label[:40]}")')  # fmt: skip
                target.click()
                time.sleep(1)
                action["activity"] = str(driver.current_activity)
                action["package"] = str(driver.current_package)
                screens.add(action["activity"])
                if len(ctx.run_cache["android_hierarchies"]) < 6:
                    ctx.run_cache["android_hierarchies"].append(driver.page_source)
                if len(evidence) < 8 and (shot := device.screenshot(ctx, f"after tapping '{label[:40]}'")):
                    evidence.append(shot)
                if action["package"] != info.package:
                    action["left_app"] = True
                    driver.activate_app(info.package)
                else:
                    driver.back()
                    time.sleep(0.5)
                    if driver.current_package != info.package:
                        driver.activate_app(info.package)
            except WebDriverException as exc:
                action["error"] = str(exc).splitlines()[0][:200]
            actions.append(action)
            if not _alive(device, info.package):
                action["app_died"] = True
                break
    finally:
        try:
            driver.quit()
        except WebDriverException:
            log.warning("appium session did not close cleanly")
    details = {"device": device.profile(), "actions": actions, "screens_visited": sorted(screens),
               "clickable_labels_found": len(labels), "action_limit": ctx.settings.android_explore_actions,
               "anr_dialog_seen": anr_dialog_seen,
               "scope": "taps on the first screen only; no login, no data entry"}  # fmt: skip
    if crashed := _crash_outcome(device, info, ctx, "exploration", "tapping through the first screen", evidence, details):
        return crashed
    if anr_dialog_seen:
        # Observed, but on an emulator this is as likely to be host load as an app defect: reported as potential.
        return CaseOutcome(
            ResultStatus.FAILED, "the system showed an 'app isn't responding' dialog during exploration", details, evidence,
            [_finding("anr-dialog", Severity.MEDIUM, "App stopped responding (ANR dialog) during UI exploration",
                      "Android displayed its 'isn't responding' dialog. On a software-rendered emulator sharing a busy host "
                      "this can be caused by the test environment; confirm on a real device.", info, device, evidence,
                      "Move long work off the main thread; re-test on a physical device to confirm.",
                      classification=Classification.POTENTIAL)],
        )  # fmt: skip
    if not actions:
        return CaseOutcome.blocked("Appium connected but found no labelled interactive element of the app on the first screen")
    return CaseOutcome(ResultStatus.PASSED, f"{len(actions)} UI action(s) on {len(screens)} screen(s) without a crash",
                       details, evidence)  # fmt: skip


def _lifecycle(ctx: EngineContext, device: Device, info: android_static.ApkInfo) -> CaseOutcome:
    device.clear_logcat()
    _launch(device, info, cold=True)
    steps = []
    for name, command in [
        ("send to background (HOME)", "input keyevent 3"),
        ("return to foreground", f"monkey -p {info.package} -c android.intent.category.LAUNCHER 1"),
        ("rotate to landscape", "settings put system accelerometer_rotation 0; settings put system user_rotation 1"),
        ("rotate to portrait", "settings put system user_rotation 0"),
        ("press BACK", "input keyevent 4"),
        ("relaunch", f"monkey -p {info.package} -c android.intent.category.LAUNCHER 1"),
    ]:
        device.shell(command)
        time.sleep(2)
        steps.append({"step": name, "process_alive": _alive(device, info.package)})
    evidence: list[EvidenceDraft] = []
    if shot := device.screenshot(ctx, "after lifecycle sequence"):
        evidence.append(shot)
    details = {"device": device.profile(), "steps": steps}
    if crashed := _crash_outcome(device, info, ctx, "lifecycle", "background / foreground / rotation / back", evidence, details):
        return crashed
    return CaseOutcome(ResultStatus.PASSED, "survived background, foreground, rotation, back and relaunch", details, evidence)


def _offline(ctx: EngineContext, device: Device, info: android_static.ApkInfo) -> CaseOutcome:
    if not device.is_emulator:
        return CaseOutcome(ResultStatus.SKIPPED, "network is not switched off on a physical device")
    device.clear_logcat()
    device.shell("svc wifi disable; svc data disable")
    try:
        time.sleep(2)
        _launch(device, info, cold=True)
        time.sleep(3)
        evidence: list[EvidenceDraft] = []
        if shot := device.screenshot(ctx, "app started without network"):
            evidence.append(shot)
        details = {"device": device.profile(), "process_alive": _alive(device, info.package)}
        crashed = _crash_outcome(device, info, ctx, "offline", "starting the app without a network connection", evidence, details)
    finally:
        device.shell("svc wifi enable; svc data enable")
    if crashed:
        return crashed
    return CaseOutcome(ResultStatus.PASSED, "starts without a network connection and does not crash", details, evidence)


def _accessibility(ctx: EngineContext, device: Device, info: android_static.ApkInfo) -> CaseOutcome:
    hierarchies = list(ctx.run_cache.get("android_hierarchies") or [])
    if not hierarchies:
        _launch(device, info, cold=False)
        time.sleep(2)
        device.shell("uiautomator dump /sdcard/qai-ui.xml")
        dump = device.shell("cat /sdcard/qai-ui.xml")
        if "<hierarchy" in dump:
            hierarchies.append(dump[dump.index("<?xml") if "<?xml" in dump else dump.index("<hierarchy") :])
    if not hierarchies:
        return CaseOutcome.blocked("the UI hierarchy could not be read from the device")
    density = int(re.sub(r"\D", "", device.properties.get("density_dpi", "")) or 160)
    unlabeled: set[str] = set()
    small: set[str] = set()
    inspected = 0
    for xml_text in hierarchies:
        try:
            root = ET.fromstring(xml_text)  # noqa: S314 - produced by uiautomator on the test device
        except ET.ParseError:
            continue
        list_items = {child for parent in root.iter() if parent.get("class") in LIST_CONTAINERS for child in parent}
        for node in root.iter():
            interactive = node.get("clickable") == "true" or node in list_items
            if not interactive or node.get("package") not in (info.package, None):
                continue
            inspected += 1
            identity = node.get("resource-id") or node.get("class") or "node"
            label = (node.get("text") or node.get("content-desc") or "").strip()
            has_labelled_child = any((c.get("text") or c.get("content-desc") or "").strip() for c in node.iter() if c is not node)
            if not label and not has_labelled_child:
                unlabeled.add(f"{identity} {node.get('bounds', '')}")
            if match := _BOUNDS.match(node.get("bounds", "")):
                x1, y1, x2, y2 = map(int, match.groups())
                width_dp, height_dp = (x2 - x1) * 160 / density, (y2 - y1) * 160 / density
                if 0 < width_dp < MIN_TOUCH_TARGET_DP or 0 < height_dp < MIN_TOUCH_TARGET_DP:
                    small.add(f"{identity} '{label[:30]}' {width_dp:.0f}x{height_dp:.0f}dp")
    details = {"device": device.profile(), "screens_inspected": len(hierarchies), "clickable_nodes": inspected,
               "unlabeled": sorted(unlabeled)[:40], "small_touch_targets": sorted(small)[:40],
               "scope": "labels and touch-target size from the UI hierarchy; contrast, focus order and TalkBack are not checked"}  # fmt: skip
    evidence = [EvidenceDraft("log", "accessibility inspection", json.dumps(details, indent=2))]
    findings = []
    if unlabeled:
        findings.append(_finding("a11y-unlabeled", Severity.MEDIUM, f"{len(unlabeled)} clickable element(s) without an accessible label",
                                 "Screen readers announce these as unlabelled buttons:\n" + "\n".join(f"- {u}" for u in sorted(unlabeled)[:25]),
                                 info, device, evidence, "Set contentDescription (or visible text) on every interactive view.",
                                 category=Category.ACCESSIBILITY, count=len(unlabeled)))  # fmt: skip
    if small:
        findings.append(_finding("a11y-touch-target", Severity.LOW, f"{len(small)} touch target(s) smaller than {MIN_TOUCH_TARGET_DP}dp",
                                 "Android accessibility guidance asks for at least 48x48dp:\n" + "\n".join(f"- {s}" for s in sorted(small)[:25]),
                                 info, device, evidence, "Increase the size or touch delegate of small interactive views.",
                                 category=Category.ACCESSIBILITY, count=len(small)))  # fmt: skip
    if inspected == 0:
        return CaseOutcome.blocked("no clickable node belonging to the app was found in the captured hierarchy")
    status = ResultStatus.FAILED if findings else ResultStatus.PASSED
    return CaseOutcome(status, f"{len(unlabeled)} unlabeled, {len(small)} undersized of {inspected} clickable nodes", details,
                       evidence, findings)  # fmt: skip


def _performance(ctx: EngineContext, device: Device, info: android_static.ApkInfo) -> CaseOutcome:
    cold = [t for t in (_launch(device, info, cold=True)[0] for _ in range(3)) if t is not None]
    device.shell("input keyevent 3")
    time.sleep(1)
    warm, _ = _launch(device, info, cold=False)
    time.sleep(2)
    meminfo = device.shell(f"dumpsys meminfo {info.package}")
    gfx = device.shell(f"dumpsys gfxinfo {info.package}")
    pss = _PSS.search(meminfo)
    jank, frames = _JANK.search(gfx), _FRAMES.search(gfx)
    details: dict[str, Any] = {
        "device": device.profile(), "cold_start_ms": cold, "cold_start_median_ms": sorted(cold)[len(cold) // 2] if cold else None,
        "warm_start_ms": warm, "total_pss_kb": int(pss.group(1)) if pss else None,
        "frames_rendered": int(frames.group(1)) if frames else None, "janky_frames_percent": float(jank.group(2)) if jank else None,
        "note": "Measured on the device profile above (a software-rendered emulator is much slower than a phone). "
                "Do not compare these numbers with measurements from another device.",
        "not_measured": ["battery", "CPU profile", "network latency"],
    }  # fmt: skip
    evidence = [EvidenceDraft("log", "start-up, memory and frame statistics", json.dumps(details, indent=2))]
    if not cold:
        return CaseOutcome.blocked("`am start -W` did not report a start time (no launcher activity?)")
    median = details["cold_start_median_ms"]
    if median > COLD_START_SLOW_MS and not device.is_emulator:
        return CaseOutcome(
            ResultStatus.FAILED, f"cold start median {median} ms exceeds {COLD_START_SLOW_MS} ms", details, evidence,
            [_finding("slow-start", Severity.MEDIUM, f"Slow cold start ({median} ms)", "Median of three cold starts.", info, device,
                      evidence, "Profile Application.onCreate and the first activity; defer work off the main thread.",
                      category=Category.PERFORMANCE, classification=Classification.POTENTIAL)],
        )  # fmt: skip
    return CaseOutcome(ResultStatus.PASSED, f"cold start median {median} ms, warm {warm} ms, PSS {details['total_pss_kb']} kB "
                                            f"({'emulator' if device.is_emulator else 'device'})", details, evidence)  # fmt: skip


_CHECKS = {
    "install_launch": _install_launch,
    "ui_exploration": _ui_exploration,
    "lifecycle": _lifecycle,
    "offline": _offline,
    "accessibility": _accessibility,
    "performance": _performance,
}


def run_case(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    check = _CHECKS.get(str(spec.get("check", "")))
    if check is None:
        return CaseOutcome.blocked(f"unknown Android check '{spec.get('check')}'")
    prepared = _prepare(ctx)
    if isinstance(prepared, CaseOutcome):
        return prepared
    device, info = prepared
    try:
        return check(ctx, device, info)
    except subprocess.TimeoutExpired as exc:
        return CaseOutcome.blocked(f"the device did not answer in time: {exc.cmd[-1] if exc.cmd else 'adb'}")


def cleanup(ctx: EngineContext) -> dict[str, Any]:
    stop_appium(ctx)
    return android_device.release(ctx)
