"""FastAPI application for the Jira Outlook environment."""

try:
    from openenv.core.env_server.http_server import create_app
except Exception as e:  # pragma: no cover
    raise ImportError(
        "openenv is required for the web interface. Install dependencies with '\n    uv sync\n'"
    ) from e

try:
    from models import JiraOutlookAction, JiraOutlookObservation
    from .jira_outlook_env_environment import JiraOutlookEnvEnvironment
except ModuleNotFoundError:
    from models import JiraOutlookAction, JiraOutlookObservation
    from server.jira_outlook_env_environment import JiraOutlookEnvEnvironment


app = create_app(
    JiraOutlookEnvEnvironment,
    JiraOutlookAction,
    JiraOutlookObservation,
    env_name="jira_outlook_env",
    max_concurrent_envs=4,
)


def main(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
