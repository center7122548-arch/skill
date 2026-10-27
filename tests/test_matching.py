import json
import sqlite3
import unittest

from server import MATCH_WEIGHTS, match_components, offer_rows


class MatchComponentsTests(unittest.TestCase):
    def score(self, **overrides):
        values = {
            "interests": ["เทคโนโลยี", "Python"],
            "desired_level": "เริ่มต้น",
            "wanted": {"mon-m", "tue-e"},
            "offer_title": "Python สำหรับผู้เริ่มต้น",
            "offer_category": "เทคโนโลยี",
            "offer_level": "เริ่มต้น",
            "offer_slots": {"mon-m"},
            "rating": 4.0,
            "review_count": 2,
        }
        values.update(overrides)
        return match_components(**values)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(MATCH_WEIGHTS.values()), 1.0)
        self.assertEqual(MATCH_WEIGHTS, {"skill": 0.40, "time": 0.30, "level": 0.15, "rep": 0.15})

    def test_skill_requires_both_keyword_and_category_for_full_score(self):
        self.assertEqual(self.score()["skill"], 1.0)
        self.assertEqual(self.score(interests=["เทคโนโลยี"])["skill"], 0.5)
        self.assertEqual(self.score(interests=["Python"])["skill"], 0.0)
        self.assertEqual(self.score(interests=["ดนตรี"])["skill"], 0.0)

    def test_time_score_is_overlap_divided_by_learner_slots(self):
        self.assertEqual(self.score()["time"], 0.5)
        self.assertEqual(self.score(wanted=set())["time"], 0.0)

    def test_level_score_matches_report_rules(self):
        self.assertEqual(self.score()["level"], 1.0)
        self.assertEqual(self.score(offer_level="ทุกระดับ")["level"], 1.0)
        self.assertEqual(self.score(offer_level="ขั้นสูง")["level"], 0.5)
        self.assertEqual(self.score(desired_level="ทุกระดับ", offer_level="เริ่มต้น")["level"], 0.5)

    def test_reputation_uses_average_over_five_and_cold_start_half(self):
        self.assertEqual(self.score()["rep"], 0.8)
        self.assertEqual(self.score(rating=0.0, review_count=0)["rep"], 0.5)

    def test_total_is_weighted_sum_of_components(self):
        result = self.score()
        expected = 0.40 * 1.0 + 0.30 * 0.5 + 0.15 * 1.0 + 0.15 * 0.8
        self.assertAlmostEqual(result["total"], expected)


class OfferRankingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, role TEXT, is_active INTEGER, full_name TEXT,
                faculty TEXT, bio TEXT, avatar_data TEXT, avatar_mime TEXT,
                interests TEXT, desired_level TEXT, preferred_slots TEXT
            );
            CREATE TABLE offers (
                id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, category TEXT,
                level TEXT, description TEXT, availability TEXT, slots TEXT,
                active INTEGER, deleted_at TEXT, created_at TEXT
            );
            CREATE TABLE reviews (
                id INTEGER PRIMARY KEY, request_id INTEGER, reviewee_id INTEGER, rating INTEGER
            );
            CREATE TABLE requests (
                id INTEGER PRIMARY KEY, requester_id INTEGER, provider_id INTEGER,
                duration_minutes INTEGER, status TEXT
            );
            CREATE TABLE ledger (user_id INTEGER, amount_minutes INTEGER);
            CREATE TABLE shop_equipped (user_id INTEGER, frame TEXT, sticker TEXT);
        """)
        users = [
            (1, "member", 1, "Learner", "", "", "", "", json.dumps(["เทคโนโลยี", "Python"]), "เริ่มต้น", json.dumps(["mon-m"])),
            (2, "member", 1, "Exact match", "", "", "", "", "[]", "", "[]"),
            (3, "member", 1, "Category only", "", "", "", "", "[]", "", "[]"),
        ]
        self.db.executemany("INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?)", users)
        offers = [
            (1, 1, "My Python class", "เทคโนโลยี", "เริ่มต้น", "", "", '["mon-m"]', 1, "", "2026-01-01"),
            (2, 2, "Python for beginners", "เทคโนโลยี", "เริ่มต้น", "", "", '["mon-m"]', 1, "", "2026-01-02"),
            (3, 3, "Other topic", "เทคโนโลยี", "เริ่มต้น", "", "", '["mon-m"]', 1, "", "2026-01-03"),
        ]
        self.db.executemany("INSERT INTO offers VALUES(?,?,?,?,?,?,?,?,?,?,?)", offers)

    def tearDown(self):
        self.db.close()

    def test_ranking_excludes_users_own_offers_and_orders_by_score(self):
        offers = offer_rows(self.db, 1)
        self.assertEqual([offer["user_id"] for offer in offers], [2, 3])
        self.assertGreater(offers[0]["match_score"], offers[1]["match_score"])
        self.assertEqual(offers[0]["score_detail"]["skill"], 100)
        self.assertEqual(offers[1]["score_detail"]["skill"], 50)

    def test_dashboard_recommendations_require_minimum_available_credit(self):
        self.assertEqual(offer_rows(self.db, 1, recommend=True), [])
        self.db.execute("INSERT INTO ledger(user_id,amount_minutes) VALUES(1,30)")
        offers = offer_rows(self.db, 1, recommend=True)
        self.assertEqual([offer["user_id"] for offer in offers], [2, 3])


if __name__ == "__main__":
    unittest.main()
