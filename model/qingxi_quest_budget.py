"""Wall-time bounds shared by the Qingxi mainline parent and browser worker."""

QUEST_IDENTITY_SEC = 90
QUEST_ENTRY_SEC = 650
QUEST_HOME_SEC = 900
QUEST_HEALTH_SEC = 900
QUEST_INSPECT_SEC = 60
QUEST_MAINLINE_SEC = 900
QUEST_MAINLINE_DRAIN_SEC = 60
QUEST_FINISH_SEC = 240  # Browser startup, final status, shutdown and polling slack.

# One initial entry, at most one return-and-reentry, and one mainline run.
QUEST_WORKER_SEC = sum((
    QUEST_IDENTITY_SEC, QUEST_ENTRY_SEC, QUEST_HOME_SEC, QUEST_HEALTH_SEC,
    QUEST_ENTRY_SEC, QUEST_INSPECT_SEC, QUEST_MAINLINE_SEC, QUEST_MAINLINE_DRAIN_SEC,
    QUEST_FINISH_SEC,
))
