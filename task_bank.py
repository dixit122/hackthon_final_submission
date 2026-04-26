"""Task loading and SQLite-backed search utilities."""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path
from typing import Any

try:
    from models import JiraRecord, OutlookRecord, ResolutionDecision
except ImportError:
    from models import JiraRecord, OutlookRecord, ResolutionDecision

DATA_DIR = Path(__file__).resolve().parent / "data"
TASKS_DIR = DATA_DIR / "tasks"
EPISODES_DIR = TASKS_DIR / "robust_episodes"
REWARD_TASK_FILE = "jira_outlook_robust_case.json"
VISIBLE_TO_REWARD_TASK_ID = {
    "jira_outlook_robust_training_case": "jira_outlook_robust_case",
}


class TaskBank:
    def __init__(self) -> None:
        self.tasks = self._load_visible_tasks()
        self.reward_tasks = self._load_reward_tasks()
        self.jira_by_id: dict[str, JiraRecord] = {}
        self.mail_by_id: dict[str, OutlookRecord] = {}
        self.mail_by_subject: dict[str, OutlookRecord] = {}
        self.reward_jira_by_task_id: dict[str, dict[str, JiraRecord]] = {}
        self._build_indexes()

    def _load_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text())

    def _load_visible_tasks(self) -> list[dict[str, Any]]:
        tasks = [self._load_json(path) for path in sorted(EPISODES_DIR.glob('*.json'))]
        if not tasks:
            raise ValueError(f"No visible task files found in {EPISODES_DIR}")
        return tasks

    def _load_reward_tasks(self) -> dict[str, dict[str, Any]]:
        path = DATA_DIR / REWARD_TASK_FILE
        if not path.exists():
            raise ValueError(f"Reward task file not found: {path}")
        task = self._load_json(path)
        reward_tasks = {task["task_id"]: task}
        for visible_task in self.tasks:
            reward_tasks[visible_task["task_id"]] = task
        return reward_tasks

    def _build_indexes(self) -> None:
        visible_jira: dict[str, JiraRecord] = {}
        visible_mail: dict[str, OutlookRecord] = {}
        visible_subjects: dict[str, OutlookRecord] = {}
        for task in self.tasks:
            for jira in task["jira_records"]:
                record = JiraRecord(
                    ticket_number=jira["ticket_number"],
                    assignee=jira["assignee"],
                    logs=jira["logs"],
                    date=jira["date"],
                    build_number=jira["build_number"],
                    status=jira["status"],
                    resolution=ResolutionDecision(jira["resolution"]) if jira.get("resolution") else None,
                    resolution_notes=jira.get("resolution_notes"),
                )
                visible_jira[record.ticket_number] = record
            for mail in task["outlook_records"]:
                record = OutlookRecord(**mail)
                visible_mail[record.mail_id] = record
                visible_subjects[record.subject] = record
        self.jira_by_id = visible_jira
        self.mail_by_id = visible_mail
        self.mail_by_subject = visible_subjects

        for task_id, task in self.reward_tasks.items():
            reward_records: dict[str, JiraRecord] = {}
            for jira in task["jira_records"]:
                reward_records[jira["ticket_number"]] = JiraRecord(
                    ticket_number=jira["ticket_number"],
                    assignee=jira["assignee"],
                    logs=jira["logs"],
                    date=jira["date"],
                    build_number=jira["build_number"],
                    status=jira["status"],
                    resolution=ResolutionDecision(jira["resolution"]) if jira.get("resolution") else None,
                    resolution_notes=jira.get("resolution_notes"),
                    ground_truth_path=jira.get("ground_truth_path", []),
                )
            self.reward_jira_by_task_id[task_id] = reward_records

    def choose_task(
        self,
        task_id: str | None = None,
        seed: int | None = None,
        difficulty: str | None = None,
    ) -> dict[str, Any]:
        if task_id:
            for task in self.tasks:
                if task["task_id"] == task_id:
                    return task
            raise ValueError(f"Unknown task_id: {task_id}")

        candidates = self.tasks
        if difficulty:
            candidates = [task for task in self.tasks if task.get("difficulty") == difficulty]
            if not candidates:
                raise ValueError(f"No tasks found for difficulty: {difficulty}")

        rng = random.Random(seed)
        return rng.choice(candidates)

    def get_reward_ticket(self, task_id: str, ticket_number: str) -> JiraRecord:
        task_id = VISIBLE_TO_REWARD_TASK_ID.get(task_id, task_id)
        task_records = self.reward_jira_by_task_id.get(task_id)
        if task_records is None:
            raise ValueError(f"Unknown reward task_id: {task_id}")
        if ticket_number not in task_records:
            raise ValueError(f"Unknown reward ticket: {ticket_number}")
        return task_records[ticket_number]

    def build_search_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE jira(ticket_number TEXT PRIMARY KEY, assignee TEXT, logs TEXT, date TEXT, build_number TEXT, status TEXT, resolution TEXT, resolution_notes TEXT)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE jira_fts USING fts5(ticket_number UNINDEXED, logs, content='jira', content_rowid='rowid')"
        )
        conn.execute(
            "CREATE TABLE outlook(mail_id TEXT PRIMARY KEY, subject TEXT, body TEXT)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE outlook_fts USING fts5(mail_id UNINDEXED, subject, body, content='outlook', content_rowid='rowid')"
        )
        for jira in self.jira_by_id.values():
            conn.execute(
                "INSERT INTO jira VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    jira.ticket_number,
                    jira.assignee,
                    jira.logs,
                    jira.date,
                    jira.build_number,
                    jira.status.value,
                    jira.resolution.value if jira.resolution else None,
                    jira.resolution_notes,
                ),
            )
        conn.execute("INSERT INTO jira_fts(jira_fts) VALUES ('rebuild')")
        for mail in self.mail_by_id.values():
            conn.execute(
                "INSERT INTO outlook VALUES (?, ?, ?)",
                (mail.mail_id, mail.subject, mail.body),
            )
        conn.execute("INSERT INTO outlook_fts(outlook_fts) VALUES ('rebuild')")
        return conn
