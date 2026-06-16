from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Project(BaseModel):
    name: str
    role: str
    description: str
    tech_stack: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class SkillProficiency(BaseModel):
    name: str
    level: Optional[str] = Field(default="familiar", description="expert / proficient / familiar / basic")
    years: Optional[float] = None


class Resume(BaseModel):
    name: str
    title: Optional[str] = None  # 当前/最近职位
    skills: list[SkillProficiency] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience_years: Optional[float] = None
    education: Optional[str] = None
    raw_text: str = ""
