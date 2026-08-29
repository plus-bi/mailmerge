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
