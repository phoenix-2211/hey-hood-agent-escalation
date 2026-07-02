from __future__ import annotations

import os
import sys
from datetime import datetime

from firebase_admin import firestore
from google.adk.agents.context import Context
from google.adk.apps.app import App
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.workflow import Edge, Workflow, node

current_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.abspath(os.path.join(current_dir, "..", "shared"))
if not os.path.exists(os.path.join(shared_dir, "firestore_client.py")):
    shared_dir = os.path.abspath(os.path.join(current_dir, "..", "..", "shared"))
sys.path.append(shared_dir)
import firestore_client  # noqa: E402

ESCALATION_CHAIN = {
    "Ward Councillor": "Zone Officer",
    "Zone Officer": "MLA",
    "MLA": "District Collector",
    "District Collector": "State Department",
}


@node
def scan_overdue_issues(ctx: Context, node_input: dict):
    """Scans all issues past their deadline."""
    db = firestore_client.get_db()
    now = datetime.utcnow().isoformat()

    overdue = (
        db.collection("issues")
        .where("status", "not-in", ["Resolved", "Closed"])
        .stream()
    )

    overdue_list = []
    for doc in overdue:
        data = doc.to_dict()
        deadline = data.get("resolution_deadline", "")
        if deadline and deadline < now:
            overdue_list.append({"issue_id": doc.id, **data})

    ctx.state["overdue_issues"] = overdue_list

    if not overdue_list:
        yield Event(output={"status": "no_overdue_issues"})
        return

    yield Event(
        output={"overdue_count": len(overdue_list)},
        actions=EventActions(route="process_escalations"),
    )


@node
def process_escalations(ctx: Context, node_input: dict):
    """Escalates each overdue issue up the chain."""
    db = firestore_client.get_db()
    overdue_list = ctx.state.get("overdue_issues", [])

    for issue in overdue_list:
        issue_id = issue.get("issue_id")
        current_role = issue.get("assigned_role", "Ward Councillor")
        current_official = issue.get("assigned_to", "")

        next_role = ESCALATION_CHAIN.get(current_role)

        if not next_role:
            continue

        # Decrement accountability score
        if current_official:
            db.collection("officials").document(current_official).update(
                {"accountability_score": firestore.Increment(-5)}
            )

        # Update issue status
        db.collection("issues").document(issue_id).update(
            {
                "assigned_role": next_role,
                "status": "Escalated",
                "timeline": firestore.ArrayUnion(
                    [
                        {
                            "status": f"Escalated to {next_role}",
                            "timestamp": datetime.utcnow().isoformat(),
                            "reason": "Deadline missed",
                        }
                    ]
                ),
            }
        )

        # Create escalation alert
        db.collection("alerts").add(
            {
                "type": "Issue Escalated",
                "title": f"Issue Escalated to {next_role}",
                "description": f"Issue {issue_id} was unresolved and escalated.",
                "issue_id": issue_id,
                "read": False,
                "created_at": datetime.utcnow().isoformat(),
            }
        )

    yield Event(
        output={"status": "escalations_complete", "escalated_count": len(overdue_list)}
    )


root_agent = Workflow(
    name="escalation_workflow",
    edges=[
        ("START", scan_overdue_issues),
        Edge(
            from_node=scan_overdue_issues,
            to_node=process_escalations,
            route="process_escalations",
        ),
    ],
)

app = App(
    name="escalation_agent",
    root_agent=root_agent,
)
