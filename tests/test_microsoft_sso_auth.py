import sys
sys.path.insert(0, '.')
import json
import unittest
import tempfile
import os
import time
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.app import app
from src.database import init_database, close_database, get_db
from src.routes.auth import (
    create_signed_session_token,
    verify_signed_session_token,
    AUTH_COOKIE_NAME,
    AUTH_REVIEWER_COOKIE,
)


class TestMicrosoftSSOAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.temp_dir.name, "test_sso_auth.db")
        init_database(self.db_file)
        self.client = TestClient(app)

    def tearDown(self):
        close_database()
        self.temp_dir.cleanup()

    def test_signed_session_token_roundtrip(self):
        """Verify HMAC-SHA256 session token creates and validates correctly."""
        payload = {
            "email": "musaib.khan@kombee.com",
            "display_name": "Musaib Khan",
            "role": "PM",
            "user_id": "c5a63f53-cc7a-4c05-ac9a-77e6991bc974",
        }
        token = create_signed_session_token(payload)
        self.assertIsNotNone(token)
        self.assertIn(".", token)

        verified = verify_signed_session_token(token)
        self.assertIsNotNone(verified)
        self.assertEqual(verified["email"], "musaib.khan@kombee.com")
        self.assertEqual(verified["display_name"], "Musaib Khan")
        self.assertEqual(verified["role"], "PM")

    def test_tampered_token_rejected(self):
        """Verify that tampered tokens fail signature validation."""
        payload = {"email": "attacker@evil.com", "display_name": "Attacker", "role": "PM"}
        token = create_signed_session_token(payload)
        parts = token.split(".")
        tampered_token = f"{parts[0]}.badsignature12345"
        self.assertIsNone(verify_signed_session_token(tampered_token))

    def test_expired_token_rejected(self):
        """Verify that expired tokens fail validation."""
        payload = {"email": "test@kombee.com", "display_name": "Old User", "role": "PM", "exp": int(time.time()) - 100}
        token = create_signed_session_token(payload)
        self.assertIsNone(verify_signed_session_token(token))

    def test_auth_status_unauthenticated(self):
        """Verify /auth/status returns authenticated: false when no cookie present."""
        res = self.client.get("/auth/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["authenticated"])
        self.assertIsNone(data["user"])

    def test_auth_status_authenticated(self):
        """Verify /auth/status returns verified manager info when signed cookie present."""
        token = create_signed_session_token({
            "email": "hemil.g.kombee@outlook.com",
            "display_name": "Hemil Ghori",
            "role": "HM",
        })
        self.client.cookies.set(AUTH_COOKIE_NAME, token)
        res = self.client.get("/auth/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["display_name"], "Hemil Ghori")
        self.assertEqual(data["user"]["role"], "HM")

    def test_auth_logout_clears_cookies(self):
        """Verify /auth/logout clears auth cookies and redirects."""
        token = create_signed_session_token({"display_name": "Yash Kalkani", "role": "TL"})
        self.client.cookies.set(AUTH_COOKIE_NAME, token)
        res = self.client.get("/auth/logout", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        # Check that cookies are deleted (empty or max-age 0)
        set_cookies = res.headers.get("set-cookie", "")
        self.assertIn(AUTH_COOKIE_NAME, set_cookies)

    def test_browser_without_auth_redirects_to_login(self):
        """Verify an unauthenticated browser visiting approval gets redirected to /auth/login."""
        db = get_db()
        db.execute('''
            INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'msg-sso-redirect-test',
            'client-1',
            'Dhruv dobariya',
            'App crashes on save',
            'AWAITING_FINAL_CONFIRMATION',
            json.dumps({'summary': 'Crash bug', 'suggested_assignee': 'Santosh Yadav', 'issues': [{'summary': 'Crash bug'}]})
        ))

        # Simulate real browser request (Accept: text/html and real browser User-Agent)
        browser_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }

        with patch("src.config.config.microsoft.tenant_id", "test-tenant-123"), \
             patch("src.config.config.microsoft.client_id", "test-client-123"):
            res = self.client.get(
                "/api/jira/confirm-approval/msg-sso-redirect-test",
                headers=browser_headers,
                follow_redirects=False,
            )
            self.assertEqual(res.status_code, 302)
            self.assertIn("/auth/login?redirect_after=", res.headers.get("location"))

    def test_browser_with_sso_cookie_auto_approves_and_has_autoclose(self):
        """Verify that browser with verified manager SSO cookie approves and includes auto-close script."""
        db = get_db()
        db.execute('''
            INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'msg-sso-success-test',
            'client-1',
            'Dhruv dobariya',
            'Feature request for report export',
            'AWAITING_FINAL_CONFIRMATION',
            json.dumps({'summary': 'Report export', 'suggested_assignee': 'Santosh Yadav', 'issues': [{'summary': 'Report export'}]})
        ))

        token = create_signed_session_token({
            "email": "musaib.khan@kombee.com",
            "display_name": "Musaib Khan",
            "role": "PM",
            "user_id": "c5a63f53-cc7a-4c05-ac9a-77e6991bc974",
        })
        self.client.cookies.set(AUTH_COOKIE_NAME, token)

        browser_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }

        from unittest.mock import AsyncMock
        with patch("src.services.message_service.execute_jira_ticket_creation", new_callable=AsyncMock) as mock_jira:
            mock_jira.return_value = {"success": True, "key": "SCRUM-999", "url": "https://jira.com/SCRUM-999"}

            res = self.client.get(
                "/api/jira/confirm-approval/msg-sso-success-test",
                headers=browser_headers,
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn("SCRUM-999", res.text)
            self.assertIn("Musaib", res.text)
            # Verify auto-close script and countdown element are present
            self.assertIn("autoCloseSec", res.text)
            self.assertIn("window.close()", res.text)

    def test_sso_callback_blocks_developer_or_client(self):
        """Verify that if a developer or client logs into Microsoft, they are blocked with 403."""
        mock_msal_result = {
            "id_token_claims": {
                "preferred_username": "dhruv.d.kombee@outlook.com",
                "name": "Dhruv dobariya",
            }
        }
        with patch("msal.ConfidentialClientApplication.acquire_token_by_authorization_code", return_value=mock_msal_result):
            res = self.client.get("/auth/callback?code=mock_code&state=eyd0YXJnZXQnOiAnLyd9")
            self.assertEqual(res.status_code, 403)
            self.assertIn("Strictly Not Permitted", res.text)
            self.assertIn("Management Authority Required", res.text)


if __name__ == "__main__":
    unittest.main()
