"""Trial-run inference script for the Jira/Outlook environment using an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
from dataclasses import dataclass
from typing import Any

import httpx
from openai import OpenAI
from pydantic import ValidationError

from client import JiraOutlookEnv
from models import JiraOutlookAction

DEFAULT_API_BASE_URL = "http://127.0.0.1:8001/v1"
DEFAULT_MODEL = "anthropic::claude-4-5-sonnet"
API_KEY_ENV_VAR = "OPENAI_API_KEY"
API_BASE_URL_ENV_VAR = "OPENAI_API_BASE_URL"
MODEL_ENV_VAR = "OPENAI_MODEL"
SYSTEM_PROMPT = (
    "You are a careful ticket triage agent working in a constrained tool-use environment. "
    "Reason from the observed Jira and Outlook evidence only. Do not invent fields, tools, or record ids. "
    "Prefer a small number of targeted retrieval steps over broad or repetitive searching. "
    "Return exactly one JSON object for the next action and nothing else."
)
JIRA_ALLOWED_FIELDS = [
    "ticket_number",
    "assignee",
    "logs",
    "date",
    "build_number",
    "status",
    "resolution",
    "resolution_notes",
]
OUTLOOK_ALLOWED_FIELDS = [
    "mail_id",
    "subject",
    "body",
]
MAX_REPAIR_ATTEMPTS = 2


@dataclass
class TrialConfig:
    model: str = DEFAULT_MODEL
    api_base_url: str = DEFAULT_API_BASE_URL
    api_key: str = ""
    env_base_url: str = "http://127.0.0.1:8000"
    ca_bundle: str | None = None
    insecure: bool = False
    task_id: str | None = None
    difficulty: str | None = None
    max_agent_steps: int = 6
    temperature: float = 0.0


class TrialRunner:
    def __init__(self, config: TrialConfig) -> None:
        self.config = config
        verify: bool | str | ssl.SSLContext
        if config.ca_bundle:
            verify = config.ca_bundle
        elif config.insecure:
            verify = False
        else:
            verify = True
        self.http_client = httpx.Client(verify=verify, timeout=60.0)
        self.client = OpenAI(api_key=config.api_key, base_url=config.api_base_url, http_client=self.http_client)
        self.env = JiraOutlookEnv(base_url=config.env_base_url)

    async def run(self) -> dict[str, Any]:
        reset = await self.env.reset(task_id=self.config.task_id, difficulty=self.config.difficulty)
        observation = reset.observation
        transcript: list[dict[str, Any]] = []

        for step_index in range(self.config.max_agent_steps):
            action, llm_output, repair_errors = self._choose_action(observation, transcript)
            transcript.append(
                {
                    "step": step_index + 1,
                    "llm_output": llm_output,
                    "repair_errors": repair_errors,
                    "action": action.model_dump(mode="json"),
                }
            )
            step = await self.env.step(action)
            observation = step.observation
            if step.reward is not None:
                observation.reward = step.reward
            if step.done:
                observation.done = True
            transcript[-1]["observation"] = self._observation_snapshot(observation)
            if step.done or observation.done:
                break

        result = {
            "task_id": observation.task.task_id if observation.task else None,
            "assigned_ticket_number": observation.task.assigned_ticket_number if observation.task else None,
            "done": observation.done,
            "final_reward": observation.reward,
            "last_action_error": observation.last_action_error,
            "final_observation": self._observation_snapshot(observation),
            "transcript": transcript,
        }
        await self.env.close()
        self.http_client.close()
        return result

    def _choose_action(
        self,
        observation: Any,
        transcript: list[dict[str, Any]],
    ) -> tuple[JiraOutlookAction, str, list[str]]:
        repair_errors: list[str] = []
        latest_output = ""
        corrective_message = None

        for _ in range(MAX_REPAIR_ATTEMPTS + 1):
            prompt = self._build_prompt(observation, transcript, corrective_message)
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            latest_output = response.choices[0].message.content or ""
            try:
                return self._parse_action(latest_output), latest_output, repair_errors
            except (ValueError, ValidationError) as exc:
                repair_errors.append(str(exc))
                corrective_message = (
                    "Your previous JSON action was invalid for this environment. "
                    f"Error: {exc}. "
                    "Return a corrected JSON action using only the allowed tools and fields. "
                    "Do not repeat the invalid structure."
                )

        raise ValueError(f"Model failed to produce a valid action after retries: {latest_output}")

    def _build_prompt(
        self,
        observation: Any,
        transcript: list[dict[str, Any]],
        corrective_message: str | None,
    ) -> str:
        assigned = observation.assigned_ticket.model_dump(mode="json") if observation.assigned_ticket else None
        prompt = {
            "task": observation.task.model_dump(mode="json") if observation.task else None,
            "instructions": [
                "Choose the single best next tool action.",
                "Use small, precise searches before submitting a resolution.",
                "Do not reference hidden ground truth or unavailable fields.",
                "Return valid JSON only, with no markdown fences.",
                "Use only fields explicitly allowed for each tool.",
                "For submit_resolution, allowed resolution values are duplicate or needs_more_info.",
                "If resolution is duplicate, resolution_notes must be the canonical Jira id.",
            ],
            "allowed_tools": {
                "get_jira_ticket": {
                    "required": ["tool", "ticket_number"],
                    "optional": ["fields"],
                    "allowed_fields": JIRA_ALLOWED_FIELDS,
                },
                "search_jira": {
                    "required": ["tool", "query"],
                    "optional": ["fields", "top_k"],
                    "allowed_fields": JIRA_ALLOWED_FIELDS,
                },
                "get_outlook_mail": {
                    "required_one_of": [["mail_id"], ["subject"]],
                    "optional": ["fields"],
                    "allowed_fields": OUTLOOK_ALLOWED_FIELDS,
                },
                "search_outlook": {
                    "required": ["tool", "query"],
                    "optional": ["fields", "top_k"],
                    "allowed_fields": OUTLOOK_ALLOWED_FIELDS,
                },
                "submit_resolution": {
                    "required": ["tool", "resolution"],
                    "optional": ["ticket_number", "resolution_notes"],
                    "allowed_resolution_values": ["duplicate", "needs_more_info"],
                },
            },
            "assigned_ticket": assigned,
            "steps_taken": observation.steps_taken,
            "current_observation": self._observation_snapshot(observation),
            "previous_steps": transcript,
            "json_schema": {
                "tool": "string",
                "ticket_number": "string|null",
                "mail_id": "string|null",
                "subject": "string|null",
                "query": "string|null",
                "fields": ["string"],
                "top_k": "integer|null",
                "resolution": "duplicate|needs_more_info|null",
                "resolution_notes": "string|null",
            },
        }
        if corrective_message:
            prompt["correction"] = corrective_message
        return json.dumps(prompt, indent=2)

    def _parse_action(self, content: str) -> JiraOutlookAction:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError(f"Model did not return JSON: {content}")
        payload = json.loads(content[start : end + 1])
        return JiraOutlookAction(**payload)

    def _observation_snapshot(self, observation: Any) -> dict[str, Any]:
        return {
            "task": observation.task.model_dump(mode="json") if observation.task else None,
            "assigned_ticket": observation.assigned_ticket.model_dump(mode="json") if observation.assigned_ticket else None,
            "returned_record": observation.returned_record,
            "jira_results": [hit.model_dump(mode="json") for hit in observation.jira_results],
            "outlook_results": [hit.model_dump(mode="json") for hit in observation.outlook_results],
            "reward": observation.reward,
            "done": observation.done,
            "last_action_error": observation.last_action_error,
            "steps_taken": observation.steps_taken,
        }


def parse_args() -> TrialConfig:
    parser = argparse.ArgumentParser(description="Run a trial inference loop against the Jira/Outlook environment.")
    parser.add_argument("--task-id", help="Specific episode task id to run", default=None)
    parser.add_argument("--difficulty", help="Difficulty bucket to sample from", default=None)
    parser.add_argument("--model", default=os.environ.get(MODEL_ENV_VAR, DEFAULT_MODEL))
    parser.add_argument("--api-base-url", default=os.environ.get(API_BASE_URL_ENV_VAR, DEFAULT_API_BASE_URL))
    parser.add_argument("--api-key-env", default=API_KEY_ENV_VAR, help="Environment variable name containing the model API key")
    parser.add_argument("--env-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ca-bundle", default=os.environ.get("SSL_CERT_FILE"))
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for the model API call")
    parser.add_argument("--max-agent-steps", type=int, default=6)
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set")

    return TrialConfig(
        model=args.model,
        api_base_url=args.api_base_url,
        api_key=api_key,
        env_base_url=args.env_base_url,
        ca_bundle=args.ca_bundle,
        insecure=args.insecure,
        task_id=args.task_id,
        difficulty=args.difficulty,
        max_agent_steps=args.max_agent_steps,
        temperature=args.temperature,
    )


async def amain() -> None:
    config = parse_args()
    runner = TrialRunner(config)
    result = await runner.run()
    print(json.dumps(result, indent=2))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
