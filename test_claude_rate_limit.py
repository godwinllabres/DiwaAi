"""Regression tests — ClaudeRequestLimiter request budgets and ClaudeLLM gating."""
import os
import unittest
from unittest import mock

from api.hybrid_chatbot import ClaudeLLM, ClaudeRequestLimiter


def _limiter(**env):
    base = {"CLAUDE_RATE_LIMIT_RPM": "0", "CLAUDE_RATE_LIMIT_PER_USER": "0",
            "CLAUDE_DAILY_REQUEST_CAP": "0", "CLAUDE_RATE_LIMIT_WINDOW": "60"}
    base.update(env)
    with mock.patch.dict(os.environ, base):
        return ClaudeRequestLimiter()


class LimiterTests(unittest.TestCase):
    def test_per_user_limit_is_per_caller(self):
        lim = _limiter(CLAUDE_RATE_LIMIT_PER_USER="2")
        self.assertIsNone(lim.try_acquire("a"))
        self.assertIsNone(lim.try_acquire("a"))
        self.assertIn("per-user", lim.try_acquire("a"))
        self.assertIsNone(lim.try_acquire("b"))

    def test_global_limit(self):
        lim = _limiter(CLAUDE_RATE_LIMIT_RPM="3")
        for caller in ("a", "b", "c"):
            self.assertIsNone(lim.try_acquire(caller))
        self.assertIn("global", lim.try_acquire("d"))

    def test_window_expiry(self):
        lim = _limiter(CLAUDE_RATE_LIMIT_RPM="1", CLAUDE_RATE_LIMIT_WINDOW="10")
        with mock.patch("api.hybrid_chatbot.time.monotonic", return_value=100.0):
            self.assertIsNone(lim.try_acquire(None))
            self.assertIsNotNone(lim.try_acquire(None))
        with mock.patch("api.hybrid_chatbot.time.monotonic", return_value=111.0):
            self.assertIsNone(lim.try_acquire(None))

    def test_daily_cap(self):
        lim = _limiter(CLAUDE_DAILY_REQUEST_CAP="2")
        self.assertIsNone(lim.try_acquire("a"))
        self.assertIsNone(lim.try_acquire("b"))
        self.assertIn("daily cap", lim.try_acquire("c"))
        self.assertEqual(lim.snapshot()["used_today"], 2)

    def test_refused_request_not_counted(self):
        lim = _limiter(CLAUDE_RATE_LIMIT_PER_USER="1", CLAUDE_DAILY_REQUEST_CAP="5")
        lim.try_acquire("a")
        lim.try_acquire("a")  # refused
        self.assertEqual(lim.snapshot()["used_today"], 1)


class ClaudeLLMGatingTests(unittest.TestCase):
    def _llm(self, model="claude-haiku-4-5", **env):
        env = {"CLAUDE_RATE_LIMIT_PER_USER": "1", "CLAUDE_RATE_LIMIT_RPM": "0",
               "CLAUDE_DAILY_REQUEST_CAP": "0", **env}
        with mock.patch.dict(os.environ, env):
            llm = ClaudeLLM(api_key="test-key", model=model, system_prompt="sys")
        llm.client = mock.MagicMock()
        resp = mock.MagicMock(stop_reason="end_turn")
        resp.content = [mock.MagicMock(type="text", text="hello")]
        llm.client.messages.create.return_value = resp
        return llm

    def test_limit_skips_api_call(self):
        llm = self._llm()
        self.assertEqual(llm.generate("hi", caller="u1"), "hello")
        self.assertIsNone(llm.generate("hi again", caller="u1"))
        self.assertEqual(llm.client.messages.create.call_count, 1)

    def test_no_sampling_params_sent(self):
        # anthropic SDK 1.x raises TypeError on temperature/top_p/top_k.
        llm = self._llm()
        llm.generate("hi", caller="x")
        kwargs = llm.client.messages.create.call_args.kwargs
        for p in ("temperature", "top_p", "top_k"):
            self.assertNotIn(p, kwargs)


if __name__ == "__main__":
    unittest.main()
