"""Human-friendly action rendering helpers."""

from __future__ import annotations

from models import JiraOutlookAction


def render_action(action: JiraOutlookAction) -> str:
    return action.model_dump_json(exclude_none=True)
