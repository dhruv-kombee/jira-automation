import unittest
from src.services.teams_notifier import build_pending_approval_card

class TestTwoIssueFlow(unittest.TestCase):
    def test_two_issue_card_actions(self):
        issues = [
            {"summary": "Fix password reset 404", "suggested_assignee": "Santosh Yadav"},
            {"summary": "Profile pic upload freeze", "suggested_assignee": "Musaib Khan"},
        ]
        card = build_pending_approval_card(
            "msg-test-123",
            issues,
            "Client",
            "test msg",
            "2026-10-07T12:00:00Z",
            base_url="http://localhost:3000",
        )
        actions = card["attachments"][0]["content"]["actions"]
        self.assertEqual(len(actions), 6)
        
        # Verify 6 root actions
        self.assertIn("Approve #1", actions[0]["title"])
        self.assertEqual(actions[0]["type"], "Action.ShowCard")
        
        self.assertEqual(actions[1]["title"], "❌ Reject #1")
        self.assertEqual(actions[1]["type"], "Action.OpenUrl")
        self.assertIn("/api/jira/decline-issue/msg-test-123/0", actions[1]["url"])
        
        self.assertIn("Approve #2", actions[2]["title"])
        self.assertEqual(actions[2]["type"], "Action.ShowCard")
        
        self.assertEqual(actions[3]["title"], "❌ Reject #2")
        self.assertEqual(actions[3]["type"], "Action.OpenUrl")
        self.assertIn("/api/jira/decline-issue/msg-test-123/1", actions[3]["url"])
        
        self.assertIn("Approve All (2)", actions[4]["title"])
        self.assertEqual(actions[4]["type"], "Action.ShowCard")
        
        self.assertEqual(actions[5]["title"], "❌ Reject All (2)")
        self.assertEqual(actions[5]["type"], "Action.OpenUrl")
        self.assertIn("/api/jira/decline-approval/msg-test-123", actions[5]["url"])
        
        # Subactions for Issue #1
        sub1 = actions[0]["card"]["actions"]
        titles1 = [s["title"] for s in sub1]
        self.assertTrue(any("Approve (Santosh)" in t for t in titles1))
        self.assertTrue(any("Assignee Dropdown (Web)" in t for t in titles1))
        self.assertTrue(any("Reject Issue #1 Only" in t for t in titles1))

        # Subactions for Issue #2
        sub2 = actions[2]["card"]["actions"]
        titles2 = [s["title"] for s in sub2]
        self.assertTrue(any("Approve (Musaib)" in t for t in titles2))
        self.assertTrue(any("Assignee Dropdown (Web)" in t for t in titles2))
        self.assertTrue(any("Reject Issue #2 Only" in t for t in titles2))

    def test_state_machine_isolation(self):
        import asyncio
        from unittest.mock import patch, AsyncMock
        from src.database import get_db
        from src.services.message_service import (
            execute_jira_ticket_creation,
            execute_jira_ticket_decline,
        )
        import json

        async def run_async_test():
            db = get_db()
            test_msg_id = "test-concurrency-msg-999"
            
            # Setup test message in DB
            sample_ai_ticket = {
                "summary": "Two bugs reported",
                "issues": [
                    {
                        "summary": "Issue 1: Auth bug",
                        "suggested_assignee": "Santosh Yadav",
                        "status": "PENDING",
                    },
                    {
                        "summary": "Issue 2: Upload freeze",
                        "suggested_assignee": "Musaib Khan",
                        "status": "PENDING",
                    },
                ]
            }
            db.execute("DELETE FROM messages WHERE message_id = ?", (test_msg_id,))
            db.execute(
                """INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, ai_ticket, confirmation_status, chat_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    test_msg_id,
                    "user-1",
                    "Client",
                    "We have two bugs",
                    json.dumps(sample_ai_ticket),
                    "CONFIRMATION_PENDING",
                    "chat-1",
                ),
            )

            # Mock create_jira_issue and send_ticket_created_notification
            with patch("src.services.jira_service.create_jira_issue", new=AsyncMock(return_value={"success": True, "key": "SCRUM-991", "url": "https://test.jira/SCRUM-991", "id": "10991"})):
                with patch("src.services.teams_notifier.send_ticket_created_notification", new=AsyncMock(return_value={"success": True})):
                    with patch("src.services.teams_notifier.send_ticket_declined_notification", new=AsyncMock(return_value={"success": True})):
                        # Step 1: Approve Issue #0
                        res1 = await execute_jira_ticket_creation(test_msg_id, approver_name="PM Hemil", issue_idx=0)
                        self.assertTrue(res1.get("success"))
                        self.assertEqual(res1.get("key"), "SCRUM-991")

                        # Step 2: Decline Issue #1
                        res2 = await execute_jira_ticket_decline(test_msg_id, approver_name="PM Hemil", issue_idx=1)
                        self.assertTrue(res2.get("success"))

                        # Step 3: Now attempt to decline Issue #0 -> MUST FAIL!
                        res3 = await execute_jira_ticket_decline(test_msg_id, approver_name="PM Hemil", issue_idx=0)
                        self.assertFalse(res3.get("success"), "Rejecting an already approved ticket MUST NOT succeed")
                        self.assertIn("already active in Jira", res3.get("error", ""))

                        # Step 4: Now attempt to approve Issue #1 -> MUST FAIL!
                        res4 = await execute_jira_ticket_creation(test_msg_id, approver_name="PM Hemil", issue_idx=1)
                        self.assertFalse(res4.get("success"), "Approving an already declined ticket MUST NOT succeed")
                        self.assertIn("already declined", res4.get("error", ""))

                        # Step 5: Now attempt bulk decline -> MUST NOT overwrite Issue #0
                        res5 = await execute_jira_ticket_decline(test_msg_id, approver_name="PM Hemil")
                        # Check database state to ensure Issue #0 is still active
                        row = db.execute("SELECT ai_ticket, confirmation_status FROM messages WHERE message_id = ?", (test_msg_id,)).fetchone()
                        saved_ticket = json.loads(row["ai_ticket"])
                        self.assertEqual(saved_ticket["issues"][0]["status"], "APPROVED")
                        self.assertEqual(saved_ticket["issues"][0]["jira_key"], "SCRUM-991")
                        self.assertEqual(saved_ticket["issues"][1]["status"], "DECLINED")

            # Clean up test row
            db.execute("DELETE FROM messages WHERE message_id = ?", (test_msg_id,))

        asyncio.run(run_async_test())

if __name__ == "__main__":
    unittest.main()

