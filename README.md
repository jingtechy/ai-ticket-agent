# AI Ticket Agent

## Project overview

This project listens for Slack slash commands and interactive button actions, creates Jira issues, and records a local ticket log. The primary goals are:

- Create Jira issues from Slack (`/ticket`).
- Query Jira issue status (`/ticket_status`).
- Present approval/rejection interactive buttons in Slack and record the result.

The service is intentionally small and easy to adapt. It is implemented with FastAPI and uses `httpx` for async HTTP to Jira and `slack_sdk` AsyncWebClient for Slack.

## Architecture & important files

- `main.py` — FastAPI application, Slack endpoints (`/slack/command`, `/slack/actions`, `/slack/events`).
- `jira.py` — Jira API helpers for creating issues and fetching issue status.
- `slack.py` — Slack helper to post messages and construct the approval block.
- `llm.py` — (optional) uses OpenAI to classify tickets; currently code uses a placeholder.
- `db.py` — SQLAlchemy engine/session factory and `init_db()` function.
- `models.py` — SQLAlchemy ORM model `TicketLog`.
- `config.yml` — YAML config file with Jira base URL, email, project key.
- `requirements.txt` — Python dependencies used by the project.

## Requirements

- Python 3.10+ recommended
- The project uses a virtual environment. Install dependencies from `requirements.txt`.

## Configuration

1. `config.yml` (checked into repo) contains basic values read at runtime. Example:

```yaml
JIRA_BASE_URL: https://your-domain.atlassian.net
JIRA_EMAIL: you@example.com
JIRA_PROJECT_KEY: KAN
```

2. Environment variables (recommended to set in a `.env` file or system env):

- `JIRA_API_TOKEN` — API token for Jira (used with `JIRA_EMAIL` for basic auth).
- `SLACK_BOT_TOKEN` — Slack bot token used by `slack_sdk`.
- `OPENAI_API_KEY` — (optional) API key used by `llm.py` if you enable classification.

Place a `.env` file in the project root with entries like:

```
JIRA_API_TOKEN=your_jira_api_token
SLACK_BOT_TOKEN=xoxb-...
OPENAI_API_KEY=sk-...
```

Notes:

- `config.yml` is read by `jira.py`. Update `JIRA_BASE_URL` and `JIRA_EMAIL` there. 
- Project-specific IDs (project id / issue type id) are currently passed to `create_jira_issue` from `main.py` as numeric strings (e.g. `"10000"`), so you'll need to either find those numeric ids in your Jira instance or modify the helper to use `project key` instead.

## Running locally

1. Create and activate a virtual environment (example):

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

2. Create the SQLite database and tables (the app calls `init_db()` on startup, but you can also run a short script to ensure the DB exists):

```bash
python -c "from db import init_db; init_db()"
```

3. Run the app:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

4. Expose the server to Slack for development (optional) using `ngrok` or similar so Slack can reach your `/slack/command` and `/slack/actions` endpoints.

## Slack commands and interactive flows

- `/ticket <summary>` — Creates a Jira issue with the provided summary and description.
  - `main.py` currently uses `create_jira_issue(summary, description, project_id, issue_type_id)` and then stores the returned Jira `key` in the local DB as both `ticket_id` and `jira_issue_key`.
  - After creation the service posts an approval block to the same Slack channel.

- `/ticket_status <ISSUE-KEY>` — Fetches the status of the Jira issue using `get_jira_status` and posts the status back to the channel.

- Interactive approval buttons — When a user presses Approve/Reject the `/slack/actions` endpoint updates the `status` column in the `ticket_logs` table.

Note: Slack interactive payloads are handled for both `application/json` and form-encoded `payload` strings.

## Jira integration details & important behaviour

- `jira.py` authenticates using Basic auth with Jira email and API token. It builds requests against `{JIRA_BASE_URL}/rest/api/3/...`.
- When creating issues it constructs an Atlassian Document Format (ADF) description and posts a JSON payload to create an issue.
- Jira's issue key numbering is controlled by Jira. Deleting issues does not reuse their numeric sequence. If you delete issues in a project you will see gaps in numbers; new issues will continue increasing. This behavior is standard for Jira Cloud/Server.

IDs vs Keys:

- The project currently passes numeric `project_id` and `issue_type_id` into `create_jira_issue`. Many Jira APIs accept `project.key` (string) as well as numeric ids. If you prefer to use project key, modify `build_jira_payload` in `jira.py` to include `"project": {"key": "KAN"}` instead of `{"id": project_id}`.

Resetting numbering:

- To start issue numbering from 1 you must create a new project (different key) inside the same Jira instance — its numbering starts at 1. Recreating an existing project key or resetting numbering requires destructive operations or DB-level changes and is not recommended.

## Database

- SQLite file: `tickets.db` (created in repo root by SQLAlchemy engine). The `db.py` uses `sqlite:///tickets.db`.
- Table: `ticket_logs` defined in `models.py`:

  - id (PK)
  - slack_user
  - slack_channel
  - ticket_id (stored Jira key)
  - jira_issue_key
  - llm_result
  - status
  - created_at

The app writes a `TicketLog` entry whenever a ticket is successfully created.

## Troubleshooting & tips

- Slack "channel_not_found" or "not_in_channel": ensure the bot is invited to the channel and `SLACK_BOT_TOKEN` has scopes `chat:write`, `channels:read`, `conversations:open` as needed.
- Jira create failing: `jira.py` prints the payload and the Jira response. Look for `status` and response `json` printed to the logs. Ensure `JIRA_API_TOKEN` and `JIRA_EMAIL` are correct and the account has permission to create issues in the target project.
- Invalid blocks in Slack: `slack.py` logs block payloads and types. Use these logs to debug malformed block structures.
- If Jira returns HTML or non-JSON on error `jira.py` will print raw response bytes to help debugging.

## Development notes & recommended changes

- Make `project_id` / `issue_type_id` configurable (via `config.yml` or `.env`) rather than hard-coded values in `main.py`.
- Consider switching to `project.key` in payloads to avoid having to discover numeric project IDs.
- Add unit tests for `jira.py` and `slack.py` helpers. Use mocking for network calls (e.g., `respx` or `pytest-httpx`).
- Add validation and error handling around Slack form parsing in `main.py` (guard against missing fields).

## Quick checklist to adapt or reconfigure the project

1. Update `config.yml` with your `JIRA_BASE_URL` and `JIRA_EMAIL`.
2. Set environment variables in `.env` (`JIRA_API_TOKEN`, `SLACK_BOT_TOKEN`, optional `OPENAI_API_KEY`).
3. Ensure the Slack app has the correct request url set to your ngrok/public URL.
4. Start the app(FastAPI) with `uvicorn main:app` and test `/ticket` and `/ticket_status` from Slack.

