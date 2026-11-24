"""Regression tests — RAG meta-phrase scrubber, follow-up detector, campus scope."""
import unittest

from api.conversation_polish import is_contextual_followup, satellite_campus, strip_rag_meta


class StripRagMetaTests(unittest.TestCase):
    def test_leading_clause_removed_and_recapitalised(self):
        self.assertEqual(
            strip_rag_meta("Based on the excerpts provided, enrollment opens in June."),
            "Enrollment opens in June.")
        self.assertEqual(
            strip_rag_meta("According to the context, the Registrar issues the TOR."),
            "The Registrar issues the TOR.")

    def test_inline_qualifier_removed(self):
        self.assertEqual(
            strip_rag_meta("I don't have the faculty list in the excerpts provided."),
            "I don't have the faculty list.")

    def test_excerpts_as_subject_rewritten(self):
        self.assertEqual(
            strip_rag_meta("The excerpts don't mention midyear classes."),
            "I don't have details on midyear classes.")

    def test_list_items_keep_markers(self):
        self.assertEqual(
            strip_rag_meta("- based on the information given, bring two IDs"),
            "- Bring two IDs")

    def test_clean_reply_unchanged(self):
        reply = "Visit the Registrar at the Admin Building.\n\n- Bring your ID"
        self.assertEqual(strip_rag_meta(reply), reply)
        self.assertIsNone(strip_rag_meta(None))

    def test_real_phrases_not_mangled(self):
        # "information" used normally mid-sentence must survive.
        reply = "You can find more information from the Registrar."
        self.assertEqual(strip_rag_meta(reply), reply)


class FollowupTests(unittest.TestCase):
    def test_followups(self):
        for text in ("why only trece?", "what about Imus?", "how about graduate school",
                     "that one?", "bakit lang trece?", "paano naman sa Imus?",
                     "just create a list available online",
                     "and the requirements?"):
            self.assertTrue(is_contextual_followup(text), text)

    def test_standalone_questions(self):
        for text in ("where is the registrar", "tuition fee",
                     "how can I enroll next year at CvSU as a freshman applicant",
                     "who is the president of CvSU",
                     "Why does CvSU require a medical certificate for all incoming freshmen applicants?"):
            self.assertFalse(is_contextual_followup(text), text)


class SatelliteCampusTests(unittest.TestCase):
    def test_campus_pages(self):
        self.assertEqual(
            satellite_campus("“Trece Martires City Campus” — official CvSU site "
                             "(https://cvsu.edu.ph/trece-martires-city-campus/)"),
            "Trece Martires City Campus")
        self.assertEqual(satellite_campus("Imus Campus Officials"), "Imus City Campus")

    def test_main_and_general_pages_pass(self):
        self.assertIsNone(satellite_campus("“Admission” — official CvSU site (https://cvsu.edu.ph/admission/)"))
        self.assertIsNone(satellite_campus("Indang Main Campus map"))

    def test_news_naming_a_town_is_not_campus_scoped(self):
        self.assertIsNone(satellite_campus(
            "CvSU turns over seagrass research data to DENR, LGU Maragondon"))


if __name__ == "__main__":
    unittest.main()
