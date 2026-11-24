"""Regression tests — RAG meta-phrase scrubber, repeat/pushback helpers,
follow-up detector, campus scope."""
import re
import unittest

from api.conversation_polish import (dedupe_reply, followup_kind, is_contextual_followup,
                                     is_live_lookup,
                                     is_pushback, pivot_reply, satellite_campus,
                                     strip_rag_meta, wants_repeat)
from api.hybrid_chatbot import _filipino_ratio


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

    def test_provided_to_me_phrases_removed(self):
        self.assertEqual(
            strip_rag_meta("I only have enrollment details for Trece Martires in what was provided to me."),
            "I only have enrollment details for Trece Martires.")
        self.assertEqual(
            strip_rag_meta("I don't have those steps in the documents in front of me right now."),
            "I don't have those steps.")

    def test_excerpts_as_subject_rewritten(self):
        self.assertEqual(
            strip_rag_meta("The excerpts don't mention midyear classes."),
            "I don't have details on midyear classes.")

    def test_live_haiku_leaks(self):
        # Verbatim shapes from the 2026-11-23 live run against Claude Haiku.
        self.assertEqual(
            strip_rag_meta("The excerpt I have mentions that two faculty members "
                           "were Fulbright scholars in 2022–2023, but doesn't name them."),
            "I only know that two faculty members were Fulbright scholars in "
            "2022–2023, but doesn't name them.")
        self.assertEqual(
            strip_rag_meta("I don't have specific information about Imus campus "
                           "procedures in my current set of excerpts. Please check the site."),
            "I don't have specific information about Imus campus procedures. "
            "Please check the site.")
        self.assertEqual(
            strip_rag_meta("The excerpts I have don't mention the dorm fees."),
            "I don't have details on the dorm fees.")
        self.assertEqual(
            strip_rag_meta("The excerpts I have access to only mention two faculty "
                           "members who were Fulbright scholars."),
            "I only have details on two faculty members who were Fulbright scholars.")
        self.assertEqual(
            strip_rag_meta("contact the **Office of the University Registrar (OSAS)** or "
                           "the Registrar (OSAS) directly."),
            "contact the **Office of the University Registrar** or the Registrar directly.")
        self.assertEqual(
            strip_rag_meta("I don't have a complete faculty directory in my current "
                           "knowledge base. Visit https://cvsu.edu.ph."),
            "I don't have a complete faculty directory. Visit https://cvsu.edu.ph.")
        self.assertEqual(
            strip_rag_meta("I don't have enough context to answer that. Could you clarify?"),
            "Could you clarify?")
        self.assertEqual(
            strip_rag_meta("I don't have enough context to answer that — you're asking "
                           "about programs at a campus. Which one?"),
            "You're asking about programs at a campus. Which one?")
        self.assertEqual(
            strip_rag_meta("I don't have that yet. The excerpt I have lists Imus Campus as a "
                           "branch but doesn't detail its programs."),
            "I don't have that yet. My information lists Imus Campus as a branch but "
            "doesn't detail its programs.")
        self.assertEqual(
            strip_rag_meta("The excerpt mentions two Fulbright scholars, but the passage "
                           "doesn't name them."),
            "My information mentions two Fulbright scholars, but my information "
            "doesn't name them.")
        # Ordinary uses of "excerpt" (no verb after it as a subject) stay.
        reply = "Bring an excerpt of your birth certificate."
        self.assertEqual(strip_rag_meta(reply), reply)
        # A lead-in that names what can't be confirmed is a caveat, not filler,
        # and stays (review 2026-11-24) — as does a generic lead-in that is
        # not followed by a clarification question.
        for reply in (
            "I don't have enough context to confirm this applies to Imus. "
            "The form goes to the Registrar.",
            "I don't have enough context to say whether Imus offers it — "
            "check with the campus. Which program?",
            "I don't have enough context to answer that. The form goes to the Registrar.",
        ):
            self.assertEqual(strip_rag_meta(reply), reply)
        # Mid-reply "context" sentences are not openers and stay.
        reply = "Here are the steps. I don't have context on fees, so ask the Cashier."
        self.assertEqual(strip_rag_meta(reply), reply)
        # OSAS on its own is correct and stays.
        reply = "Submit your documents to OSAS at your campus."
        self.assertEqual(strip_rag_meta(reply), reply)
        # Not clause-final, so not a meta-phrase.
        reply = "Bring the excerpt from your current set of grades."
        self.assertEqual(strip_rag_meta(reply), reply)

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

    CASES = [
        ("Based on the excerpts and conversation context, X.", "X."),
        ("Based on the official CvSU excerpts provided, X.", "X."),
        ("Based on the information I have, X.", "X."),
        ("I'm sorry, but based on the excerpts, X.", "I'm sorry, but X."),
        ("Enrollment is in June. Based on the excerpts, you need an ID.",
         "Enrollment is in June. You need an ID."),
        ("**Based on the excerpts provided**, X.", "X."),
        ("According to , X.", "X."),
        ("According to, X.", "X."),
        ("The excerpts mention only Trece Martires.", "I only have details on Trece Martires."),
        ("Batay sa mga excerpt na ibinigay, X.", "X."),
        ("Base sa mga excerpt, X.", "X."),
        ("Ayon sa ibinigay na impormasyon, X.", "X."),
        ("Sa mga ibinigay na excerpt, wala pong X.", "Wala pong X."),
        ("Hindi nabanggit sa mga excerpt ang X.", "Wala po akong detalye tungkol sa X."),
        ("Batay sa mga ibinigay na sipi, X.", "X."),
        ("In my knowledge base, the Registrar issues the TOR.", "The Registrar issues the TOR."),
        ("In the excerpts, the Registrar issues the TOR.", "The Registrar issues the TOR."),
        ("According to my knowledge base, the Registrar issues the TOR.",
         "The Registrar issues the TOR."),
        ("Based on my knowledge base, X.", "X."),
        ("- In the context provided, bring ID.", "- Bring ID."),
        ("This is not in the documents I have.", "This is not."),
    ]
    UNCHANGED = [
        "Ayon sa Registrar, bring two IDs.",
        "According to the Citizens' Charter [CvSU Citizens' Charter, p. 3], X.",
        "Based on the information provided by the Registrar, X.",
        "[OUT_OF_SCOPE]",
        # "in the context of" is real prose.
        "Within the context of RA 10931, tuition is free.",
        "NSTP is taken in the context of the CWTS program.",
        "Ask the Registrar for help with this, in the context of your shifting request.",
        "In the context of enrollment, bring your ID.",
        # "sipi" is also the plain word for a document copy.
        "Isama ang ID sa mga sipi ng Form 138.",
        "Magdala ng Form 138 at sa mga sipi, isulat ang pangalan.",
        "Hindi makikita sa mga sipi ang iyong pangalan kung mali.",
        # Mid-clause, "in the documents I have" is real prose.
        "Submit them in the documents I have listed above.",
        "As stated in the documents I have reviewed, the fee is due.",
    ]
    CITED = ("Based on the excerpts provided [Admission — official CvSU site "
             "(https://cvsu.edu.ph/admission/)], X.")

    def test_widened_lead_ins(self):
        for text, want in self.CASES:
            self.assertEqual(strip_rag_meta(text), want, text)

    def test_negatives_unchanged(self):
        for text in self.UNCHANGED:
            self.assertEqual(strip_rag_meta(text), text)

    def test_citation_moves_verbatim(self):
        bracket = "[Admission — official CvSU site (https://cvsu.edu.ph/admission/)]"
        self.assertEqual(strip_rag_meta(self.CITED), f"According to {bracket}, X.")

    def test_idempotent(self):
        for text in [c for c, _ in self.CASES] + self.UNCHANGED + [self.CITED]:
            once = strip_rag_meta(text)
            self.assertEqual(strip_rag_meta(once), once, text)


class DedupeTests(unittest.TestCase):
    BLOCK = "- Visit the Office of Admission\n- Check the portal\n- Ask the Registrar"

    def test_block_repeated_in_reply(self):
        reply, repeated = dedupe_reply(f"Here is what to do:\n\n{self.BLOCK}\n\n{self.BLOCK}", None)
        self.assertEqual(reply, f"Here is what to do:\n\n{self.BLOCK}")
        self.assertFalse(repeated)

    def test_repeat_of_previous(self):
        prev = "You can enroll at the Registrar.\n\n" + self.BLOCK
        self.assertTrue(dedupe_reply(prev, prev)[1])
        self.assertTrue(dedupe_reply("You can enroll at the **Registrar**.\n" + self.BLOCK, prev)[1])

    def test_shared_short_line_not_repeat(self):
        prev = "Enrollment opens in June.\nPlease check https://cvsu.edu.ph."
        reply = ("The Registrar issues the transcript of records.\nBring a valid school ID.\n"
                 "Please check https://cvsu.edu.ph.")
        self.assertEqual(dedupe_reply(reply, prev), (reply, False))

    def test_same_shape_different_campus_not_repeat(self):
        prev = "At Imus campus, the enrollment fee is paid at the Cashier's Office in the Administration Building."
        reply = prev.replace("Imus", "Bacoor")
        self.assertEqual(dedupe_reply(reply, prev), (reply, False))
        prev = "The enrollment fee for Imus campus is listed in the schedule."
        self.assertFalse(dedupe_reply(prev.replace("Imus", "Bacoor"), prev)[1])
        prev = "Enrollment is in June 2026."
        self.assertFalse(dedupe_reply("Enrollment is in June 2027.", prev)[1])

    def test_no_previous(self):
        self.assertEqual(dedupe_reply("Hello there.", None), ("Hello there.", False))


class PushbackTests(unittest.TestCase):
    def test_is_pushback(self):
        for text in ("just create a list available online", "can you just search the website",
                     "i said give me the list", "hanapin mo na lang online"):
            self.assertTrue(is_pushback(text), text)
        self.assertFalse(is_pushback("where is the registrar"))

    def test_live_lookup(self):
        self.assertTrue(is_live_lookup("just create a list available online"))
        self.assertTrue(is_live_lookup("can you just search the website"))
        self.assertFalse(is_live_lookup("i said give me the list"))
        self.assertTrue(is_live_lookup("hanapin mo na lang online"))
        # "online"/"website"/"live" as a topic, not a fetch request.
        for text in ("no, I mean the online programs", "i mean online enrollment",
                     "just the online application steps", "i meant the CvSU website address",
                     "no, the live streaming of graduation", "give me the online programs",
                     "what is on the website for admission"):
            self.assertFalse(is_live_lookup(text), text)

    def test_wants_repeat(self):
        self.assertTrue(wants_repeat("can you repeat that"))
        self.assertTrue(wants_repeat("ulitin mo nga"))
        self.assertFalse(wants_repeat("why only trece?"))

    def test_pivot_reply(self):
        en = pivot_reply(False, True)
        self.assertIn("offline assistant", en)
        for fil in (True, False):
            for live in (True, False):
                urls = [u.rstrip(".,") for u in re.findall(r"https?://\S+", pivot_reply(fil, live))]
                self.assertEqual(urls, ["https://cvsu.edu.ph"], (fil, live))
        self.assertGreater(_filipino_ratio(pivot_reply(True, True)), 0.1)
        self.assertGreater(_filipino_ratio(pivot_reply(True, False)), 0.1)


class FollowupTests(unittest.TestCase):
    def test_followups(self):
        for text in ("why only trece?", "what about Imus?", "how about graduate school",
                     "that one?", "bakit lang trece?", "paano naman sa Imus?",
                     "just create a list available online",
                     "and the requirements?", "bakit trece lang?", "eh sa graduate school?",
                     "e paano kung transferee ako?", "is that free?"):
            self.assertTrue(is_contextual_followup(text), text)

    def test_followup_kind(self):
        self.assertEqual(followup_kind("just create a list available online"), "pushback")
        self.assertEqual(followup_kind("why only trece?"), "contrast")
        self.assertIsNone(followup_kind("where is the registrar"))

    def test_standalone_questions(self):
        for text in ("where is the registrar", "tuition fee",
                     "how can I enroll next year at CvSU as a freshman applicant",
                     "who is the president of CvSU",
                     "Why does CvSU require a medical certificate for all incoming freshmen applicants?",
                     "why is tuition free at CvSU?", "what if I fail the entrance exam",
                     "so how do I enroll", "bakit mahal ang tuition", "also where is the library",
                     "can i also apply for a scholarship", "no thanks", "no, thank you",
                     "just checking", "how about you?"):
            self.assertFalse(is_contextual_followup(text), text)

    def test_openers_with_their_own_topic_are_standalone(self):
        # Review regressions: each has a curated answer of its own.
        for text in ("is it possible to shift courses", "is it true that tuition is free",
                     "are there night classes", "how about the dorm fees",
                     "what about scholarships for athletes", "and how much is the tuition",
                     "ano naman ang requirements sa scholarship",
                     "bakit hindi pa lumalabas ang grades ko", "just give me the list of deans",
                     "and where is the clinic", "what about the CEIT building",
                     "no, I mean the online programs", "i mean online enrollment",
                     "just the online application steps", "no, the live streaming of graduation"):
            self.assertIsNone(followup_kind(text), text)

    def test_topic_free_followups(self):
        self.assertEqual(followup_kind("what about for graduate students?"), "contrast")
        self.assertEqual(followup_kind("why not Imus?"), "contrast")
        self.assertEqual(followup_kind("no, I mean Imus"), "pushback")
        self.assertEqual(followup_kind("you already said that"), "pushback")


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
