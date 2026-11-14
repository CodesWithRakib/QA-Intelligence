# Test readiness

Before a check runs, the platform works out whether it *can* run and, if not, exactly why. A check
that cannot run is recorded as blocked with that reason. It is never executed half-way, never
skipped silently, and never reported as passed.

## States

| State | Meaning | Typical reason |
|---|---|---|
| `ready` | Can run now | - |
| `partially_ready` | Can run with reduced scope | (reserved; not produced by the current adapters) |
| `requires_configuration` | The owner must change a setting | Target not on the authorised list; no environment profile |
| `requires_credentials` | Needs test accounts | (manual cases explain this; no automated check uses credentials yet) |
| `requires_source_access` | Needs source files | URL-only project; original git repository no longer at its path |
| `requires_device` | Needs an Android device | No emulator profile; not enough free RAM to start the emulator; physical device not authorised |
| `requires_test_environment` | Needs a running application | No target URL; target not reachable; managed environment failed to start |
| `blocked` | A tool is missing or not running | Docker stopped; image not pulled; aapt2 / Appium not installed |
| `not_applicable` | Does not apply | Manual case; no APK in the project |

## Where readiness is computed

| When | How | Shown |
|---|---|---|
| Any time, for a plan | `GET /api/plans/{id}/readiness`: every enabled case is asked, nothing is executed | Plan screen: summary and a reason per case; "Re-check readiness" button |
| During a run, immediately before each case | `runs.service.case_readiness()` | Stored in `run.readiness`; blocked cases get the reason as their result message |
| Per adapter, without a project | `GET /api/system/capabilities` | System Health: adapters, tools, images, devices, free RAM / disk / VRAM |

Readiness is re-evaluated per case because it changes during a run: the managed environment appears
after its start case, a device session is reused once open, and a target can go away.

## What each requirement checks

| Requirement | Check |
|---|---|
| Source files | The version has a workspace |
| Running target | URL present → on the authorised list (and not a metadata address) → TCP connection to host:port succeeds (cached for the run) |
| Managed environment | A profile is stored; after a failed start every dependent case reports "the managed test environment did not start: <first line of the error>" |
| Docker tool | Daemon reachable and the specific image present |
| Local tool | File exists under `.tools` (aapt2, JDK, adb, emulator, Appium, Lighthouse) |
| Android device | An emulator is connected, or a physical device is connected **and** authorised in Execution settings, or the emulator can be started: profile exists and free RAM ≥ `android_min_free_ram_gb` |
| Git history | The original local repository still has a `.git` folder |

## From readiness to results

| Readiness | Result state | Message |
|---|---|---|
| `ready`, `partially_ready` | whatever the engine returns | engine message |
| `not_applicable` | `not_applicable` | reason |
| anything else | `blocked` | `<state>: <reason>; <reason>` and the full report in `details.readiness` |
| manual case | `not_tested` | why there is no automation |
| case disabled by the owner | `skipped` | "case disabled in the plan" |
| run cancelled before the case | `skipped` | "run cancelled by the owner before this case started" |

An engine can still end `blocked` after a `ready` verdict (a tool crashes, a download fails, a page
returns 500 before a layout check). The reason is then the engine's own message.

## Verified behaviour

| Behaviour | Evidence |
|---|---|
| Blocked case is not executed and carries the reason | `test_blocked_readiness_is_reported_with_the_exact_reason` |
| Missing Android tools are named | `test_android_readiness_names_the_missing_pieces` |
| Emulator refused when RAM is short | `test_emulator_is_not_started_without_enough_free_memory`; observed live: "the emulator needs about 3.0 GB of free RAM and only 2.1 GB is available" |
| Physical device ignored unless enabled | `test_physical_device_is_ignored_unless_the_owner_enabled_it` |
| Dynamic cases wait for the managed environment | `test_managed_plan_requires_a_profile_and_plans_dynamic_checks_without_a_url` |
| Failed environment start explains every dependent block | `test_failed_start_is_a_potential_finding_and_blocks_dynamic_checks_with_the_reason` |
| Cancelled run: remaining cases skipped, none passed | `test_run_can_be_cancelled_and_remaining_cases_are_skipped_not_passed` |

## Limits

- Readiness does not predict memory pressure inside the Docker VM: a Node build can still be killed
  (exit 137), which is then reported as blocked with the memory limit.
- Credentials are not modelled yet, so `requires_credentials` only appears as the explanation on manual cases.
- Target reachability is a TCP connect, not an application health check.
