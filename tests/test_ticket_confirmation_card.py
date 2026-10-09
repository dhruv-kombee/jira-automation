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

    def test_solution1_reactions_removed_and_no_auto_execution(self):
        """Solution 1: Verify reaction text is completely removed and no buttons have auto=1."""
        target_tls = [{"display_name": "Nishi Sharma", "user_id": "tl-nishi"}]
        card = build_pending_approval_card(
            message_id="msg-solution1-check",
            issues=[{"summary": "Test issue", "suggested_assignee": "Santosh Yadav"}],
            reporter="Dhruv dobariya",
            raw_message="Need fix",
            created_at="2026-10-09T10:00:00Z",
            target_users=target_tls,
            target_role="TL",
            base_url="http://localhost:3000",
        )
        card_str = json.dumps(card)

        # 1. No reaction prompt in card
        self.assertNotIn("react 👍", card_str.lower())
        self.assertNotIn("react", card_str.lower())
        self.assertNotIn("❌ to decline", card_str.lower())
        self.assertIn("Please review and confirm ticket creation using the buttons below:", card_str)

        # 2. No auto=1 in any URL (prevents unauthenticated 1-click execution)
        self.assertNotIn("auto=1", card_str)

    def test_solution1_authorized_approver_resolution(self):
        """Solution 1: Verify resolve_and_verify_approver strictly allows TL/PM/HM and blocks Client/Dev/Unassigned."""
        from src.routes.dashboard import resolve_and_verify_approver

        # Authorized management roles
        pm_member = resolve_and_verify_approver("Musaib Khan")
        self.assertIsNotNone(pm_member)
        self.assertEqual(pm_member.get("role"), "PM")

        hm_member = resolve_and_verify_approver("Hemil Ghori")
        self.assertIsNotNone(hm_member)
        self.assertEqual(hm_member.get("role"), "HM")

        tl_member = resolve_and_verify_approver("Nishi Sharma")
        self.assertIsNotNone(tl_member)
        self.assertEqual(tl_member.get("role"), "TL")

        # Unauthorized roles
        # Santosh is DEVELOPER (can_approve: False)
        dev_member = resolve_and_verify_approver("Santosh Yadav")
        self.assertIsNone(dev_member)

        # Dhruv dobariya is UNASSIGNED / CLIENT (can_approve: False)
        client_member = resolve_and_verify_approver("Dhruv dobariya")
        self.assertIsNone(client_member)

        # Non-existent member
        unknown_member = resolve_and_verify_approver("Random Person")
        self.assertIsNone(unknown_member)

        # None / Empty
        self.assertIsNone(resolve_and_verify_approver(None))
        self.assertIsNone(resolve_and_verify_approver(""))

    def test_web_assignee_modal_has_role_authority_verification(self):
        """Verify that web modal checks role authority and displays warning for Client / Developer."""
        from src.routes.dashboard import render_assignee_dropdown_html

        # 1. When default reviewer is Client (Dhruv dobariya) -> No Authority banner and disabled button
        resp_client = render_assignee_dropdown_html(
            message_id="msg-101",
            summary="Bug summary",
            project_key="SCRUM",
            suggested_assignee="Santosh Yadav",
            members=[{"name": "Santosh Yadav", "specialty": "Dev"}],
            approver="PM Musaib Khan",
            default_reviewer="Dhruv dobariya",
        )
        html_client = resp_client.body.decode("utf-8")
        self.assertIn('name="assignee"', html_client)
        self.assertIn('name="reviewer"', html_client)
        self.assertIn("No Authority to Confirm", html_client)
        self.assertIn("btn-disabled", html_client)

        # 2. When default reviewer is PM (Musaib Khan) -> Authorized banner and enabled button
        resp_pm = render_assignee_dropdown_html(
            message_id="msg-102",
            summary="Bug summary",
            project_key="SCRUM",
            suggested_assignee="Santosh Yadav",
            members=[{"name": "Santosh Yadav", "specialty": "Dev"}],
            approver="PM Musaib Khan",
            default_reviewer="Musaib Khan",
        )
        html_pm = resp_pm.body.decode("utf-8")
        self.assertIn("Authorized Management Reviewer", html_pm)
        self.assertNotIn('class="btn-approve btn-disabled"', html_pm)
        self.assertNotIn('disabled', html_pm.split('<button')[1].split('</button>')[0])

    def test_web_endpoint_role_authority_enforcement(self):
        """Verify web approval and decline endpoints strictly require authorized management role (TL/PM/HM)."""
        from fastapi.testclient import TestClient
        from unittest.mock import patch, AsyncMock
        from src.app import app

        client = TestClient(app)

        # 1. POST confirm-approval with Client reviewer -> 403 Forbidden
        res_client = client.post(
            "/api/jira/confirm-approval/test-msg-flow",
            data={"assignee": "Santosh Yadav", "reviewer": "Dhruv dobariya"}
        )
        self.assertEqual(res_client.status_code, 403)
        self.assertIn("No Authority to Confirm", res_client.text)

        # 2. POST confirm-approval with Developer reviewer -> 403 Forbidden
        res_dev = client.post(
            "/api/jira/confirm-approval/test-msg-flow",
            data={"assignee": "Santosh Yadav", "reviewer": "Santosh Yadav"}
        )
        self.assertEqual(res_dev.status_code, 403)
        self.assertIn("No Authority to Confirm", res_dev.text)

        # 3. POST confirm-approval without reviewer -> 403 Forbidden
        res_none = client.post(
            "/api/jira/confirm-approval/test-msg-flow",
            data={"assignee": "Santosh Yadav"}
        )
        self.assertEqual(res_none.status_code, 403)
        self.assertIn("No Authority to Confirm", res_none.text)

        # 4. POST confirm-approval with authorized PM -> 200 OK
        with patch("src.services.message_service.execute_jira_ticket_creation", new=AsyncMock(return_value={"success": True, "key": "SCRUM-202"})) as mock_create:
            res_pm = client.post(
                "/api/jira/confirm-approval/test-msg-flow",
                data={"assignee": "Santosh Yadav", "reviewer": "Musaib Khan"}
            )
            self.assertEqual(res_pm.status_code, 200)
            mock_create.assert_called_once()
            self.assertIn("PM Musaib", mock_create.call_args[1].get("approver_name"))

        # 5. POST decline-approval with Client reviewer -> 403 Forbidden
        res_dec_client = client.post(
            "/api/jira/decline-approval/test-msg-flow",
            data={"reviewer": "Dhruv dobariya"}
        )
        self.assertEqual(res_dec_client.status_code, 403)
        self.assertIn("No Authority to Reject", res_dec_client.text)

        # 6. POST decline-approval with authorized PM -> 200 OK
        with patch("src.services.message_service.execute_jira_ticket_decline", new=AsyncMock(return_value={"success": True, "status": "DECLINED"})) as mock_decline:
            res_dec_pm = client.post(
                "/api/jira/decline-approval/test-msg-flow",
                data={"reviewer": "Musaib Khan"}
            )
            self.assertEqual(res_dec_pm.status_code, 200)
            mock_decline.assert_called_once()
            self.assertIn("Ticket Creation Rejected", res_dec_pm.text)

        # 3. Chat authorization check: Client and Developer reactions are blocked, only TL/PM/HM can approve
        from src.services.sender_service import is_user_authorized_approver
        self.assertFalse(is_user_authorized_approver(display_name="Dhruv dobariya"))  # Client / Unassigned -> False
        self.assertFalse(is_user_authorized_approver(display_name="Santosh Yadav"))   # Developer -> False
        self.assertTrue(is_user_authorized_approver(display_name="Musaib Khan"))     # PM -> True
        self.assertTrue(is_user_authorized_approver(display_name="Nishi Sharma"))    # TL -> True
        self.assertTrue(is_user_authorized_approver(display_name="Hemil Ghori"))     # HM -> True


