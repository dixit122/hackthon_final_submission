from server.jira_outlook_env_environment import JiraOutlookEnvEnvironment
from models import JiraOutlookAction, ToolName


def _jira_ids(observation) -> list[str]:
    return [hit.record_id for hit in observation.jira_results]


def _outlook_ids(observation) -> list[str]:
    return [hit.record_id for hit in observation.outlook_results]


def test_jira_query_surfaces_closed_canonical_ticket() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2104")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_JIRA,
            query="NotificationPreferenceAssembler empty locale map profile hydration retry",
            fields=["ticket_number", "resolution"],
            top_k=5,
        )
    )
    ids = _jira_ids(observation)
    assert ids
    assert "JIRA-2044" in ids
    duplicate_hit = next(hit for hit in observation.jira_results if hit.record_id == "JIRA-2044")
    assert duplicate_hit.fields["resolution"] == "closed"


def test_jira_query_with_finance_terms_returns_relevant_hits() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2122")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_JIRA,
            query="duplicate redemption ledger_event_id entitlement retry",
            fields=["ticket_number"],
            top_k=5,
        )
    )
    ids = _jira_ids(observation)
    assert ids
    assert any(ticket in ids for ticket in {"JIRA-2122", "JIRA-2150", "JIRA-2151", "JIRA-2050"})


def test_outlook_open_investigation_query_ranks_request_mail_first() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2110")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="export job id template revision sample generated PDF tenant customization history",
            fields=["mail_id", "subject"],
            top_k=5,
        )
    )
    ids = _outlook_ids(observation)
    assert ids[0] == "MAIL-605"
    assert observation.outlook_results[0].fields["subject"]


def test_outlook_precise_query_reduces_noise_to_relevant_top_hit() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2170")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query='"JIRA-2172 final acknowledgement template bridge mail points to JIRA-2085"',
            fields=["mail_id", "subject"],
            top_k=3,
        )
    )
    ids = _outlook_ids(observation)
    assert ids
    assert ids[0] == "MAIL-662"


def test_jira_search_scores_are_monotonic_for_ranked_results() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2104")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_JIRA,
            query="notification preference locale map",
            fields=["ticket_number"],
            top_k=5,
        )
    )
    scores = [hit.score for hit in observation.jira_results]
    assert scores == sorted(scores, reverse=True)


def test_outlook_search_scores_are_monotonic_for_ranked_results() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2110")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="template revision sample generated PDF missing",
            fields=["mail_id"],
            top_k=5,
        )
    )
    scores = [hit.score for hit in observation.outlook_results]
    assert scores == sorted(scores, reverse=True)


def test_search_top_k_caps_result_count() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2104")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_JIRA,
            query="notification",
            fields=["ticket_number"],
            top_k=1,
        )
    )
    assert len(observation.jira_results) == 1


def test_irrelevant_terms_return_no_hits() -> None:
    env = JiraOutlookEnvEnvironment()
    env.reset(task_id="robust_jira_2110")
    observation = env.step(
        JiraOutlookAction(
            tool=ToolName.SEARCH_OUTLOOK,
            query="zebra neutrino glacier",
            fields=["mail_id"],
            top_k=5,
        )
    )
    assert observation.outlook_results == []
