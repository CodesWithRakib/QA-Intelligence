"""Result types produced by discovery."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComponentInfo:
    name: str
    kind: str
    path: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteInfo:
    path: str
    framework: str
    source_file: str


@dataclass
class EndpointInfo:
    method: str
    path: str
    origin: str  # "openapi" | "code"
    source_file: str
    operation_id: str | None = None
    spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    inventory: dict[str, Any]
    unsupported: list[str]
    components: list[ComponentInfo]
    routes: list[RouteInfo]
    endpoints: list[EndpointInfo]
