"""Seven-sect decisions ported from wxjerry/xiuxian 21900044."""
import itertools
import math
import re

SECTS = ("黄枫谷", "掩月宗", "灵兽山", "清虚门", "化刀坞", "天阙堡", "巨剑门")


def observation(text):
    match = re.search(r"【密议·第\s*(\d+)\s*关】准\s*\d+\s*:\s*驳\s*\d+，([^\n]+?) 出征：(关隘无恙|暗通\s*(\d+)\s*张)", text)
    if not match:
        return None
    return {"round": int(match[1]), "sects": [s for s in SECTS if s in match[2]], "sabotages": int(match[4] or 0)}


def choice(count, observations):
    """Weight all 21 mole pairs by the public outcomes; each mole acts with p=.7."""
    worlds = []
    for pair in itertools.combinations(SECTS, 2):
        weight = 1.0
        for obs in observations:
            n, k = len(set(pair) & set(obs["sects"])), obs["sabotages"]
            weight *= math.comb(n, k) * .7 ** k * .3 ** (n - k) if 0 <= k <= n else 0
        worlds.append((set(pair), weight))
    if not any(weight for _, weight in worlds):
        return ()  # Contradictory evidence is not permission to reset knowledge.
    return max(itertools.combinations(SECTS, count),
               key=lambda selected: round(sum(w * .3 ** len(pair & set(selected)) for pair, w in worlds), 12))


def panel(text, members, observations):
    nominate = re.search(r"第\s*(\d)\s*关（守住\s*\d+，失守\s*\d+）：主帅\s*@([^\s]+)\s*请\s*\.点将\s*(\d)\s*家", text)
    if nominate:
        stage, commander, count = int(nominate[1]), nominate[2].lower(), int(nominate[3])
        actor = next((m["identity_id"] for m in members if m["username"].lower() == commander), None)
        selected = choice(count, observations) if 2 <= count <= 4 else ()
        return stage * 2 - 1, 120, [(actor, ".点将 " + " ".join(selected))] if actor and selected else []
    vote = re.search(r"【密议·第\s*(\d)\s*关】主帅\s*@([^\s]+)\s*点将：", text)
    if vote and ".准" in text:
        return int(vote[1]) * 2, 60, [(m["identity_id"], ".准") for m in members if m["username"].lower() != vote[2].lower()]
    return None
