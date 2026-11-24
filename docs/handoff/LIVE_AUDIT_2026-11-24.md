# Sevi live conversation audit, 2026-11-24

Run: 8 realistic sessions, 34 turns, in-process against `claude-haiku-4-5` (LLM tier) with the current uncommitted tree. Full transcript: `live_audit_2026-11-24_transcript.json`.
Routing mix: 21 curated (NB / NN / intent retrieval), 12 Claude, 1 scope gate. LLM turns took 1.2-5.7 s; curated turns ≤0.1 s.

**Score: 18 of 34 turns were both correct and natural.** 16 had a problem: 3 factual, 6 context or answer-fit, 7 tone or language.

## What works
- Context-aware LLM follow-ups: TOR → "how long does it take?" → "can my mom get it for me?" stayed on TOR, answered honestly, and suggested an authorization letter.
- Taglish in, Taglish out (shifting session); Filipino curated answers chosen for Filipino questions.
- "Assuming the Main Campus" shown once per session.
- Pushback pivot ("I'm an offline assistant…"), scope refusal (capital of France).
- No "Based on the excerpts…" openers.

## Findings (priority order)

| # | Sev | Turn | Problem | Likely fix |
|---|---|---|---|---|
| 1 | High | faculty list, shifting "ano requirements" | **Factual error:** Claude says "Office of the University Registrar (OSAS)". OSAS is Student Affairs, not the Registrar. Seen in 3 of 12 LLM turns across two runs. | Add the correct office names to the campus glossary / system prompt ("OSAS = Office of Student Affairs and Services; Registrar = OUR"), plus a guard rule for "Registrar (OSAS)". |
| 2 | High | "who is the dean of CEIT?" | Returns all 12 deans instead of "Dr. Willie C. Buclatin". | A deterministic dean lookup by college (the college_programs tier already detects college names). |
| 3 | High | "how about the vice presidents?" | Repeats the previous answer word for word (the same officials roster). The repeat check only covers LLM replies. | Run the cross-turn repeat check on curated replies too; when the same intent answers twice in a row, reply briefly ("That's the list above — anything specific?"). |
| 4 | High | "how do I enroll at Trece campus" | Generic 11-campus blurb; the question is not answered. | Satellite campus + enroll/apply verb → enrollment answer with a campus note, or LLM with Trece-scoped grounding. |
| 5 | Med | "what programs are offered there?" (after Imus) | Ignores "there" = Imus; gives the all-colleges list. The follow-up detector does not treat "there" as a back-reference. | Add "there / doon / dun" to the follow-up reference cues when a campus is in session state. |
| 6 | Med | "where is the registrar office?" | Describes the Registrar's services, never says where it is. | Let the Place Resolver answer "where is X" before the registrar intent, or add the location to the answer. |
| 7 | Med | "pwede ba kahit 2nd year na ko" (shifting) | The LLM answered about retention policy for Criminology/DVM: grounding pulled the wrong chunk because the bare follow-up was the retrieval query. | Build the follow-up's retrieval query from the previous user question plus the follow-up. |
| 8 | Med | "can you list all faculty members" | Meta leak: "The excerpts I have access to only mention…". | Add the "I have access to" variant to strip_rag_meta. |
| 9 | Med | "magandang araw po", "salamat po" | Filipino greeting/thanks answered in English: greeting, thanks and chitchat have no Filipino variants. | Add Filipino variants (picked automatically by the existing language ranking). |
| 10 | Med | python homework → "what can you help with?" | Both get the identical "Hi! I'm Sevi…" re-greeting; homework is never declined. The pick is deterministic, so chitchat always repeats. | A scope-decline line for homework/general asks; rotate variants within a session. |
| 11 | Low | "hi" | Always "Greetings! I'm here to assist you…" (stiff; the longest variant always wins). | Prefer the shortest variant for greeting/thanks, or rotate. |
| 12 | Low | "how much is the tuition?" | Correct and warm opener, but ~30 lines: answer first, then everything. | Trim to the answer + 3 lines + a "want the details?" offer. |
| 13 | Low | "why only trece?" (after a full list) | "I don't have enough context to answer that question" reads robotic (the question was odd in this session). | Prompt: ask a clarifying question without "I don't have enough context". |
| 14 | Low | dorm (FIL), several curated | Stock closers "Salamat sa inyong tanong!" and bare "Source: https://…" lines remain on untouched templates. | Extend the template rewrite to the remaining intents. |
| 15 | Low | apply (LLM) vs admissions (curated) | The two tiers give different application channels (charter "downloadable form, submit to OSAS" vs portal admission.cvsu.edu.ph). | Owner decision on the current process; align charter-grounded answers. |

## Fixes applied (same day), verified live
| # | Status | Change |
|---|---|---|
| 1 | Fixed | OSAS/OUR added to the LLM glossary + "Registrar (OSAS)" → "Registrar" net in strip_rag_meta. Live: Claude now writes "Office of the University Registrar (OUR)". |
| 2 | Fixed | Step 0.75 Dean Lookup (hybrid_chatbot._dean_lookup_reply): one dean when a college is named (abbreviation, full name, or subject word like "nursing"), read from the curated college_deans answer. |
| 3 | Fixed | chat(): a curated answer identical to the previous reply → another same-language variant, else a short pointer. Explicit "repeat"/same question still repeats; refusals exempt. |
| 5 | Fixed | "there/doon/dun/that campus" with a campus in session → follow-up kind "place": grounding scoped to that campus, "there" resolved in the prompt. Live: answers about Imus. |
| 8 | Fixed | strip_rag_meta: "I have access to", "in my current knowledge base / set of excerpts", "I don't have enough context…" opener, and a catch-all "The excerpt <verb>s" → "My information <verb>s". |
| 9 | Fixed | Filipino greeting/thanks/chitchat variants (responses_map.json, cavsu_intents.json, DB); stiff "Greetings!" variant reworded. |
| 10 | Fixed | chat(): chitchat/greeting that ScopeGate rejects (homework etc.) → scope refusal. Variants rotate on a repeat. |
| 4, 6, 7, 11-15 | Open | Trece enrollment, Registrar location, tuition length, leftover stock closers, charter vs portal application channel. #7 improved live (shifting context kept). |
New test: test_live_audit_fixes.py (15 checks). All 30 test scripts pass; lint clean; classifier at pinned hash.
Side note: replies starting "I don't have that specific information yet" are labelled "<provider> (out-of-scope)" by the existing prose-refusal check, so OOS metrics count them as refusals.
