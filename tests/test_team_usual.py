"""
"Compared with your usual": 2026-10-06 was the team's first win together, and the replays showed why -- the
first kill in 78% of rounds against a usual 44%, and the team has won ~94% of rounds where it got the first kill
and ~9% where it didn't. The debrief should say that by itself, from code, every time.
"""
from types import SimpleNamespace as NS

from analysis import team_facts as tf

OURS = {"hector", "elijah"}


def stat(name, k, d):
    return NS(player=NS(name=name, is_team_member=False), kills=k, deaths=d, assists=0,
              engagements_won=0, engagements_taken=0, operator=NS(name=""), ability_start=0, ability_used=0)


def match(plan):
    """plan: [(outcome, we_got_first_kill, hector (k, d), elijah (k, d))]"""
    rounds, events = [], {}
    for i, (out, fk, h, e) in enumerate(plan, 1):
        rounds.append(NS(round_number=i, side="attack" if i <= len(plan) // 2 else "defense", outcome=out, site="",
                         player_stats=[stat("Hector", *h), stat("Elijah", *e), stat("Enemy", 1, 1)]))
        events[i] = {"opening_duel_won": fk, "kill_order": "elapsed"}
    return NS(rounds=rounds, map="Border", opponent_name="Imported", result="win"), events


def usual_night():
    # 8 rounds: first kill in 3 (all won), conceded 5 (won 1)
    return match([("win", True, (1, 0), (1, 1))] * 3 + [("loss", False, (0, 1), (0, 1))] * 4
                 + [("win", False, (1, 1), (1, 0))])


def big_night():
    return match([("win", True, (2, 0), (1, 0))] * 7 + [("loss", False, (0, 1), (0, 1))] * 2)


def baseline():
    return tf.usual_baseline([tf.match_record(*usual_night()[:1], OURS, usual_night()[1]) for _ in range(3)])


def test_too_little_history_means_no_comparison():
    m, ev = usual_night()
    two = [tf.match_record(m, OURS, ev)] * 2
    assert tf.usual_baseline(two) is None
    assert tf.usual_lines(tf.match_record(*big_night()[:1], OURS, big_night()[1]), None) == []


def test_a_one_round_package_is_never_compared():
    m, ev = match([("win", True, (1, 0), (1, 0))])
    assert tf.usual_lines(tf.match_record(m, OURS, ev), baseline()) == []


def test_a_night_that_wins_the_first_kill_is_called_out_against_the_usual():
    m, ev = big_night()
    lines = tf.usual_lines(tf.match_record(m, OURS, ev), baseline())
    text = "\n".join(lines)
    assert "we got it in 7 of 9 rounds (78%), well above your usual 38% over 3 earlier matches" in text
    assert "you won 100% of the rounds where you got it and 20% of the rounds where they did" in text
    assert "Here: 7 of 7 and 0 of 2." in text
    assert "Staying alive" in text and "Team K/D" in text
    assert all(line.startswith("- ") for line in lines)


def test_trades_plants_and_sites_are_compared_too():
    blank = {"rounds": 9, "won": 7, "fk": 0, "fk_won": 0, "conc": 0, "conc_won": 0, "k": 0, "d": 0,
             "player_rounds": 0, "survived": 0}
    this = {**blank, "deaths": 10, "traded": 4, "atk": 4, "planted": 3, "planted_won": 3, "unplanted_won": 0,
            "sites": {"defense|B Wine Cellar": [3, 2], "attack|2F Office": [2, 1]}}
    base = {**blank, "matches": 20, "deaths": 600, "traded": 54, "atk": 80, "planted": 25, "planted_won": 23,
            "unplanted_won": 21, "sites": {"defense|B Wine Cellar": [9, 2], "attack|2F Office": [3, 1]}}
    text = "\n".join(tf.usual_lines(this, base))
    assert "Trades: 4 of our 10 deaths were avenged within seconds (40%), well above your usual 9%" in text
    assert "won 92% of the attack rounds where you planted and 38% of those where you didn't" in text
    assert "Defending B Wine Cellar: 2-1 here; 2-7 in earlier matches." in text
    assert "2F Office" not in text                                     # only 3 earlier rounds there: too few to quote


def test_match_record_counts_traded_deaths_plants_and_sites_from_the_replay():
    m, ev = big_night()
    for r in m.rounds:
        r.site = "B Wine Cellar"
    ev[1]["kills"] = [{"victim": "Hector", "trade": True}, {"victim": "Enemy", "trade": True}, {"victim": "Elijah"}]
    ev[2]["bomb_planted"] = True
    rec = tf.match_record(m, OURS, ev)
    assert (rec["deaths"], rec["traded"]) == (2, 1)
    assert (rec["atk"], rec["planted"], rec["planted_won"]) == (4, 1, 1)
    assert rec["sites"]["attack|B Wine Cellar"] == [4, 4]
    base = tf.usual_baseline([rec, rec, rec])
    assert base["sites"]["attack|B Wine Cellar"] == [12, 12] and base["deaths"] == 6


def test_the_section_appears_in_the_report_after_round_patterns():
    m, ev = big_night()
    facts = tf.build_match_facts(m, OURS, ev, {}, usual=baseline())
    report = tf.assemble_report("## MATCH SUMMARY\nGood.\n## COMMUNICATION\nFine.", facts, "- drill")
    assert report.index("## ROUND PATTERNS") < report.index("## COMPARED WITH YOUR USUAL") < report.index("## WHAT TO FOCUS")
    facts = tf.build_match_facts(m, OURS, ev, {})                       # no history: no section at all
    assert "COMPARED WITH YOUR USUAL" not in tf.assemble_report("## MATCH SUMMARY\nGood.", facts, "- drill")


def test_the_engine_builds_the_usual_from_other_matches_only_and_never_fails(tmp_path):
    from analysis.intel_engine import IntelEngine

    class Repo:
        def __init__(self):
            import sqlite3
            self.path = tmp_path / "m.db"
            c = sqlite3.connect(self.path)
            c.execute("create table matches (match_id int)")
            c.execute("create table derived_metrics (match_id int, metric_name text, metric_text text)")
            for mid in (1, 2, 3, 4):
                c.execute("insert into matches values (?)", (mid,))
                c.execute("insert into derived_metrics values (?, 'our_players', '[\"Hector\", \"Elijah\"]')", (mid,))
            c.commit(); c.close()
            outer = self

            class DB:
                def get_connection(self):
                    import sqlite3
                    conn = sqlite3.connect(outer.path)
                    conn.row_factory = sqlite3.Row
                    return conn
            self.db = DB()

        def get_match_full(self, mid):
            if mid == 2:
                raise RuntimeError("broken match")
            return usual_night()[0]

    eng = IntelEngine.__new__(IntelEngine)
    eng._get_round_events = lambda mid: usual_night()[1]
    seen = []
    repo = Repo()
    orig = repo.get_match_full
    repo.get_match_full = lambda mid: (seen.append(mid), orig(mid))[1]
    base = eng._team_usual(repo, 4)
    assert 4 not in seen                                                # the match being judged is never its own yardstick
    assert base is None                                                 # only 2 usable earlier matches (2 is broken)
    eng._team_usual(None, 4)                                            # any trouble: no comparison, no exception
