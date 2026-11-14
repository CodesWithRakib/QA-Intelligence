"""APK static analysis with the official Android build tools (aapt2, apksigner).

The APK is untrusted input: it is only parsed, never executed here. Findings are observations from
the manifest and package contents - evidence of configuration, not proof of an exploit.
"""

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import tooling
from app.domain import Category, Classification, ResultStatus, Severity
from app.engines.base import CaseOutcome, EngineContext, EvidenceDraft, FindingDraft, mask_secret

NS = "http://schemas.android.com/apk/res/android:"
_NODE = re.compile(r"^(\s*)E: (\S+)")
_ATTR = re.compile(r"^\s*A: (?:\S+:)?([A-Za-z_]+)(?:\(0x[0-9a-f]+\))?=(.*)$")
_BADGING = re.compile(r"(\w[\w-]*)='([^']*)'")
DANGEROUS_PERMISSIONS = {
    "READ_CONTACTS", "WRITE_CONTACTS", "GET_ACCOUNTS", "READ_CALENDAR", "WRITE_CALENDAR", "CAMERA", "RECORD_AUDIO",
    "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION", "READ_PHONE_STATE", "CALL_PHONE",
    "READ_CALL_LOG", "WRITE_CALL_LOG", "SEND_SMS", "RECEIVE_SMS", "READ_SMS", "READ_EXTERNAL_STORAGE",
    "WRITE_EXTERNAL_STORAGE", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO", "BODY_SENSORS",
    "POST_NOTIFICATIONS", "BLUETOOTH_CONNECT", "BLUETOOTH_SCAN", "NEARBY_WIFI_DEVICES", "SYSTEM_ALERT_WINDOW",
    "REQUEST_INSTALL_PACKAGES", "MANAGE_EXTERNAL_STORAGE", "QUERY_ALL_PACKAGES",
}  # fmt: skip
_CLEARTEXT_URL = re.compile(rb"http://[A-Za-z0-9][A-Za-z0-9.-]{3,60}\.[A-Za-z]{2,10}(?:[/:][^\s\"'<>\x00]{0,60})?")
_IGNORED_HOSTS = (b"schemas.android.com", b"www.w3.org", b"ns.adobe.com", b"xmlpull.org", b"www.apache.org",
                  b"xml.org", b"java.sun.com", b"schemas.xmlsoap.org", b"localhost", b"example.com", b"www.example.com")  # fmt: skip
_SECRET_PATTERNS = {
    "Google API key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    "AWS access key id": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "Private key block": re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    "Stripe live secret key": re.compile(rb"sk_live_[0-9a-zA-Z]{24,}"),
}
MAX_ENTRY_SCAN_BYTES = 40 * 1024 * 1024
MIN_RECOMMENDED_TARGET_SDK = 34


@dataclass
class Component:
    kind: str
    name: str
    exported: bool | None
    permission: str
    actions: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


@dataclass
class ApkInfo:
    package: str = ""
    label: str = ""
    version_name: str = ""
    version_code: str = ""
    min_sdk: int | None = None
    target_sdk: int | None = None
    launch_activity: str = ""
    permissions: list[str] = field(default_factory=list)
    application: dict[str, str] = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)
    native_abis: list[str] = field(default_factory=list)
    native_libraries: list[str] = field(default_factory=list)
    signing: dict[str, Any] = field(default_factory=dict)
    file_count: int = 0
    size_bytes: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "package": self.package, "label": self.label, "version_name": self.version_name,
            "version_code": self.version_code, "min_sdk": self.min_sdk, "target_sdk": self.target_sdk,
            "launch_activity": self.launch_activity, "permissions": self.permissions, "application": self.application,
            "components": {kind: sum(1 for c in self.components if c.kind == kind)
                           for kind in ("activity", "service", "receiver", "provider")},
            "exported_components": [f"{c.kind}:{c.name}" for c in self.components if is_exported(c, self.target_sdk)],
            "native_abis": self.native_abis, "native_libraries": self.native_libraries[:40], "signing": self.signing,
            "file_count": self.file_count, "size_bytes": self.size_bytes,
        }  # fmt: skip


class ApkAnalysisError(Exception):
    """The APK could not be analysed (tool missing, not an APK, tool failure)."""


def find_apk(workspace: Path | None) -> Path | None:
    if workspace is None:
        return None
    return next(iter(sorted(workspace.glob("*.apk"))), None)


def _clean(value: str) -> str:
    value = re.sub(r"\s*\(Raw: .*\)\s*$", "", value.strip())
    return value.strip('"')


def parse_xmltree(text: str) -> list[tuple[int, str, dict[str, str]]]:
    """Flatten `aapt2 dump xmltree` output into (depth, element, attributes)."""
    nodes: list[tuple[int, str, dict[str, str]]] = []
    for line in text.splitlines():
        if match := _NODE.match(line):
            nodes.append((len(match.group(1)), match.group(2), {}))
        elif nodes and (attr := _ATTR.match(line)):
            nodes[-1][2][attr.group(1)] = _clean(attr.group(2))
    return nodes


def _truthy(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in {"true", "0xffffffff", "-1"}


def is_exported(component: Component, target_sdk: int | None) -> bool:
    if component.exported is not None:
        return component.exported
    # Before Android 12 (API 31) a component with an intent filter was exported implicitly.
    return bool(component.actions) and (target_sdk or 0) < 31


def _manifest(info: ApkInfo, tree: list[tuple[int, str, dict[str, str]]]) -> None:
    current: Component | None = None
    component_depth = 0
    for depth, element, attrs in tree:
        if element == "application":
            info.application = {k: v for k, v in attrs.items() if k in {
                "debuggable", "allowBackup", "usesCleartextTraffic", "networkSecurityConfig", "testOnly", "name"}}  # fmt: skip
        elif element in {"activity", "activity-alias", "service", "receiver", "provider"}:
            current = Component(element.replace("-alias", ""), attrs.get("name", ""), _truthy(attrs.get("exported")),
                                attrs.get("permission", "") or attrs.get("readPermission", ""))  # fmt: skip
            component_depth = depth
            info.components.append(current)
        elif current and depth > component_depth and element == "action":
            current.actions.append(attrs.get("name", ""))
        elif current and depth > component_depth and element == "category":
            current.categories.append(attrs.get("name", ""))
        elif current and depth <= component_depth:
            current = None


def _signing(apk: Path) -> dict[str, Any]:
    if not (tooling.apksigner().available and tooling.java().available):
        return {"status": "not_checked", "reason": "apksigner or JDK not installed"}
    result = tooling.run([tooling.java().path, "-jar", tooling.apksigner().path, "verify", "--print-certs", "--verbose", apk],
                         timeout=120)  # fmt: skip
    text = result.stdout + result.stderr
    schemes = {m.group(1): m.group(2) == "true" for m in re.finditer(r"Verified using (v[\d.]+) scheme[^:]*: (true|false)", text)}
    subject = re.search(r"certificate DN: (.+)", text)
    digest = re.search(r"certificate SHA-256 digest: ([0-9a-f]+)", text)
    return {
        "status": "verified" if result.returncode == 0 else "invalid", "schemes": schemes,
        "subject": subject.group(1).strip() if subject else "", "sha256": digest.group(1) if digest else "",
        "debug_certificate": bool(subject and "Android Debug" in subject.group(1)),
        "detail": "" if result.returncode == 0 else text.strip()[-300:],
    }  # fmt: skip


def analyse(apk: Path) -> ApkInfo:
    """Extract package metadata, manifest facts and signing information."""
    if not zipfile.is_zipfile(apk):
        raise ApkAnalysisError("the file is not a valid APK (not a ZIP archive)")
    with zipfile.ZipFile(apk) as bundle:
        names = bundle.namelist()
    if "AndroidManifest.xml" not in names:
        raise ApkAnalysisError("the archive has no AndroidManifest.xml: it is not an Android application package")
    if not tooling.aapt2().available:
        raise ApkAnalysisError(f"aapt2 is not installed (run {tooling.aapt2().install_hint})")
    info = ApkInfo(file_count=len(names), size_bytes=apk.stat().st_size)
    libraries = [n for n in names if n.startswith("lib/") and n.endswith(".so")]
    info.native_abis = sorted({n.split("/")[1] for n in libraries})
    info.native_libraries = sorted({n.rsplit("/", 1)[-1] for n in libraries})

    badging = tooling.run([tooling.aapt2().path, "dump", "badging", apk], timeout=120)
    if badging.returncode != 0:
        raise ApkAnalysisError(f"aapt2 could not read the package: {badging.stderr.strip()[-300:]}")
    for line in badging.stdout.splitlines():
        fields = dict(_BADGING.findall(line))
        if line.startswith("package:"):
            info.package, info.version_name = fields.get("name", ""), fields.get("versionName", "")
            info.version_code = fields.get("versionCode", "")
        elif line.startswith(("sdkVersion:", "minSdkVersion:")):
            info.min_sdk = int(line.split("'")[1]) if line.split("'")[1].isdigit() else None
        elif line.startswith("targetSdkVersion:"):
            info.target_sdk = int(line.split("'")[1]) if line.split("'")[1].isdigit() else None
        elif line.startswith("uses-permission:"):
            info.permissions.append(fields.get("name", ""))
        elif line.startswith("application-label:") and not info.label:
            info.label = line.split("'")[1]
        elif line.startswith("launchable-activity:") and not info.launch_activity:
            info.launch_activity = fields.get("name", "")
    info.permissions = sorted(set(filter(None, info.permissions)))

    tree = tooling.run([tooling.aapt2().path, "dump", "xmltree", apk, "--file", "AndroidManifest.xml"], timeout=120)
    if tree.returncode == 0:
        _manifest(info, parse_xmltree(tree.stdout))
    info.signing = _signing(apk)
    return info


def scan_contents(apk: Path) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Search package contents for cleartext URLs and well-known credential formats (values masked)."""
    urls: set[str] = set()
    secrets: list[tuple[str, str, str]] = []
    with zipfile.ZipFile(apk) as bundle:
        for entry in bundle.infolist():
            if (
                entry.is_dir()
                or entry.file_size > MAX_ENTRY_SCAN_BYTES
                or entry.filename.endswith((".png", ".webp", ".jpg", ".so"))
            ):
                continue
            data = bundle.read(entry)
            for match in _CLEARTEXT_URL.findall(data):
                if not any(host in match for host in _IGNORED_HOSTS):
                    urls.add(match.decode("ascii", "replace")[:120])
            for label, pattern in _SECRET_PATTERNS.items():
                for match in pattern.findall(data)[:3]:
                    secrets.append((label, entry.filename, mask_secret(match.decode("ascii", "replace")[:12]) + "…"))
    return sorted(urls)[:60], secrets[:30]


def _finding(key: str, severity: Severity, title: str, description: str, remediation: str, info: ApkInfo,
             evidence: list[EvidenceDraft], **facts: Any) -> FindingDraft:  # fmt: skip
    return FindingDraft(
        key=f"apk:{info.package}:{key}", category=Category.ANDROID, severity=severity, title=title, description=description,
        source_tool="apk-static", affected_component=f"{info.package} {info.version_name}".strip(), confidence=0.9,
        reproduction_steps=["Run `aapt2 dump badging <apk>` and `aapt2 dump xmltree <apk> --file AndroidManifest.xml`.",
                            "Inspect the attribute named in this finding."],
        remediation=remediation, facts={"kind": "apk_static", "check": key, **facts}, evidence=evidence,
        # The manifest attribute itself is an observed fact; whether it is exploitable is not established.
        classification=Classification.CONFIRMED if facts.get("observed_fact") else Classification.POTENTIAL,
    )  # fmt: skip


def manifest_findings(info: ApkInfo, evidence: list[EvidenceDraft]) -> list[FindingDraft]:
    findings = []
    app = info.application
    if _truthy(app.get("debuggable")):
        findings.append(_finding("debuggable", Severity.HIGH, "Application is debuggable",
                                 "android:debuggable is true: anyone with device access can attach a debugger and read app data.",
                                 "Build release variants with debuggable=false.", info, evidence, observed_fact=True))  # fmt: skip
    if _truthy(app.get("testOnly")):
        findings.append(_finding("test-only", Severity.MEDIUM, "Package is marked testOnly",
                                 "android:testOnly is true: the package can only be installed with `adb install -t`.",
                                 "Do not distribute test-only builds.", info, evidence, observed_fact=True))  # fmt: skip
    if app.get("allowBackup") is None or _truthy(app.get("allowBackup")):
        findings.append(_finding("allow-backup", Severity.LOW, "Application data backup is allowed",
                                 "android:allowBackup is true or unset, so app data can be extracted through adb/cloud backup.",
                                 "Set allowBackup=false or define backup rules that exclude sensitive data.", info, evidence,
                                 observed_fact=True))  # fmt: skip
    cleartext = _truthy(app.get("usesCleartextTraffic"))
    if cleartext or (cleartext is None and (info.target_sdk or 0) < 28 and not app.get("networkSecurityConfig")):
        findings.append(_finding("cleartext", Severity.MEDIUM, "Cleartext (HTTP) traffic is permitted",
                                 "usesCleartextTraffic is true, or the target SDK is below 28 where cleartext is allowed by default.",
                                 "Disable cleartext traffic and use HTTPS; restrict exceptions in a network security config.",
                                 info, evidence, observed_fact=True))  # fmt: skip
    if info.target_sdk is not None and info.target_sdk < MIN_RECOMMENDED_TARGET_SDK:
        findings.append(_finding("target-sdk", Severity.MEDIUM, f"Outdated targetSdkVersion ({info.target_sdk})",
                                 f"The app targets API {info.target_sdk}; it opts out of platform security behaviour added since, "
                                 f"and Google Play requires recent target levels (API {MIN_RECOMMENDED_TARGET_SDK}+ used here as baseline).",
                                 "Raise targetSdkVersion and test against the behaviour changes.", info, evidence,
                                 observed_fact=True, target_sdk=info.target_sdk))  # fmt: skip
    exported = [c for c in info.components if is_exported(c, info.target_sdk) and not c.permission
                and "android.intent.category.LAUNCHER" not in c.categories]  # fmt: skip
    if exported:
        listing = "\n".join(f"- {c.kind} {c.name}" + (f" (actions: {', '.join(c.actions[:3])})" if c.actions else "")
                            for c in exported[:40])  # fmt: skip
        findings.append(_finding("exported-components", Severity.MEDIUM,
                                 f"{len(exported)} exported component(s) without a permission",
                                 "Other apps can start or bind these components. Whether that is harmful depends on what each "
                                 f"component does and must be reviewed.\n{listing}",
                                 "Set android:exported=false where external access is not needed, or protect with a permission.",
                                 info, evidence, count=len(exported), components=[c.name for c in exported[:60]]))  # fmt: skip
    dangerous = [p for p in info.permissions if p.rsplit(".", 1)[-1] in DANGEROUS_PERMISSIONS]
    if dangerous:
        findings.append(_finding("dangerous-permissions", Severity.LOW if len(dangerous) < 6 else Severity.MEDIUM,
                                 f"Requests {len(dangerous)} dangerous permission(s)",
                                 "Each should be justified by a user-visible feature:\n" + "\n".join(f"- {p}" for p in dangerous),
                                 "Remove permissions that are not needed; request the rest at the point of use.", info, evidence,
                                 permissions=dangerous))  # fmt: skip
    signing = info.signing
    if signing.get("status") == "invalid":
        findings.append(_finding("signature-invalid", Severity.HIGH, "APK signature does not verify", signing.get("detail", ""),
                                 "Re-sign the package with a valid key.", info, evidence, observed_fact=True))  # fmt: skip
    elif signing.get("debug_certificate"):
        findings.append(_finding("debug-certificate", Severity.MEDIUM, "Signed with the Android debug certificate",
                                 "The debug key is public knowledge; a build signed with it must not be distributed.",
                                 "Sign release builds with a private release key.", info, evidence, observed_fact=True))  # fmt: skip
    schemes = signing.get("schemes") or {}
    if schemes and not any(ok for name, ok in schemes.items() if name != "v1"):
        findings.append(_finding("v1-only-signature", Severity.LOW, "Only the legacy v1 (JAR) signature scheme is used",
                                 "v1 signatures do not protect the whole file. v2+ is required on modern Android versions.",
                                 "Enable APK Signature Scheme v2/v3 when signing.", info, evidence, observed_fact=True))  # fmt: skip
    return findings


def _load(ctx: EngineContext) -> tuple[Path, ApkInfo] | CaseOutcome:
    apk = find_apk(ctx.workspace)
    if apk is None:
        return CaseOutcome(ResultStatus.NOT_APPLICABLE, "the project contains no APK")
    cached = ctx.run_cache.get("apk_info")
    if isinstance(cached, ApkInfo):
        return apk, cached
    try:
        info = analyse(apk)
    except ApkAnalysisError as exc:
        return CaseOutcome.blocked(str(exc))
    ctx.run_cache["apk_info"] = info
    return apk, info


def run_case(ctx: EngineContext, spec: dict[str, Any]) -> CaseOutcome:
    loaded = _load(ctx)
    if isinstance(loaded, CaseOutcome):
        return loaded
    apk, info = loaded
    check = spec.get("check")
    evidence = [EvidenceDraft("tool_output", "APK metadata (aapt2, apksigner)", json.dumps(info.summary(), indent=2))]
    if check == "metadata":
        ok = bool(info.package and info.target_sdk)
        return CaseOutcome(ResultStatus.PASSED if ok else ResultStatus.FAILED,
                           f"{info.package} {info.version_name} (min SDK {info.min_sdk}, target SDK {info.target_sdk}), "
                           f"{len(info.permissions)} permissions, {len(info.components)} components",
                           info.summary(), evidence)  # fmt: skip
    if check == "manifest":
        findings = manifest_findings(info, evidence)
        return CaseOutcome(ResultStatus.FAILED if findings else ResultStatus.PASSED,
                           f"{len(findings)} manifest/signing observation(s)", {"checks": [f.facts["check"] for f in findings]},
                           evidence, findings)  # fmt: skip
    if check == "contents":
        urls, secrets = scan_contents(apk)
        findings = []
        if urls:
            findings.append(_finding("cleartext-urls", Severity.LOW, f"{len(urls)} hard-coded http:// URL(s) in the package",
                                     "These may be used for cleartext network traffic (some may be unused strings):\n"
                                     + "\n".join(f"- {u}" for u in urls[:30]),
                                     "Use https:// endpoints.", info, evidence, urls=urls[:30]))  # fmt: skip
        for label, file, masked in secrets:
            findings.append(_finding(f"secret:{label}:{file}", Severity.HIGH, f"{label} pattern found in {file}",
                                     f"A value matching the {label} format is embedded in the package (masked: {masked}). "
                                     "Anything shipped in an APK is readable by anyone.",
                                     "Remove the credential from the app, rotate it, and proxy the call through your backend.",
                                     info, evidence, file=file))  # fmt: skip
        return CaseOutcome(ResultStatus.FAILED if findings else ResultStatus.PASSED,
                           f"{len(urls)} cleartext URL(s), {len(secrets)} credential pattern(s)",
                           {"cleartext_urls": urls, "secret_matches": len(secrets),
                            "scope": "strings in dex/resources; no decompilation (JADX/apktool are not used)"},
                           evidence, findings)  # fmt: skip
    return CaseOutcome.blocked(f"unknown APK check '{check}'")
