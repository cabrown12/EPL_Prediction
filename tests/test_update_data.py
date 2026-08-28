import csv
import io
import json
import unittest
from datetime import date

import update_data


class UpdateDataTests(unittest.TestCase):
    def test_current_season_start(self):
        self.assertEqual(update_data.current_season_start(date(2026, 8, 28)), 2026)
        self.assertEqual(update_data.current_season_start(date(2026, 2, 1)), 2025)
        self.assertEqual(update_data.football_data_season_code(2000), "0001")
        self.assertEqual(update_data.football_data_season_code(2026), "2627")

    def test_football_data_closing_average_is_devigged(self):
        fields = ["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
                  "AvgH", "AvgD", "AvgA", "AvgCH", "AvgCD", "AvgCA"]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "Div": "E0", "Date": "21/08/2026", "HomeTeam": "Brighton",
            "AwayTeam": "Wolves", "FTHG": "2", "FTAG": "1", "FTR": "H",
            "AvgH": "2.1", "AvgD": "3.4", "AvgA": "3.5",
            "AvgCH": "2.0", "AvgCD": "3.5", "AvgCA": "4.0",
        })
        rows = update_data.parse_football_data(
            stream.getvalue().encode(), 2026, 1, "2026-08-28T00:00:00+00:00"
        )
        self.assertEqual(rows[0]["MarketSource"], "closing_average")
        self.assertEqual(rows[0]["HomeTeam"], "Brighton & Hove Albion")
        self.assertAlmostEqual(sum(float(rows[0][key]) for key in ("FairH", "FairD", "FairA")), 1)

    def test_parse_understat_keeps_completed_matches_only(self):
        completed = {
            "id": "123", "isResult": True,
            "h": {"title": "Brighton"}, "a": {"title": "Wolves"},
            "goals": {"h": "2", "a": "1"}, "xG": {"h": "1.75", "a": "0.8"},
            "datetime": "2026-08-22 14:00:00",
        }
        future = dict(completed, id="124", isResult=False)
        payload = json.dumps({"dates": [completed, future] + [future] * 298}).encode()
        rows = update_data.parse_understat(payload, 2026, "2026-08-28T00:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["HomeTeam"], "Brighton & Hove Albion")
        self.assertEqual(rows[0]["AwayTeam"], "Wolverhampton Wanderers")

    def test_results_score_mismatch_is_rejected(self):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=update_data.RESULT_COLUMNS)
        writer.writeheader()
        for number in range(100_000):
            writer.writerow({
                "Date": "2026-08-21", "Season": "2026/2027",
                "HomeTeam": f"Home {number}", "AwayTeam": f"Away {number}",
                "Score": "1-0", "hGoal": "1", "aGoal": "0", "Division": "Premier League",
                "Tier": "1", "Result": "A" if number == 0 else "H",
            })
        with self.assertRaises(update_data.DataValidationError):
            update_data.parse_top_two_results(stream.getvalue().encode())


if __name__ == "__main__":
    unittest.main()
