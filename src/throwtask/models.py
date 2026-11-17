import re
from datetime import date
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Day = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
ExternalID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TaskState = Literal["active", "waiting", "blocked"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ChecklistItem(Model):
    id: UUID = Field(default_factory=uuid4)
    title: Title
    isCompleted: StrictBool = False


class RepeatRule(Model):
    unit: Literal["day", "week", "month", "year"] = "day"
    interval: StrictInt = Field(default=1, ge=1, le=10000)
    anchorDay: Day | None = None

    @field_validator("anchorDay")
    @classmethod
    def validate_day(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class TaskFields(Model):
    title: Title
    notes: str = Field(default="", max_length=100000)
    scheduledDay: Day | None = None
    deadlineDay: Day | None = None
    deadlineTime: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    deadlineTimeZone: str | None = None
    sharedRepeat: RepeatRule | None = None
    scheduledRepeat: RepeatRule | None = None
    deadlineRepeat: RepeatRule | None = None
    usesSeparateRepeats: StrictBool = False
    recurrenceParentID: UUID | None = None
    projectID: UUID | None = None
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=100
    )
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=200)
    externalID: ExternalID | None = None
    source: Source | None = None
    state: TaskState = "active"
    isCompleted: StrictBool = False
    isSomeday: StrictBool = False
    sortOrder: StrictInt = Field(default=0, ge=-(2**53 - 1), le=2**53 - 1)

    @field_validator("scheduledDay", "deadlineDay")
    @classmethod
    def validate_day(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_task(self):
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("duplicate tags")
        if len(self.checklist) != len({item.id for item in self.checklist}):
            raise ValueError("duplicate checklist ids")
        if self.deadlineTime is not None and (self.deadlineDay is None or self.deadlineTimeZone is None):
            raise ValueError("a timed deadline needs its day and time zone")
        if self.deadlineTimeZone is not None:
            try:
                ZoneInfo(self.deadlineTimeZone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("invalid time zone") from None
        return self


class TaskCreate(TaskFields):
    id: UUID = Field(default_factory=uuid4)


class TaskPatch(Model):
    title: Title | None = None
    notes: str | None = Field(default=None, max_length=100000)
    scheduledDay: Day | None = None
    deadlineDay: Day | None = None
    deadlineTime: str | None = None
    deadlineTimeZone: str | None = None
    sharedRepeat: RepeatRule | None = None
    scheduledRepeat: RepeatRule | None = None
    deadlineRepeat: RepeatRule | None = None
    usesSeparateRepeats: StrictBool | None = None
    projectID: UUID | None = None
    tags: list[str] | None = None
    checklist: list[ChecklistItem] | None = None
    externalID: ExternalID | None = None
    source: Source | None = None
    state: TaskState | None = None
    isCompleted: StrictBool | None = None
    isSomeday: StrictBool | None = None
    sortOrder: StrictInt | None = None


class ProjectFields(Model):
    title: Title
    symbol: str = Field(default="folder", min_length=1, max_length=100)
    colorIndex: StrictInt = Field(default=0, ge=0, le=1000)
    notes: str | None = Field(default=None, max_length=100000)
    sortOrder: StrictInt = Field(default=0, ge=-(2**53 - 1), le=2**53 - 1)


class ProjectCreate(ProjectFields):
    id: UUID = Field(default_factory=uuid4)


class ProjectPatch(Model):
    title: Title | None = None
    symbol: str | None = None
    colorIndex: StrictInt | None = None
    notes: str | None = None
    sortOrder: StrictInt | None = None


def parse_model(model, value):
    # JSON mode permits UUID strings, while retaining strict numbers/booleans.
    import json

    return model.model_validate_json(json.dumps(value))


def revision_from_header(value: str | None) -> int:
    from .errors import TodoError

    if value is None:
        raise TodoError("PRECONDITION_REQUIRED", 428)
    if not re.fullmatch(r'"[1-9][0-9]{0,14}"', value):
        raise TodoError("INVALID_REVISION", 400)
    return int(value[1:-1])
