from itertools import combinations
import math

# Baseline replacement-level projections for a 12-team PPR league (Points Per Game)
REPLACEMENT_BASELINES = {
    "QB": 15.5, # e.g., QB13 on the waiver wire
    "RB": 9.0,  # e.g., RB31 on the waiver wire (scarce)
    "WR": 10.5, # e.g., WR49 on the waiver wire
    "TE": 7.0,  # e.g., TE13 on the waiver wire
    "K": 7.0,
    "DEF": 6.0
}

# Number of weeks remaining in the fantasy regular season
REMAINING_WEEKS = 14

def get_ros_proj(p: dict) -> float:
    """Returns the expected points per game (PPG) projection."""
    pts = p.get("live_proj", 0.0)
    if pts and pts > 0:
        return float(pts)
    # Fallback if no live projection
    pos = p.get("position", "N/A")
    base = REPLACEMENT_BASELINES.get(pos, 5.0)
    return base + 4.0 if p.get("is_starter") else base + 1.0

REMAINING_WEEKS = 13 # Set to the remaining weeks in the fantasy season

def calculate_player_vorp(p: dict) -> float:
    """
    Calculates Rest-of-Season VORP using Real-Time Sleeper Injury Data.
    """
    raw_ppg = p.get("_cached_ppg")
    pos = p.get("position", "N/A")
    baseline = REPLACEMENT_BASELINES.get(pos, 5.0)
    
    # 1. Fetch Real-Time Status from the Sleeper API
    status = p.get("injury_status")
    
    # 2. Map official designations to Expected Value (EV) probabilities
    if status in ["IR", "Out", "PUP", "Suspended"]:
        p_healthy = 0.0   # 0% chance to play
    elif status == "Doubtful":
        p_healthy = 0.25  # 25% chance to play
    elif status == "Questionable":
        p_healthy = 0.60  # 60% chance to play
    else:
        p_healthy = 0.95  # Standard baseline NFL health attrition rate

    # Expected Value Formula: 
    # (Probability they play * Their Projection) + (Probability they sit * Waiver Wire points)
    ev_ppg = (p_healthy * raw_ppg) + ((1.0 - p_healthy) * baseline)
    
    # 3. Calculate VORP and apply exponential scaling for superstars
    weekly_vorp = max(0, ev_ppg - baseline)
    scaled_weekly_vorp = weekly_vorp ** 1.15 
    
    ros_vorp = scaled_weekly_vorp * REMAINING_WEEKS
    return round(ros_vorp, 1)

def evaluate_roster_needs(players: list) -> dict:
    """
    Determines a team's strength at each position to generate trade weights.
    Returns a dictionary of positional multipliers (e.g., {"RB": 1.15, "WR": 0.85})
    """
    pos_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    pos_vorp = {"QB": 0.0, "RB": 0.0, "WR": 0.0, "TE": 0.0}
    
    for p in players:
        pos = p.get("position")
        if pos in pos_counts:
            pos_counts[pos] += 1
            # Only count positive VORP toward team strength
            pos_vorp[pos] += max(0, p.get("_cached_vorp", 0.0))

    # Standard starting requirements (1 QB, 2 RB, 2 WR, 1 TE)
    # We penalize positions with too much VORP (surplus) and boost positions lacking VORP (need)
    need_multipliers = {}
    
    # Calculate how desperate a team is for a position based on total accumulated VORP
    # Higher multiplier = Team desperately needs this position
    # Lower multiplier = Team is looking to trade this position away
    need_multipliers["QB"] = 1.0 + (50.0 - pos_vorp["QB"]) / 200.0
    need_multipliers["RB"] = 1.0 + (120.0 - pos_vorp["RB"]) / 200.0
    need_multipliers["WR"] = 1.0 + (120.0 - pos_vorp["WR"]) / 200.0
    need_multipliers["TE"] = 1.0 + (40.0 - pos_vorp["TE"]) / 200.0

    # Cap multipliers to prevent extreme distortion
    for pos in need_multipliers:
        need_multipliers[pos] = max(0.7, min(1.3, need_multipliers[pos]))

    return need_multipliers

def fast_lineup_score(players: list) -> float:
    """Calculates optimal starting lineup PPG."""
    qbs, rbs, wrs, tes = [], [], [], []
    for p in players:
        pos = p.get("position")
        val = p.get("_cached_ppg")
        if pos == "QB": qbs.append(val)
        elif pos == "RB": rbs.append(val)
        elif pos == "WR": wrs.append(val)
        elif pos == "TE": tes.append(val)

    qbs.sort(reverse=True); rbs.sort(reverse=True)
    wrs.sort(reverse=True); tes.sort(reverse=True)

    score = 0.0
    if qbs: score += qbs[0]
    score += sum(rbs[:2])
    remaining_rbs = rbs[2:]
    score += sum(wrs[:2])
    remaining_wrs = wrs[2:]
    if tes: score += tes[0]
    remaining_tes = tes[1:]

    flex_pool = remaining_rbs + remaining_wrs + remaining_tes
    if flex_pool: score += max(flex_pool)

    return round(score, 2)

def prepare_roster(players: list):
    """Pre-calculates PPG and VORP to avoid recalculating inside the loop."""
    for p in players:
        p["_cached_ppg"] = get_ros_proj(p)
        p["_cached_vorp"] = calculate_player_vorp(p)
    return players

def generate_packages(players: list, anchor_id: str = None, max_size: int = 2):
    valid_pos = {"QB", "RB", "WR", "TE"}
    pool = [p for p in players if p.get("position") in valid_pos]

    packages = []
    if anchor_id:
        anchor = next((p for p in pool if p["player_id"] == anchor_id), None)
        if not anchor: return []
        packages.append([anchor])
        if max_size >= 2:
            for other in pool:
                if other["player_id"] != anchor_id:
                    packages.append([anchor, other])
        return packages

    # Filter pool to startable assets to prevent calculating junk trades
    pool.sort(key=lambda x: x["_cached_vorp"], reverse=True)
    active_pool = pool[:8]

    for p in active_pool:
        packages.append([p])
    if max_size >= 2:
        for p1, p2 in combinations(active_pool, 2):
            packages.append([p1, p2])
    return packages

def format_package(package: list) -> str:
    return " + ".join([f"{p['name']} ({p['position']}, {p['_cached_vorp']:.1f} VORP)" for p in package])

def find_2team_trades(my_team: dict, other_team: dict, anchor_id: str = None):
    """
    Advanced trade evaluator that biases toward the active manager 
    while maintaining psychological realism for the partner.
    """
    prepare_roster(my_team["players"])
    prepare_roster(other_team["players"])

    my_needs = evaluate_roster_needs(my_team["players"])
    other_needs = evaluate_roster_needs(other_team["players"])

    base_a = fast_lineup_score(my_team["players"])
    base_b = fast_lineup_score(other_team["players"])

    pkgs_a = generate_packages(my_team["players"], anchor_id=anchor_id, max_size=2)
    pkgs_b = generate_packages(other_team["players"], anchor_id=None, max_size=2)

    proposals = []
    
    # Value of an empty bench spot (average waiver pickup VORP)
    WAIVER_WIRE_VORP = 8.0 

    for pa in pkgs_a:
        ids_a = {p["player_id"] for p in pa}
        rem_a = [p for p in my_team["players"] if p["player_id"] not in ids_a]
        vorp_sent_a = sum([p["_cached_vorp"] * my_needs[p["position"]] for p in pa])

        for pb in pkgs_b:
            # Skip 2-for-2 trades that swap the exact same positions (pointless trades)
            if len(pa) == 1 and len(pb) == 1 and pa[0]["position"] == pb[0]["position"]:
                continue

            ids_b = {p["player_id"] for p in pb}
            rem_b = [p for p in other_team["players"] if p["player_id"] not in ids_b]

            # 1. Starting Lineup Delta
            delta_a = round(fast_lineup_score(rem_a + pb) - base_a, 2)
            delta_b = round(fast_lineup_score(rem_b + pa) - base_b, 2)

            # THE BIAS: You MUST gain lineup points. Partner can lose slightly (-1.5 max).
            if delta_a < 0.5 or delta_b < -1.5:
                continue

            # 2. Roster Spot Adjustment (If trade is uneven, e.g., 2-for-1)
            size_diff = len(pa) - len(pb)
            hidden_value_a = WAIVER_WIRE_VORP if size_diff > 0 else 0
            hidden_value_b = WAIVER_WIRE_VORP if size_diff < 0 else 0

            # 3. Contextual VORP Check with Need Multipliers
            vorp_received_a = sum([p["_cached_vorp"] * my_needs[p["position"]] for p in pb])
            net_vorp_a = round((vorp_received_a + hidden_value_a) - vorp_sent_a, 1)

            vorp_sent_b = sum([p["_cached_vorp"] * other_needs[p["position"]] for p in pb])
            vorp_received_b = sum([p["_cached_vorp"] * other_needs[p["position"]] for p in pa])
            net_vorp_b = round((vorp_received_b + hidden_value_b) - vorp_sent_b, 1)

            # THE BIAS: Ensure you win the raw value, but the partner feels okay based on their needs
            if net_vorp_a > 0.0 and net_vorp_b > -15.0:
                
                # Grading the bias
                if delta_b >= 0.5 and net_vorp_b >= 0:
                    grade = "True Win-Win"
                elif delta_b >= -1.5 and net_vorp_b > -15.0:
                    grade = "Slight Edge (Your Favor)" # This is your sweet spot!
                else:
                    grade = "Fleece"

                proposals.append({
                    "type": f"{len(pa)}-for-{len(pb)}",
                    "partner": other_team["owner"],
                    "gives": format_package(pa),
                    "receives": format_package(pb),
                    "delta_my_team": delta_a,
                    "delta_partner": delta_b,
                    "net_vorp_you": net_vorp_a,
                    "net_vorp_partner": net_vorp_b,
                    "grade": grade
                })

    # Sort primarily by the trades that give YOU the highest VORP and lineup jump
    proposals.sort(key=lambda x: (x["delta_my_team"], x["net_vorp_you"]), reverse=True)
    
    # Filter out extreme fleeces so you only see realistic offers
    return [p for p in proposals if p["grade"] != "Fleece"]

# (Keep find_3team_trades as is, or remove it temporarily while testing this logic)
def find_3team_trades(team_a, team_b, team_c, anchor_id=None, min_gain=0.5):
    return []