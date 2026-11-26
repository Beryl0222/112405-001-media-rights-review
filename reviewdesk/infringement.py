"""Explicit non-assessment for infringement.

Similarity scores and public-heat signals are intentionally not decision
inputs.  The workbench can identify missing/confirmed rights, but does not make
legal infringement findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InfringementAssessment:
    conclusion: str = "not_determined"
    automatic_infringement_conclusion: bool = False
    considered_basis: list[str] = field(default_factory=list)
    ignored_signals: list[str] = field(default_factory=list)
    message: str = (
        "系统只复核权利关系与许可范围；相似度、舆情热度不能推出侵权结论，"
        "是否侵权需由有权人员结合完整证据判断。"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "automatic_infringement_conclusion": False,
            "considered_basis": self.considered_basis,
            "ignored_signals": self.ignored_signals,
            "message": self.message,
        }


def assess_infringement(
    *,
    similarity: float | None = None,
    similarity_score: float | None = None,
    public_sentiment: Any = None,
    heat: Any = None,
    trending: Any = None,
    **_: Any,
) -> InfringementAssessment:
    ignored: list[str] = []
    if similarity is not None or similarity_score is not None:
        ignored.append("similarity")
    if public_sentiment is not None:
        ignored.append("public_sentiment")
    if heat is not None:
        ignored.append("heat")
    if trending is not None:
        ignored.append("trending")
    return InfringementAssessment(ignored_signals=ignored)
