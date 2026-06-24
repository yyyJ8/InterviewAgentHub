from __future__ import annotations

from typing import Optional
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from models.jd import JD
from models.resume import Resume
from models.question import Question, Answer, JudgeResult


class RoundState(BaseModel):
    round_number: int
    skill: str
    question: Question
    answer: Optional[Answer] = None
    judge: Optional[JudgeResult] = None
    created_at: datetime = Field(default_factory=datetime.now)


class InterviewStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class InterviewState(BaseModel):
    interview_id: str = ""
    status: InterviewStatus = InterviewStatus.CREATED
    jd: Optional[JD] = None
    resume: Optional[Resume] = None
    gap_analysis: Optional[dict] = None  # 能力缺口映射（含 ordered_skills 等）
    rounds: list[RoundState] = Field(default_factory=list)
    current_round: int = 0
    question: Optional[Question] = None   # 待回答的当前题目
    answer: Optional[str] = ""            # 候选人对当前题目的回答
    candidate_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
