from pydantic import ValidationError
from pytest import approx

from models import JiraOutlookAction, JiraStatus, ResolutionDecision, ToolName
from server.jira_outlook_env_environment import JiraOutlookEnvEnvironment

TASK_ID = "robust_jira_2104"


def test_reset_assigns_ticket() -> None:
    env = JiraOutlookEnvEnvironment()
    observation = env.reset(task_id=TASK_ID)
    assert observation.task is not None
    assert observation.task.assigned_ticket_number == "JIRA-2104"
    assert observation.assigned_ticket is not None
    assert observation.assigned_ticket.ticket_number == "JIRA-2104"
    assert observation.assigned_ticket.status == JiraStatus.OPEN


def test_seeded_reset_is_deterministic_for_difficulty_bucket() -> None:
    env = JiraOutlookEnvEnvironment()
    first = env.reset(seed=7, difficulty="easy")
    second = env.reset(seed=7, difficulty="easy")
    assert first.task is not None
    assert second.task is not None
    assert first.task.task_id == second.task.task_id
    assert first.task.assigned_ticket_number == second.task.assigned_ticket_number


def test_reset_can_filter_by_difficulty() -> None:
    env = JiraOutlookEnvEnvironment()
    observation = env.reset(difficulty="hard", seed=1)
    assert observation.task is not None
    assert observation.task.task_id.startswith("robust_jira_")
    assert observation.task.assigned_ticket_number in {
        "JIRA-2122",
        "JIRA-2150",
        "JIRA-2151",
        "JIRA-2161",
        "JIRA-2170",
        "JIRA-2171",
        "JIRA-2172",
        "JIRA-2180",
        "JIRA-2181",
    }


def test_get_jira_ticket_fields() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id=TASK_ID)
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.GET_JIRA_TICKET,
            ticket_number="JIRA-2104",
            fields=["ticket_number", "build_number", "status", "resolution_notes"],
        )
    )
    assert observation.returned_record == {
        "ticket_number": "JIRA-2104",
        "build_number": "2026.05.7",
        "status": "open",
        "resolution_notes": None,
    }


def test_search_outlook_returns_hits() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2110")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="invoice footer template revision sample generated PDF",
            fields=["mail_id", "subject"],
            top_k=2,
        )
    )
    assert observation.outlook_results
    assert observation.outlook_results[0].record_id == "MAIL-605"


def test_invalid_jira_field_is_rejected_by_action_model() -> None:
    try:
        JiraOutlookAction(
            tool=ToolName.GET_JIRA_TICKET,
            ticket_number="JIRA-2104",
            fields=["not_a_real_field"],
        )
    except ValidationError as exc:
        assert "Unsupported Jira fields requested" in str(exc)
    else:
        raise AssertionError("Expected action validation to fail")


def test_duplicate_submission_requires_resolution_notes() -> None:
    try:
        JiraOutlookAction(
            tool=ToolName.SUBMIT_RESOLUTION,
            ticket_number="JIRA-2104",
            resolution=ResolutionDecision.DUPLICATE,
        )
    except ValidationError as exc:
        assert "duplicate submissions require resolution_notes" in str(exc)
    else:
        raise AssertionError("Expected duplicate submission validation to fail")


def test_submit_resolution_uses_hidden_ground_truth() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id=TASK_ID)
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SUBMIT_RESOLUTION,
            ticket_number="JIRA-2104",
            resolution=ResolutionDecision.DUPLICATE,
            resolution_notes="JIRA-2044",
        )
    )
    assert observation.done is True
    assert observation.reward == 1.0
    assert observation.returned_record is not None
    assert observation.returned_record["correct"] is True


def test_duplicate_submission_checks_hidden_target() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id=TASK_ID)
    wrong = env.step(
        JiraOutlookAction(
            tool=ToolName.SUBMIT_RESOLUTION,
            ticket_number="JIRA-2104",
            resolution=ResolutionDecision.DUPLICATE,
            resolution_notes="JIRA-2015",
        )
    )
    assert wrong.reward == -1.0
    assert wrong.returned_record["expected_resolution"] == "duplicate"


def test_closed_submission_is_rejected_by_action_model() -> None:
    try:
        JiraOutlookAction(
            tool=ToolName.SUBMIT_RESOLUTION,
            ticket_number="JIRA-2104",
            resolution=ResolutionDecision.CLOSED,
        )
    except ValidationError as exc:
        assert "does not allow closed" in str(exc)
    else:
        raise AssertionError("Expected closed submission validation to fail")


def test_repeated_jira_fetch_gets_discipline_penalty() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id=TASK_ID)

    first = env.step(
        JiraOutlookAction(
            tool=ToolName.GET_JIRA_TICKET,
            ticket_number="JIRA-2104",
            fields=["ticket_number"],
        )
    )
    second = env.step(
        JiraOutlookAction(
            tool=ToolName.GET_JIRA_TICKET,
            ticket_number="JIRA-2104",
            fields=["ticket_number"],
        )
    )

    assert first.reward == approx(0.04)
    assert second.reward == approx(0.01)


def test_repeated_query_gets_discipline_penalty() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id=TASK_ID)

    first = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="notification preference locale map",
            fields=["mail_id"],
            top_k=3,
        )
    )
    second = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="notification preference locale map",
            fields=["mail_id"],
            top_k=3,
        )
    )

    assert first.reward == approx(0.07)
    assert second.reward == approx(0.02)


def test_empty_search_gets_extra_penalty() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id=TASK_ID)

    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="zebra neutrino glacier",
            fields=["mail_id"],
            top_k=3,
        )
    )

    assert observation.outlook_results == []
    assert observation.reward == approx(-0.02)
