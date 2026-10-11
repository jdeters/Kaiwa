"""Learner model + constrained generation (Phase 1).

Keeps an explicit record of what the learner knows (vocabulary from Anki and
textbook progress, grammar from a canonical concept list) and checks generated
Japanese against it before the learner sees it.
"""
