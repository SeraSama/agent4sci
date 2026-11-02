"""Real API smoke tests. Run with: uv run pytest -m live (needs API keys in the environment)."""

import os

import pytest

from agent4sci.core.models import profile_from_preset
from agent4sci.llm.probe import test_connection as probe_connection

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_anthropic_connection():
    report = await probe_connection(profile_from_preset("anthropic"), os.environ["ANTHROPIC_API_KEY"])
    assert report["ok"], report
    assert report.get("tools_ok"), report


@pytest.mark.skipif(not (os.environ.get("OPENAI_API_KEY") and os.environ.get("A4S_TEST_OPENAI_MODEL")),
                    reason="OPENAI_API_KEY / A4S_TEST_OPENAI_MODEL not set")
async def test_openai_connection():
    profile = profile_from_preset("openai", model=os.environ["A4S_TEST_OPENAI_MODEL"])
    report = await probe_connection(profile, os.environ["OPENAI_API_KEY"])
    assert report["ok"], report
