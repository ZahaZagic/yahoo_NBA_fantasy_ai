STAT_MAPPING = {
    '5': 'FG%',
    '8': 'FT%',
    '10': '3PTM',
    '12': 'PTS',
    '13': 'REB', 
    '15': 'REB',
    '16': 'AST',
    '17': 'STL',
    '18': 'BLK',
    '19': 'TO',
    '0': 'GP',
    '2': 'MIN',
    '28': 'MIN_AVG',
    '9004003': 'FGM/A',
    '9007006': 'FTM/A'
}

def robust_flatten(item):
    """Simplistic robust flatten for Yahoo JSON response."""
    res = {}
    if isinstance(item, list):
        for sub in item: res.update(robust_flatten(sub))
    elif isinstance(item, dict):
        for k, v in item.items():
            if k in ['is_keeper', 'is_flex']: continue
            if k in ['stats', 'player_stats', 'teams', 'matchups', 'player', 'team', 'team_stats', 'team_points', 'name', 'percent_owned', 'team_id', 'team_key', 'status', 'status_full', 'is_playing_today', 'is_starting', 'has_game_today', 'eligible_positions', 'player_id', 'is_editable', 'ranks', 'player_ranks']:
                res[k] = v
            elif isinstance(v, (list, dict)):
                inner = robust_flatten(v)
                res.update(inner)
            else: res[k] = v
    return res

def parse_raw_stats_list(stats_data):
    result = {}
    if not stats_data: return result
    
    items = []
    if isinstance(stats_data, list):
        items = stats_data
    elif isinstance(stats_data, dict):
        items = [stats_data[k] for k in stats_data if k != 'count']
    else: return result

    raw = {}
    for s in items:
        try:
            s_obj = s.get('stat', s)
            sid = str(s_obj['stat_id'])
            val = s_obj['value']
            if sid == '4': raw['FGM'] = val
            if sid == '3': raw['FGA'] = val
            if sid == '7': raw['FTM'] = val
            if sid == '6': raw['FTA'] = val
            if sid in STAT_MAPPING:
                stat_name = STAT_MAPPING[sid]
                if stat_name in ['FGM/A', 'FTM/A']: result[stat_name] = str(val)
                else:
                    try:
                        if isinstance(val, str) and ":" in val:
                            parts = val.split(":")
                            if len(parts) == 2:
                                f_val = float(parts[0]) + float(parts[1])/60.0
                            else: f_val = float(parts[0])
                        else:
                            f_val = 0.0 if val in ['-', ''] else float(val)
                    except (TypeError, ValueError):
                        f_val = 0.0
                    result[stat_name] = f_val
        except (KeyError, TypeError, AttributeError):
            # Malformed stat entry for one player; skip it rather than abort.
            continue
    if 'FGM/A' not in result and 'FGM' in raw and 'FGA' in raw:
        result['FGM/A'] = f"{raw['FGM']}/{raw['FGA']}"
    if 'FTM/A' not in result and 'FTM' in raw and 'FTA' in raw:
        result['FTM/A'] = f"{raw['FTM']}/{raw['FTA']}"
    return result

def get_mapped_status(p_flat):
    s = p_flat.get('status')
    sf = p_flat.get('status_full')
    if isinstance(s, str) and len(s) > 1 and s.upper() != 'OK': return s.upper()
    if not sf: return "OK"
    sf = sf.lower()
    if 'injured' in sf or 'il-eligible' in sf: return 'INJ'
    if 'out' in sf: return 'O'
    if 'game time decision' in sf or 'gtd' in sf: return 'GTD'
    if 'suspended' in sf: return 'SSPD'
    return "OK"
