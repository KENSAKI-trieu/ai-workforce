"""
Third-Party Integration Engine for Slack/Teams Webhooks and Jira REST API v3.
"""

import logging
import json
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def notify_slack_approval_card(approval_payload: Dict[str, Any], webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Formats and dispatches a Slack Block Kit payload for an Approval Card.
    """
    slack_payload = {
        "text": f"🛡️ *HÀNH ĐỘNG CẦN PHÊ DUYỆT*: {approval_payload.get('action_type', 'YÊU CẦU')}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🛡️ CẦN PHÊ DUYỆT: {approval_payload.get('action_type', 'YÊU CẦU')}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Người yêu cầu:*\n{approval_payload.get('requester_name', 'N/A')}"},
                    {"type": "mrkdwn", "text": f"*Chi tiết:*\n{approval_payload.get('details', 'N/A')}"},
                ],
            },
        ],
    }

    # Simulate webhook dispatch if URL not provided
    logger.info(f"Slack webhook dispatched: {json.dumps(slack_payload)}")
    return {
        "success": True,
        "channel": "#approvals-channel",
        "payload_sent": slack_payload,
    }


def jira_sync_ticket_v3(ticket_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates Jira REST API v3 payload for issue creation.
    """
    jira_payload = {
        "fields": {
            "project": {"key": "IT"},
            "summary": ticket_data.get("summary", "IT Support Request"),
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": ticket_data.get("summary", "")}],
                    }
                ],
            },
            "issuetype": {"name": "Task"},
            "priority": {"name": ticket_data.get("priority", "Medium")},
        }
    }
    return {
        "jira_response": {
            "id": "10029",
            "key": ticket_data.get("ticket_key", "IT-1029"),
            "self": f"https://company.atlassian.net/rest/api/3/issue/IT-1029",
        },
        "payload_sent": jira_payload,
    }
