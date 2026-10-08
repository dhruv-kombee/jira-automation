import unittest
import json
from src.services.teams_notifier import build_pending_approval_card

class TestTicketConfirmationCard(unittest.TestCase):
    def test_single_issue_card_fields_and_actions(self):
        issues = [
            {
                "summary": "[Authentication] OTP verification submit button stuck in infinite loading state",
                "suggested_assignee": "Hemil Ghori",
            }
        ]
        card = build_pending_approval_card(
            message_id="msg-otp-test-1",
            issues=issues,
            reporter="Hemil Ghori",
            raw_message="OTP verification stuck in infinite loading",
            created_at="2026-10-08T10:00:00Z",
            base_url="http://localhost:3000",
            extractor_mode="gemini (gemini-3.5-flash-lite)",
        )

        card_json = json.dumps(card)
        attachment_content = card["attachments"][0]["content"]
        body = attachment_content["body"]
        actions = attachment_content["actions"]

        # 1. Header is "Ticket Confirmation" and NOT "📋 Issue Approval Required"
        self.assertIn("Ticket Confirmation", card_json)
        self.assertNotIn("📋 Issue Approval Required", card_json)
        header_block = body[0]["items"][0]
        self.assertEqual(header_block["text"], "Ticket Confirmation")

        # 2. Extractor is NOT present
        self.assertNotIn("🤖 Extractor", card_json)
        self.assertNotIn("gemini-3.5-flash-lite", card_json)

        # 3. React prompt is NOT present
        self.assertNotIn("React 👍 to Approve or select assignee below:", card_json)
        self.assertNotIn("React 👍", card_json)

        # 4. Facts strictly contain Topic Details, Project, Reporter Name, and NOT Assignee
        factset = next(item for item in body if item.get("type") == "FactSet")
        fact_titles = [f["title"] for f in factset["facts"]]
        self.assertEqual(fact_titles, ["Topic Details:", "Project:", "Reporter Name:"])
        self.assertEqual(factset["facts"][0]["value"], "[Authentication] OTP verification submit button stuck in infinite loading state")
        self.assertEqual(factset["facts"][1]["value"], "SCRUM")
        self.assertEqual(factset["facts"][2]["value"], "Hemil Ghori")

        self.assertNotIn("Assignee:", fact_titles)
        self.assertNotIn("Assignee", [f["title"] for f in factset["facts"]])

        # 5. Buttons have Approve with member dropdown and ❌ Reject
        self.assertEqual(len(actions), 2)
        self.assertIn("Approve (Hemil)", actions[0]["title"])
        self.assertEqual(actions[0]["type"], "Action.ShowCard")
        subactions = actions[0]["card"]["actions"]
        subaction_titles = [s["title"] for s in subactions]
        self.assertTrue(any("Approve (Hemil)" in t for t in subaction_titles))
        self.assertTrue(any("Assignee Dropdown (Web)" in t for t in subaction_titles))
        self.assertTrue(any("Assign" in t for t in subaction_titles))

        self.assertEqual(actions[1]["title"], "❌ Reject")
        self.assertEqual(actions[1]["type"], "Action.OpenUrl")
        self.assertIn("/api/jira/decline-approval/msg-otp-test-1", actions[1]["url"])

    def test_multi_issue_card_header_and_facts(self):
        issues = [
            {"summary": "Issue 1", "suggested_assignee": "Dev A"},
            {"summary": "Issue 2", "suggested_assignee": "Dev B"},
        ]
        card = build_pending_approval_card(
            message_id="msg-multi-test",
            issues=issues,
            reporter="Santosh Yadav",
            raw_message="Multiple bugs",
            created_at="2026-10-08T10:00:00Z",
            base_url="http://localhost:3000",
        )
        body = card["attachments"][0]["content"]["body"]
        header_block = body[0]["items"][0]
        self.assertEqual(header_block["text"], "Ticket Confirmation (2 Issues)")

        factset = next(item for item in body if item.get("type") == "FactSet")
        fact_titles = [f["title"] for f in factset["facts"]]
        self.assertEqual(fact_titles, ["Topic Details #1:", "Topic Details #2:", "Project:", "Reporter Name:"])
        self.assertNotIn("Assignee", str(fact_titles))

    def test_rejection_card_fields(self):
        from src.services.teams_notifier import build_declined_card_payload
        issues = [
            {"summary": "[Authentication] Password reset link redirects to 404 page"}
        ]
        card = build_declined_card_payload(
            message_id="msg-reject-test",
            issues=issues,
            reporter="Dhruv dobariya",
            approver="PM Hemil Ghori",
            reason="Rejected by PM: Issue #1",
        )
        card_json = json.dumps(card)
        body = card["attachments"][0]["content"]["body"]

        # 1. Header has "Ticket Creation Rejected" and timestamp
        top_container = body[0]
        self.assertEqual(len(top_container["items"]), 2)
        self.assertEqual(top_container["items"][0]["text"], "Ticket Creation Rejected")
        self.assertTrue(bool(top_container["items"][1]["text"]))
        self.assertNotIn("rejected creating Jira ticket(s)", card_json)
        self.assertNotIn("Rejected by PM", top_container["items"][0]["text"])

        # 2. FactSet strictly has Rejected By and Reporter
        factset = next(item for item in body if item.get("type") == "FactSet")
        fact_titles = [f["title"] for f in factset["facts"]]
        self.assertEqual(fact_titles, ["Rejected By:", "Reporter:"])
        self.assertEqual(factset["facts"][0]["value"], "Hemil Ghori")
        self.assertEqual(factset["facts"][1]["value"], "Dhruv dobariya")

        # 3. Status and Reason removed
        self.assertNotIn("Status:", fact_titles)
        self.assertNotIn("Reason:", fact_titles)
        self.assertNotIn("Rejected / Not Created in Jira", card_json)

    def test_ticket_created_card_header_and_timestamp(self):
        from src.services.teams_notifier import build_adaptive_card_payload
        card = build_adaptive_card_payload(
            ticket_key="SCRUM-83",
            ticket_url="https://jira/browse/SCRUM-83",
            summary="[Admin Panel] User deletion bug",
            assignee="Santosh Yadav",
            created_at="2026-10-08T06:18:42.367Z",
            status="To Do",
        )
        body = card["attachments"][0]["content"]["body"]
        top_container = body[0]
        self.assertEqual(len(top_container["items"]), 2)
        self.assertEqual(top_container["items"][0]["text"], "🎟️ Ticket Created")
        self.assertTrue(bool(top_container["items"][1]["text"]))

    def test_three_issue_card_actions_dropdown(self):
        issues = [
            {"summary": "Issue 1", "suggested_assignee": "Hemil Ghori"},
            {"summary": "Issue 2", "suggested_assignee": "Musaib Khan"},
            {"summary": "Issue 3", "suggested_assignee": "Santosh Yadav"},
        ]
        card = build_pending_approval_card(
            message_id="msg-three-issues",
            issues=issues,
            reporter="Dhruv dobariya",
            raw_message="3 issues found",
            created_at="2026-10-08T10:00:00Z",
            base_url="http://localhost:3000",
        )
        actions = card["attachments"][0]["content"]["actions"]

        # Approve #1 is ShowCard with member choices
        self.assertEqual(actions[0]["type"], "Action.ShowCard")
        self.assertIn("Approve #1 (Hemil)", actions[0]["title"])
        sub1 = actions[0]["card"]["actions"]
        self.assertTrue(any("Approve (Hemil)" in s["title"] for s in sub1))
        self.assertTrue(any("Assign" in s["title"] for s in sub1))

        # Approve #2 is ShowCard with member choices
        self.assertEqual(actions[2]["type"], "Action.ShowCard")
        self.assertIn("Approve #2 (Musaib)", actions[2]["title"])
        sub2 = actions[2]["card"]["actions"]
        self.assertTrue(any("Approve (Musaib)" in s["title"] for s in sub2))
        self.assertTrue(any("Assign" in s["title"] for s in sub2))

        # More (1) / All is ShowCard
        self.assertEqual(actions[4]["type"], "Action.ShowCard")
        self.assertIn("More (1) / All", actions[4]["title"])
        more_subactions = actions[4]["card"]["actions"]
        more_titles = [s["title"] for s in more_subactions]
        self.assertEqual(len(more_subactions), 2)
        self.assertTrue(any("Approve #3" in t for t in more_titles))
        self.assertTrue(any("Reject #3" in t for t in more_titles))
        self.assertFalse(any("Dropdown #3" in t for t in more_titles))
        self.assertFalse(any("🌐" in t for t in more_titles))
        self.assertFalse(any("Assign All" in t for t in more_titles))


