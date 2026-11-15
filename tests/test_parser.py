from bga_arknova.parser import parse_history_api_row, parse_history_row, parse_replay_payload, performance_rating


def test_history_row():
    row = {"title": "方舟动物园 #947744175", "date": "2026/11/14 14:58 48 mn",
           "results": "第1名 example_player 115 第2名 example_opponent 108", "rating": "+6 → 1952 分 4 → 703",
           "player_names": ["example_player", "example_opponent"], "player_ids": ["12345678", "87654321"]}
    game = parse_history_row(row, 12345678, "example_player")
    assert game["table_id"] == 947744175
    assert game["is_arena"] == 1
    assert game["player_elo_before"] == 1946
    assert game["won"] == 1


def test_replay_payload():
    got = parse_replay_payload([{"move_nbr": 32, "zoo_map": "A", "log": "example_player upgraded Animals action"}])
    assert got["moves"] == 32
    assert got["map_name"] == "A"
    assert got["action_animals"] == 1


def test_performance_rating_balanced():
    assert performance_rating(5, 5, 1800) == 1800


def test_history_api_keeps_arena_separate_from_elo():
    row = {"table_id": "1", "players": "12345678,2", "player_names": "example_player,opponent",
           "scores": "115,108", "ranks": "1,2", "start": "1794244753", "end": "1794246783",
           "elo_win": "8", "elo_after": "1959", "arena_win": "0.0014", "arena_after": "501.1880",
           "normalend": "1", "unranked": "0", "concede": "0"}
    game = parse_history_api_row(row, 12345678, "example_player")
    assert game["player_elo_after"] == 1959
    assert game["arena_rating_after"] == 501.188
    assert "arena_after" not in game

