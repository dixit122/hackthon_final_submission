"""Typed OpenEnv client for the environment."""

from __future__ import annotations

from typing import Any

from openenv.core import EnvClient
from openenv.core.client_types import StepResult

from models import JiraOutlookAction, JiraOutlookObservation, JiraOutlookState


class JiraOutlookEnv(EnvClient[JiraOutlookAction, JiraOutlookObservation, JiraOutlookState]):
    def _step_payload(self, action: JiraOutlookAction) -> dict[str, Any]:
        return action.model_dump(mode="json", exclude_none=True)

    def _parse_result(self, payload: dict[str, Any]) -> StepResult[JiraOutlookObservation]:
        observation = JiraOutlookObservation.model_validate(payload.get("observation", {}))
        return StepResult(
            observation=observation,
            reward=payload.get("reward"),
            done=payload.get("done", False),
        )

    def _parse_state(self, payload: dict[str, Any]) -> JiraOutlookState:
        return JiraOutlookState.model_validate(payload)
