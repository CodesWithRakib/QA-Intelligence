# Phase 2 installation plan and outcome

Everything new is installed by one idempotent script into a project-local directory:

```powershell
scripts\install-phase2-tools.ps1                 # everything
scripts\install-phase2-tools.ps1 -Only web       # browsers, Lighthouse, gitleaks, k6, ZAP
scripts\install-phase2-tools.ps1 -Only android   # JDK, Android SDK, emulator, Appium, sample APK
scripts\install-phase2-tools.ps1 -SkipZap        # skip the 3.75 GB ZAP image
```

- Location: `<repo>\.tools` (override with `QAI_TOOLS_DIR`), git-ignored. Remove the folder to uninstall.
- No administrator rights, no restart, no PATH or system environment changes, nothing installed system-wide.
- Refuses to start below 40 GB free and stops before a component below 20 GB free. Nothing is deleted to make room.
- Installing the Android SDK accepts the Android SDK licences on your behalf.

## Hardware audit (2026-11-14)

| Item | Value |
|---|---|
| CPU | AMD Ryzen 5 3400G, 4 cores / 8 threads, virtualization enabled in firmware |
| Hypervisor | Present (WSL 2); `emulator -accel-check`: "WHPX(10.0.26200) is installed and usable" |
| RAM | 15.9 GB total; 2.1-3.5 GB free during the session |
| GPU | NVIDIA GeForce GTX 1660 SUPER, 6144 MiB, driver 616.92 (Windows driver 32.0.16.1692) |
| CUDA | Not installed as a toolkit and not needed: Ollama ships its own runtime; no other Phase 2 tool uses the GPU |
| Disk `C:` | 237.5 GB; 57.8 GB free before, 39.3 GB free after installation |

## Dependencies

| Dependency | Purpose | Before | Required | Download | Installed | Location | Verification | Restart | Outcome |
|---|---|---|---|---|---|---|---|---|---|
| Playwright Firefox 1572 | Cross-browser checks | missing | yes | ~110 MB | 353 MB | `%LOCALAPPDATA%\ms-playwright` | `playwright install --list` | no | installed |
| Playwright WebKit 2388 (27.2) | Cross-browser checks | missing | yes | 62 MB | 175 MB | same | same | no | installed |
| Lighthouse 12.8.2 | Performance audit | missing | yes | ~35 MB | 145 MB | `.tools\lighthouse` | `node .tools\lighthouse\node_modules\lighthouse\cli\index.js --version` | no | installed |
| gitleaks 8.30.1 (image) | Secret scanning, incl. git history | missing | yes | 28 MB | 77 MB | Docker | `docker run --rm zricethezav/gitleaks version` | no | installed |
| k6 2.4.0 (image) | Bounded load tests | missing | yes | 40 MB | 111 MB | Docker | `docker run --rm grafana/k6 version` | no | installed |
| OWASP ZAP stable (image) | Passive baseline scan | missing | optional | ~1.3 GB | 3.75 GB | Docker | `docker image inspect ghcr.io/zaproxy/zaproxy:stable` | no | installed, **not yet executed** |
| Temurin JDK 17.0.21 | apksigner, SDK tools, Appium | missing | yes (Android) | 190 MB | 303 MB | `.tools\jdk-17` | `.tools\jdk-17\bin\java -version` | no | installed |
| Android command-line tools | sdkmanager, avdmanager | missing | yes (Android) | 150 MB | 157 MB | `.tools\android-sdk\cmdline-tools` | `sdkmanager --list_installed` | no | installed |
| platform-tools (adb 1.0.41) | Device communication | missing | yes (Android) | 7 MB | 17 MB | `.tools\android-sdk` | `adb version` | no | installed |
| build-tools 34.0.0 | aapt2, apksigner | missing | yes (Android) | 60 MB | 137 MB | same | `aapt2 version` | no | installed |
| Android emulator 37.2.13 | Virtual device | missing | yes (Android) | 380 MB | 1070 MB | same | `emulator -accel-check` | no | installed |
| platform android-34 | SDK platform | missing | yes (Android) | 60 MB | 95 MB | same | - | no | installed |
| System image `android-34;google_apis;x86_64` (one only) | Emulator OS | missing | yes (Android) | 1.5 GB | 4262 MB | same | `avdmanager list avd` | no | installed |
| AVD `qai_api34` (Pixel 5 profile) | The single emulator | missing | yes (Android) | - | 3574 MB after first boot | `.tools\avd` | `emulator -list-avds` | no | created |
| Appium 3.9.0 + UiAutomator2 driver 8.7.3 | Android UI automation | missing | yes (Android) | ~60 MB | 182 MB | `.tools\appium` | `appium --version`, `appium driver list --installed` | no | installed |
| Appium-Python-Client, Pillow 13 | Appium client, image comparison | missing | yes | ~15 MB | ~40 MB | `backend\.venv` | `pip show` | no | installed |
| ApiDemos-debug.apk (Appium sample) | Harmless app to validate the Android engine | missing | yes | 5 MB | 5 MB | `.tools\samples` | file exists | no | downloaded |
| Android Studio | IDE | missing | **no** | ~1.2 GB | ~3 GB | - | - | no | **not installed**: the command-line SDK provides everything the automated engine uses |
| JADX, Apktool | APK decompilation | missing | optional | ~50 MB | ~120 MB | - | - | no | **not installed**: manifest, signing and string analysis use aapt2/apksigner; decompilation is not implemented |
| MobSF | Mobile security framework | missing | optional | ~2 GB | ~4 GB | - | - | no | **not installed**: too heavy for this machine's disk budget |
| Appium Inspector | Manual UI inspection | missing | optional | - | - | - | - | no | not installed (GUI tool, not needed for automation) |
| GitHub CLI | Remote repository access | missing | no | - | - | - | - | no | not installed: all three candidate repositories exist locally |
| Redis | Job queue | n/a | no | - | - | - | - | no | not needed (PostgreSQL queue, ADR-004) |
| CUDA toolkit, extra AI models | - | n/a | no | - | - | - | - | no | not installed; no model was downloaded |

Already present and reused: Git, Python, Node.js, pnpm, Docker Desktop, WSL 2, PostgreSQL (container), Ollama,
Playwright Chromium, Trivy, Semgrep, HTTPX, pytest, axe-core.

## Storage used by Phase 2

| Item | Size |
|---|---|
| `.tools` (SDK 5.7 GB, AVD 3.6 GB, JDK, Appium, Lighthouse, downloads) | 10.3 GB |
| Playwright Firefox + WebKit | 0.5 GB |
| Docker images (ZAP 3.75 GB, k6, gitleaks) | 3.9 GB |
| **Total** | **about 14.7 GB** (57.8 GB free → 39.3 GB free, including test data) |

`.tools\downloads` (319 MB of installers) can be deleted. The ZAP image is the largest optional item.

## Installation problems encountered

| Problem | Resolution |
|---|---|
| `appium@2` is rejected by the current UiAutomator2 driver ("requires ^4.0.0-beta.8") | Installed the current Appium (3.9.0); Appium selected the latest compatible driver (8.7.3) itself |
| `gitleaks detect --report-path /dev/stdout` produced no report on 8.30 | Switched to the current `gitleaks dir` / `gitleaks git` commands with a report file |
| First emulator session: Appium helper APK installation exceeded the default 20 s adb timeout | Raised the Appium adb / install / launch timeouts to 180 s |

## Constraints after installation

- Free disk is below 40 GB: per the installation policy no further heavyweight component is installed.
- The emulator and a 2.5 GB Node build cannot both run comfortably beside the owner's IDE and browsers. The
  worker runs one job at a time, and the emulator is refused when less than the configured amount of RAM is free.
