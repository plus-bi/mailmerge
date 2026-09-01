from __future__ import annotations

import os
import uuid

import httpx
import pytest

from unsubscribe_service.main import sign_token


RUN_LIVE = os.getenv("MAILMERGE_RUN_LIVE_TESTS") == "1"


def _latest_cursor(client: httpx.Client, sync_secret: str) -> int:
    cursor = 0
    while True:
        response = client.get(
            "/api/v1/events",
            params={"cursor": cursor},
            headers={"Authorization": f"Bearer {sync_secret}"},
        )
        assert response.status_code == 200
        payload = response.json()
        next_cursor = int(payload["cursor"])
        if not payload["events"] or next_cursor == cursor:
            return cursor
        cursor = next_cursor


@pytest.mark.live
@pytest.mark.skipif(not RUN_LIVE, reason="set MAILMERGE_RUN_LIVE_TESTS=1 to test the deployed service")
def test_live_manual_and_one_click_unsubscribe_flows():
    signing_secret = os.environ["UNSUBSCRIBE_SIGNING_SECRET"]
    sync_secret = os.environ["UNSUBSCRIBE_SYNC_SECRET"]
    base_url = os.getenv("MAILMERGE_UNSUBSCRIBE_BASE_URL", "https://unsub.plus.bi").rstrip("/")
    run_id = uuid.uuid4().hex
    campaign_id = f"live-unsubscribe-check-{run_id}"
    manual_recipient = f"manual-{run_id}@example.invalid"
    one_click_recipient = f"one-click-{run_id}@example.invalid"

    with httpx.Client(base_url=base_url, timeout=15, follow_redirects=False) as client:
        cursor = _latest_cursor(client, sync_secret)

        manual_token = sign_token(campaign_id, manual_recipient, secret=signing_secret)
        manual_path = f"/u/{manual_token}"
        confirmation = client.get(manual_path)
        assert confirmation.status_code == 200
        assert "Confirm unsubscribe" in confirmation.text
        manual_result = client.post(
            manual_path,
            content=b"",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert manual_result.status_code == 200
        assert "unsubscribed" in manual_result.text.lower()

        one_click_token = sign_token(campaign_id, one_click_recipient, secret=signing_secret)
        one_click_result = client.post(
            f"/u/{one_click_token}",
            content=b"List-Unsubscribe=One-Click",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert one_click_result.status_code == 200
        assert "unsubscribed" in one_click_result.text.lower()

        events = client.get(
            "/api/v1/events",
            params={"cursor": cursor},
            headers={"Authorization": f"Bearer {sync_secret}"},
        )
        assert events.status_code == 200
        recorded = {(event["campaign_id"], event["recipient_id"]) for event in events.json()["events"]}
        assert (campaign_id, manual_recipient) in recorded
        assert (campaign_id, one_click_recipient) in recorded
