"""Public models for the Jira/Outlook resolution environment."""

from __future__ import annotations

from enum import Enum

from openenv.core.env_server.types import Action, Observation, State
from pydantic import BaseModel, Field, model_validator


class JiraStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ResolutionDecision(str, Enum):
    CLOSED = "closed"
    DUPLICATE = "duplicate"
    NEEDS_MORE_INFO = "needs_more_info"
    UNRESOLVED = "unresolved"


class ToolName(str, Enum):
    GET_JIRA_TICKET = "get_jira_ticket"
    SEARCH_JIRA = "search_jira"
    GET_OUTLOOK_MAIL = "get_outlook_mail"
    SEARCH_OUTLOOK = "search_outlook"
    SUBMIT_RESOLUTION = "submit_resolution"


JIRA_FIELDS = {
    "ticket_number",
    "assignee",
    "logs",
    "date",
    "build_number",
    "status",
    "resolution",
    "resolution_notes",
    "ground_truth_path",
}
OUTLOOK_FIELDS = {
    "mail_id",
    "subject",
    "body",
}


class JiraRecord(BaseModel):
    ticket_number: str
    assignee: str
    logs: str
    date: str
    build_number: str
    status: JiraStatus
    resolution: ResolutionDecision | None = None
    resolution_notes: str | None = None
    ground_truth_path: list[str] = Field(default_factory=list)


class OutlookRecord(BaseModel):
    mail_id: str
    subject: str
    body: str


class SearchHit(BaseModel):
    record_id: str
    source: str
    score: float = Field(ge=0.0)
    fields: dict[str, object] = Field(default_factory=dict)


class TaskSummary(BaseModel):
    task_id: str
    objective: str
    assigned_ticket_number: str
    max_steps: int = Field(ge=1)


class JiraOutlookAction(Action):
    tool: ToolName
    ticket_number: str | None = None
    mail_id: str | None = None
    subject: str | None = None
    query: str | None = None
    fields: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)
    resolution: ResolutionDecision | None = None
    resolution_notes: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "JiraOutlookAction":
        if self.tool == ToolName.GET_JIRA_TICKET and not self.ticket_number:
            raise ValueError("get_jira_ticket requires ticket_number")
        if self.tool == ToolName.SEARCH_JIRA and not self.query:
            raise ValueError("search_jira requires query")
        if self.tool == ToolName.GET_OUTLOOK_MAIL and not (self.mail_id or self.subject):
            raise ValueError("get_outlook_mail requires mail_id or subject")
        if self.tool == ToolName.SEARCH_OUTLOOK and not self.query:
            raise ValueError("search_outlook requires query")
        if self.tool == ToolName.SUBMIT_RESOLUTION:
            if not self.resolution:
                raise ValueError("submit_resolution requires resolution")
            if self.resolution == ResolutionDecision.CLOSED:
                raise ValueError("submit_resolution does not allow closed")
            if self.resolution == ResolutionDecision.DUPLICATE and not self.resolution_notes:
                raise ValueError("duplicate submissions require resolution_notes ticket id")
        if self.tool in {ToolName.GET_JIRA_TICKET, ToolName.SEARCH_JIRA}:
            invalid = set(self.fields) - JIRA_FIELDS
            if invalid:
                raise ValueError(f"Unsupported Jira fields requested: {sorted(invalid)}")
        if self.tool in {ToolName.GET_OUTLOOK_MAIL, ToolName.SEARCH_OUTLOOK}:
            invalid = set(self.fields) - OUTLOOK_FIELDS
            if invalid:
                raise ValueError(f"Unsupported Outlook fields requested: {sorted(invalid)}")
        return self


class JiraOutlookObservation(Observation):
    task: TaskSummary | None = None
    assigned_ticket: JiraRecord | None = None
    jira_results: list[SearchHit] = Field(default_factory=list)
    outlook_results: list[SearchHit] = Field(default_factory=list)
    returned_record: dict[str, object] | None = None
    reward: float = 0.0
    done: bool = False
    last_action_error: str | None = None
    steps_taken: int = 0


class JiraOutlookState(State):
    task: TaskSummary | None = None
    steps_taken: int = 0
    done: bool = False
    assigned_ticket_number: str | None = None
    discovered_jira_ids: list[str] = Field(default_factory=list)
    discovered_mail_ids: list[str] = Field(default_factory=list)
    query_history: list[str] = Field(default_factory=list)
    fetched_jira_ids: list[str] = Field(default_factory=list)
    fetched_mail_ids: list[str] = Field(default_factory=list)
