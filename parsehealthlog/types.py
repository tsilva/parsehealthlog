"""Shared type definitions for parsehealthlog."""

from __future__ import annotations

from typing import Literal, TypedDict

DependencyMap = dict[str, str]
ProgressStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
    "completed_with_errors",
    "failed",
]
ScalarLike = str | int | float | bool | None


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class PersistedState(TypedDict, total=False):
    status: ProgressStatus
    started_at: str | None
    completed_at: str | None
    sections_total: int
    reports_generated: list[str]


class ProgressSnapshot(TypedDict, total=False):
    status: ProgressStatus
    started_at: str | None
    completed_at: str | None
    sections_total: int
    sections_processed: int
    sections_failed: list[str]
    extractions_failed: list[str]
    reports_generated: list[str]


class ExamFrontMatter(TypedDict, total=False):
    title: str
    exam_name_raw: str
    exam_date: str
    doctor: str
    facility: str
    department: str
    category: str


class LabGroupPayload(TypedDict):
    tests: list[str]
    subgroups: dict[str, list[str]]


class ExtractionStats(TypedDict):
    converted: int
    deleted: int
    failed: int
    total: int
