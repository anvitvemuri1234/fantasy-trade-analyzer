import os
import json
import requests
from engine import (
    prepare_roster,
    fast_lineup_score,
    find_2team_trades
)

BASE_URL = "https://api.sleeper.app/v1"
CACHE_FILE = "nfl_players_cache.json"
PROJECTIONS_CACHE_FILE = "nfl_projections_cache.json"

def fetch_json(url: str):
    try:
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Request error for {url}: {e}")
    return None

def get_player_database():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    print("Downloading master player database...")
    data = fetch_json(f"{BASE_URL}/players/nfl") or {}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data

def get_weekly_projections(season: str = "2026", week: str = "5"):
    if os.path.exists(PROJECTIONS_CACHE_FILE):
        with open(PROJECTIONS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    print("Downloading weekly projections...")
    data = fetch_json(f"{BASE_URL}/projections/nfl/regular/{season}/{week}") or {}
    with open(PROJECTIONS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data

def parse_league(league_id: str):
    league = fetch_json(f"{BASE_URL}/league/{league_id}")
    users = fetch_json(f"{BASE_URL}/league/{league_id}/users") or []
    rosters = fetch_json(f"{BASE_URL}/league/{league_id}/rosters") or []
    
    user_map = {u["user_id"]: u.get("display_name", "Unknown") for u in users}
    player_db = get_player_database()
    projections_db = get_weekly_projections()

    parsed_teams = []
    for r in rosters:
        owner_name = user_map.get(r.get("owner_id"), f"Team_{r.get('roster_id')}")
        raw_ids = r.get("players") or []
        starters = set(r.get("starters") or [])

        team_players = []
        for pid in raw_ids:
            p_info = player_db.get(str(pid), {})
            proj_data = projections_db.get(str(pid), {})
            pts = proj_data.get("pts_ppr", 0.0)

            team_players.append({
                "player_id": str(pid),
                "name": p_info.get("full_name") or f"Player {pid}",
                "position": p_info.get("position", "N/A"),
                "team": p_info.get("team", "FA"),
                "is_starter": str(pid) in starters,
                "live_proj": float(pts),
                "injury_status": p_info.get("injury_status") # <--- REAL TIME API DATA
            })

        parsed_teams.append({
            "roster_id": r.get("roster_id"),
            "owner": owner_name,
            "players": team_players
        })
    return league, parsed_teams

def main():
    LEAGUE_ID = "1382555726110539776"
    print("\nSyncing Sleeper league data...")
    league_meta, teams = parse_league(LEAGUE_ID)
    print(f"Loaded: {league_meta.get('name')} ({len(teams)} teams)\n")

    # 1. Team Selection Menu
    print("--- Select Your Team ---")
    for i, t in enumerate(teams, 1):
        print(f"[{i}] {t['owner']}")
    
    choice = input("\nEnter your team number (e.g. 3): ").strip()
    idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(teams) else 0
    my_team = teams[idx]
    other_teams = [t for i, t in enumerate(teams) if i != idx]

    prepare_roster(my_team["players"])
    my_base = fast_lineup_score(my_team["players"])
    
    print(f"\nActive Manager: {my_team['owner']}")
    print(f"Starting Lineup Baseline: {my_base} pts/wk\n")

    # 2. Anchor Player Selection
    print("--- Roster Players ---")
    skill_players = [p for p in my_team["players"] if p["position"] in ["QB", "RB", "WR", "TE"]]
    for i, p in enumerate(skill_players, 1):
        pts = p.get("live_proj", 0.0)
        print(f"  [{i}] {p['name']} ({p['position']}) - Proj: {pts:.1f} pts")

    anchor_input = input("\nEnter a player # to trade AWAY (or press Enter to search all): ").strip()
    anchor_id = None
    if anchor_input.isdigit() and 1 <= int(anchor_input) <= len(skill_players):
        selected_player = skill_players[int(anchor_input) - 1]
        anchor_id = selected_player["player_id"]
        print(f">> Anchoring trades around: {selected_player['name']}\n")

    # 3. Target Partner Selection
    print("--- Trade Partner ---")
    print("[0] Scan Entire League (All Teams)")
    for i, t in enumerate(other_teams, 1):
        print(f"[{i}] {t['owner']}")
    
    partner_choice = input("\nEnter team number to target (or 0 for all): ").strip()
    
    if partner_choice.isdigit() and 1 <= int(partner_choice) <= len(other_teams):
        target_opponents = [other_teams[int(partner_choice) - 1]]
        print(f">> Targeting specific trades with: {target_opponents[0]['owner']}\n")
    else:
        target_opponents = other_teams
        print(">> Scanning entire league...\n")

    # 4. Search and Evaluate
    all_2team_trades = []
    
    # If scanning the whole league, limit to the top 2 best trades per opponent
    # so one team doesn't flood all 15 slots
    max_per_team = 2 if len(target_opponents) > 1 else 15

    for opponent in target_opponents:
        trades = find_2team_trades(my_team, opponent, anchor_id=anchor_id)
        # Take only the best N trades from this specific opponent
        all_2team_trades.extend(trades[:max_per_team])

    # Now sort the diverse league-wide list by your lineup gain
    all_2team_trades.sort(key=lambda x: (x["delta_my_team"], x["net_vorp_you"]), reverse=True)

    print(f"=======================================================")
    print(f" TOP LEAGUE-WIDE TRADES FOUND: {len(all_2team_trades)}")
    print(f"=======================================================")
    if not all_2team_trades:
        print("No trades found within the tolerance limits.")
    else:
        for i, tr in enumerate(all_2team_trades[:15], 1):
            print(f"{i}. Partner: [{tr['partner']}] | {tr['type']} | **{tr['grade']}**")
            print(f"   You Give:     {tr['gives']}")
            print(f"   You Receive:  {tr['receives']}")
            print(f"   Lineup Delta: You (+{tr['delta_my_team']} pts) | {tr['partner']} ({tr['delta_partner']} pts)")
            print(f"   Context VORP: You (+{tr['net_vorp_you']}) | Partner ({tr['net_vorp_partner']})\n")

if __name__ == "__main__":
    main()