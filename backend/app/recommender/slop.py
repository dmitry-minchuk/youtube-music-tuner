"""Local mass-generated-content heuristics (docs/05 section 4, "Слоп-фильтр").

YouTube radio drags farm-produced AI tracks into the graph: hashtag-stuffed
titles, "type beat" uploads, background-music formulas, artist names that
proudly say "A.I.". Nothing here calls outside or runs a model (ADR-002) —
it is a coarse net over metadata, calibrated on the live catalogue
(2026-08-09: 28 excluded, 53 penalised, zero false hits on likes). Tracks
with the listener's own positive signal are never scored at all.
"""

from __future__ import annotations

import re

# Score >= EXCLUDE removes the candidate from the pool outright; exactly
# PENALTY_SCORE subtracts SLOP_PENALTY from its ranking score.
SLOP_EXCLUDE_SCORE = 3
SLOP_PENALTY_SCORE = 2
SLOP_PENALTY = 0.6

_HASHTAG = re.compile(r"#\w+")
_EMOJI = re.compile(r"[\U0001f300-\U0001faff☀-➿]")
_AI_ARTIST = re.compile(r"(?i)(?:\b|_)(a\.?i\.?|prompt\w*|suno|udio)(?:\b|_)")
_TYPE_BEAT = re.compile(r"(?i)type beat")
_BACKGROUND = re.compile(
    r"(?i)(lounge music|for (endless )?(relaxation|study(ing)?|sleep)|no copyright|royalty.?free)"
)
_PIPE_TAIL = re.compile(r"\|[^|]{3,30}$")

TITLE_LENGTH_SUSPECT = 60


def slop_score(title: str, artist_names: list[str]) -> int:
    """How much the metadata smells of mass generation, in whole points."""
    score = 0
    hashtags = len(_HASHTAG.findall(title))
    if hashtags >= 2:
        score += 2
    elif hashtags == 1:
        score += 1
    if _TYPE_BEAT.search(title):
        score += 2
    if _BACKGROUND.search(title):
        score += 2
    if _EMOJI.search(title):
        score += 1
    if _PIPE_TAIL.search(title):
        score += 1
    if len(title) > TITLE_LENGTH_SUSPECT:
        score += 1
    if any(_AI_ARTIST.search(name) for name in artist_names):
        score += 2
    return score
