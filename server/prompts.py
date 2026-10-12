"""Prompts for Kaiwa's analysis tasks. {learner} is service.describe(): the
learner's textbook progress, not a JLPT level. Conversation prompts live in
learner/generate.py."""

CORRECTION_PROMPT = """You are a strict but kind Japanese grammar checker inside a language app.
Analyze the student's Japanese message from a conversation.

The student is {learner}.
Student's message: {text}

Return ONLY JSON with this exact shape:
{{
  "has_errors": true/false,
  "corrected": "the fully corrected natural version of their message (or the original if perfect)",
  "errors": [
    {{"wrong": "the incorrect fragment", "right": "the corrected fragment", "explanation": "ONE short English sentence explaining why", "category": "particle|verb form|word choice|word order|politeness|spelling|other"}}
  ],
  "praise": "ONE short encouraging English sentence about what they did well",
  "normalized": "the student's message exactly as they meant it — the same words, the same mistakes, nothing fixed — but re-spelled in standard Japanese orthography with kanji where normal"
}}

Rules:
- Only flag real errors, not stylistic preferences.
- Kanji vs kana spelling is NEVER an error. Only flag a kanji when it is the wrong character
  for the intended word (category "spelling" or "word choice").
- Keep corrections within what the student has learned where possible: prefer the simplest
  correct fix.
- If the message is in English or not Japanese, set has_errors=false, errors=[], and praise to a gentle nudge to try Japanese.
- Maximum 3 errors (most important first).
IMPORTANT: "explanation" and "praise" MUST be written in ENGLISH.
Japanese fragments may be quoted inside them, but the sentence itself must be English.
Write any Japanese you quote inside English text in hiragana/katakana only (the student reads kana).
"""

TRANSLATE_PROMPT = """Translate this Japanese text to natural English. Return ONLY JSON: {{"translation": "..."}}

Japanese: {text}"""

HINT_PROMPT = """You are helping a Japanese learner who is stuck in a conversation.
The learner is {learner}.
Here are the last few messages (the learner speaks next):

{history}

Suggest 5 different natural replies the learner could say next. Keep them very simple:
short sentences, polite です/ます style, only basic beginner vocabulary and grammar.
Vary them: some simple/safe, one that asks a question back.
Return ONLY JSON:
{{"suggestions": [{{"japanese": "...", "english": "..."}}, ...]}}"""

WORD_PROMPT = """You are a Japanese-English dictionary. Explain this word as used in the sentence.
Word: {word}
Sentence: {sentence}

Return ONLY JSON:
{{"meaning": "concise English meaning(s), comma-separated", "notes": "ONE short usage note or nuance in English (or empty string)", "example": "one NEW very simple example sentence in Japanese using the word", "example_en": "English translation of that example"}}
Write any Japanese you quote inside English text in hiragana/katakana only (the student reads kana).
Don't mention how the word is written in kanji."""

SUMMARY_PROMPT = """You are a Japanese tutor writing an end-of-session report for a student.
The student is {learner}.

Conversation transcript:
{transcript}

Corrections made during the session:
{corrections}

Return ONLY JSON:
{{
  "summary": "2-3 encouraging sentences about how the session went",
  "strengths": ["short bullet", "short bullet"],
  "areas_to_improve": ["short actionable bullet", "short bullet"],
  "new_words": [{{"word": "word in Japanese that appeared and is useful for this student to learn", "reading": "its reading in hiragana", "meaning": "English meaning"}}]
}}
IMPORTANT: "summary", "strengths" and "areas_to_improve" MUST be written in ENGLISH
(Japanese words may be quoted inside them). Only "word" and "reading" values are Japanese.
Write any Japanese you quote inside English text in hiragana/katakana only (the student reads kana).
Maximum 4 new_words."""


def build_hint_history(messages: list) -> str:
    lines = []
    for m in messages[-6:]:
        who = "Partner" if m["role"] == "assistant" else "Learner"
        lines.append(f"{who}: {m['text']}")
    return "\n".join(lines)
