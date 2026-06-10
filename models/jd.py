from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Skill(BaseModel):
    name: str
    weight: int = Field(ge=1, le=100, description="权重 1-100")
    is_bonus: bool = False  # True = 加分项


class JD(BaseModel):
    title: str  # 岗位名称
    company: Optional[str] = None
    required_skills: list[Skill] = Field(default_factory=list)
    bonus_skills: list[Skill] = Field(default_factory=list)
    experience_years: Optional[int] = None
    education: Optional[str] = None
    soft_skills: list[str] = Field(default_factory=list)
    raw_text: str = ""  # 原始文本，调试用
