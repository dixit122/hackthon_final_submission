"""Environment implementation for Jira and Outlook search tasks."""

from __future__ import annotations

import json
import re
from typing import Any

from openenv.core.env_server.interfaces import Environment

try:
    from models import (
        JIRA_FIELDS,
        OUTLOOK_FIELDS,
        JiraOutlookAction,
        JiraOutlookObservation,
        JiraOutlookState,
        ResolutionDecision,
        SearchHit,
        TaskSummary,
        ToolName,
    )
    from task_bank import TaskBank
except ImportError:
    from models import (
        JIRA_FIELDS,
        OUTLOOK_FIELDS,
        JiraOutlookAction,
        JiraOutlookObservation,
        JiraOutlookState,
        ResolutionDecision,
        SearchHit,
        TaskSummary,
        ToolName,
    )
    from task_bank import TaskBank


class JiraOutlookEnvEnvironment(Environment[JiraOutlookAction, JiraOutlookObservation, JiraOutlookState]):
    SUPPORTS_CONCURRENT_SESSIONS = True
    STEP_COST = -0.01
    REPEATED_QUERY_PENALTY = -0.05
    REPEATED_FETCH_PENALTY = -0.03
    EMPTY_SEARCH_PENALTY = -0.02

    def __init__(self) -> None:
        super().__init__()
        self.task_bank = TaskBank()
        self.conn = self.task_bank.build_search_db()
        self._state = JiraOutlookState()
        self._task_data: dict[str, Any] | None = None

    def reset(self, seed: int | None = None, episode_id: str | None = None, **kwargs: Any) -> JiraOutlookObservation:
        task = self.task_bank.choose_task(
            task_id=kwargs.get("task_id"),
            seed=seed,
            difficulty=kwargs.get("difficulty"),
        )
        assigned_ticket_number = task["assigned_ticket_number"]
        self._task_data = task
        self._state = JiraOutlookState(
            task=TaskSummary(
                task_id=task["task_id"],
                objective=task["objective"],
                assigned_ticket_number=assigned_ticket_number,
                max_steps=task["max_steps"],
            ),
            steps_taken=0,
            done=False,
            assigned_ticket_number=assigned_ticket_number,
        )
        return JiraOutlookObservation(
            task=self._state.task,
            assigned_ticket=self.task_bank.jira_by_id[assigned_ticket_number],
            reward=0.0,
            done=False,
            steps_taken=0,
        )

    def step(self, action: JiraOutlookAction, timeout_s: float | None = None, **kwargs: Any) -> JiraOutlookObservation:
        if self._state.done or self._task_data is None:
            return JiraOutlookObservation(
                last_action_error="Episode not active. Call reset() first.",
                done=True,
                steps_taken=self._state.steps_taken,
            )

        self._state.steps_taken += 1
        observation = JiraOutlookObservation(task=self._state.task, steps_taken=self._state.steps_taken)

        try:
            if action.tool == ToolName.GET_JIRA_TICKET:
                observation.returned_record = self._get_jira_ticket(action.ticket_number, action.fields)
                observation.reward = 0.05
            elif action.tool == ToolName.SEARCH_JIRA:
                observation.jira_results = self._search_jira(action.query or "", action.fields, action.top_k)
                observation.reward = 0.08 if observation.jira_results else 0.01
            elif action.tool == ToolName.GET_OUTLOOK_MAIL:
                observation.returned_record = self._get_outlook_mail(action.mail_id, action.subject, action.fields)
                observation.reward = 0.05
            elif action.tool == ToolName.SEARCH_OUTLOOK:
                observation.outlook_results = self._search_outlook(action.query or "", action.fields, action.top_k)
                observation.reward = 0.08 if observation.outlook_results else 0.01
            elif action.tool == ToolName.SUBMIT_RESOLUTION:
                observation = self._submit_resolution(action)
            else:
                observation.last_action_error = f"Unsupported tool: {action.tool}"
                observation.reward = -0.1
        except Exception as exc:
            observation.last_action_error = str(exc)
            observation.reward = -0.1

        if not observation.done:
            observation.reward += self._discipline_adjustment(action, observation)

        if self._state.steps_taken >= (self._state.task.max_steps if self._state.task else 0) and not observation.done:
            self._state.done = True
            observation.done = True
            observation.reward = observation.reward - 0.2
            observation.last_action_error = observation.last_action_error or "Maximum steps reached"

        return observation

    @property
    def state(self) -> JiraOutlookState:
        return self._state

    def close(self) -> None:
        self.conn.close()

    def _discipline_adjustment(self, action: JiraOutlookAction, observation: JiraOutlookObservation) -> float:
        adjustment = self.STEP_COST

        if action.tool in {ToolName.SEARCH_JIRA, ToolName.SEARCH_OUTLOOK}:
            normalized_query = (action.query or "").strip().lower()
            if normalized_query in self._state.query_history:
                adjustment += self.REPEATED_QUERY_PENALTY
            self._state.query_history.append(normalized_query)

            no_hits = action.tool == ToolName.SEARCH_JIRA and not observation.jira_results
            no_hits = no_hits or (action.tool == ToolName.SEARCH_OUTLOOK and not observation.outlook_results)
            if no_hits:
                adjustment += self.EMPTY_SEARCH_PENALTY

        if action.tool == ToolName.GET_JIRA_TICKET and action.ticket_number:
            if action.ticket_number in self._state.fetched_jira_ids:
                adjustment += self.REPEATED_FETCH_PENALTY
            self._state.fetched_jira_ids.append(action.ticket_number)

        if action.tool == ToolName.GET_OUTLOOK_MAIL:
            identifier = action.mail_id or action.subject
            if identifier:
                if identifier in self._state.fetched_mail_ids:
                    adjustment += self.REPEATED_FETCH_PENALTY
                self._state.fetched_mail_ids.append(identifier)

        return adjustment

    def _get_jira_ticket(self, ticket_number: str | None, fields: list[str]) -> dict[str, object]:
        if not ticket_number or ticket_number not in self.task_bank.jira_by_id:
            raise ValueError(f"Unknown Jira ticket: {ticket_number}")
        record = self.task_bank.jira_by_id[ticket_number].model_dump(mode="json")
        if ticket_number not in self._state.discovered_jira_ids:
            self._state.discovered_jira_ids.append(ticket_number)
        return self._filter_fields(record, fields, JIRA_FIELDS)

    def _get_outlook_mail(self, mail_id: str | None, subject: str | None, fields: list[str]) -> dict[str, object]:
        record = None
        if mail_id:
            record = self.task_bank.mail_by_id.get(mail_id)
        elif subject:
            record = self.task_bank.mail_by_subject.get(subject)
        if record is None:
            raise ValueError("Unknown Outlook mail")
        if record.mail_id not in self._state.discovered_mail_ids:
            self._state.discovered_mail_ids.append(record.mail_id)
        return self._filter_fields(record.model_dump(mode="json"), fields, OUTLOOK_FIELDS)

    def _search_jira(self, query: str, fields: list[str], top_k: int) -> list[SearchHit]:
        query = self._normalize_search_query(query)
        sql = (
            "SELECT j.ticket_number, j.assignee, j.logs, j.date, j.build_number, j.status, j.resolution, j.resolution_notes, "
            "bm25(jira_fts) AS rank "
            "FROM jira_fts JOIN jira j ON jira_fts.rowid = j.rowid "
            "WHERE jira_fts MATCH ? ORDER BY rank, j.ticket_number LIMIT ?"
        )
        rows = self.conn.execute(sql, (query, top_k)).fetchall()
        hits = []
        for row in rows:
            ticket_number = row["ticket_number"]
            if ticket_number not in self._state.discovered_jira_ids:
                self._state.discovered_jira_ids.append(ticket_number)
            hits.append(
                SearchHit(
                    record_id=ticket_number,
                    source="jira",
                    score=max(0.0, float(-row["rank"])),
                    fields=self._filter_fields(dict(row), fields, JIRA_FIELDS),
                )
            )
        return hits

    def _search_outlook(self, query: str, fields: list[str], top_k: int) -> list[SearchHit]:
        query = self._normalize_search_query(query)
        sql = (
            "SELECT o.mail_id, o.subject, o.body, bm25(outlook_fts) AS rank "
            "FROM outlook_fts JOIN outlook o ON outlook_fts.rowid = o.rowid "
            "WHERE outlook_fts MATCH ? ORDER BY rank, o.mail_id LIMIT ?"
        )
        rows = self.conn.execute(sql, (query, top_k)).fetchall()
        hits = []
        for row in rows:
            payload = dict(row)
            mail_id = row["mail_id"]
            if mail_id not in self._state.discovered_mail_ids:
                self._state.discovered_mail_ids.append(mail_id)
            hits.append(
                SearchHit(
                    record_id=mail_id,
                    source="outlook",
                    score=max(0.0, float(-row["rank"])),
                    fields=self._filter_fields(payload, fields, OUTLOOK_FIELDS),
                )
            )
        return hits

    def _submit_resolution(self, action: JiraOutlookAction) -> JiraOutlookObservation:
        assigned_ticket_number = self._state.assigned_ticket_number
        expected_ticket = self.task_bank.get_reward_ticket(self._state.task.task_id, assigned_ticket_number)
        expected_resolution = expected_ticket.resolution
        expected_notes = expected_ticket.resolution_notes

        correct = action.resolution == expected_resolution
        if correct and action.resolution == ResolutionDecision.DUPLICATE:
            correct = action.resolution_notes == expected_notes

        self._state.done = True
        reward = 1.0 if correct else -1.0
        return JiraOutlookObservation(
            task=self._state.task,
            assigned_ticket=expected_ticket,
            returned_record={
                "submitted_resolution": action.resolution.value if action.resolution else None,
                "submitted_resolution_notes": action.resolution_notes,
                "expected_resolution": expected_resolution.value if expected_resolution else None,
                "expected_resolution_notes": expected_notes,
                "ticket_number": assigned_ticket_number,
                "correct": correct,
                "final_score": 0.99 if correct else 0.01,
            },
            reward=reward,
            done=True,
            steps_taken=self._state.steps_taken,
        )

    def _normalize_search_query(self, query: str) -> str:
        cleaned = re.sub(r"[^\w\s\"]+", " ", query)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or query

    def _filter_fields(self, payload: dict[str, Any], fields: list[str], allowed_fields: set[str]) -> dict[str, object]:
        if not fields:
            return {key: value for key, value in payload.items() if key in allowed_fields}
        return {key: payload[key] for key in fields if key in payload and key in allowed_fields}
