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
        self.api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        self.model = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')


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


class AppConfig:
    def __init__(self):
        self.microsoft = MicrosoftConfig()
        self.teams = TeamsConfig()
        self.roles = RolesConfig()
        self.gemini = GeminiConfig()
        self.jira = JiraConfig()
        self.port = int(os.getenv('PORT', '3000'))
        self.webhook_public_url = os.getenv('WEBHOOK_PUBLIC_URL')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        self.database_path = os.getenv('DATABASE_PATH', './data/messages.db')


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
