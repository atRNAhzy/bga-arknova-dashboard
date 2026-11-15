from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
  table_id INTEGER PRIMARY KEY,
  played_at TEXT,
  duration_minutes INTEGER,
  player_name TEXT,
  opponent_name TEXT,
  player_id INTEGER,
  opponent_id INTEGER,
  player_score REAL,
  opponent_score REAL,
  won INTEGER,
  player_elo_before REAL,
  player_elo_after REAL,
  elo_delta REAL,
  opponent_elo REAL,
  arena_before REAL,
  arena_after REAL,
  arena_delta REAL,
  arena_rating_before REAL,
  arena_rating_after REAL,
  arena_rating_delta REAL,
  is_arena INTEGER DEFAULT 0,
  surrendered INTEGER DEFAULT 0,
  unfinished INTEGER DEFAULT 0,
  map_name TEXT,
  moves INTEGER,
  replay_url TEXT,
  replay_parsed INTEGER DEFAULT 0,
  action_animals INTEGER,
  action_build INTEGER,
  action_cards INTEGER,
  action_association INTEGER,
  action_sponsors INTEGER,
  diagnostics TEXT,
  raw_history TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    existing = {row[1] for row in con.execute("PRAGMA table_info(games)")}
    for column in ("arena_rating_before", "arena_rating_after", "arena_rating_delta"):
        if column not in existing:
            con.execute(f"ALTER TABLE games ADD COLUMN {column} REAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def upsert_game(con: sqlite3.Connection, game: dict[str, Any]) -> None:
    keys = list(game)
    values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in game.values()]
    assignments = ",".join(f"{k}=excluded.{k}" for k in keys if k != "table_id")
    sql = f"INSERT INTO games ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)}) " \
          f"ON CONFLICT(table_id) DO UPDATE SET {assignments}, updated_at=CURRENT_TIMESTAMP"
    con.execute(sql, values)

