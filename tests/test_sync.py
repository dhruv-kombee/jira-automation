import pytest
from src.services.sender_service import identify_sender_role, Roles
from src.services.subscription_manager import get_active_subscription_info, AUTO_RENEW_THRESHOLD_SECONDS


def test_role_identification_with_display_name_fallback():
    # 1. By ID
    assert identify_sender_role(user_id="35e03956-1723-469c-b561-90f03fc566ed") == Roles.CLIENT

    # 2. By Name
    assert identify_sender_role(user_id=None, display_name="Dhruv dobariya") == Roles.CLIENT
    assert identify_sender_role(user_id="unknown-guid", display_name="Santosh Yadav") == Roles.PM
    assert identify_sender_role(user_id=None, display_name="Musaib Khan") == Roles.DEVELOPER
    assert identify_sender_role(user_id=None, display_name="Musain") == Roles.DEVELOPER
    assert identify_sender_role(user_id=None, display_name="Someone Else") == Roles.UNKNOWN


def test_auto_renew_threshold_configuration():
    assert AUTO_RENEW_THRESHOLD_SECONDS == 1800  # 30 minutes
    info = get_active_subscription_info()
    assert info["autoRenewEnabled"] is True
    assert info["autoRenewThresholdSeconds"] == 1800
    assert info["autoRenewThresholdText"] == "30m"


def test_is_pm_approval():
    from src.services.sender_service import is_pm_approval

    # Positive approval by PM (Santosh Yadav)
    pm_reactions = [{"userId": "d7bc3c28-33d9-4973-816e-445d51556b8b", "displayName": "Santosh Yadav", "reactionType": "👍"}]
    assert is_pm_approval(pm_reactions) is True

    # Like by client (Dhruv) should not trigger PM approval
    client_reactions = [{"userId": "35e03956-1723-469c-b561-90f03fc566ed", "displayName": "Dhruv dobariya", "reactionType": "👍"}]
    assert is_pm_approval(client_reactions) is False

    # Empty / None
    assert is_pm_approval([]) is False
    assert is_pm_approval(None) is False

