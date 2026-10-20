import requests

def get_active_nba_teams(target_date=None):
    """Dynamically fetch NBA teams playing on target_date (YYYY-MM-DD). Defaults to today."""
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        if target_date:
            # target_date format: YYYYMMDD
            url += f"?dates={target_date.replace('-', '')}"
        
        resp = requests.get(url, timeout=10)
        data = resp.json()
        teams = set()
        # Mapping for inconsistencies (ESPN -> Yahoo)
        mapping = {
            "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS",
            "UTAH": "UTA", "PHO": "PHX", "BK": "BKN", "CH": "CHA"
        }
        for event in data.get('events', []):
            for competition in event.get('competitions', []):
                for competitor in competition.get('competitors', []):
                    abbr = competitor.get('team', {}).get('abbreviation')
                    if abbr:
                        teams.add(mapping.get(abbr, abbr))
        return list(teams)
    except Exception as e:
        print(f"Warning: Could not fetch schedule for {target_date} ({e}).")
        return []
