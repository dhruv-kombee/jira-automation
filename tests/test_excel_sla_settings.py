import os
import openpyxl
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.services.member_sync_service import (
    DEFAULT_SYSTEM_SETTINGS,
    get_system_settings_from_excel,
    get_sla_timelines,
    is_within_business_hours,
    get_jira_default_settings,
    get_issue_keyword_filters,
    read_members_from_excel,
    export_members_to_excel,
)


def test_get_system_settings_reads_from_member_xlsx():
    """Verify that settings can be read directly from data/Member.xlsx."""
    settings = get_system_settings_from_excel(force_refresh=True)
    assert isinstance(settings, dict)
    assert "STAGE1_TL_MINUTES" in settings
    assert settings["STAGE1_TL_MINUTES"] == 10
    assert settings["STAGE2_PM_MINUTES"] == 20
    assert settings["STAGE3_HM_EMAIL_MINUTES"] == 20
    assert settings["STAGE1_ENABLED"] is True
    assert settings["STAGE2_ENABLED"] is True
    assert settings["STAGE3_ENABLED"] is True
    assert settings["BACKLOG_CUTOFF_HOURS"] == 24
    assert settings["REMINDER_CHECK_INTERVAL"] == 30
    assert "SCRUM" in settings.get("DEFAULT_JIRA_PROJECT", "")
    assert isinstance(settings.get("TRIGGER_KEYWORDS"), list)


def test_get_sla_timelines_returns_clean_dict():
    """Verify get_sla_timelines helper parses cleanly."""
    sla = get_sla_timelines()
    assert sla["stage1_tl_minutes"] == 10
    assert sla["stage1_enabled"] is True
    assert sla["stage2_pm_minutes"] == 20
    assert sla["stage2_enabled"] is True
    assert sla["stage3_hm_email_minutes"] == 20
    assert sla["stage3_enabled"] is True
    assert sla["backlog_cutoff_hours"] == 24
    assert sla["check_interval"] == 30


def test_business_hours_disabled_returns_true():
    """When BUSINESS_HOURS_ONLY is False, is_within_business_hours returns True 24/7."""
    with patch("src.services.member_sync_service.get_system_settings_from_excel") as mock_settings:
        mock_settings.return_value = {
            "BUSINESS_HOURS_ONLY": False,
            "BUSINESS_START_TIME": "09:00",
            "BUSINESS_END_TIME": "19:00",
            "EXCLUDE_WEEKENDS": True,
            "TIMEZONE": "Asia/Kolkata",
        }
        # Midnight on Sunday
        sun_midnight = datetime(2026, 10, 11, 23, 30, tzinfo=timezone.utc)
        assert is_within_business_hours(sun_midnight) is True


def test_business_hours_enabled_evaluates_correctly():
    """When BUSINESS_HOURS_ONLY is True, respects workday boundaries and weekends."""
    with patch("src.services.member_sync_service.get_system_settings_from_excel") as mock_settings:
        mock_settings.return_value = {
            "BUSINESS_HOURS_ONLY": True,
            "BUSINESS_START_TIME": "09:00",
            "BUSINESS_END_TIME": "19:00",
            "EXCLUDE_WEEKENDS": True,
            "TIMEZONE": "UTC",
        }
        # Wednesday at 14:00 UTC (within 09:00 - 19:00)
        wed_workhour = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)
        assert is_within_business_hours(wed_workhour) is True

        # Wednesday at 22:00 UTC (outside work hours)
        wed_night = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
        assert is_within_business_hours(wed_night) is False

        # Saturday at 14:00 UTC (weekend excluded)
        sat_workhour = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
        assert is_within_business_hours(sat_workhour) is False


def test_sheet_isolation_between_team_and_sla():
    """Verify that read_members_from_excel reads strictly from Team Directory without SLA pollution."""
    members = read_members_from_excel()
    assert len(members) > 0
    for m in members:
        # None of the member names should be setting categories or setting names
        assert m["display_name"] not in ("SLA Timeline", "Business Hours", "Jira Defaults", "AI & Triggers")
        assert not m["display_name"].startswith("STAGE")


def test_export_preserves_sla_settings_sheet(tmp_path):
    """Verify that export_members_to_excel preserves the SLA & Settings tab when refreshing."""
    test_file = tmp_path / "Member.xlsx"
    # Generate initial export
    export_members_to_excel(test_file)
    wb = openpyxl.load_workbook(test_file)
    assert "Team Directory" in wb.sheetnames
    assert "SLA & Settings" in wb.sheetnames

    # Check that settings can be read from it
    sla_ws = wb["SLA & Settings"]
    assert sla_ws.cell(row=1, column=1).value is not None
