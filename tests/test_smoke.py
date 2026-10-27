"""Offline smoke tests for the business-lead-intelligence skill.

Run from the repository root:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import base64
import csv
import json
import sys
import tempfile
import unittest
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "business-lead-intelligence" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from bootstrap import (  # noqa: E402
    DEFAULT_MANIFEST,
    load_manifest,
    npm_install_command,
    skill_names,
    skills_command,
)
from build_report import (  # noqa: E402
    OPT_OUT_LINES,
    cold_email,
    email_greeting,
    email_problem,
    indefinite_article,
    opening_line,
    opt_out_line,
    outreach_hook,
    possessive,
    recommended_offer,
    short_business_name,
)
from common import phones_match  # noqa: E402
from find_decision_makers import (  # noqa: E402
    dedupe_candidates,
    plausible_name,
    strip_trailing_role,
)
from gmail_sender import (  # noqa: E402
    DEFAULT_REDIRECT_URI,
    apply_sender_placeholders,
    build_raw_message,
    load_client,
    load_ready_rows,
    token_is_fresh,
    validate_email,
)
from run_pipeline import build_queries, matches_pre_filters  # noqa: E402


class PhoneTests(unittest.TestCase):
    def test_same_number_in_different_formats(self) -> None:
        self.assertTrue(phones_match("+92 300 4870666", "0300-4870666"))
        self.assertTrue(phones_match("+92 303 0171000", "tel:%2003030171000"))
        self.assertTrue(phones_match("+92 327 7088881", "200327-7088881"))

    def test_different_numbers_do_not_match(self) -> None:
        self.assertFalse(phones_match("+92 300 4380601", "+92 325 0445558"))


class NameTests(unittest.TestCase):
    def test_trailing_roles_are_removed(self) -> None:
        self.assertEqual(strip_trailing_role("Dr Ahmed Waleed Managing"), "Dr Ahmed Waleed")
        self.assertEqual(strip_trailing_role("Sarah Khan CEO"), "Sarah Khan")

    def test_page_text_is_not_a_person(self) -> None:
        for value in ("Oral Health", "Front Desk", "Model Town", "Confident You"):
            self.assertFalse(plausible_name(value, ""), value)
        self.assertTrue(plausible_name("Dr. Ahmed Waleed Wyne", ""))

    def test_spelling_variants_merge(self) -> None:
        base = {
            "role": "Doctor",
            "seniority": "clinical",
            "confidence": "high",
            "source_url": "https://example.com",
            "email": "",
            "linkedin": "",
        }
        merged = dedupe_candidates(
            [
                {"name": "Dr. Rumsha Zulfikar", **base},
                {"name": "Dr. Rumsha Zulfiqar", **base},
            ]
        )
        self.assertEqual(len(merged), 1)


class ReportTests(unittest.TestCase):
    def test_no_website_hook_uses_rating_and_reviews(self) -> None:
        business = {"name": "Smile Clinic", "rating": "4.8", "reviews": "82"}
        audit = {"website_status": "no_website", "score": 0, "findings": []}
        hook = outreach_hook(business, audit, [])
        self.assertIn("4.8", hook)
        self.assertIn("82", hook)
        self.assertIn("No website", hook)

    def test_opening_line_has_one_terminal_period(self) -> None:
        business = {"name": "De'Dentiste'"}
        audit = {
            "website_status": "up",
            "score": 5,
            "findings": [
                {"severity": "critical", "title": "No HTTPS", "evidence": "The final URL is http://example.com/."}
            ],
        }
        hook = outreach_hook(business, audit, audit["findings"])
        line = opening_line(business, audit, hook)
        self.assertIn("De'Dentiste's website", line)
        self.assertFalse(line.endswith(".."))

    def test_offer_tracks_site_state(self) -> None:
        self.assertIn("5-page", recommended_offer({"website_status": "no_website"}))
        self.assertIn("Emergency", recommended_offer({"website_status": "down_http_500"}))
        self.assertIn("Light-touch", recommended_offer({"website_status": "up", "score": 85}))

    def test_possessive(self) -> None:
        self.assertEqual(possessive("De'Dentiste'"), "De'Dentiste's")
        self.assertEqual(possessive("SmileOn"), "SmileOn's")

    def test_slow_email_uses_correct_article(self) -> None:
        self.assertEqual(indefinite_article("11.1"), "an")
        self.assertEqual(indefinite_article("8.5"), "an")
        self.assertEqual(indefinite_article("18"), "an")
        self.assertEqual(indefinite_article("22.8"), "a")
        self.assertEqual(indefinite_article("7"), "a")
        finding = {
            "title": "Homepage response is slow",
            "evidence": "Initial HTML response took 11.1s.",
        }
        _, value = email_problem(finding, "slow", {})
        self.assertIn("an 11.1-second wait", value)


class ColdEmailTests(unittest.TestCase):
    sender = {"name": "Alex", "company": "Example Co", "address": "1 Main St"}

    def test_no_website_email_is_grounded_and_has_opt_out(self) -> None:
        business = {"name": "Dr Ayesha", "rating": "4.8", "reviews": "82"}
        audit = {"website_status": "no_website", "score": 0, "findings": []}
        subject, body, status, notes, evidence = cold_email(
            business, audit, {}, self.sender
        )
        self.assertEqual(status, "no_published_email")
        self.assertIn("4.8", body)
        self.assertIn("82", body)
        self.assertIn("no website", body.lower())
        self.assertTrue(any(line in body for line in OPT_OUT_LINES))
        self.assertNotIn("Not relevant?", body)
        self.assertIn("Alex", body)
        self.assertIn("1 Main St", body)
        self.assertNotIn("—", body)
        self.assertLessEqual(len(body.split()), 130)
        self.assertEqual(len(subject.split()), 3)
        self.assertIn("No website", evidence)

    def test_named_buyer_and_specific_slow_finding(self) -> None:
        business = {
            "name": "Dental Avenue Hospital",
            "email": "hello@example.com",
            "rating": "4.8",
            "reviews": "340",
        }
        audit = {
            "website_status": "up",
            "score": 37,
            "findings": [
                {
                    "severity": "high",
                    "title": "Homepage response is slow",
                    "category": "Performance",
                    "evidence": "Initial HTML response took 7.0s (TTFB plus download).",
                }
            ],
        }
        dm = {
            "candidates": [
                {
                    "name": "Dr. Ahmed Waleed Wyne",
                    "role": "Managing Director",
                    "seniority": "executive",
                }
            ]
        }
        subject, body, status, notes, evidence = cold_email(
            business, audit, dm, self.sender
        )
        self.assertEqual(status, "ready")
        self.assertTrue(body.startswith("Hi Dr. Wyne,"))
        self.assertIn("7 seconds", body)
        self.assertIn("Homepage response is slow", evidence)
        self.assertIn("Managing Director", notes)
        self.assertNotIn("—", body)

    def test_healthy_site_does_not_pitch_speed(self) -> None:
        business = {
            "name": "SmileOn Dental",
            "email": "hello@example.com",
            "rating": "4.9",
            "reviews": "582",
        }
        audit = {
            "website_status": "up",
            "score": 82,
            "findings": [
                {
                    "severity": "low",
                    "title": "Homepage response could be faster",
                    "category": "Performance",
                    "evidence": "Initial HTML response took 1.5s.",
                },
                {
                    "severity": "medium",
                    "title": "No address or location block on the homepage",
                    "category": "Trust",
                    "evidence": "No address/location/visit-us language was found.",
                },
            ],
        }
        _, body, _, _, evidence = cold_email(business, audit, {}, self.sender)
        self.assertNotIn("slow first load", body)
        self.assertIn("clinic address", body)
        self.assertIn("No address", evidence)

    def test_short_names_and_greeting(self) -> None:
        self.assertEqual(short_business_name("Dental Works and Aesthetics"), "Dental Works")
        self.assertEqual(short_business_name("32 Dental Care"), "32 Dental")
        dm = {
            "candidates": [
                {"name": "Dr. Hafsa Rao", "role": "Manager", "seniority": "management"}
            ]
        }
        self.assertEqual(email_greeting(dm), "Hi Dr. Rao,")
        self.assertEqual(email_greeting({}), "Hi team,")

    def test_opt_out_lines_are_natural_and_stable(self) -> None:
        self.assertEqual(opt_out_line("Cantt Dental Care"), opt_out_line("Cantt Dental Care"))
        self.assertIn(opt_out_line("Cantt Dental Care"), OPT_OUT_LINES)
        self.assertNotIn("Not relevant?", OPT_OUT_LINES)


class GmailSenderTests(unittest.TestCase):
    def test_sender_placeholders_are_replaced(self) -> None:
        body = (
            "Hi team,\n\n"
            "One verified detail.\n\n"
            "[Your name]\n"
            "[Your company]\n"
            "[Your postal address]\n"
        )
        result = apply_sender_placeholders(
            body,
            sender_name="Alex Khan",
            sender_company="",
            sender_address="1 Main St, Lahore",
        )
        self.assertIn("Alex Khan", result)
        self.assertIn("1 Main St, Lahore", result)
        self.assertNotIn("[Your name]", result)
        self.assertNotIn("[Your company]", result)
        self.assertNotIn("[Your postal address]", result)

    def test_sender_placeholders_survive_when_values_are_missing(self) -> None:
        result = apply_sender_placeholders(
            "[Your name]\n[Your postal address]\n",
            sender_name="",
            sender_company="",
            sender_address="",
        )
        self.assertIn("[Your name]", result)
        self.assertIn("[Your postal address]", result)

    def test_raw_message_is_plain_text_with_opt_out_header(self) -> None:
        raw = build_raw_message(
            to="owner@example.com",
            subject="clinic security",
            body="Hi team,\n\nA verified detail.\n\nNot relevant? Reply \"no\".\n",
            sender="Alex <alex@example.com>",
            unsubscribe="alex@example.com",
        )
        message = message_from_bytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(message["To"], "owner@example.com")
        self.assertEqual(message["Subject"], "clinic security")
        self.assertEqual(message["From"], "Alex <alex@example.com>")
        self.assertEqual(
            message["List-Unsubscribe"],
            "<mailto:alex@example.com?subject=unsubscribe>",
        )
        self.assertTrue(message["Message-ID"].endswith("@example.com>"))
        plain = message.get_payload(decode=True).decode("utf-8")
        self.assertIn("Not relevant?", plain)

    def test_email_validation_rejects_lists_and_guesses(self) -> None:
        self.assertTrue(validate_email("info@example.com"))
        self.assertFalse(validate_email(""))
        self.assertFalse(validate_email("info@example.com, owner@example.com"))
        self.assertFalse(validate_email("not-an-email"))

    def test_client_file_uses_loopback_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            path.write_text(
                json.dumps(
                    {
                        "web": {
                            "client_id": "client-id",
                            "client_secret": "client-secret",
                            "redirect_uris": [DEFAULT_REDIRECT_URI],
                        }
                    }
                ),
                encoding="utf-8",
            )
            client = load_client(path)
            self.assertEqual(client["client_id"], "client-id")
            self.assertEqual(client["redirect_uri"], DEFAULT_REDIRECT_URI)

    def test_ready_rows_filter_status_recipient_and_limit(self) -> None:
        fields = [
            "business_name",
            "email",
            "cold_email_status",
            "cold_email_subject",
            "cold_email_body",
        ]
        rows = [
            ["Ready Clinic", "info@ready.example", "ready", "ready subject", "Ready body"],
            ["No Email Clinic", "", "no_published_email", "subject", "body"],
            ["Broken Clinic", "bad-email", "ready", "subject", "body"],
            ["Other Clinic", "owner@other.example", "ready", "other subject", "Other body"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(fields)
                writer.writerows(rows)
            selected = load_ready_rows(path, limit=10)
            self.assertEqual([row["email"] for row in selected], ["info@ready.example", "owner@other.example"])
            self.assertEqual(len(load_ready_rows(path, limit=1)), 1)
            filtered = load_ready_rows(path, only="Other", limit=10)
            self.assertEqual([row["email"] for row in filtered], ["owner@other.example"])

    def test_expired_token_is_not_fresh(self) -> None:
        self.assertFalse(token_is_fresh({"access_token": "x", "expires_at": 1}))
        self.assertFalse(token_is_fresh({}))


class FilterTests(unittest.TestCase):
    def test_query_matrix_includes_areas_and_dedupes(self) -> None:
        queries = build_queries(["dentist"], "Lahore", ["Gulberg"])
        self.assertIn("dentist in Lahore", queries)
        self.assertIn("dentist in Gulberg Lahore", queries)
        self.assertEqual(len(queries), len(set(q.lower() for q in queries)))

    def test_pre_filters(self) -> None:
        args = SimpleNamespace(min_rating=4.5, min_reviews=50, website="none")
        self.assertTrue(matches_pre_filters({"rating": "4.8", "reviews": "120", "website": ""}, args))
        self.assertFalse(matches_pre_filters({"rating": "4.0", "reviews": "120", "website": ""}, args))
        self.assertFalse(matches_pre_filters({"rating": "4.8", "reviews": "10", "website": ""}, args))
        self.assertFalse(matches_pre_filters({"rating": "4.8", "reviews": "120", "website": "x.com"}, args))


class BootstrapTests(unittest.TestCase):
    def test_manifest_declares_companion_skills(self) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)
        names = skill_names(manifest)
        for expected in ("cold-email", "prospecting", "web-quality-audit"):
            self.assertIn(expected, names)
        self.assertIn("performance", names)
        self.assertIn("browser-control", names)

    def test_skills_command_installs_named_skills_globally(self) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)
        group = manifest["skills"][0]
        command = skills_command(
            group,
            cli=manifest["skills_cli"],
            agent="opencode",
            global_scope=True,
        )
        self.assertEqual(command[:5], ["npx", "-y", manifest["skills_cli"], "add", group["source"]])
        self.assertIn("cold-email", command)
        self.assertIn("prospecting", command)
        self.assertIn("-g", command)
        self.assertIn("--copy", command)
        self.assertIn("-y", command)

    def test_npm_tool_command(self) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)
        tool = manifest["npm_tools"][0]
        self.assertEqual(
            npm_install_command(tool),
            ["npm", "install", "-g", tool["package"]],
        )


if __name__ == "__main__":
    unittest.main()
