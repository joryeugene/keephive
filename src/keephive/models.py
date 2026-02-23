"""Pydantic models for all structured data in keephive.

This is where the root cause of the bash version's bugs gets fixed:
native JSON validation with exact field names and types.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

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
    rule_suggestions: list[str] = []  # short imperative rules, max 2
    completed_todos: list[str] = []  # TODOs resolved in this conversation


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
    issues: list[str] = []


class CleanerPerspective(BaseModel):
    analysis: str
    issues: list[str] = []


class StrategistPerspective(BaseModel):
    analysis: str
    issues: list[str] = []


class AuditPlay(BaseModel):
    issue: str  # One-line problem description
    command: str  # Exact hive command


class AuditSynthesis(BaseModel):
    plays: list[AuditPlay]  # 3-5 ranked actions, most impactful first
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


# ---- Note Extract ----


class NoteExtractResponse(BaseModel):
    items: list[str]  # extracted action items from freeform text


# ---- KingBee ----


class MorningBriefingResponse(BaseModel):
    content: str = Field(description="The morning briefing text, under 150 words")


class StaleCheckResponse(BaseModel):
    content: str = Field(description="Stale fact warnings, one per line, or 'Nothing stale'")


class SoulUpdateResponse(BaseModel):
    content: str = Field(description="Complete updated SOUL.md content")


class ProposedSkill(BaseModel):
    name: str = Field(description="Slug name for the skill guide, e.g. 'fast-git-summary'")
    rationale: str = Field(description="Why this skill would help, with evidence from logs")
    content: str = Field(description="Complete skill markdown content")


class ProposedTask(BaseModel):
    name: str = Field(description="Task identifier for daemon.json, e.g. 'weekly-git-activity'")
    rationale: str = Field(description="Why this task would help, with evidence from logs")
    config: dict = Field(description="daemon.json task config dict (enabled, time, day)")


class ProposedRule(BaseModel):
    rule: str = Field(description="Behavioral rule text for rules.md")
    rationale: str = Field(description="Evidence from logs (N sessions showing this pattern)")


class ProposedEdit(BaseModel):
    """Refine, prune, or merge existing skills/tasks/rules based on observed usage."""

    action: str = Field(description="'edit' | 'prune' | 'merge'")
    target_type: str = Field(description="'skill' | 'task' | 'rule'")
    target_name: str = Field(description="Name/slug of the existing item to modify")
    rationale: str = Field(description="Evidence from logs: why this edit improves things")
    changes: str = Field(
        description=(
            "For edit: full updated content. "
            "For prune: reason to remove. "
            "For merge: merged full content combining both."
        )
    )
    merge_with: str | None = Field(
        default=None,
        description="For merge: name of second item to combine with target_name",
    )


class ImprovementResponse(BaseModel):
    proposed_skills: list[ProposedSkill] = Field(default_factory=list)
    proposed_tasks: list[ProposedTask] = Field(default_factory=list)
    proposed_rules: list[ProposedRule] = Field(default_factory=list)
    proposed_edits: list[ProposedEdit] = Field(default_factory=list)
    summary: str = Field(description="Brief summary of what patterns triggered these proposals")
