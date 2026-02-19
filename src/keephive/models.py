"""Pydantic models for all structured data in keephive.

This is where the root cause of the bash version's bugs gets fixed:
native JSON validation with exact field names and types.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


# ---- Verify ----

class Verdict(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    UNCERTAIN = "UNCERTAIN"


class FactVerdict(BaseModel):
    index: int
    verdict: Verdict
    reason: str
    correction: str | None = None


class VerifyResponse(BaseModel):
    verdicts: list[FactVerdict]


# ---- PreCompact ----

class InsightCategory(str, Enum):
    DECISION = "DECISION"
    FACT = "FACT"
    CORRECTION = "CORRECTION"
    TODO = "TODO"
    INSIGHT = "INSIGHT"


class Insight(BaseModel):
    category: InsightCategory
    description: str


class MemoryAction(str, Enum):
    ADD = "add"
    CORRECT = "correct"


class MemoryUpdate(BaseModel):
    action: MemoryAction
    text: str
    replaces: str | None = None  # for corrections: old text to find


class PreCompactResponse(BaseModel):
    insights: list[Insight]
    memory_updates: list[MemoryUpdate] = []


# ---- Reflect Analyze ----

class Pattern(BaseModel):
    topic: str
    days: int
    has_guide: bool


class Addition(BaseModel):
    fact: str
    source: str


class Contradiction(BaseModel):
    memory: str
    log: str
    date: str


class ReflectAnalyzeResponse(BaseModel):
    patterns: list[Pattern]
    additions: list[Addition]
    contradictions: list[Contradiction]
    actions: list[str] = []


# ---- Audit ----

class VaultPerspective(BaseModel):
    analysis: str
    issues: list[str]


class CleanerPerspective(BaseModel):
    analysis: str
    issues: list[str]


class StrategistPerspective(BaseModel):
    analysis: str
    issues: list[str]


class AuditPlay(BaseModel):
    issue: str       # One-line problem description
    command: str     # Exact hive command


class AuditSynthesis(BaseModel):
    plays: list[AuditPlay]   # 3-5 ranked actions, most impactful first
    connection: str
    tension: str
    wild_card: str


# ---- Standup ----

class StandupResponse(BaseModel):
    yesterday: list[str]
    today: list[str]
    blockers: list[str]


# ---- Doctor ----

class DuplicateGroup(BaseModel):
    entries: list[str]
    suggestion: str


class DoctorDuplicatesResponse(BaseModel):
    duplicate_groups: list[DuplicateGroup]
    orphaned_todos: list[str]


# ---- Reflect Draft ----

class GuideDraftResponse(BaseModel):
    title: str
    content: str


# ---- Recall Expand ----

class RecallExpandResponse(BaseModel):
    terms: list[str]


# ---- Log Summarize ----

class DailySummaryResponse(BaseModel):
    bullets: list[str]  # 3-5 summary points


