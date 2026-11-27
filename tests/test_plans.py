from app import main


def test_premium_alias():
    assert main.derive_plan({"plan_name": "Premium"}, True)[0] == "premium"


def test_standard_with_ads_alias():
    assert main.derive_plan({"plan_name": "Standard with Ads"}, True)[0] == "standard_with_ads"


def test_streams_fallback():
    assert main.derive_plan({"streams": "4", "quality": "UHD"}, True)[0] == "premium"
    assert main.derive_plan({"streams": "2"}, True)[0] == "standard"
    assert main.derive_plan({"streams": "1"}, True)[0] == "basic"


def test_free_without_plan():
    assert main.derive_plan({}, False)[0] == "free"


def test_extra_member():
    info = {"plan_name": "Premium", "membership_status": "Extra member on someone else's plan"}
    assert main.is_extra_member(info) is True
    assert main.is_subscribed(info) is True


def test_on_hold():
    assert main.is_on_hold({"hold": "Yes"}) is True
    assert main.is_on_hold({"membership_status": "past_due"}) is True
    assert main.is_on_hold({"membership_status": "current_member"}) is False


def test_sort_premium_first():
    rows = [
        {"index": 0, "status": "valid", "plan": "basic", "country": "FR"},
        {"index": 1, "status": "valid", "plan": "premium", "country": "FR"},
        {"index": 2, "status": "invalid", "plan": "invalid", "country": "FR"},
    ]
    assert [row["plan"] for row in sorted(rows, key=main.sort_key)] == ["premium", "basic", "invalid"]
