import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)


class MicrosoftConfig:
    def __init__(self):
        self.tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        self.client_id = os.getenv('MICROSOFT_CLIENT_ID')
        self.client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')


class TeamsConfig:
    def __init__(self):
        self.chat_id = os.getenv('TEAMS_CHAT_ID')
        self.team_id = os.getenv('TEAMS_TEAM_ID')
        self.channel_id = os.getenv('TEAMS_CHANNEL_ID')
        self.webhook_url = os.getenv('TEAMS_WEBHOOK_URL')


class RolesConfig:
    def __init__(self):
        self.client = os.getenv('TEST_CLIENT_USER_ID')
        self.pm = os.getenv('TEST_PM_USER_ID')
        self.developer = os.getenv('TEST_DEVELOPER_USER_ID')
        self.allow_self_approval = os.getenv('ALLOW_SELF_APPROVAL', 'true').lower() in ('true', '1', 'yes')



class GeminiConfig:
    def __init__(self):
        # 1. Gather numbered keys: GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3...
        numbered_keys = []
        for i in range(1, 10):
            val = os.getenv(f'GEMINI_API_KEY_{i}')
            if val and val.strip():
                numbered_keys.append(val.strip())

        # 2. Gather comma-separated GEMINI_API_KEYS
        raw_keys = os.getenv('GEMINI_API_KEYS') or ''
        csv_keys = [k.strip() for k in raw_keys.split(',') if k.strip()]

        # 3. Fallback to single GEMINI_API_KEY or GOOGLE_API_KEY
        single_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or ''
        single_keys = [single_key.strip()] if single_key and single_key.strip() else []

        # Combine in priority order without duplicates
        all_keys = []
        for k in numbered_keys + csv_keys + single_keys:
            if k and k not in all_keys:
                all_keys.append(k)

        self.api_keys = all_keys
        self.api_key = self.api_keys[0] if self.api_keys else None

        # Default to gemini-3.5-flash-lite, normalizing user string variations
        raw_model = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite').strip()
        cleaned_model = raw_model.lower().replace(" ", "-").replace("_", "-")
        if "3.5" in cleaned_model and "lite" in cleaned_model:
            self.model = "gemini-3.5-flash-lite"
        elif "2.5" in cleaned_model and "lite" in cleaned_model:
            self.model = "gemini-2.5-flash-lite"
        elif cleaned_model in ("3.5-flash-lite", "flash-lite", "gemini-flash-lite"):
            self.model = "gemini-3.5-flash-lite"
        else:
            self.model = raw_model


class JiraConfig:
    def __init__(self):
        self.base_url = (os.getenv('JIRA_BASE_URL') or '').rstrip('/')
        self.email = os.getenv('JIRA_EMAIL')
        self.api_token = os.getenv('JIRA_API_TOKEN')
        self.project_key = (os.getenv('JIRA_PROJECT_KEY') or '').upper()
        self.default_issue_type = os.getenv('JIRA_DEFAULT_ISSUE_TYPE', 'Bug')

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.email and self.api_token and self.project_key)


class EmailConfig:
    def __init__(self):
        self.smtp_host = os.getenv('SMTP_HOST')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.smtp_from = os.getenv('SMTP_FROM_EMAIL') or os.getenv('SMTP_USER') or 'automation@kombee.com'
        self.smtp_use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() in ('true', '1', 'yes')
        self.graph_sender = os.getenv('GRAPH_SENDER_EMAIL')

    @property
    def is_smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)


class AppConfig:
    def __init__(self):
        self.reload()

    def reload(self):
        """Reload configuration from disk without restarting process."""
        load_dotenv(dotenv_path=env_path, override=True)
        self.microsoft = MicrosoftConfig()
        self.teams = TeamsConfig()
        self.roles = RolesConfig()
        self.gemini = GeminiConfig()
        self.jira = JiraConfig()
        self.email = EmailConfig()
        self.port = int(os.getenv('PORT', '3000'))
        self.webhook_public_url = os.getenv('WEBHOOK_PUBLIC_URL')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        self.database_path = os.getenv('DATABASE_PATH', './data/messages.db')
        self.pm_followup_timeout_minutes = int(os.getenv('PM_FOLLOWUP_TIMEOUT_MINUTES', '10'))
        self.pm_email_timeout_minutes = int(os.getenv('PM_EMAIL_TIMEOUT_MINUTES', '15'))
        self.pm_reminder_timeout_minutes = int(os.getenv('PM_REMINDER_TIMEOUT_MINUTES', str(self.pm_followup_timeout_minutes)))


config = AppConfig()


def validate_config() -> list[str]:
    """Validate that critical configuration values are present.

    Returns a list of missing environment variable names.
    """
    missing = []
    if not config.microsoft.tenant_id:
        missing.append('MICROSOFT_TENANT_ID')
    if not config.microsoft.client_id:
        missing.append('MICROSOFT_CLIENT_ID')
    if not config.microsoft.client_secret:
        missing.append('MICROSOFT_CLIENT_SECRET')

    # Either a group chat ID or both team ID and channel ID must be provided
    has_chat = bool(config.teams.chat_id and config.teams.chat_id.strip())
    has_channel = bool(
        config.teams.team_id and config.teams.team_id.strip() and
        config.teams.channel_id and config.teams.channel_id.strip()
    )

    if not (has_chat or has_channel):
        missing.append('TEAMS_CHAT_ID or (TEAMS_TEAM_ID and TEAMS_CHANNEL_ID)')

    return missing
