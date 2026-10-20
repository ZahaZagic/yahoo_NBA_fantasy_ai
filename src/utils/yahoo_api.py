import os
import datetime
import pandas as pd
from tabulate import tabulate

try:  # package imports (python -m src.main)
    from .stats_parser import robust_flatten, parse_raw_stats_list, get_mapped_status
    from .scheduler import get_active_nba_teams
except ImportError:  # script imports (python src/main.py)
    from utils.stats_parser import robust_flatten, parse_raw_stats_list, get_mapped_status
    from utils.scheduler import get_active_nba_teams

# LEAGUE_ID removed, now loaded from config/state

def get_team_roster(query, league_id, team_id_num, team_name=None):
    title = f" ROSTER: {team_name} (Team {team_id_num}) " if team_name else f" ROSTER: Team {team_id_num} "
    print(f"\n{'='*30}\n{title}\n{'='*30}")
    try:
        game_key = query.game_id
        if not game_key or not str(game_key).isdigit() or int(game_key) < 400:
            # Guessing the season would silently read a different year's league.
            raise ValueError(
                "query.game_id is not set; the season game key must be resolved "
                "before fetching a roster."
            )

        full_team_key = f"{game_key}.l.{league_id}.t.{team_id_num}"
        # 1. Fetch Roster with Stats
        roster_url = f"https://fantasysports.yahooapis.com/fantasy/v2/team/{full_team_key}/roster/players/stats;type=season?format=json"
        resp = query.oauth.session.get(roster_url)
        data = resp.json()
        
        player_keys = []
        roster_meta = {}
        
        if 'fantasy_content' in data and 'team' in data['fantasy_content']:
            team_wrapper = data['fantasy_content']['team']
            players_block = None
            if isinstance(team_wrapper, list):
                for p in team_wrapper:
                    if isinstance(p, dict) and 'roster' in p:
                        players_block = p['roster']['0']['players']
                        break
            elif isinstance(team_wrapper, dict) and 'roster' in team_wrapper:
                players_block = team_wrapper['roster']['0']['players']
            
            if players_block:
                for idx in players_block:
                    if idx == 'count': continue
                    p_info = players_block[idx].get('player', [])
                    p_flat = robust_flatten(p_info)
                    pk = p_flat.get('player_key')
                    if pk:
                        player_keys.append(pk)
                        # Identify selected position
                        sel_pos = "BN"
                        for p_item in p_info:
                            if isinstance(p_item, dict) and 'selected_position' in p_item:
                                sel_pos = p_item['selected_position'][1].get('position')
                                break
                        roster_meta[pk] = {
                            "selected_position": sel_pos,
                            "is_editable": p_flat.get('is_editable', 1),
                            "status": p_flat.get('status')
                        }

        if not player_keys: return []

        # 2. Batch Fetch Season Stats for these Players
        stats_url = f"https://fantasysports.yahooapis.com/fantasy/v2/players;player_keys={','.join(player_keys)}/stats;type=season?format=json"
        resp_s = query.oauth.session.get(stats_url)
        data_s = resp_s.json()
        
        roster_data = []
        if 'fantasy_content' in data_s and 'players' in data_s['fantasy_content']:
            players_list = data_s['fantasy_content']['players']
            today_str = datetime.date.today().strftime("%Y%m%d")
            active_teams = get_active_nba_teams(today_str)

            for idx in players_list:
                if idx == 'count': continue
                p_flat = robust_flatten(players_list[idx].get('player', []))
                pk = p_flat.get('player_key')
                name = p_flat.get('name', {}).get('full', 'Unknown')
                
                # Metadata
                player_id = p_flat.get('player_id')
                eligible_pos = p_flat.get('eligible_positions', [])
                pos_list = []
                if isinstance(eligible_pos, list):
                    for pos_item in eligible_pos:
                        if isinstance(pos_item, dict): pos_list.append(pos_item.get('position'))
                        else: pos_list.append(str(pos_item))
                elif isinstance(eligible_pos, dict):
                    pos_list = [v for k, v in eligible_pos.items() if k != 'count']
                
                team_abbr = p_flat.get('editorial_team_abbr')
                is_editable = roster_meta.get(pk, {}).get('is_editable', 1)
                
                # Game Today
                native_flag = int(p_flat.get('is_playing_today', p_flat.get('has_game_today', 0)))
                has_game = 1 if (active_teams is not None and team_abbr in active_teams) else native_flag

                # Rankings
                ranks = p_flat.get('player_ranks', p_flat.get('ranks', []))
                season_rank, current_rank = "-", "-"
                if isinstance(ranks, list):
                    for r in ranks:
                        if isinstance(r, dict) and 'player_rank' in r:
                            r_type = r['player_rank'].get('rank_type')
                            r_val = r['player_rank'].get('rank_value')
                            if r_type == 'season': season_rank = r_val
                            if r_type == 'current': current_rank = r_val

                row = {
                    "Player": name, 
                    "Status": get_mapped_status(p_flat), 
                    "StatusFull": p_flat.get('status_full', 'OK'),
                    "player_id": player_id,
                    "player_key": pk,
                    "eligible_positions": pos_list,
                    "is_editable": is_editable,
                    "has_game_today": has_game,
                    "Season Rank": season_rank,
                    "Current Rank": current_rank,
                    "Team": team_abbr,
                    "injury_note": p_flat.get('injury_note', ''),
                    "selected_position": roster_meta.get(pk, {}).get("selected_position", "BN")
                }
                
                s_list = p_flat.get('player_stats', {}).get('stats', [])
                if not s_list: s_list = p_flat.get('stats', [])
                if s_list: row.update(parse_raw_stats_list(s_list))
                
                # Averages
                gp = float(row.get('GP', 0))
                if gp > 0:
                    for stat_key in ['PTS', 'REB', 'AST', 'STL', 'BLK', 'TO', '3PTM', 'MIN']:
                        if stat_key in row:
                            row[stat_key] = round(float(row[stat_key]) / gp, 1)
                    
                    stat_vals = {str(s.get('stat', {}).get('stat_id')): s.get('stat', {}).get('value') for s in s_list}
                    def get_avg_str(m_id, a_id):
                        m = float(stat_vals.get(m_id, 0))
                        a = float(stat_vals.get(a_id, 0))
                        return f"{round(m/gp, 1)}/{round(a/gp, 1)}"
                    
                    if '4' in stat_vals and '3' in stat_vals: row['FGM/A'] = get_avg_str('4', '3')
                    if '7' in stat_vals and '6' in stat_vals: row['FTM/A'] = get_avg_str('7', '6')
                    row['MPG'] = row.get('MIN', 0)
                else:
                    row['MPG'] = 0.0
                
                roster_data.append(row)
        
        if roster_data:
            try:
                df = pd.DataFrame(roster_data)
                order = ["Player", "Status", "Team", "MPG", "FGM/A", "FG%", "FTM/A", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
                cols = [c for c in order if c in df.columns]
                
                # Categorize segments
                active = df.iloc[:10]
                bench = df.iloc[10:13]
                il_plus = df.iloc[13:]
                
                if not active.empty:
                    print("\n[ACTIVE (Top 10)]")
                    print(tabulate(active[cols], headers='keys', tablefmt='psql', floatfmt=".2f"))
                if not bench.empty:
                    print("\n[BENCH (11-13)]")
                    print(tabulate(bench[cols], headers='keys', tablefmt='psql', floatfmt=".2f"))
                if not il_plus.empty:
                    print("\n[IL / IL+ (Extended)]")
                    print(tabulate(il_plus[cols], headers='keys', tablefmt='psql', floatfmt=".2f"))
            except Exception as pe:
                print(f"DEBUG: Roster table print error (ignoring): {pe}")
                
        return roster_data
    except Exception as e: 
        print(f"Error getting roster for Team {team_id_num}: {e}")
        return []

def get_free_agents_by_date(query, league_key, target_date_str=None, sort_type="R_PO"):
    sort_label = "Ownership %" if sort_type == "R_PO" else "Current Rank"
    date_label = f"playing {target_date_str}" if target_date_str else "Overall"
    print(f"\n{'='*30}\n FREE AGENTS ({date_label}) (Sorted by: {sort_label}) \n{'='*30}")
    try:
        player_keys = []; ownership_map = {}
        # Fetch top 75
        fetch_counts = [0, 25, 50]
        
        for start_idx in fetch_counts:
            print(f"   [FA] Fetching player list (sort={sort_type}, start={start_idx})...")
            url_own = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;status=FA;sort={sort_type};start={start_idx};count=25/percent_owned?format=json"
            r_own = query.oauth.session.get(url_own)
            d_own = r_own.json()
            if 'fantasy_content' in d_own and 'league' in d_own['fantasy_content']:
                l_parts = d_own['fantasy_content']['league']
                p_block = None
                for p in (l_parts if isinstance(l_parts, list) else [l_parts]):
                    if isinstance(p, dict) and 'players' in p: p_block = p['players']; break
                if p_block:
                    for idx in p_block:
                        if idx == 'count': continue
                        f = robust_flatten(p_block[idx].get('player', []))
                        pk = f.get('player_key')
                        if pk:
                            player_keys.append(pk)
                            po = f.get('percent_owned', {})
                            p_own = robust_flatten(po).get('value', 0) if isinstance(po, (list, dict)) else 0
                            ownership_map[pk] = p_own

        if not player_keys: print("No free agents found."); return []
        
        all_rows = []
        active_teams = None
        if target_date_str:
            espn_date = target_date_str.replace("-", "")
            active_teams = get_active_nba_teams(espn_date)
        
        for i in range(0, len(player_keys), 25):
            chunk = player_keys[i:i+25]
            print(f"   [FA] Fetching stats for chunk of {len(chunk)} players...")
            url_batch = f"https://fantasysports.yahooapis.com/fantasy/v2/players;player_keys={','.join(chunk)}/stats;type=season?format=json"
            resp_b = query.oauth.session.get(url_batch)
            data_b = resp_b.json()
            if 'fantasy_content' in data_b and 'players' in data_b.get('fantasy_content', {}):
                p_items = data_b['fantasy_content']['players']
                for pk in p_items:
                    if pk == 'count': continue
                    pf = robust_flatten(p_items[pk].get('player', []))
                    p_key = pf.get('player_key')
                    if not p_key: continue
                    
                    p_stat = get_mapped_status(pf)
                    raw_status = pf.get('status')
                    if raw_status == 'W': continue # Skip Waivers
                    if p_stat != 'OK': continue 
                    
                    name = pf.get('name', {}).get('full', 'Unknown')
                    team_abbr = pf.get('editorial_team_abbr')
                    
                    # Game Today detection for FA
                    if target_date_str:
                        # 1. Native flag
                        native_flag = int(pf.get('is_playing_today', pf.get('has_game_today', 0)))
                        
                        # 2. Strict Schedule check
                        if active_teams is not None:
                            if team_abbr not in active_teams: continue
                        elif native_flag == 0:
                            continue # If no schedule fetch, rely on native flag
                    
                    row = {
                        "Player": name, 
                        "Status": p_stat, 
                        "Team": team_abbr,
                        "% Owned": ownership_map.get(p_key, 0),
                        "player_id": p_key.split('.')[-1] if '.' in p_key else p_key,
                        "playing_today": int(pf.get('is_playing_today', pf.get('has_game_today', 0))) == 1
                    }
                   
                    # Rankings
                    ranks = pf.get('player_ranks', pf.get('ranks', []))
                    season_rank = "-"
                    current_rank = "-"
                    if isinstance(ranks, list):
                        for r in ranks:
                            if isinstance(r, dict) and 'player_rank' in r:
                                r_type = r['player_rank'].get('rank_type')
                                r_val = r['player_rank'].get('rank_value')
                                if r_type == 'season': season_rank = r_val
                                if r_type == 'current': current_rank = r_val
                    row["Season Rank"] = season_rank
                    row["Current Rank"] = current_rank

                    s_list = pf.get('player_stats', {}).get('stats')
                    if not s_list: s_list = pf.get('stats')
                    if s_list: 
                        parsed = parse_raw_stats_list(s_list)
                        row.update(parsed)
                    
                    # Calculate Averages (Since we used type=season)
                    gp = float(row.get('GP', 0))
                    if gp > 0:
                        for stat_key in ['PTS', 'REB', 'AST', 'STL', 'BLK', 'TO', '3PTM', 'MIN']:
                            if stat_key in row:
                                row[stat_key] = round(float(row[stat_key]) / gp, 1)
                        
                        # Re-calculate FGM/A and FTM/A averages
                        stat_vals = {str(s.get('stat', {}).get('stat_id')): s.get('stat', {}).get('value') for s in s_list}
                        def get_avg_str(m_id, a_id):
                            m = float(stat_vals.get(m_id, 0))
                            a = float(stat_vals.get(a_id, 0))
                            return f"{round(m/gp, 1)}/{round(a/gp, 1)}"
                            
                        if '4' in stat_vals and '3' in stat_vals: row['FGM/A'] = get_avg_str('4', '3')
                        if '7' in stat_vals and '6' in stat_vals: row['FTM/A'] = get_avg_str('7', '6')
                        
                        row['MPG'] = row.get('MIN', 0)
                        row['FGA'] = round(float(stat_vals.get('3', 0)) / gp, 1) if '3' in stat_vals else 0.0
                    else:
                        row['MPG'] = 0.0
                        row['FGA'] = 0.0
                    
                    reb, blk = row.get('REB', 0), row.get('BLK', 0)
                    if reb >= 8.0: row["Player"] = f"{row['Player']} (REB+)"
                    if blk >= 1.5: row["Player"] = f"{row['Player']} (BLK+)"
                    all_rows.append(row)
        
        # Display FA Table
        if all_rows:
            try:
                df = pd.DataFrame(all_rows)
                order = ["Player", "Status", "Team", "MPG", "% Owned", "FGM/A", "FG%", "FTM/A", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
                cols = [c for c in order if c in df.columns]
                print(tabulate(df[cols].head(25), headers='keys', tablefmt='psql', floatfmt=".2f"))
            except Exception as pe:
                print(f"DEBUG: Free-agent table print error (ignoring): {pe}")
            
        return all_rows
    except Exception as e: 
        print(f"Error getting free agents: {e}")
        return []

def get_historical_team_summary(query, league_id, team_id_num, current_week):
    if not team_id_num: return []
    print(f"\n{'='*30}\n HISTORICAL SUMMARY: Team {team_id_num} \n{'='*30}")
    try:
        game_key = query.game_id 
        full_team_key = f"{game_key}.l.{league_id}.t.{team_id_num}"
        print(f"   [History] Requesting matchup history for Team {team_id_num}...")
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/team/{full_team_key}/matchups?format=json"
        resp = query.oauth.session.get(url)
        data = resp.json()
        summary_rows = []
        if 'fantasy_content' in data and 'team' in data['fantasy_content']:
            team_wrapper = data['fantasy_content']['team']
            m_wrap = None
            if isinstance(team_wrapper, list):
                for part in team_wrapper:
                    if isinstance(part, dict) and 'matchups' in part: m_wrap = part['matchups']; break
            if m_wrap:
                valid = []
                for idx in m_wrap:
                    if idx == 'count': continue
                    m_f = robust_flatten(m_wrap[idx])
                    w = str(m_f.get('week', '0'))
                    if m_f.get('status') == 'postevent' and int(float(w)) < int(current_week): 
                        valid.append((int(float(w)), m_wrap[idx]))
                
                valid.sort(key=lambda x: x[0], reverse=True)
                for week_num, matchup_raw in valid:
                    m_f = robust_flatten(matchup_raw)
                    teams_data = m_f.get('teams', {})
                    for tk in teams_data:
                        if tk == 'count': continue
                        tf = robust_flatten(teams_data[tk]['team'])
                        if str(tf.get('team_id')) == str(team_id_num):
                            row = {"Week": f"Week {week_num}"}
                            if 'team_stats' in tf and 'stats' in tf['team_stats']: 
                                row.update(parse_raw_stats_list(tf['team_stats']['stats']))
                            summary_rows.append(row)
        if summary_rows:
            try:
                df = pd.DataFrame(summary_rows)
                order = ["Week", "FGM/A", "FG%", "FTM/A", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
                cols = [c for c in order if c in df.columns]
                print(tabulate(df[cols], headers='keys', tablefmt='psql', floatfmt=".3f"))
            except Exception as pe:
                print(f"DEBUG: Historical table print error (ignoring): {pe}")
        return summary_rows
    except Exception as e: 
        print(f"Error fetching historical summary: {e}")
        return []

def get_live_matchup(query, league_id, team_id_num, target_week):
    print(f"\n{'='*30}\n LIVE MATCHUP (Week {target_week}) \n{'='*30}")
    try:
        game_key = query.game_id 
        full_team_key = f"{game_key}.l.{league_id}.t.{team_id_num}"
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/team/{full_team_key}/matchups?format=json"
        resp = query.oauth.session.get(url)
        data = resp.json()
        if 'fantasy_content' in data and 'team' in data['fantasy_content']:
            team_wrapper = data['fantasy_content']['team']
            m_wrap = None
            if isinstance(team_wrapper, list):
                for part in team_wrapper:
                    if isinstance(part, dict) and 'matchups' in part: m_wrap = part['matchups']; break
            if m_wrap:
                for idx in m_wrap:
                    if idx == 'count': continue
                    m_flat = robust_flatten(m_wrap[idx])
                    if str(m_flat.get('week')) == str(target_week):
                        print(f"Matchup Status: {m_flat.get('status', 'Unknown')}")
                        if 'teams' in m_flat:
                            rows = []
                            teams_data = m_flat['teams']
                            opp_id = None
                            for tk in teams_data:
                                if tk == 'count': continue
                                tf = robust_flatten(teams_data[tk]['team'])
                                row = {"Team": tf.get('name', 'Unknown')}
                                if str(tf.get('team_id')) != str(team_id_num): opp_id = str(tf.get('team_id'))
                                if 'team_stats' in tf and 'stats' in tf['team_stats']: 
                                    row.update(parse_raw_stats_list(tf['team_stats']['stats']))
                                    row["team_id"] = str(tf.get('team_id'))
                                rows.append(row)
                            if rows:
                                try:
                                    df = pd.DataFrame(rows); order = ["Team", "FGM/A", "FG%", "FTM/A", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
                                    cols = [c for c in order if c in df.columns]
                                    print(tabulate(df[cols], headers='keys', tablefmt='psql', floatfmt=".3f"))
                                except Exception as pe:
                                    print(f"DEBUG: Live matchup table print error (ignoring): {pe}")
                                return opp_id, rows
        return None, []
    except Exception as e: 
        print(f"Error getting live matchup: {e}")
        return None, []

def update_roster_positions(query, league_id, team_id, position_map):
    """
    Updates player positions in Yahoo.
    position_map: {player_id: position_name}
    """
    print(f"-> Updating roster positions for Team {team_id}...")
    league_id = str(league_id)
    game_id = str(query.game_id)
    team_key = f"{game_id}.l.{league_id}.t.{team_id}"
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/team/{team_key}/roster"
    
    # Construct XML payload
    xml_header = '<?xml version="1.0" encoding="UTF-8"?>'
    players_xml = ""
    for pid, pos in position_map.items():
        players_xml += f"""
        <player>
            <player_key>{game_id}.p.{pid}</player_key>
            <selected_position>{pos}</selected_position>
        </player>"""
    
    payload = f"""{xml_header}
    <fantasy_content>
        <roster>
            <coverage_type>date</coverage_type>
            <date>{datetime.date.today().strftime("%Y-%m-%d")}</date>
            <players>
                {players_xml}
            </players>
        </roster>
    </fantasy_content>"""
    
    try:
        resp = query.oauth.session.put(url, data=payload, headers={'Content-Type': 'application/xml'})
        if resp.status_code in [200, 201]:
            print("   [Success] Roster positions updated.")
            return True
        else:
            print(f"   [Error] Roster update failed: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"   [Error] Exception during roster update: {e}")
        return False

def execute_transaction(query, league_id, transaction, my_team_id):
    """
    Executes an add, drop, or activate transaction.
    transaction: {"type": "add"/"drop"/"activate", "player": "Name", "drop": "Name", "player_id": "...", "drop_id": "..."}
    """
    t_type = transaction['type'].lower()
    p_name = transaction.get('player', 'Unknown')
    d_name = transaction.get('drop', 'Unknown')
    
    print("\n" + "!"*40)
    print(f"!!! INTENDED TRANSACTION: {t_type.upper()} !!!")
    if t_type == "add":
        print(f"!!! ACTION: Add {p_name} and Drop {d_name}")
    elif t_type == "activate":
        print(f"!!! ACTION: Activate {p_name} (from IL) and Drop {d_name}")
    elif t_type == "move":
        print(f"!!! ACTION: Move {p_name} (from IL) to Active and Move {d_name} to IL (Internal Swap)")
    elif t_type == "drop":
        print(f"!!! ACTION: Drop {d_name}")
    print("!"*40 + "\n")

    game_id = str(query.game_id)
    league_key = f"{game_id}.l.{league_id}"
    
    pid = transaction.get('player_id')
    did = transaction.get('drop_id')

    if t_type == "move" and pid and did:
        print(f"-> Executing internal roster swap for {p_name} and {d_name}...")
        # Internal moves reuse the roster-position endpoint, which takes
        # (query, league_id, team_id, position_map).
        return update_roster_positions(query, league_id, my_team_id, {pid: "BN", did: "IL"})

    print(f"-> Sending {t_type.upper()} request to Yahoo API...")
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/transactions"
    
    xml_header = '<?xml version="1.0" encoding="UTF-8"?>'
    
    if t_type == "add" and pid and did:
        # Add player and drop another
        payload = f"""{xml_header}
        <fantasy_content>
            <transaction>
                <type>add/drop</type>
                <players>
                    <player>
                        <player_key>{game_id}.p.{pid}</player_key>
                        <transaction_data>
                            <type>add</type>
                            <destination_team>{league_key}.t.{my_team_id}</destination_team>
                        </transaction_data>
                    </player>
                    <player>
                        <player_key>{game_id}.p.{did}</player_key>
                        <transaction_data>
                            <type>drop</type>
                            <source_team>{league_key}.t.{my_team_id}</source_team>
                        </transaction_data>
                    </player>
                </players>
            </transaction>
        </fantasy_content>"""
        try:
            resp = query.oauth.session.post(url, data=payload, headers={'Content-Type': 'application/xml'})
            if resp.status_code in [200, 201]:
                print(f"   [Success] {t_type.upper()} transaction executed.")
                return True
            else:
                print(f"   [Error] Transaction failed: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            print(f"   [Error] Exception during transaction: {e}")
            return False
    elif (t_type == "activate" or t_type == "drop") and did:
        # Move from IL to Active and drop active player OR just drop
        if t_type == "activate":
             print(f"   [Step 1] Attempting to Drop {d_name} to make room...")
        else:
             print(f"   [Step 1] Attempting to Drop {d_name}...")
             
        drop_payload = f"""{xml_header}
        <fantasy_content>
            <transaction>
                <type>drop</type>
                <players>
                    <player>
                        <player_key>{game_id}.p.{did}</player_key>
                        <transaction_data>
                            <type>drop</type>
                            <source_team>{league_key}.t.{my_team_id}</source_team>
                        </transaction_data>
                    </player>
                </players>
            </transaction>
        </fantasy_content>"""
        try:
            resp = query.oauth.session.post(url, data=drop_payload, headers={'Content-Type': 'application/xml'})
            if resp.status_code not in [200, 201]:
                print(f"   [Error] Drop failed: {resp.status_code} - {resp.text}")
                return False
            
            if t_type == "activate":
                print(f"   [Step 2] Drop success. Now activating {p_name} to Bench...")
                # Position update to 'BN' or any active slot
                return update_roster_positions(query, league_id, my_team_id, {pid: "BN"})
            return True
        except Exception as e:
            print(f"   [Error] Exception during {t_type}: {e}")
            return False
    else:
        print(f"   [Error] Unsupported transaction type or missing IDs: {t_type}")
        return False
