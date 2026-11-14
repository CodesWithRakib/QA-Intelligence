"""Android adapters: detection, APK static analysis, dynamic testing, accessibility and performance."""

from typing import Any

from app import tooling
from app.adapters.base import Adapter, PlanContext, PlannedCase, PlanResult, ReadinessReport
from app.domain import Category, Engine, Readiness, SourceKind
from app.engines import android_device, android_dynamic, android_static
from app.engines.base import CaseOutcome, EngineContext
from app.services.settings import ExecutionSettings


def _android(plan: PlanContext) -> dict[str, Any]:
    return dict(plan.inventory.get("android") or {})


def _has_apk(plan: PlanContext) -> bool:
    return plan.source_kind == SourceKind.APK.value or bool(_android(plan).get("apk_files"))


def _case(
    adapter: Adapter, prefix: str, title: str, category: Category, engine: Engine, check: str, priority: int = 2
) -> PlannedCase:
    return PlannedCase(prefix, title, category, engine, {"adapter": adapter.name, "check": check}, priority)


class AndroidDetectionAdapter(Adapter):
    """Reports whether the project contains an Android application. Plans nothing."""

    name, title, agent = "android_detection", "Android detection", "project_discovery"

    def capabilities(self, plan: PlanContext) -> list[str]:
        android = _android(plan)
        found = []
        if _has_apk(plan):
            found.append("APK artifact")
        for project in android.get("source_projects", []):
            found.append(f"{project.get('kind', 'android')} source project at {project.get('path') or '.'}")
        return found

    def plan(self, plan: PlanContext) -> PlanResult:
        android = _android(plan)
        notes = [f"Android source project detected ({p.get('kind')}, applicationId {p.get('application_id') or 'unknown'}): "
                 "building Android sources in the sandbox is not implemented; upload a built APK to test it."
                 for p in android.get("source_projects", [])]  # fmt: skip
        notes += [f"Trusted Web Activity configuration found at {path}: it wraps the web app for Android. No APK is built "
                  "from it here; the web engine covers the wrapped site." for path in android.get("twa_manifests", [])]  # fmt: skip
        return PlanResult(notes=notes)


class ApkStaticAdapter(Adapter):
    name, title, agent = "apk_static", "APK static analysis", "security_qa"
    needs_workspace = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        return ["package metadata", "manifest and signing review", "content scan"] if _has_apk(plan) else []

    def environment(self, settings: ExecutionSettings) -> ReadinessReport:
        report = ReadinessReport(requirements=["Android build-tools (aapt2, apksigner)", "JDK 17"])
        for tool in (tooling.aapt2(), tooling.java()):
            if not tool.available:
                report.block(Readiness.BLOCKED, f"{tool.name} is not installed ({tool.install_hint})")
        return report

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [
                _case(self, "APK-META", "APK package metadata can be extracted (package, version, SDK levels, components)",
                      Category.ANDROID, Engine.ANDROID_STATIC, "metadata", 1),
                _case(self, "APK-SEC", "Manifest and signing review (debuggable, backup, cleartext, exported components, "
                      "permissions, certificate)", Category.SECURITY, Engine.ANDROID_STATIC, "manifest", 1),
                _case(self, "APK-SEC", "Package contents: cleartext URLs and embedded credential formats", Category.SECURITY,
                      Engine.ANDROID_STATIC, "contents"),
            ],
            ["APK decompilation (JADX / apktool) and MobSF are not used: analysis covers the manifest, signature and string "
             "contents only. Weak cryptography and third-party library vulnerabilities are not assessed."],
        )  # fmt: skip

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return android_static.run_case(ctx, spec)


class _DeviceAdapter(Adapter):
    """Shared readiness for anything that needs a device."""

    needs_workspace = True

    def capabilities(self, plan: PlanContext) -> list[str]:
        return ["installable APK"] if _has_apk(plan) else []

    def environment(self, settings: ExecutionSettings) -> ReadinessReport:
        report = ReadinessReport(requirements=["one Android emulator or authorised device", "about 3 GB of free RAM"])
        if not tooling.aapt2().available:
            report.block(Readiness.BLOCKED, f"aapt2 is not installed ({tooling.aapt2().install_hint})")
        ready = [serial for serial, state in android_device.connected() if state == "device"]
        usable = [s for s in ready if s.startswith("emulator-") or settings.android_allow_physical_device]
        if usable:
            return report
        if ready:
            report.reasons.append(
                "a physical device is connected but not authorised in Execution settings; the emulator will be used"
            )
        for problem in android_device.environment_problems():
            report.block(Readiness.REQUIRES_DEVICE, problem)
        if report.runnable and android_device.free_ram_gb() < android_device.MIN_FREE_RAM_GB:
            report.block(Readiness.REQUIRES_DEVICE,
                         f"no device is connected and the emulator cannot be started: {android_device.free_ram_gb():.1f} GB RAM free, "
                         f"about {android_device.MIN_FREE_RAM_GB:.0f} GB needed")  # fmt: skip
        return report

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        # Once the run holds a device, later cases are ready without re-checking free memory.
        if isinstance(ctx.run_cache.get(android_device.CACHE_KEY), android_device.Device):
            return ReadinessReport(requirements=["device session already open"])
        return super().readiness(ctx, spec)

    def execute(self, ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
        return android_dynamic.run_case(ctx, spec)


class AndroidDynamicAdapter(_DeviceAdapter):
    name, title, agent = "android_dynamic", "Android dynamic testing", "functional_qa"

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [
                _case(self, "AND-RUN", "APK installs and launches without crashing", Category.ANDROID, Engine.ANDROID_DYNAMIC,
                      "install_launch", 1),
                _case(self, "AND-UI", "UI exploration with Appium/UiAutomator2: tap first-screen elements, no crash",
                      Category.FUNCTIONAL, Engine.ANDROID_DYNAMIC, "ui_exploration", 1),
                _case(self, "AND-LIFE", "Lifecycle: background, foreground, rotation, back, relaunch", Category.ANDROID,
                      Engine.ANDROID_DYNAMIC, "lifecycle"),
                _case(self, "AND-NET", "Starts without a network connection", Category.ANDROID, Engine.ANDROID_DYNAMIC, "offline"),
            ],
            ["Android journeys that need accounts or data entry (login, forms, search, deep links, notifications, camera) "
             "are not automated: the exploration only taps what is visible on the first screen."],
        )  # fmt: skip

    def readiness(self, ctx: EngineContext, spec: dict[str, Any]) -> ReadinessReport:
        report = super().readiness(ctx, spec)
        if spec.get("check") == "ui_exploration" and report.runnable and not tooling.appium().available:
            report.block(Readiness.BLOCKED, f"Appium is not installed ({tooling.appium().install_hint})")
        return report

    def cleanup(self, ctx: EngineContext) -> dict[str, Any]:
        return android_dynamic.cleanup(ctx)


class AndroidAccessibilityAdapter(_DeviceAdapter):
    name, title, agent = "android_accessibility", "Android accessibility", "accessibility_qa"

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [_case(self, "AND-A11Y", "Accessible labels and touch-target size from the UI hierarchy", Category.ACCESSIBILITY,
                   Engine.ANDROID_DYNAMIC, "accessibility")],
            ["Android contrast, focus order, text scaling and TalkBack behaviour are not checked."],
        )  # fmt: skip


class AndroidPerformanceAdapter(_DeviceAdapter):
    name, title, agent = "android_performance", "Android performance", "performance_qa"

    def plan(self, plan: PlanContext) -> PlanResult:
        return PlanResult(
            [_case(self, "AND-PERF", "Cold/warm start time, memory and frame statistics on the test device", Category.PERFORMANCE,
                   Engine.ANDROID_DYNAMIC, "performance", 3)],
        )  # fmt: skip
