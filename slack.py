import os
from dotenv import load_dotenv
import yaml
import json
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

load_dotenv()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

slack_client = AsyncWebClient(token=SLACK_BOT_TOKEN)

async def send_message(channel: str, text: str, blocks=None, fallback_user: str = None):
    try:
        # Log the blocks payload for debugging
        if blocks:
            print("Slack Blocks Payload:", json.dumps(blocks, indent=2))
            # Print types of value fields to help debug invalid_blocks
            for i, block in enumerate(blocks):
                if isinstance(block, dict) and block.get("elements"):
                    for j, el in enumerate(block.get("elements", [])):
                        val = el.get("value")
                        print(f"blocks[{i}].elements[{j}].value=", repr(val), "type=", type(val))

        # If channel looks like a DM (starts with 'D') prefer opening a conversation by user id
        # when a fallback_user is provided. This avoids channel_not_found when Slack reports a DM id
        # that the bot can't post to directly.
        if channel and isinstance(channel, str) and channel.startswith("D") and fallback_user:
            try:
                conv = await slack_client.conversations_open(users=fallback_user)
                conv_channel = conv.get("channel", {}).get("id")
                if conv_channel:
                    channel = conv_channel
            except Exception as e:
                print("Failed to open IM for DM channel fallback:", e)

        response = await slack_client.chat_postMessage(
            channel=channel,
            text=text,
            blocks=blocks
        )
        if not response["ok"]:
            print(f"Slack API returned ok: False")
            print("Response:", json.dumps(response.data, indent=2))
            if response.data.get("error") == "channel_not_found":
                print("Error: The specified channel was not found. Ensure the bot is invited to the channel.")

    except SlackApiError as e:
        print("Slack send_message error (SlackApiError):", e)
        print("Error details:", e.response.data)
        err = e.response.data.get("error")
        if err == "not_in_channel":
            print("Hint: Invite your bot to the channel with /invite @your-bot-name")
        elif err == "channel_not_found":
            print("Error: The specified channel was not found. Ensure the bot is invited to the channel.")
            # Try to DM the user as a fallback if we have their user id
            if fallback_user:
                try:
                    print(f"Attempting to open IM with user {fallback_user} as fallback...")
                    conv = await slack_client.conversations_open(users=fallback_user)
                    conv_channel = conv.get("channel", {}).get("id")
                    if conv_channel:
                        print(f"Opened IM channel {conv_channel}, sending message there")
                        await slack_client.chat_postMessage(channel=conv_channel, text=text, blocks=blocks)
                except Exception as ex:
                    print("Failed to send fallback DM:", ex)

    except Exception as e:
        print(f"Slack send_message error (Generic Exception): {e}")

def build_approval_block(jira_key: str) -> list:
    safe_key = str(jira_key) if jira_key else "UNKNOWN"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Jira ticket *{safe_key}* has been created. Do you want to approve or reject it?"
            }
        },
        {
            "type": "actions",
            "block_id": f"actions_{safe_key}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    # value should carry the jira key so the actions handler can find the ticket
                    "value": safe_key,
                    "action_id": "approve_ticket"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "value": safe_key,
                    "action_id": "reject_ticket"
                }
            ]
        }
    ]
    
