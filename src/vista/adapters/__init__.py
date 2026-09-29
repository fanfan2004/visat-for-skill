"""Adapters for keeping the local pilot boundary separate from SkillsBench."""

from .skillsbench import AuditResult, SkillsBenchSourceAdapter

__all__ = ["AuditResult", "SkillsBenchSourceAdapter"]
