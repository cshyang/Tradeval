from __future__ import annotations

import os

import pytest

from retailtrader.data.sec import SecCompanyFactsClient

pytestmark = pytest.mark.integration


def test_sec_companyfacts_live() -> None:
    if os.getenv("RETAILTRADER_LIVE_SEC") != "1":
        pytest.skip("set RETAILTRADER_LIVE_SEC=1 to call data.sec.gov")
    user_agent = os.getenv("RETAILTRADER_SEC_USER_AGENT")
    if not user_agent:
        pytest.skip("set RETAILTRADER_SEC_USER_AGENT to an application name and contact email")

    document = SecCompanyFactsClient(user_agent=user_agent).fetch("0000320193")

    assert document.cik == "0000320193"
    assert document.entity_name
    assert len(document.observations) > 20
