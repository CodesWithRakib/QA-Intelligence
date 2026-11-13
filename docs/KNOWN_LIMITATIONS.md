# Known limitations

Read this before relying on a result. Nothing here is hidden elsewhere in the product: plans list
what they do not cover, and reports repeat it.

## Verification gaps in this delivery

- **The dashboard has not been exercised in a browser against the running API.** It type-checks,
  lints and builds (25 routes), and the API it calls passed the end-to-end run, but no screen was
  clicked through. Expect small runtime issues on first use.
- `scripts/dev.ps1`, `stop.ps1`, `setup.ps1`, `restore.ps1` and `install-prereqs.ps1 -Apply` were not
  executed. During development the processes were started by hand.
- Setup on a clean machine was not performed.
- The Anthropic provider has never made a request. The OpenAI provider does not exist.
- The Node.js sandbox runtime, AI test-case suggestions, the AI architecture summary and the benchmark
  job were implemented but not run end to end. Benchmark graders are unit-tested.
- The AI repair succeeded once with `qwen3.5:4b` on a one-line defect. That is one data point, not a success rate.
- No fault injection was done for sandbox limits (memory, PID, timeout), and worker crash recovery was not exercised.

## Not implemented

Git/GitHub/GitLab import · OWASP ZAP · k6 load testing · visual regression · cross-browser runs ·
authenticated testing (browser or API) · generated user-journey and form tests · live database checks ·
product-intelligence agent · multi-user accounts and roles · report export to PDF/HTML ·
deleting or archiving projects from the UI · automatic data retention · scheduled runs.

## Limits of what is implemented

**Discovery**
- Pattern matching, not a parser: router prefixes applied in another file (`include_router`, `app.use`) are not resolved; dynamically registered routes are missed.
- Deep extraction only for Python and JavaScript/TypeScript. Other languages get manifest-level detection.
- Test coverage is not measured; only the presence of test files is reported.
- Files over 512 KB and anything beyond 6000 source files are not scanned (stated in the inventory when it happens).

**Testing**
- Browser checks start at `/` and follow same-origin links up to the page limit; pages behind login or forms are not reached.
- axe covers only the machine-checkable part of WCAG.
- Performance numbers are single-host lab samples. LCP may be unavailable on trivial pages.
- API contract tests need an OpenAPI file in the project; only local `$ref`s are resolved; a valid mutating call needs an `example` in the spec.
- "Request cap per API check" applies per check, not per run.
- One run at a time; a full run of the demo takes several minutes, mostly scanner start-up and dependency installation.

**Findings**
- Tool severities are passed through, not re-scored for your context.
- The built-in credential check is a heuristic (confidence 0.6) and skips paths containing "test".
- Fingerprints depend on file and line for SAST findings, so moving code can create a new finding.

**Repair**
- Automated strategies: pinned `requirements.txt` upgrades, and AI patches for failing pytest suites. Nothing else.
- A fix is only "verified" if the project has runnable tests. Without them the outcome is "failed: regression could not be verified", even when the targeted check passes.
- A verified fix exists as a branch in `data/repairs/<id>` and a downloadable patch. Applying it to your repository is manual.
- Patches containing authentication/payment-like paths are detected by path name only.

**Security of the platform**
- See the residual-risk table in [SECURITY_MODEL.md](SECURITY_MODEL.md): Docker is not a hard boundary, dependency installation has network access, there are no disk quotas, and the browser engine runs on the host.
- Container images are referenced by tag (`latest` for Trivy and Semgrep), not pinned by digest.

**Operations**
- Windows + PowerShell 7 only. No Linux/macOS start scripts.
- 16 GB RAM is the practical minimum; expect memory pressure with Docker, Ollama and an IDE open.
- `data/` (workspaces, evidence, repair copies) is never cleaned automatically.
- Not production-ready and not designed for network exposure.
