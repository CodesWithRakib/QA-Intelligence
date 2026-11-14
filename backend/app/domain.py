"""Domain vocabularies shared by models, schemas and engines."""

from enum import StrEnum


class SourceKind(StrEnum):
    LOCAL_FOLDER = "local_folder"
    ZIP = "zip"
    URL = "url"
    OPENAPI = "openapi"
    GIT = "git"
    APK = "apk"


class ResultStatus(StrEnum):
    """The only valid outcome states. Unknown is never reported as passed."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    NOT_TESTED = "not_tested"
    NOT_APPLICABLE = "not_applicable"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"  # the run itself crashed; individual results keep their own states
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Category(StrEnum):
    FUNCTIONAL = "functional"
    BROWSER = "browser"
    API = "api"
    DATABASE = "database"
    SECURITY = "security"
    PERFORMANCE = "performance"
    ACCESSIBILITY = "accessibility"
    PRODUCT = "product"
    VISUAL = "visual"
    BUILD = "build"
    COMPATIBILITY = "compatibility"
    ANDROID = "android"


class Engine(StrEnum):
    BROWSER = "browser"
    API = "api"
    DEPENDENCY_SCAN = "dependency_scan"
    STATIC_ANALYSIS = "static_analysis"
    PROJECT_TESTS = "project_tests"
    DATABASE_STATIC = "database_static"
    LIGHTHOUSE = "lighthouse"
    LOAD_TEST = "load_test"
    DAST = "dast"
    ANDROID_STATIC = "android_static"
    ANDROID_DYNAMIC = "android_dynamic"
    MANUAL = "manual"  # documented case with no automated engine -> always not_tested


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(StrEnum):
    OPEN = "open"
    FIX_PROPOSED = "fix_proposed"
    FIXED = "fixed"
    WONT_FIX = "wont_fix"
    FALSE_POSITIVE = "false_positive"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED_FIXED = "verified_fixed"
    VERIFICATION_FAILED = "verification_failed"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    VERIFIED = "verified"
    FAILED = "failed"


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    REVIEW_REJECTED = "review_rejected"


class Readiness(StrEnum):
    """Whether a check can actually be executed right now. Computed, never assumed."""

    READY = "ready"
    PARTIALLY_READY = "partially_ready"
    REQUIRES_CONFIGURATION = "requires_configuration"
    REQUIRES_CREDENTIALS = "requires_credentials"
    REQUIRES_SOURCE = "requires_source_access"
    REQUIRES_DEVICE = "requires_device"
    REQUIRES_ENVIRONMENT = "requires_test_environment"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class Classification(StrEnum):
    """How much a finding has been established. Scanner output is not a confirmed defect by default."""

    CONFIRMED = "confirmed_defect"  # reproduced deterministically by the platform (observed behaviour)
    POTENTIAL = "potential_defect"  # reported by a tool/heuristic, not independently reproduced
    AI_HYPOTHESIS = "ai_hypothesis"
    ENVIRONMENT = "environment_failure"
    FALSE_POSITIVE = "false_positive"


class Origin(StrEnum):
    DETERMINISTIC = "deterministic"
    AI_SUGGESTED = "ai_suggested"


def values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]
