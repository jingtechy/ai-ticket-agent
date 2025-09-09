import requests
from fastapi import FastAPI, Request, Form, Body
from db import init_db, SessionLocal
from llm import classify_ticket
from jira import create_jira_issue, get_jira_status, JIRA_PROJECT_KEY
from slack import send_message, build_approval_block
from models import TicketLog
import uvicorn
import asyncio
import yaml
import json
import os


app = FastAPI()
init_db()

@app.post("/slack/command")
async def slack_command(request: Request):
    form = await request.form()
    command = form.get("command")
    text = form.get("text")
    user_id = form.get("user_id")
    channel_id = form.get("channel_id")
    # Slack provides a response_url for delayed responses if needed
    response_url = form.get("response_url")

    db = SessionLocal()

    if command == "/ticket":
        # Acknowledge immediately to Slack to avoid operation_timeout.
        # Do the heavy work (LLM classification, Jira creation, posting messages) in a background task.
        print(f"Received /ticket from user {user_id} in channel {channel_id}; scheduling background job")

        async def _background_handle_ticket(text, user_id, channel_id, response_url):
            db_bg = SessionLocal()
            try:
                # Run LLM classification (async). Fall back to "Task" on error or empty text.
                try:
                    category = await classify_ticket(text) if text else "Task"
                except Exception as e:
                    print("LLM classification failed:", e)
                    category = "Task"

                # Map LLM label to Jira issue type name in your project
                label_to_issue_type = {
                    "Task": "Task",
                    "Bug": "Bug",
                    "Incident": "Incident",
                    "Feature Request": "Task",
                    "Question": "Question",
                }
                issue_type_name = label_to_issue_type.get(category, "Task")

                # Use configured project key from jira.py (JIRA_PROJECT_KEY)
                jira_key = await create_jira_issue(
                    summary=text,
                    description=text,
                    project_id=JIRA_PROJECT_KEY,
                    issue_type_id=issue_type_name,
                )
                if jira_key:
                    log = TicketLog(
                        slack_user=user_id,
                        slack_channel=channel_id,
                        ticket_id=jira_key,
                        jira_issue_key=jira_key,
                        llm_result=category,
                        status="created"
                    )
                    db_bg.add(log)
                    db_bg.commit()
                    # Post the approval block to the channel
                    try:
                        await send_message(channel_id, f"Ticket has been created: {jira_key}", blocks=build_approval_block(jira_key), fallback_user=user_id)
                    except Exception as e:
                        print("Failed to send Slack message after creating Jira ticket:", e)
                else:
                    print("Jira ticket creation failed. Check logs for details.")
                    try:
                        await send_message(channel_id, "Failed to create Jira ticket. Please check server logs.", fallback_user=user_id)
                    except Exception as e:
                        print("Failed to send failure message to Slack:", e)
            finally:
                db_bg.close()

        # schedule background work and immediately ack Slack
        asyncio.create_task(_background_handle_ticket(text, user_id, channel_id, response_url))
        # Close the short-lived request DB session and return an ephemeral acknowledgement
        db.close()
        return {"response_type": "ephemeral", "text": "Processing your ticket — I will post an update in the channel when ready."}

    elif command == "/ticket_status":
        jira_key = (text or "").strip()
        if not jira_key:
            db.close()
            return {"response_type": "ephemeral", "text": "Please provide a ticket key, e.g. /ticket_status KAN-1"}
        status = await get_jira_status(jira_key)
        db.close()
        # Return an ephemeral message so only the invoking user sees the status
        return {"response_type": "ephemeral", "text": f"Ticket {jira_key} Status: {status}"}

    # unknown command
    db.close()
    return {"text": "unknown command"}


@app.post("/slack/actions")
async def slack_actions(request: Request):
    # Slack sends interactive actions as application/x-www-form-urlencoded
    # with a `payload` field containing a JSON string. Support both forms.
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload_str = form.get("payload")
        if not payload_str:
            # fallback: try raw body
            try:
                payload = await request.json()
            except Exception:
                return {"text": "invalid payload"}
        else:
            payload = json.loads(payload_str)

    action_id = payload["actions"][0]["action_id"]
    ticket_id = payload["actions"][0].get("value")
    # channel can be under 'channel' or 'container'
    channel_id = None
    if payload.get("channel") and payload["channel"].get("id"):
        channel_id = payload["channel"]["id"]
    elif payload.get("container") and payload["container"].get("channel_id"):
        channel_id = payload["container"]["channel_id"]
    else:
        channel_id = payload.get("channel_id")

    db = SessionLocal()
    log = db.query(TicketLog).filter(TicketLog.ticket_id == ticket_id).first()
    if not log:
        await send_message(channel_id, f"Ticket {ticket_id} not found in the database.")
        db.close()
        return {"text": "ticket not found"}

    if action_id == "approve_ticket":
        log.status = "approved"
        await send_message(channel_id, f"Ticket {ticket_id} has been approved")
    elif action_id == "reject_ticket":
        log.status = "rejected"
        await send_message(channel_id, f"Ticket {ticket_id} has been rejected")
    db.commit()
    db.close()
    return {"text": "completed"}


@app.post("/slack/events")
async def slack_events(request: Request):
    payload = await request.json()
    # Slack URL verification
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    # ...handle other event types if needed...
    return {}

@app.get("/")
def read_root():
    return {"message": "AI Ticket Agent is running."}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
