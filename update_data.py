#!/usr/bin/env python3
"""Rebuild top-two English results, Premier League xG, and historical odds.

The script deliberately uses only Python's standard library. Writes are atomic: an
invalid or partial download never replaces a previously valid output file.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


RESULTS_URL = (
    "https://raw.githubusercontent.com/seanelvidge/"
    "England-football-results/main/EnglandLeagueResults.csv"
)
UNDERSTAT_URL = "https://understat.com/getLeagueData/EPL/{season}"
FOOTBALL_DATA_URL = "https://www.football-data.co.uk/mmz4281/{season_code}/{division}.csv"
USER_AGENT = "EPL-Prediction data updater/1.0 (+weekly research use)"

RESULT_COLUMNS = [
    "Date", "Season", "HomeTeam", "AwayTeam", "Score", "hGoal", "aGoal",
    "Division", "Tier", "Result",
]
XG_COLUMNS = [
    "Date", "Season", "HomeTeam", "AwayTeam", "hGoal", "aGoal", "hXG",
    "aXG", "UnderstatMatchID", "RetrievedAtUTC",
]
ODDS_COLUMNS = [
    "Date", "Season", "Tier", "HomeTeam", "AwayTeam", "hGoal", "aGoal", "Result",
    "B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA", "MaxH", "MaxD", "MaxA",
    "B365CH", "B365CD", "B365CA", "PSCH", "PSCD", "PSCA",
    "AvgCH", "AvgCD", "AvgCA", "MaxCH", "MaxCD", "MaxCA", "BFECH", "BFECD", "BFECA",
    "MarketSource", "MarketH", "MarketD", "MarketA", "MarketOverround",
    "FairH", "FairD", "FairA", "RetrievedAtUTC",
]

# Understat uses shorter display names than England-football-results. Unlisted
# names are retained, then checked against the results data before output.
UNDERSTAT_TEAM_NAMES = {
    "Bournemouth": "AFC Bournemouth",
    "Brighton": "Brighton & Hove Albion",
    "Burton": "Burton Albion",
    "Cardiff": "Cardiff City",
    "Coventry": "Coventry City",
    "Crewe": "Crewe Alexandra",
    "Huddersfield": "Huddersfield Town",
    "Hull": "Hull City",
    "Ipswich": "Ipswich Town",
    "Leeds": "Leeds United",
    "Leicester": "Leicester City",
    "Luton": "Luton Town",
    "MK Dons": "Milton Keynes Dons",
    "Norwich": "Norwich City",
    "Stoke": "Stoke City",
    "Swansea": "Swansea City",
    "Tottenham": "Tottenham Hotspur",
    "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers",
}

FOOTBALL_DATA_TEAM_NAMES = {
    "Birmingham": "Birmingham City",
    "Blackburn": "Blackburn Rovers",
    "Bolton": "Bolton Wanderers",
    "Bournemouth": "AFC Bournemouth",
    "Bradford": "Bradford City",
    "Brighton": "Brighton & Hove Albion",
    "Burton": "Burton Albion",
    "Cardiff": "Cardiff City",
    "Charlton": "Charlton Athletic",
    "Colchester": "Colchester United",
    "Coventry": "Coventry City",
    "Crewe": "Crewe Alexandra",
    "Derby": "Derby County",
    "Doncaster": "Doncaster Rovers",
    "Grimsby": "Grimsby Town",
    "Huddersfield": "Huddersfield Town",
    "Hull": "Hull City",
    "Ipswich": "Ipswich Town",
    "Leeds": "Leeds United",
    "Leicester": "Leicester City",
    "Lincoln": "Lincoln City",
    "Luton": "Luton Town",
    "MK Dons": "Milton Keynes Dons",
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle United",
    "Norwich": "Norwich City",
    "Nott'm Forest": "Nottingham Forest",
    "Oldham": "Oldham Athletic",
    "Oxford": "Oxford United",
    "Peterboro": "Peterborough United",
    "Plymouth": "Plymouth Argyle",
    "Preston": "Preston North End",
    "QPR": "Queens Park Rangers",
    "Rotherham": "Rotherham United",
    "Scunthorpe": "Scunthorpe United",
    "Sheffield Weds": "Sheffield Wednesday",
    "Southend": "Southend United",
    "Stoke": "Stoke City",
    "Stockport": "Stockport County",
    "Swansea": "Swansea City",
    "Tottenham": "Tottenham Hotspur",
    "Tranmere": "Tranmere Rovers",
    "West Brom": "West Bromwich Albion",
    "West Ham": "West Ham United",
    "Wigan": "Wigan Athletic",
    "Wolves": "Wolverhampton Wanderers",
    "Wycombe": "Wycombe Wanderers",
    "Yeovil": "Yeovil Town",
}


class DataValidationError(RuntimeError):
    """Raised when a source response is structurally or logically invalid."""


def fetch(url: str, *, understat: bool = False, timeout: int = 60) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv,*/*;q=0.8"}
    if understat:
        headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://understat.com/league/EPL",
            "X-Requested-With": "XMLHttpRequest",
        })
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            encoding = response.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                body = gzip.decompress(body)
            elif encoding not in ("", "identity"):
                raise DataValidationError(
                    f"Unsupported content encoding {encoding!r} from {url}"
                )
            return body
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not download {url}: {exc}") from exc


def parse_top_two_results(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != RESULT_COLUMNS:
        raise DataValidationError(
            f"Results schema changed: expected {RESULT_COLUMNS}, got {reader.fieldnames}"
        )

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for line_number, row in enumerate(reader, 2):
        if row["Tier"] not in {"1", "2"}:
            continue
        try:
            date.fromisoformat(row["Date"])
            home_goals, away_goals = int(row["hGoal"]), int(row["aGoal"])
        except ValueError as exc:
            raise DataValidationError(f"Invalid results row {line_number}: {row}") from exc
        expected_result = "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D"
        if row["Score"] != f"{home_goals}-{away_goals}" or row["Result"] != expected_result:
            raise DataValidationError(f"Score/result mismatch on row {line_number}: {row}")
        key = (row["Date"], row["Season"], row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataValidationError(f"Duplicate result: {key}")
        seen.add(key)
        rows.append(row)

    if len(rows) < 100_000:
        raise DataValidationError(f"Only {len(rows):,} top-two results found; source may be incomplete")
    return rows


def season_label(start_year: int) -> str:
    return f"{start_year}/{start_year + 1}"


def parse_understat(payload: bytes, start_year: int, retrieved_at: str) -> list[dict[str, str]]:
    try:
        source = json.loads(payload)
        fixtures = source["dates"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DataValidationError("Understat response does not contain a valid dates array") from exc
    if not isinstance(fixtures, list) or not 300 <= len(fixtures) <= 400:
        raise DataValidationError(f"Unexpected Understat fixture count: {len(fixtures)}")

    rows = []
    ids = set()
    for fixture in fixtures:
        if fixture.get("isResult") is not True:
            continue
        try:
            match_id = str(fixture["id"])
            match_date = datetime.strptime(fixture["datetime"], "%Y-%m-%d %H:%M:%S").date()
            home = UNDERSTAT_TEAM_NAMES.get(fixture["h"]["title"], fixture["h"]["title"])
            away = UNDERSTAT_TEAM_NAMES.get(fixture["a"]["title"], fixture["a"]["title"])
            home_goals, away_goals = int(fixture["goals"]["h"]), int(fixture["goals"]["a"])
            home_xg, away_xg = float(fixture["xG"]["h"]), float(fixture["xG"]["a"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataValidationError(f"Malformed completed Understat fixture: {fixture}") from exc
        if match_id in ids or min(home_xg, away_xg) < 0:
            raise DataValidationError(f"Invalid or duplicate Understat match {match_id}")
        ids.add(match_id)
        rows.append({
            "Date": match_date.isoformat(),
            "Season": season_label(start_year),
            "HomeTeam": home,
            "AwayTeam": away,
            "hGoal": str(home_goals),
            "aGoal": str(away_goals),
            "hXG": format(home_xg, ".8g"),
            "aXG": format(away_xg, ".8g"),
            "UnderstatMatchID": match_id,
            "RetrievedAtUTC": retrieved_at,
        })
    return rows


def football_data_season_code(start_year: int) -> str:
    """Return Football-Data's four-digit season path, including century rollover."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def first_present(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = (row.get(name) or "").strip()
        if value:
            return value
    return ""


def valid_odds_triplet(values: Iterable[str]) -> tuple[float, float, float] | None:
    try:
        odds = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if len(odds) != 3 or any(value <= 1.0 for value in odds):
        return None
    return odds  # type: ignore[return-value]


def choose_market(row: dict[str, str]) -> tuple[str, tuple[float, float, float]]:
    """Choose one coherent 1X2 triplet; never mix maxima from different books."""
    candidates = [
        ("closing_average", ("AvgCH", "AvgCD", "AvgCA")),
        ("closing_pinnacle", ("PSCH", "PSCD", "PSCA")),
        ("closing_bet365", ("B365CH", "B365CD", "B365CA")),
        ("preclosing_average", ("AvgH", "AvgD", "AvgA")),
        ("preclosing_bet365", ("B365H", "B365D", "B365A")),
    ]
    for source, columns in candidates:
        odds = valid_odds_triplet(row.get(column, "") for column in columns)
        if odds:
            return source, odds
    return "", (0.0, 0.0, 0.0)


def legacy_bookmaker_mean(row: dict[str, str]) -> tuple[float, float, float] | None:
    """Average complete 1X2 triples used before aggregate market fields existed."""
    prefixes = ("1XB", "B365", "BW", "GB", "IW", "LB", "SB", "WH", "SJ", "VC", "BS")
    triplets = []
    for prefix in prefixes:
        triplet = valid_odds_triplet(row.get(f"{prefix}{outcome}", "") for outcome in "HDA")
        if triplet:
            triplets.append(triplet)
    if not triplets:
        return None
    return tuple(sum(values) / len(values) for values in zip(*triplets))  # type: ignore[return-value]


def parse_football_data(
    payload: bytes, start_year: int, tier: int, retrieved_at: str
) -> list[dict[str, str]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise DataValidationError(
            f"Football-Data schema missing {sorted(required - set(reader.fieldnames or []))}"
        )

    rows = []
    seen = set()
    for line_number, source in enumerate(reader, 2):
        # Some historical files contain trailing empty lines or unplayed fixtures.
        if not (source.get("Date") and source.get("HomeTeam") and source.get("FTHG")):
            continue
        try:
            raw_date = source["Date"].strip()
            try:
                match_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
            except ValueError:
                match_date = datetime.strptime(raw_date, "%d/%m/%y").date()
            home_goals, away_goals = int(source["FTHG"]), int(source["FTAG"])
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                f"Invalid Football-Data row {line_number} in {season_label(start_year)} tier {tier}"
            ) from exc
        result = "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D"
        if source["FTR"].strip() != result:
            raise DataValidationError(f"Football-Data score/result mismatch on row {line_number}")

        home = FOOTBALL_DATA_TEAM_NAMES.get(source["HomeTeam"].strip(), source["HomeTeam"].strip())
        away = FOOTBALL_DATA_TEAM_NAMES.get(source["AwayTeam"].strip(), source["AwayTeam"].strip())
        key = (season_label(start_year), str(tier), home, away)
        if key in seen:
            raise DataValidationError(f"Duplicate Football-Data fixture: {key}")
        seen.add(key)

        normalized = {column: "" for column in ODDS_COLUMNS}
        normalized.update({
            "Date": match_date.isoformat(), "Season": season_label(start_year), "Tier": str(tier),
            "HomeTeam": home, "AwayTeam": away, "hGoal": str(home_goals),
            "aGoal": str(away_goals), "Result": result, "RetrievedAtUTC": retrieved_at,
        })
        for column in (
            "B365H", "B365D", "B365A", "B365CH", "B365CD", "B365CA",
            "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA",
            "BFECH", "BFECD", "BFECA",
        ):
            normalized[column] = first_present(source, column)
        # Older files called the market aggregates BbMx/BbAv.
        for outcome in "HDA":
            normalized[f"Max{outcome}"] = first_present(source, f"Max{outcome}", f"BbMx{outcome}")
            normalized[f"Avg{outcome}"] = first_present(source, f"Avg{outcome}", f"BbAv{outcome}")
            normalized[f"AvgC{outcome}"] = first_present(source, f"AvgC{outcome}")

        market_source, market_odds = choose_market(normalized)
        if not market_source:
            legacy_market = legacy_bookmaker_mean(source)
            if legacy_market:
                market_source, market_odds = "preclosing_bookmaker_mean", legacy_market
        normalized["MarketSource"] = market_source
        if market_source:
            inverse = [1.0 / value for value in market_odds]
            overround = sum(inverse)
            normalized.update({
                "MarketH": format(market_odds[0], ".8g"),
                "MarketD": format(market_odds[1], ".8g"),
                "MarketA": format(market_odds[2], ".8g"),
                "MarketOverround": format(overround, ".8g"),
                "FairH": format(inverse[0] / overround, ".8g"),
                "FairD": format(inverse[1] / overround, ".8g"),
                "FairA": format(inverse[2] / overround, ".8g"),
            })
        rows.append(normalized)
    return rows


def validate_xg_against_results(
    xg_rows: Iterable[dict[str, str]], results_rows: Iterable[dict[str, str]]
) -> None:
    result_index = {
        (r["Season"], r["HomeTeam"], r["AwayTeam"]): (r["hGoal"], r["aGoal"], r["Date"])
        for r in results_rows if r["Tier"] == "1"
    }
    errors = []
    for row in xg_rows:
        key = (row["Season"], row["HomeTeam"], row["AwayTeam"])
        actual = result_index.get(key)
        if actual is None:
            errors.append(f"not in results: {key}")
        elif actual[:2] != (row["hGoal"], row["aGoal"]):
            errors.append(f"score differs: {key}, results={actual[:2]}, Understat={(row['hGoal'], row['aGoal'])}")
    if errors:
        preview = "\n  ".join(errors[:10])
        raise DataValidationError(f"Understat/results reconciliation failed:\n  {preview}")


def validate_odds_against_results(
    odds_rows: Iterable[dict[str, str]], results_rows: Iterable[dict[str, str]]
) -> None:
    result_index = {
        (r["Season"], r["Tier"], r["HomeTeam"], r["AwayTeam"]):
            (r["hGoal"], r["aGoal"], r["Date"])
        for r in results_rows
    }
    errors = []
    for row in odds_rows:
        key = (row["Season"], row["Tier"], row["HomeTeam"], row["AwayTeam"])
        actual = result_index.get(key)
        if actual is None:
            errors.append(f"not in results: {key}")
        elif actual[:2] != (row["hGoal"], row["aGoal"]):
            errors.append(f"score differs: {key}, results={actual[:2]}, odds={(row['hGoal'], row['aGoal'])}")
    if errors:
        preview = "\n  ".join(errors[:15])
        raise DataValidationError(f"Football-Data/results reconciliation failed:\n  {preview}")


def read_existing_xg(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != XG_COLUMNS:
            raise DataValidationError(f"Existing xG schema is unexpected: {reader.fieldnames}")
        return list(reader)


def read_existing_odds(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ODDS_COLUMNS:
            raise DataValidationError(f"Existing odds schema is unexpected: {reader.fieldnames}")
        return list(reader)


def atomic_write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def current_season_start(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-output", type=Path, default=Path("data/england_top2_results.csv"))
    parser.add_argument("--xg-output", type=Path, default=Path("data/epl_xg.csv"))
    parser.add_argument("--odds-output", type=Path, default=Path("data/football_data_odds.csv"))
    parser.add_argument("--season", type=int, default=current_season_start(), help="Latest season start year")
    parser.add_argument(
        "--incremental", action="store_true",
        help="Refresh only the current xG/odds season; the default is a complete rebuild",
    )
    parser.add_argument(
        "--request-delay", type=float, default=1.0,
        help="Seconds between source requests during a complete rebuild (default: 1)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    results = parse_top_two_results(fetch(RESULTS_URL))
    seasons = [args.season] if args.incremental else range(2014, args.season + 1)
    fresh_xg = []
    for request_number, start_year in enumerate(seasons):
        if request_number:
            time.sleep(max(0.0, args.request_delay))
        fresh_xg.extend(parse_understat(
            fetch(UNDERSTAT_URL.format(season=start_year), understat=True),
            start_year,
            retrieved_at,
        ))
    validate_xg_against_results(fresh_xg, results)

    odds_years = [args.season] if args.incremental else range(2000, args.season + 1)
    fresh_odds = []
    odds_requests = [(year, tier, division) for year in odds_years for tier, division in ((1, "E0"), (2, "E1"))]
    for request_number, (start_year, tier, division) in enumerate(odds_requests):
        if request_number:
            time.sleep(max(0.0, args.request_delay))
        fresh_odds.extend(parse_football_data(
            fetch(FOOTBALL_DATA_URL.format(
                season_code=football_data_season_code(start_year), division=division
            )),
            start_year,
            tier,
            retrieved_at,
        ))
    validate_odds_against_results(fresh_odds, results)

    replaced_seasons = {season_label(year) for year in seasons}
    retained_xg = [r for r in read_existing_xg(args.xg_output) if r["Season"] not in replaced_seasons]
    all_xg = sorted(retained_xg + fresh_xg, key=lambda r: (r["Date"], int(r["UnderstatMatchID"])))
    replaced_odds_seasons = {season_label(year) for year in odds_years}
    retained_odds = [
        r for r in read_existing_odds(args.odds_output) if r["Season"] not in replaced_odds_seasons
    ]
    all_odds = sorted(
        retained_odds + fresh_odds,
        key=lambda r: (r["Date"], int(r["Tier"]), r["HomeTeam"], r["AwayTeam"]),
    )

    atomic_write_csv(args.results_output, RESULT_COLUMNS, results)
    atomic_write_csv(args.xg_output, XG_COLUMNS, all_xg)
    atomic_write_csv(args.odds_output, ODDS_COLUMNS, all_odds)

    by_tier = Counter(row["Tier"] for row in results)
    print(f"Wrote {len(results):,} results to {args.results_output} (tier 1: {by_tier['1']:,}; tier 2: {by_tier['2']:,})")
    print(f"Wrote {len(all_xg):,} completed EPL xG matches to {args.xg_output} ({len(fresh_xg):,} refreshed)")
    market_rows = sum(bool(row["MarketSource"]) for row in all_odds)
    print(
        f"Wrote {len(all_odds):,} historical odds rows to {args.odds_output} "
        f"({len(fresh_odds):,} refreshed; {market_rows:,} with a benchmark market)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
