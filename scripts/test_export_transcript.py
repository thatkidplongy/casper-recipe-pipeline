"""Tests for the session transcript exporter.

Stdlib unittest on purpose: the exporter must run with no third-party
dependencies installed, so its tests must too.

Run: python3 scripts/test_export_transcript.py
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_transcript import (redact, redact_tree, render_markdown,
                               conversational_records, find_credentials)

PLACEHOLDER = "[REDACTED-API-KEY]"


class TestRedactsCredentials(unittest.TestCase):
    """Anything that looks like a key must not survive into the export."""

    def assert_scrubbed(self, secret, text=None):
        text = text if text is not None else secret
        out = redact(text)
        self.assertNotIn(secret, out, f"secret survived redaction: {text!r} -> {out!r}")
        self.assertIn(PLACEHOLDER, out, f"no placeholder emitted for {text!r} -> {out!r}")

    def test_groq_key(self):
        """Groq keys are gsk_ with an underscore, not sk- with a hyphen.

        The sk- patterns do not match them, so a Groq key would pass straight
        into a committed transcript.
        """
        self.assert_scrubbed("gsk_" + "abcdefghij1234567890ABCDEFGHIJklmnopqrstuvwx")

    def test_openrouter_key(self):
        self.assert_scrubbed("sk-or-v1-" + "a" * 48)

    def test_xai_key(self):
        self.assert_scrubbed("xai-" + "b" * 48)

    def test_openai_legacy_key(self):
        self.assert_scrubbed("sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2")

    def test_openai_project_key(self):
        self.assert_scrubbed("sk-proj-" + "abcdefghij1234567890ABCDEFGHIJ_klmnop-qrstuv")

    def test_anthropic_key(self):
        self.assert_scrubbed("sk-ant-api03-" + "abcdefghij1234567890ABCDEFGHIJklmnopqrstuvwx")

    def test_github_token(self):
        self.assert_scrubbed("ghp_" + "a" * 36)

    def test_github_fine_grained_token(self):
        self.assert_scrubbed("github_pat_" + "b" * 40)

    def test_aws_access_key_id(self):
        # Split so no AKIA-shaped literal sits in the repo for scanners to flag.
        self.assert_scrubbed("AKIA" + "IOSFODNN7EXAMPLE")

    def test_google_api_key(self):
        self.assert_scrubbed("AIza" + "C" * 35)

    def test_slack_token(self):
        # Split so no xoxb-shaped literal sits in the repo for scanners to flag.
        self.assert_scrubbed("xoxb-" + "123456789012-1234567890123-abcdefghijklmnop")

    def test_bearer_header(self):
        self.assert_scrubbed(
            "aVeryLongOpaqueBearerTokenValue1234567890",
            "Authorization: Bearer aVeryLongOpaqueBearerTokenValue1234567890",
        )

    def test_jwt(self):
        self.assert_scrubbed(
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
            ".dQw4w9WgXcQdQw4w9WgXcQdQw4w9WgXcQdQw4w"
        )

    def test_private_key_block(self):
        self.assert_scrubbed(
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ",
            "-----BEGIN " + "RSA PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
            "-----END " + "RSA PRIVATE KEY-----",
        )

    def test_orphan_private_key_header_is_redacted(self):
        """A truncated log carries the BEGIN marker without its END marker.

        The paired-block rule cannot fire on that, so the header and whatever
        body follows it would otherwise survive into the export.
        """
        header = "-----BEGIN " + "EC PRIVATE KEY-----"
        body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ"
        out = redact(f"truncated log: {header}\n{body}\nmore log")
        self.assertNotIn(header, out, "the marker must go")
        self.assertNotIn(body, out, "a key body without its header is still key material")
        self.assertIn(PLACEHOLDER, out)
        self.assertIn("more log", out, "unrelated log lines must survive")

    def test_orphan_private_key_footer_is_redacted(self):
        footer = "-----END " + "EC PRIVATE KEY-----"
        self.assert_scrubbed(footer, f"tail of a truncated log: {footer}")

    def test_env_assignment_hides_value_but_keeps_name(self):
        out = redact("OPENAI_API_KEY=sk-" + "z" * 44)
        self.assertNotIn("z" * 44, out)
        self.assertIn("OPENAI_API_KEY", out, "the variable name is not a secret and aids review")

    def test_email_addresses_are_redacted(self):
        """Environment output drags personal addresses into the transcript."""
        out = redact("CLAUDE_CODE_USER_EMAIL=someone@example.com and more text")
        self.assertNotIn("someone@example.com", out)
        self.assertIn("[REDACTED-EMAIL]", out)
        self.assertIn("and more text", out, "surrounding prose must survive")

    def test_quoted_json_secret_field(self):
        self.assert_scrubbed(
            "s3cr3tV4lu3_thatIsLongEnough1234",
            '{"api_key": "s3cr3tV4lu3_thatIsLongEnough1234"}',
        )


class TestDoesNotOverRedact(unittest.TestCase):
    """Redaction must not destroy ordinary prose or the audit's own content."""

    def assert_untouched(self, text):
        self.assertEqual(redact(text), text, f"over-redacted: {text!r}")

    def test_mentioning_a_variable_name_is_safe(self):
        self.assert_untouched("Set OPENAI_API_KEY in the environment settings.")

    def test_short_numeric_settings_are_safe(self):
        self.assert_untouched("max_tokens=1000")

    def test_ordinary_prose_is_safe(self):
        self.assert_untouched("I am asking whether the risk-free approach works.")

    def test_code_reference_is_safe(self):
        self.assert_untouched("recipe_modifier.py:91 performs an exact substring replace.")


class TestRedactsWholeTree(unittest.TestCase):
    """Secrets hide in nested tool inputs and results, not just top-level strings."""

    def test_nested_structures_are_redacted(self):
        secret = "sk-" + "Q" * 48
        tree = {
            "message": {"content": [{"type": "text", "text": f"key is {secret}"}]},
            "list": [[{"deep": secret}]],
            "count": 7,
            "flag": True,
            "nothing": None,
        }
        out = redact_tree(tree)
        blob = json.dumps(out)
        self.assertNotIn(secret, blob)
        self.assertEqual(out["count"], 7, "non-string values must pass through unchanged")
        self.assertEqual(out["flag"], True)
        self.assertIsNone(out["nothing"])

    def test_dict_keys_are_preserved(self):
        out = redact_tree({"api_key": "sk-" + "R" * 48})
        self.assertIn("api_key", out, "keys are structure, only values are secret")


class TestSelectsConversationalRecords(unittest.TestCase):
    def test_bookkeeping_records_are_dropped(self):
        records = [
            {"type": "user", "message": {"role": "user", "content": "hello"}},
            {"type": "assistant", "message": {"role": "assistant", "content": []}},
            {"type": "atis-latch", "atis": {}},
            {"type": "last-prompt", "lastPrompt": "x"},
            {"type": "queue-operation", "operation": "add"},
        ]
        kept = conversational_records(records)
        self.assertEqual([r["type"] for r in kept], ["user", "assistant"])


class TestRendersReadableMarkdown(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "type": "user",
                "timestamp": "2026-09-01T23:00:00Z",
                "message": {"role": "user", "content": "Audit the pipeline."},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-01T23:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Consider the modifier."},
                        {"type": "text", "text": "Reading the modifier now."},
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "cat recipe_modifier.py"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-01T23:00:09Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": "def apply_edit(...)"}
                    ],
                },
            },
        ]

    def test_renders_turns_and_tools(self):
        md = render_markdown(self.records, session_id="test-session")
        self.assertIn("test-session", md)
        self.assertIn("Audit the pipeline.", md)
        self.assertIn("Reading the modifier now.", md)
        self.assertIn("Bash", md, "tool name must be visible to a reader")
        self.assertIn("cat recipe_modifier.py", md)
        self.assertIn("def apply_edit(...)", md)

    def test_roles_are_labelled(self):
        md = render_markdown(self.records, session_id="test-session")
        self.assertIn("User", md)
        self.assertIn("Assistant", md)

    def test_thinking_is_included_but_marked(self):
        md = render_markdown(self.records, session_id="test-session")
        self.assertIn("Consider the modifier.", md)
        self.assertIn("Thinking", md)

    def test_secrets_do_not_survive_rendering(self):
        secret = "sk-" + "T" * 48
        records = [
            {
                "type": "user",
                "timestamp": "2026-09-01T23:00:00Z",
                "message": {"role": "user", "content": f"my key is {secret}"},
            }
        ]
        md = render_markdown(redact_tree(records), session_id="s")
        self.assertNotIn(secret, md)


class TestReadabilityOfRendering(unittest.TestCase):
    """Defects found rendering the real transcript."""

    def test_records_that_render_to_nothing_are_omitted(self):
        records = [
            {
                "type": "assistant",
                "timestamp": "2026-09-01T23:00:00Z",
                "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "   "}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-01T23:00:01Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Real content."}]},
            },
        ]
        md = render_markdown(records, session_id="s")
        self.assertEqual(md.count("## Assistant"), 1, "an empty turn must not emit a heading")
        self.assertIn("Real content.", md)

    def test_tool_results_are_not_labelled_as_the_user_speaking(self):
        records = [
            {
                "type": "user",
                "timestamp": "2026-09-01T23:00:00Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "output"}]},
            }
        ]
        md = render_markdown(records, session_id="s")
        self.assertNotIn("## User", md, "tool output is not something the user said")
        self.assertIn("Tool result", md)

    def test_a_genuine_user_turn_is_still_labelled_user(self):
        records = [
            {
                "type": "user",
                "timestamp": "2026-09-01T23:00:00Z",
                "message": {"role": "user", "content": "an actual instruction"},
            }
        ]
        self.assertIn("## User", render_markdown(records, session_id="s"))


class TestScanSharesTheRedactorsPatterns(unittest.TestCase):
    """A separate scan drifts from the redactor. The Groq gap existed because
    the pre-commit check carried its own copy of the sk- pattern."""

    def test_scan_finds_every_family_the_redactor_knows(self):
        for secret in ["gsk_" + "x" * 44, "sk-" + "A" * 44, "xai-" + "z" * 44,
                       "ghp_" + "b" * 36, "AKIA" + "IOSFODNN7EXAMPLE"]:
            self.assertTrue(find_credentials(f"log line {secret} tail"),
                            f"scan missed {secret[:10]}")

    def test_scan_is_quiet_on_clean_text(self):
        self.assertEqual(find_credentials("Set OPENAI_API_KEY in the environment."), [])

    def test_scan_reports_what_it_found(self):
        hits = find_credentials("key gsk_" + "q" * 44)
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
