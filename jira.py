import httpx
import os
from dotenv import load_dotenv
import yaml
import base64
import json

load_dotenv()
with open(os.path.join(os.path.dirname(__file__), "config.yml"), "r") as f:
    config = yaml.safe_load(f)

# Read sensitive values from environment variables to avoid committing them to source control.
JIRA_BASE_URL = config["JIRA_BASE_URL"]
# Prefer the environment variable JIRA_EMAIL; fall back to config.yml for backward compatibility.
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = config.get("JIRA_PROJECT_KEY", "YOURPROJECT")  

def get_jira_auth_header():
    auth_str = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    return {"Authorization": f"Basic {b64_auth}"}


def build_jira_payload(summary, description, project_id, issue_type_id, reporter_id=None):
    adf_description = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": str(description).strip() or "No description"
                    }
                ]
            }
        ]
    }

    fields = {
        "project": {"id": project_id},
        "issuetype": {"id": issue_type_id},
        "summary": summary,
        "description": adf_description,
        "labels": []
    }

    # Only include reporter if provided and doesn't look like a Slack user id (starts with 'U' or 'W')
    if reporter_id and not str(reporter_id).startswith(("U", "W")):
        fields["reporter"] = {"id": reporter_id}

    return {"fields": fields}


async def create_jira_issue(summary: str, description: str, project_id: str, issue_type_id: str, reporter_id: str = None) -> str:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    headers = {**get_jira_auth_header(), "Content-Type": "application/json"}
    payload = build_jira_payload(summary, description, project_id, issue_type_id, reporter_id)

    # Log the payload for debugging
    print("Jira Payload:", json.dumps(payload, indent=2))
    # Also log the raw body string we're sending
    body_text = json.dumps(payload)
    print("Jira Body Text (len={}):".format(len(body_text)))
    print(body_text)

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, content=body_text)
        try:
            data = resp.json()
        except Exception as e:
            print("Failed to parse Jira response as JSON:", await resp.aread())
            return None

        if resp.status_code == 201 and "key" in data:
            return data["key"]
        else:
            print(f"Jira issue creation failed: status={resp.status_code}")
            print("Response:", json.dumps(data, indent=2))
            return None


async def get_jira_status(issue_key: str) -> str:
    if not issue_key or not str(issue_key).strip():
        print("get_jira_status: no issue_key provided")
        return "Unknown"

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    headers = {**get_jira_auth_header(), "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        try:
            print(f"Fetching Jira status for {issue_key} -> {url}")
            resp = await client.get(url, headers=headers)
        except Exception as e:
            print(f"Error while requesting Jira: {e}")
            return "Unknown"

        # Detailed logging for debugging
        print(f"Jira status response: status={resp.status_code}")
        try:
            body = resp.json()
            print("Jira response JSON:", json.dumps(body, indent=2))
        except Exception:
            text = await resp.aread()
            print("Jira response text:", text)

        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            return fields.get("status", {}).get("name", "Unknown")
        else:
            print(f"Failed to fetch Jira status: status={resp.status_code}")
            return "Unknown"
