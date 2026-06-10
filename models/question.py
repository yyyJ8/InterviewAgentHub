from __future__ import annotations

from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field


class Difficulty(str, Enum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    DEEP = "deep"


class Question(BaseModel):
    skill: str  # 对应技能名称
    difficulty: Difficulty
    content: str  # 题目内容
    context: Optional[str] = None  # 出题背景（基于哪个项目/缺口）
    expected_answer_points: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    question_id: str = ""
    content: str


class JudgeResult(BaseModel):
    score: int = Field(ge=0, le=100)
    comment: str
    strength_points: list[str] = Field(default_factory=list)
    weakness_points: list[str] = Field(default_factory=list)
    next_action: str = Field(description="deepen / clarify / switch / end")
