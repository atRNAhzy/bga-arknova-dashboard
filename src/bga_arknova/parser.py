from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ACTION_ALIASES = {
    "animals": ("animals", "animal", "动物"),
    "build": ("build", "building", "建造"),
    "cards": ("cards", "card", "卡牌"),
    "association": ("association", "协会"),
    "sponsors": ("sponsors", "sponsor", "赞助商"),
}


def parse_local_datetime(text: str, now: datetime | None = None) -> str | None:
    now = now or datetime.now()
    text = text.strip()
    hm = re.search(r"(\d{1,2}):(\d{2})", text)
    if not hm:
        return None
    if "昨天" in text or "yesterday" in text.lower():
        day = (now - timedelta(days=1)).date()
    else:
        m = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", text)
        day = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date() if m else now.date()
    return datetime.combine(day, datetime.min.time()).replace(hour=int(hm.group(1)), minute=int(hm.group(2))).isoformat()


def parse_history_row(row: dict[str, Any], player_id: int, player_name: str) -> dict[str, Any]:
    """Normalize one row collected from the gamestats DOM."""
    text = " ".join(str(row.get(k, "")) for k in ("title", "date", "results", "rating"))
    table_match = re.search(r"#?(\d{7,})", text)
    if not table_match:
        raise ValueError(f"No table id in row: {text[:200]}")
    table_id = int(table_match.group(1))
    duration = re.search(r"(\d+)\s*(?:mn|min)", text, re.I)
    ids = [int(x) for x in row.get("player_ids", []) if str(x).isdigit()]
    names = row.get("player_names", [])
    ranks = re.findall(r"(?:第\s*)?(\d+)\s*(?:名|st|nd|rd|th)", row.get("results", ""), re.I)
    won = bool(ranks and ranks[0] == "1") if names and names[0] == player_name else player_name in row.get("results", "").split("第2名")[0]
    elo = re.search(r"([+-]?\d+(?:\.\d+)?)\s*→\s*(\d+(?:\.\d+)?)\s*分", row.get("rating", ""))
    arena = re.findall(r"([+-]?\d+(?:\.\d+)?)\s*→\s*(\d+(?:\.\d+)?)", row.get("rating", ""))
    elo_delta = float(elo.group(1)) if elo else None
    elo_after = float(elo.group(2)) if elo else None
    arena_pair = arena[-1] if len(arena) > 1 else None
    surrendered = bool(re.search(r"认输|投降|conced|resign", text, re.I))
    opponent_name = next((n for n in names if n != player_name), None)
    opponent_id = next((i for i in ids if i != player_id), None)
    result_text = row.get("results", "")
    def score_after(name: str | None) -> float | None:
        if not name:
            return None
        match = re.search(rf"{re.escape(name)}\s+(-?\d+(?:\.\d+)?)", result_text, re.I)
        return float(match.group(1)) if match else None
    player_score = score_after(player_name)
    opponent_score = score_after(opponent_name)
    return {
        "table_id": table_id,
        "played_at": parse_local_datetime(row.get("date", "")),
        "duration_minutes": int(duration.group(1)) if duration else None,
        "player_name": player_name,
        "opponent_name": opponent_name,
        "player_id": player_id,
        "opponent_id": opponent_id,
        "player_score": player_score,
        "opponent_score": opponent_score,
        "won": int(won),
        "player_elo_before": elo_after - elo_delta if elo_after is not None and elo_delta is not None else None,
        "player_elo_after": elo_after,
        "elo_delta": elo_delta,
        "arena_before": float(arena_pair[1]) - float(arena_pair[0]) if arena_pair else None,
        "arena_after": float(arena_pair[1]) if arena_pair else None,
        "arena_delta": float(arena_pair[0]) if arena_pair else None,
        "is_arena": int(arena_pair is not None),
        "surrendered": int(surrendered),
        "unfinished": 0,
        "raw_history": row,
    }


def parse_history_api_row(row: dict[str, Any], player_id: int, player_name: str) -> dict[str, Any]:
    """Normalize the structured getGames response without confusing Elo and Arena rating."""
    ids = [int(x) for x in str(row.get("players") or "").split(",") if x]
    names = str(row.get("player_names") or "").split(",") if row.get("player_names") else []
    scores = str(row.get("scores") or "").split(",") if row.get("scores") is not None else []
    ranks = str(row.get("ranks") or "").split(",") if row.get("ranks") is not None else []
    try:
        own_index = ids.index(player_id)
    except ValueError:
        own_index = names.index(player_name) if player_name in names else 0
    opponent_index = next((i for i in range(len(ids)) if i != own_index), None)

    def number_at(values: list[str], index: int | None) -> float | None:
        if index is None or index >= len(values) or values[index] in ("", None):
            return None
        try:
            return float(values[index])
        except (TypeError, ValueError):
            return None

    def number(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    start = int(row.get("start") or 0)
    end = int(row.get("end") or 0)
    timestamp = end or start
    played_at = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).replace(tzinfo=None).isoformat() if timestamp else None
    elo_after = number(row.get("elo_after"))
    elo_delta = number(row.get("elo_win"))
    penalty = number(row.get("elo_penalty")) or 0
    if elo_delta is not None:
        elo_delta += penalty
    arena_after = number(row.get("arena_after"))
    arena_delta = number(row.get("arena_win"))
    result = row.get("gameResult") or {}
    surrendered = str(result.get("outcome", "")).lower() == "concede" or str(row.get("concede")) == "1"
    unfinished = str(row.get("normalend")) != "1" or not scores or not ranks
    return {
        "table_id": int(row["table_id"]),
        "played_at": played_at,
        "duration_minutes": round((end - start) / 60) if start and end and end >= start else None,
        "player_name": player_name,
        "opponent_name": names[opponent_index] if opponent_index is not None and opponent_index < len(names) else None,
        "player_id": player_id,
        "opponent_id": ids[opponent_index] if opponent_index is not None and opponent_index < len(ids) else None,
        "player_score": number_at(scores, own_index),
        "opponent_score": number_at(scores, opponent_index),
        "won": int(number_at(ranks, own_index) == 1) if ranks else 0,
        "player_elo_before": elo_after - elo_delta if elo_after is not None and elo_delta is not None else None,
        "player_elo_after": elo_after,
        "elo_delta": elo_delta,
        # BGA's arena_after/arena_win are internal fractional rating values, not displayed Elo.
        "arena_rating_before": arena_after - arena_delta if arena_after is not None and arena_delta is not None else None,
        "arena_rating_after": arena_after,
        "arena_rating_delta": arena_delta,
        "is_arena": int(arena_after is not None and str(row.get("unranked")) != "1"),
        "surrendered": int(surrendered),
        "unfinished": int(unfinished),
        "raw_history": row,
    }


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def parse_replay_payload(payloads: list[Any], visible_text: str = "", player_id: int | None = None) -> dict[str, Any]:
    """Best-effort parser across old/new BGA replay formats; unknowns stay NULL."""
    all_text_parts = [visible_text]
    max_move = 0
    opponent_elos: list[float] = []
    map_candidates: list[str] = []
    upgrades = {name: False for name in ACTION_ALIASES}
    diagnostics: dict[str, Any] = {"payload_count": len(payloads)}
    for root in payloads:
        for value in _walk(root):
            if isinstance(value, str):
                all_text_parts.append(value)
            elif isinstance(value, dict):
                for key, val in value.items():
                    lk = str(key).lower()
                    if lk in {"move_nbr", "moveno", "turn_number", "turn_no"} and str(val).isdigit():
                        max_move = max(max_move, int(val))
                    if lk in {"map", "map_id", "zoo_map", "zoo_map_id"} and val not in (None, "") and not isinstance(val, (dict, list)):
                        map_candidates.append(str(val))
                if str(value.get("name", "")).lower() == "map" and isinstance(value.get("valuelabels"), dict):
                    labels = [str(v) for v in value["valuelabels"].values() if v]
                    if labels:
                        map_candidates.append(labels[0])
                identity = value.get("player_id", value.get("id"))
                elo_value = value.get("player_elo", value.get("elo"))
                if player_id is not None and str(identity).isdigit() and int(identity) != player_id:
                    try:
                        e = float(elo_value)
                        if 500 <= e <= 4000:
                            opponent_elos.append(e)
                    except (TypeError, ValueError):
                        pass
    text = "\n".join(all_text_parts)
    if not max_move:
        nums = [int(x) for x in re.findall(r"(?:move|turn|回合|行动)\s*#?\s*(\d+)", text, re.I)]
        max_move = max(nums, default=0)
    for name, aliases in ACTION_ALIASES.items():
        alias = "|".join(map(re.escape, aliases))
        upgrades[name] = bool(re.search(rf"(?:upgrade|upgraded|升级).{{0,80}}(?:{alias})|(?:{alias}).{{0,80}}(?:upgrade|upgraded|升级)", text, re.I | re.S))
    map_match = re.search(r"\bMap\s+(\d+)\s*:\s*([^\n\r\"}]+)", text, re.I)
    map_name = map_candidates[0] if map_candidates else (f"Map {map_match.group(1)}: {map_match.group(2).strip()}" if map_match else None)
    if map_name:
        structured = re.search(r"Map\s+(\d+)\s*:\s*([^\n\r\"}]+)", map_name, re.I)
        if structured:
            map_name = f"Map {structured.group(1)}: {structured.group(2).strip()}"
        elif map_name.lower() in {"bonus", "map", "none", "null"} or map_name.startswith(("{", "[")):
            map_name = None
    diagnostics.update({"text_chars": len(text), "map_candidates": map_candidates[:10]})
    return {
        "map_name": map_name,
        "moves": max_move or None,
        "opponent_elo": round(sum(opponent_elos) / len(opponent_elos), 1) if opponent_elos else None,
        **{f"action_{k}": int(v) for k, v in upgrades.items()},
        "replay_parsed": 1,
        "diagnostics": diagnostics,
    }


def performance_rating(wins: int, losses: int, opponent_mean: float) -> float:
    return opponent_mean + 400 * math.log10((wins + 0.5) / (losses + 0.5))

