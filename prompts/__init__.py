from __future__ import annotations

import logging
import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent
_CACHE: dict[str, "PromptTemplate"] = {}

logger = logging.getLogger("prompts")

# 匹配 {variable_name} 占位符
_VAR_PATTERN = re.compile(r"\{(\w+)\}")


class PromptTemplate:
    """带变量校验的 Prompt 模板。

    加载时自动提取 {变量} 列表，format 时校验参数完整性。
    """

    __slots__ = ("name", "_template", "_variables")

    def __init__(self, name: str, template: str, variables: set[str]):
        self.name = name
        self._template = template
        self._variables = variables

    def format(self, **kwargs) -> str:
        """格式化模板，缺失/多余变量时给出明确错误信息。

        相比 str.format() 直接抛 KeyError，这里会列出具体缺了哪些变量。
        """
        given = set(kwargs.keys())
        missing = self._variables - given
        if missing:
            raise KeyError(
                f"Prompt '{self.name}' 缺少变量: {sorted(missing)}\n"
                f"  需要: {sorted(self._variables)}\n"
                f"  传入: {sorted(given)}"
            )
        extra = given - self._variables
        if extra:
            logger.warning(
                "Prompt '%s' 传入了未使用的变量，将被忽略: %s",
                self.name, sorted(extra),
            )
        return self._template.format(**kwargs)

    def __str__(self) -> str:
        """直接当作字符串使用时返回原始模板文本。

        这使得 PromptTemplate 可以传给没有变量的 Agent（如 JDParserAgent），
        它的 system_prompt 不需要 format，直接当字符串用即可。
        """
        return self._template

    def __repr__(self) -> str:
        return f"PromptTemplate({self.name!r}, vars={sorted(self._variables)})"


def load_prompt(name: str) -> PromptTemplate:
    """加载 prompt 模板（按文件名，不带 .md 后缀）。

    首次加载后会缓存，后续调用直接返回。
    """
    if name in _CACHE:
        return _CACHE[name]

    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt 文件不存在: {path}\n"
            f"  可用: {sorted(p.stem for p in _PROMPTS_DIR.glob('*.md') if p.stem != '__init__')}"
        )

    content = path.read_text(encoding="utf-8")
    variables = set(_VAR_PATTERN.findall(content))
    tmpl = PromptTemplate(name, content, variables)

    logger.debug("加载 Prompt: %s, 变量: %s", name, sorted(variables))
    _CACHE[name] = tmpl
    return tmpl


def list_prompts() -> list[str]:
    """列出所有可用的 prompt 名称。"""
    return sorted(p.stem for p in _PROMPTS_DIR.glob("*.md") if p.stem != "__init__")
