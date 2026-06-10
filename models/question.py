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
    soft_skills_assessment: Optional[str] = None  # 软技能评估备注


class RoundRecord(BaseModel):
    """单轮面试记录（用于 LangGraph state 积累）"""
    round_number: int
    skill: str
    question: Question
    answer: str
    judge: JudgeResult


class InterviewReport(BaseModel):
    """面试报告（最终输出）"""
    total_score: float = Field(ge=0, le=100)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    skill_scores: list[dict] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    overall_assessment: str = ""
    hiring_recommendation: str = ""  # strong_yes / yes / hesitate / no
