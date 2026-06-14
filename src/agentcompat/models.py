from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    expected: Any = None
    actual: Any = None


@dataclass(frozen=True, slots=True)
class ToolCall:
    trace_id: str
    tool: str
    arguments: dict[str, Any]
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class TraceResult:
    trace: ToolCall
    status: str
    issues: tuple[ValidationIssue, ...] = ()
    hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    score: float
    passed: int
    broken: int
    excluded: int
    eligible_weight: float
    passing_weight: float
    results: tuple[TraceResult, ...]


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    precision: float
    recall: float
    f1: float
    root_cause_accuracy: float
    true_positives: int
    false_positives: int
    false_negatives: int
