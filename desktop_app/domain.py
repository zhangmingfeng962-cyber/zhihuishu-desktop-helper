from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Literal


class PauseRequired(RuntimeError):
    """A recoverable condition; never convert this into a successful task."""


class Stopped(RuntimeError):
    pass


class FrameChanged(PauseRequired):
    """A target changed before input was sent; visual mode can reobserve."""


class InputFocusUnclear(PauseRequired):
    """No typing was sent because a field did not confirm focus."""


class TargetUnclear(PauseRequired):
    """A visual target is ambiguous before a click is sent."""


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalized(text).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.w, self.y + self.h

    def contains(self, other: Rect) -> bool:
        return self.x <= other.x and self.y <= other.y and self.x + self.w >= other.x + other.w and self.y + self.h >= other.y + other.h

    def moved(self, x: int = 0, y: int = 0) -> Rect:
        return Rect(self.x + x, self.y + y, self.w, self.h)

    def expanded(self, n: int, width: int, height: int) -> Rect:
        x, y = max(0, self.x - n), max(0, self.y - n)
        return Rect(x, y, min(width, self.x + self.w + n) - x, min(height, self.y + self.h + n) - y)


@dataclass
class Span:
    text: str
    box: Rect
    score: float = 1.0


@dataclass
class Option:
    label: str
    text: str
    box: Rect
    control: Rect | None = None
    selected: bool | None = None


@dataclass
class Question:
    kind: Literal["single", "multiple", "judge", "blank"]
    text: str
    index: int
    total: int | None
    scope: Literal["chapter", "popup"]
    options: list[Option] = field(default_factory=list)
    blanks: list[Rect] = field(default_factory=list)
    title: str = ""
    option_rows_complete: bool = True

    @property
    def key(self) -> str:
        return fingerprint(str(self.index) + self.kind + self.text + "|".join(o.label + o.text for o in self.options) + str(len(self.blanks)))

    def prompt_data(self) -> dict:
        return {"type": self.kind, "text": self.text, "blank_count": len(self.blanks),
                "options": [{"label": o.label, "text": o.text} for o in self.options]}


@dataclass
class Answer:
    values: list[str]
    explanation: str = ""

    @classmethod
    def parse(cls, raw: str, question: Question) -> Answer:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        try:
            data = json.loads(raw)
            values = data["answers"] if isinstance(data, dict) else data
            explanation = data.get("explanation", "") if isinstance(data, dict) else ""
        except (ValueError, KeyError, TypeError) as exc:
            raise PauseRequired("AI 返回格式错误：应为包含 answers 字符串数组的 JSON。") from exc
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values):
            raise PauseRequired("AI 返回了空答案或非字符串答案。")
        values = [v.strip() for v in values]
        if question.kind == "blank":
            if len(values) != len(question.blanks):
                raise PauseRequired(f"填空数量不一致：画面 {len(question.blanks)} 个空，AI 返回 {len(values)} 个。")
            if any("\n" in v or "\r" in v or len(v) > 1000 for v in values):
                raise PauseRequired("填空答案含换行或过长，需人工检查输入框类型。")
        else:
            allowed = {o.label for o in question.options}
            if len(values) != len(set(values)) or not set(values) <= allowed:
                raise PauseRequired("AI 返回了重复或不存在的选项。")
            if question.kind != "multiple" and len(values) != 1:
                raise PauseRequired("单选或判断题必须恰好选择一个选项。")
        return cls(values, str(explanation)[:1000])


@dataclass
class View:
    spans: list[Span]
    question: Question | None = None
    buttons: dict[str, Rect] = field(default_factory=dict)
    blocked: str | None = None
    submitted: bool = False
    confirmation: bool = False
    unfinished: bool = False
    video_finished: bool = False
    video_paused: bool = False
    popup_feedback: bool = False
    video_visible: bool = False
    video_position: int | None = None
    video_duration: int | None = None
    video_lesson: str = ''
    video_playing: bool = False
    tab_titles: list[str] = field(default_factory=list)


@dataclass
class CourseTask:
    task_id: str
    chapter: str
    code: str
    label: str
    kind: Literal['video','quiz']
    duration: int
    order: int
    status: str = 'pending'
    quiz_ref: str | None = None
    completion_source: str = ''


@dataclass
class LessonProgress:
    task_id: str
    position: int = 0
    duration: int = 0
    last_advanced_at: float = 0
    ended_since: float = 0


@dataclass
class Profile:
    width: int = 0
    height: int = 0
    regions: dict[str, Rect] = field(default_factory=dict)
    blank_overrides: dict[str, list[Rect]] = field(default_factory=dict)
    task_name: str = "当前课程"
    answer_interval: float = 1.0
    poll_interval: float = 2.0
    max_minutes: int = 0
    base_url: str = "https://api.deepseek.com/chat/completions"
    model: str = "deepseek-flash"
    mode: str = 'course'
    auto_mute: bool = True
    control_mode: str = 'vision'
    vision_region_overrides: dict[str,Rect] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        data = dict(data)
        data["regions"] = {k: Rect(**v) for k, v in data.get("regions", {}).items()}
        data["blank_overrides"] = {k: [Rect(**r) for r in rows] for k, rows in data.get("blank_overrides", {}).items()}
        data['vision_region_overrides']={k:Rect(**v) for k,v in data.get('vision_region_overrides',{}).items()}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
