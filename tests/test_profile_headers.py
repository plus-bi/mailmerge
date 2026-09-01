from mailmerge.messages import build_message
from mailmerge.models import Campaign, Profile
from mailmerge.rendering import RenderedMessage


def test_profile_reply_to_and_unsubscribe_headers():
    profile = Profile(
        name="TUM", smtp_host="postout.lrz.de", reply_to="replies@example.com",
        list_unsubscribe="https://unsubscribe.example.com/u/token", list_unsubscribe_one_click=True,
    )
    campaign = Campaign(
        name="Test", from_name="Sender", from_address="sender@example.com", purpose="operational",
    )
    message = build_message(campaign, "recipient@example.com", RenderedMessage("Test", "<p>Body</p>", "Body"), profile)
    assert message["Reply-To"] == "replies@example.com"
    assert message["List-Unsubscribe"] == "<https://unsubscribe.example.com/u/token>"
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_campaign_reply_to_overrides_profile():
    profile = Profile(name="TUM", smtp_host="postout.lrz.de", reply_to="profile@example.com")
    campaign = Campaign(name="Test", from_name="Sender", from_address="sender@example.com", reply_to="campaign@example.com")
    message = build_message(campaign, "recipient@example.com", RenderedMessage("Test", "Body", "Body"), profile)
    assert message["Reply-To"] == "campaign@example.com"


def test_campaign_unsubscribe_overrides_profile():
    profile = Profile(
        name="TUM",
        smtp_host="postout.lrz.de",
        list_unsubscribe="https://unsub.example.com/u/profile-token",
        list_unsubscribe_one_click=False,
    )
    campaign = Campaign(
        name="Test",
        from_name="Sender",
        from_address="sender@example.com",
        unsubscribe_base_url="https://unsub.example.com/u/campaign-token",
    )
    message = build_message(campaign, "recipient@example.com", RenderedMessage("Test", "Body", "Body"), profile)
    assert message["List-Unsubscribe"] == "<https://unsub.example.com/u/campaign-token>"
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_profile_sender_identity_and_campaign_override():
    profile = Profile(
        name="TUM",
        from_name="Default Profile Sender",
        from_address="profile_sender@tum.de",
        smtp_host="postout.lrz.de",
    )
    campaign_default = Campaign(
        name="Camp A",
        from_name="",
        from_address="",
        purpose="operational",
    )
    msg1 = build_message(campaign_default, "alice@example.com", RenderedMessage("Subject", "Body", "Body"), profile)
    assert msg1["From"] == "Default Profile Sender <profile_sender@tum.de>"

    campaign_override = Campaign(
        name="Camp B",
        from_name="Campaign Specific Sender",
        from_address="custom@tum.de",
        purpose="operational",
    )
    msg2 = build_message(campaign_override, "bob@example.com", RenderedMessage("Subject", "Body", "Body"), profile)
    assert msg2["From"] == "Campaign Specific Sender <custom@tum.de>"


def test_list_unsubscribe_checkbox_dynamic_token(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SIGNING_SECRET", "super-secret-key")
    campaign = Campaign(
        name="Autumn Newsletter",
        from_name="Newsletter Team",
        from_address="news@example.com",
        list_unsubscribe_enabled=True,
    )
    msg = build_message(campaign, "reader@example.com", RenderedMessage("Newsletter #1", "Hello", "Hello"))
    assert "List-Unsubscribe" in msg
    assert "List-Unsubscribe-Post" in msg
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    header = msg["List-Unsubscribe"].strip("<>")
    assert header.startswith("https://mailmerge.plus.bi/u/")
    token = header.split("/u/")[1]
    from unsubscribe_service.main import verify_token
    payload = verify_token(token, secret="super-secret-key")
    assert payload["c"] == "Autumn Newsletter"
    assert payload["r"] == "reader@example.com"

