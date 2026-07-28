#!/usr/bin/env python3
# =============================================================
#  EBK ALL-IN-ONE — FINAL  (the ONLY data script; delete all others)
#
#  python3 ebk.py                 crawl BBR (resumable; auto countries mode)
#  python3 ebk.py rebuild         rebuild season list, keep crawled data
#  python3 ebk.py redo            re-crawl EVERYTHING with the current parser (~100 min)
#  python3 ebk.py fresh           re-crawl just the 2 most recent seasons
#  python3 ebk.py offseason       + live ESPN rosters (same-day trades/rookies)
#  python3 ebk.py test DAL 2024   inspect one roster page
#  python3 ebk.py master          merge + clean -> ball-knowledge-MASTER.json
#  python3 ebk.py rarity          Wikipedia search scoring (resumable, overnight)
#  python3 ebk.py verify          confirm the data is import-ready
#  python3 ebk.py nettest         quick Wikipedia connectivity check
#
#  OFFICIAL PIPELINE (in this order):
#    python3 ebk.py redo          <- re-crawl with ID disambiguation (Bobby Jones fix)
#    python3 ebk.py offseason     <- layer current trades/rookies
#    python3 ebk.py master        <- merge twins, repair names, split same-name players
#    python3 ebk.py rarity        <- basketball-only Wikipedia scoring
#    python3 ebk.py verify        <- must print LOOKS GOOD
#    import ball-knowledge-MASTER.json in the Database Builder
# =============================================================
import json, os, re, sys, time
try:
    import requests
except ImportError:
    sys.exit("Run first:  pip3 install requests")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}
BASE = "https://www.basketball-reference.com"
DB_FILE = "ball-knowledge-db.json"
STATE_FILE = "bbr_state.json"
DELAY = 3.2  # ~19 requests/min — the max BBR allows

def get(path, tries=4):
    for a in range(tries):
        try:
            r = requests.get(BASE + path, headers=HEADERS, timeout=40)
            r.encoding = "utf-8"  # BBR is UTF-8; without this, accented names (Varejão, Jokić…) get mangled
            if r.status_code == 200:
                return r.text.replace("<!--", "").replace("-->", "")
            if r.status_code == 404:
                return ""  # page doesn't exist (future/unplayed season) — skip, no retries
            if r.status_code == 429:
                print("    (rate-limited — waiting 90s…)", flush=True)
                time.sleep(90)
                continue
            print(f"    (server said {r.status_code} for {path}, retrying…)", flush=True)
        except Exception as e:
            print(f"    (attempt {a+1} failed: {type(e).__name__}, retrying…)", flush=True)
        time.sleep(5 * (a + 1))
    return None

def parse_stats_rows(html):
    """Parse the per-game stats table — this is where CUP-OF-COFFEE players live.
    The roster table only lists the END-of-season roster, so a 10-day signee who was
    released (Judah Mintz, Andre Ingram) appears ONLY here. Also yields games played,
    which is what makes a 3-game stint scoreable as a genuinely rare pull."""
    m = re.search(r'<table[^>]*id="per_game(?:_stats)?".*?</table>', html, re.S)
    if not m:
        return []
    tbl = m.group(0)
    out = []
    for tr in tbl.split("<tr"):
        pl = re.search(r'data-stat="(?:player|name_display)"[^>]*>(?:\s*<[^>]+>)*([^<]+)', tr)
        if not pl:
            continue
        name = pl.group(1).strip()
        if not name or name.lower() in ("player", "team totals", "league average"):
            continue
        g = re.search(r'data-stat="g"[^>]*>(?:\s*<[^>]+>)*(\d+)', tr)
        pid = re.search(r'data-append-csv="([^"]+)"', tr)
        out.append((name, int(g.group(1)) if g else None, pid.group(1) if pid else ""))
    return out


def parse_rows(html):
    """Parse roster rows one <tr> at a time — immune to header cells and cross-row pairing."""
    m = re.search(r'<table[^>]*id="roster".*?</table>', html, re.S)
    if m:
        html = m.group(0)  # only the roster table — never the stats tables below it
    out = []
    for tr in html.split("<tr"):
        pl = re.search(r'data-stat="player"[^>]*>(?:\s*<[^>]+>)*([^<]+)', tr)
        if not pl:
            continue
        name = pl.group(1).strip()
        if not name or name.lower() in ("player", "team totals"):
            continue  # header / totals rows
        n = re.search(r'data-stat="number"[^>]*>(?:\s*<[^>]+>)*([^<]*)', tr)
        col = re.search(r'data-stat="college"[^>]*>(.*?)</t[dh]>', tr, re.S)
        # birth country: BBR renders the flag via a CSS class with no text, e.g. <span class="f-i f-si"></span>
        cell = re.search(r'data-stat="(?:flag|birth_country)"[^>]*>(.*?)</t[dh]>', tr, re.S)
        ctry = ""
        if cell:
            m1 = re.search(r'\bf-i\s+f-([a-z]{2})\b', cell.group(1))
            m2 = re.search(r">([a-z]{2})<", cell.group(1))
            ctry = (m1.group(1) if m1 else m2.group(1) if m2 else re.sub(r"<[^>]+>", "", cell.group(1)).strip()).strip().upper()
        pid = re.search(r'data-append-csv="([^"]+)"', tr)
        out.append((n.group(1) if n else "", name, col.group(1) if col else "", ctry, pid.group(1) if pid else ""))
    return out

COUNTRY = {"FR": "France", "RS": "Serbia", "SI": "Slovenia", "GR": "Greece", "CM": "Cameroon", "NG": "Nigeria", "CA": "Canada", "AU": "Australia", "DE": "Germany", "ES": "Spain", "IT": "Italy", "HR": "Croatia", "LT": "Lithuania", "LV": "Latvia", "TR": "Turkey", "BR": "Brazil", "AR": "Argentina", "DO": "Dominican Republic", "BS": "Bahamas", "JM": "Jamaica", "SN": "Senegal", "ML": "Mali", "SS": "South Sudan", "CD": "DR Congo", "AO": "Angola", "GE": "Georgia", "UA": "Ukraine", "RU": "Russia", "PL": "Poland", "CZ": "Czechia", "AT": "Austria", "CH": "Switzerland", "FI": "Finland", "SE": "Sweden", "DK": "Denmark", "GB": "United Kingdom", "IE": "Ireland", "PT": "Portugal", "ME": "Montenegro", "BA": "Bosnia", "MK": "North Macedonia", "IL": "Israel", "EG": "Egypt", "TN": "Tunisia", "CN": "China", "JP": "Japan", "KR": "South Korea", "PH": "Philippines", "NZ": "New Zealand", "MX": "Mexico", "PR": "Puerto Rico", "VI": "U.S. Virgin Islands", "HT": "Haiti", "TT": "Trinidad and Tobago", "GA": "Gabon", "GN": "Guinea", "CI": "Ivory Coast", "GH": "Ghana", "CG": "Congo", "CV": "Cape Verde", "LC": "Saint Lucia", "BE": "Belgium", "NL": "Netherlands", "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "SK": "Slovakia", "EE": "Estonia", "IS": "Iceland", "AL": "Albania", "XK": "Kosovo"}

CODE2NAME = {"ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BRK": "Brooklyn Nets", "NJN": "New Jersey Nets", "NJA": "New Jersey Americans", "NYN": "New York Nets", "CHH": "Charlotte Hornets", "CHO": "Charlotte Hornets", "CHA": "Charlotte Bobcats", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DNR": "Denver Rockets", "DET": "Detroit Pistons", "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers", "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NOP": "New Orleans Pelicans", "NOH": "New Orleans Hornets", "NOK": "New Orleans/Oklahoma City Hornets", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHO": "Phoenix Suns", "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs", "SEA": "Seattle SuperSonics", "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards", "WSB": "Washington Bullets", "VAN": "Vancouver Grizzlies", "KCK": "Kansas City Kings", "KCO": "Kansas City-Omaha Kings", "CIN": "Cincinnati Royals", "ROC": "Rochester Royals", "SDC": "San Diego Clippers", "BUF": "Buffalo Braves", "SDR": "San Diego Rockets", "STL": "St. Louis Hawks", "MLH": "Milwaukee Hawks", "TRI": "Tri-Cities Blackhawks", "SYR": "Syracuse Nationals", "FTW": "Fort Wayne Pistons", "MNL": "Minneapolis Lakers", "PHW": "Philadelphia Warriors", "SFW": "San Francisco Warriors", "CAP": "Capital Bullets", "BAL": "Baltimore Bullets", "CHZ": "Chicago Zephyrs", "CHP": "Chicago Packers", "NOJ": "New Orleans Jazz", "UTS": "Utah Stars", "KEN": "Kentucky Colonels", "INA": "Indiana Pacers", "MMS": "Memphis Sounds", "SSL": "Spirits of St. Louis", "VIR": "Virginia Squires", "FLO": "Floridians", "PTC": "Pittsburgh Condors", "WSC": "Washington Capitols", "AND": "Anderson Packers", "SHE": "Sheboygan Red Skins", "WAT": "Waterloo Hawks", "INO": "Indianapolis Olympians", "INJ": "Indianapolis Jets", "BLB": "Baltimore Bullets", "PRO": "Providence Steamrollers", "STB": "St. Louis Bombers", "CHS": "Chicago Stags", "DNN": "Denver Nuggets", "DTF": "Detroit Falcons", "PIT": "Pittsburgh Ironmen", "TRH": "Toronto Huskies", "CLR": "Cleveland Rebels"}

def find_gaps(db, state, max_yr):
    """Return done-keys whose team-season has ZERO players in the db — silently failed pages."""
    covered = {}
    for p in db["players"].values():
        for st in p.get("s", []):
            yrs = covered.setdefault(st["t"].lower(), set())
            for y in range(st["y1"], st["y2"] + 1):
                yrs.add(y)
    suspects, unknown = [], set()
    for key in state.get("done", []):
        code, yr = key.split(":")
        yr = int(yr)
        if yr > max_yr:
            continue
        name = CODE2NAME.get(code)
        if not name:
            unknown.add(code)
            continue
        if yr not in covered.get(name.lower(), set()):
            suspects.append(key)
    if unknown:
        print(f"  (codes with no name mapping, skipped in audit: {', '.join(sorted(unknown))})", flush=True)
    return suspects

def test_page(code, yr):
    """python3 bbr_crawler.py test CHO 2021 — fetch one roster page and show what the parser sees."""
    print(f"Fetching /teams/{code}/{yr}.html …", flush=True)
    html = get(f"/teams/{code}/{yr}.html")
    if html is None:
        sys.exit("Page could not be fetched (rate limit / network).")
    if html == "":
        sys.exit("Page does not exist (404).")
    tm = re.search(r"<h1>.*?<span>[^<]*</span>\s*<span>([^<]+)</span>", html, re.S)
    print("Team name parsed:", tm.group(1).strip() if tm else "!! NOT FOUND")
    rows = parse_rows(html)
    print(f"{len(rows)} roster rows parsed:")
    for num, name, colhtml, ctry, pid in rows:
        cols = re.findall(r">([^<]+)</a>", colhtml)
        print(f"  #{num.strip() or '?':>6}  {name:<28} {cols[-1].strip() if cols else '-'}  {ctry or '??'}")
    # show the raw roster row for the first player so we can see how BBR encodes each cell
    m = re.search(r'<table[^>]*id="roster".*?</table>', html, re.S)
    tbl = m.group(0) if m else html
    for tr in tbl.split('<tr'):
        if 'data-stat="player"' in tr and 'csk' in tr:
            print('\nRAW first roster row:')
            print(tr[:1500])
            break

def espn_offseason(db, nxt):
    """Layer ESPN's LIVE rosters on top as upcoming-season stints — catches trades/signings
    the moment they happen, months before BBR posts next season's pages."""
    print("Pulling live rosters from ESPN (reflects trades same-day)…", flush=True)
    try:
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams", headers=HEADERS, timeout=30)
        teams = [(t["team"]["id"], t["team"]["displayName"]) for t in r.json()["sports"][0]["leagues"][0]["teams"]]
    except Exception as e:
        print(f"  !! could not fetch ESPN team list ({type(e).__name__}) — skipping live layer", flush=True)
        return
    added = 0
    for tid, team in teams:
        try:
            time.sleep(0.4)
            r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{tid}/roster", headers=HEADERS, timeout=30)
            athletes = r.json().get("athletes", [])
        except Exception as e:
            print(f"  !! {team}: fetch failed ({type(e).__name__}) — skipped", flush=True)
            continue
        for a in athletes:
            name = (a.get("displayName") or a.get("fullName") or "").strip()
            if not name:
                continue
            # same-name guard: if the record under this name retired 25+ years ago, this is a
            # DIFFERENT person (a rookie namesake) — give them their own entry, never merge eras.
            ex = db["players"].get(name)
            key_name = name
            if ex is not None and ex.get("s"):
                last_yr = max(x["y2"] for x in ex["s"])
                if last_yr < nxt - 25:
                    key_name = f"{name} (rk)"  # master pass renames to career years
            p = db["players"].setdefault(key_name, {"s": [], "j": None, "c": None, "N": {}})
            # drop any previous live-layer stint for this upcoming season (player may have moved again)
            p["s"] = [x for x in p["s"] if not (x.get("live") and x["y1"] == nxt)]
            hit = next((x for x in p["s"] if x["t"] == team and x["y1"] - 1 <= nxt <= x["y2"] + 1), None)
            if hit:
                hit["y2"] = max(hit["y2"], nxt)
            else:
                p["s"].append({"t": team, "y1": nxt, "y2": nxt, "live": True})
                added += 1
            j = a.get("jersey")
            if j and str(j).isdigit():
                lst = p["N"].setdefault(team, [])
                if str(j) not in lst:
                    lst.append(str(j))
                p["j"] = p["j"] or str(j)
            col = (a.get("college") or {}).get("name") if isinstance(a.get("college"), dict) else None
            if col and not p.get("c"):
                p["c"] = col
        print(f"  {team}: {len(athletes)} players", flush=True)
    print(f"Live layer done — {added} upcoming-season stints added/updated.", flush=True)

def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "test":
        test_page(sys.argv[2], int(sys.argv[3]))
        return
    print("Starting — contacting basketball-reference.com…", flush=True)
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    if "rebuild" in sys.argv and "seasons" in state:
        print("Rebuilding the season list from scratch (keeps everything already crawled)…", flush=True)
        del state["seasons"]
    do_gaps = "gaps" in sys.argv
    if "fresh" in sys.argv and state.get("done"):
        # re-crawl the two most recent seasons for every team — catches mid-season pickups (Judah Mintz case)
        import datetime as _d
        _now = _d.date.today()
        _my = _now.year + 1 if _now.month >= 10 else _now.year
        _yrs = {str(_my), str(_my - 1), str(_my + 1)}
        _before = len(state["done"])
        state["done"] = [k for k in state["done"] if k.split(":")[1] not in _yrs]
        print(f"FRESH — re-crawling {_before - len(state['done'])} recent team-seasons (mid-season signings, 10-days, two-ways).", flush=True)
    if "redo" in sys.argv and state.get("done"):
        print(f"REDO — re-crawling all {len(state['done'])} team-seasons with the fixed parser (~100 min).", flush=True)
        state["done"] = []
    if state.get("done") and "skipcountries" not in sys.argv:
        # THIS FILE ALWAYS RUNS COUNTRIES MODE — re-crawls team-seasons whose players lack college AND country
        sys.argv.append("countries")
    if "countries" in sys.argv and state.get("done"):
        _db0 = json.load(open(DB_FILE)) if os.path.exists(DB_FILE) else {"players": {}}
        _name2code = {}
        for _cd, _nm in CODE2NAME.items():
            _name2code.setdefault(_nm.lower(), _cd)
        _hit = set()
        for _p in _db0["players"].values():
            if not _p.get("c") and not _p.get("co"):
                for _st in _p.get("s", []):
                    _cd = _name2code.get(_st["t"].lower())
                    if _cd:
                        for _y in range(_st["y1"], _st["y2"] + 1):
                            _hit.add(f"{_cd}:{_y}")
        _before = len(state["done"])
        state["done"] = [k for k in state["done"] if k not in _hit]
        print(f"COUNTRIES — re-crawling {_before - len(state['done'])} team-seasons that contain players with no college AND no country on record.", flush=True)
    db = json.load(open(DB_FILE)) if os.path.exists(DB_FILE) else {
        "players": {}, "source": "basketball-reference.com", "hist": True,
        "bdlDone": True, "gapDone": True, "aiDone": True, "fixedCur": True}
    db["players"].pop("Player", None)  # phantom entry created by the old header-parsing bug

    # 1. all franchises (active + defunct)
    if "seasons" not in state:
        idx = get("/teams/")
        if not idx:
            sys.exit("Could not reach basketball-reference.com — try again.")
        codes = sorted(set(re.findall(r'href="/teams/([A-Z]{3})/"', idx)))
        print(f"{len(codes)} franchises found", flush=True)
        seasons = []
        failed_fr = []
        for i, code in enumerate(codes, 1):
            time.sleep(DELAY)
            fp = get(f"/teams/{code}/")
            if not fp:
                failed_fr.append(code)
                print(f"  !! franchise page {code} failed — its seasons are NOT queued", flush=True)
                continue
            for c2, yr in set(re.findall(r'href="/teams/([A-Z]{3})/(\d{4})\.html"', fp)):
                seasons.append([c2, int(yr)])
            print(f"  franchise {i}/{len(codes)} ({code}) — {len(seasons)} team-seasons queued", flush=True)
        if failed_fr:
            sys.exit(f"STOPPING — {len(failed_fr)} franchise pages failed ({', '.join(failed_fr)}). "
                     f"Run again in a few minutes; the queue must be complete before crawling.")
        state["seasons"] = sorted(set(map(tuple, seasons)))
        state["seasons"] = [list(x) for x in state["seasons"]]
        json.dump(state, open(STATE_FILE, "w"))
    seasons = state["seasons"]
    import datetime
    now = datetime.date.today()
    max_yr = now.year + 1 if now.month >= 10 else now.year  # last season that has actually been played
    offseason = "offseason" in sys.argv
    seasons = [(c, y) for c, y in seasons if y <= max_yr]
    if offseason:
        nxt = max_yr + 1
        cur_codes = sorted({c for c, y in seasons if y == max_yr})
        seasons += [(c, nxt) for c in cur_codes]
        print(f"OFFSEASON mode — also crawling the upcoming {nxt - 1}\u2013{str(nxt)[2:]} rosters "
              f"({len(cur_codes)} teams; pages that don't exist yet are skipped).", flush=True)
    done = set(state.get("done", []))
    if offseason:
        done = {k for k in done if not k.endswith(f":{max_yr + 1}")}  # always re-fetch upcoming rosters — they change daily
        done = {k for k in done if not k.endswith(f":{max_yr}")} if now.month in (7, 8, 9) else done  # summer: refresh last season too (late corrections)
    if do_gaps:
        print("Auditing the database against every crawled team-season…", flush=True)
        suspects = find_gaps(db, state, max_yr)
        if suspects:
            print(f"  {len(suspects)} team-seasons have ZERO players recorded — re-crawling them:", flush=True)
            print("  " + ", ".join(sorted(suspects)[:40]) + (" …" if len(suspects) > 40 else ""), flush=True)
            done -= set(suspects)
        else:
            print("  Audit clean — every crawled team-season has players in the database.", flush=True)
    todo = [(c, y) for c, y in seasons if f"{c}:{y}" not in done]
    print(f"{len(todo)} team-seasons to crawl ({len(done)} already done)", flush=True)

    for n, (code, yr) in enumerate(todo, 1):
        time.sleep(DELAY)
        html = get(f"/teams/{code}/{yr}.html")
        key = f"{code}:{yr}"
        if html is None:
            print(f"    !! could not fetch {key} — will retry on the next run", flush=True)
        if html == "":  # 404 — season page doesn't exist; mark done and move on
            done.add(key)
            html = None
        if html:
            tm = re.search(r"<h1>.*?<span>[^<]*</span>\s*<span>([^<]+)</span>", html, re.S)
            team = tm.group(1).strip() if tm else CODE2NAME.get(code, code)
            rows = parse_rows(html)
            if not rows:
                print(f"    !! {key}: page loaded but 0 roster rows parsed — NOT marked done; run 'test {code} {yr}' to inspect", flush=True)
                continue
            for num, name, colhtml, ctry, pid in rows:
                # same name, different Basketball-Reference id = different PERSON (Bobby Jones x2).
                # The second person gets a disambiguated key so their careers never merge.
                key_name = name
                if pid:
                    ex = db["players"].get(name)
                    if ex is not None and ex.get("id") and ex["id"] != pid:
                        key_name = f"{name} ({pid[-2:]})"  # e.g. "Bobby Jones (02)" — master pass renames to years
                p = db["players"].setdefault(key_name, {"s": [], "j": None, "c": None, "N": {}})
                if pid and not p.get("id"):
                    p["id"] = pid
                if ctry and ctry != "US" and not p.get("co"):
                    p["co"] = COUNTRY.get(ctry, ctry)
                elif ctry == "US" and not p.get("co"):
                    p["co"] = "USA"  # preps-to-pros / no college: country is the fallback link
                hit = next((x for x in p["s"] if x["t"] == team and x["y1"] - 1 <= yr <= x["y2"] + 1), None)
                if hit:
                    hit["y1"] = min(hit["y1"], yr); hit["y2"] = max(hit["y2"], yr)
                else:
                    p["s"].append({"t": team, "y1": yr, "y2": yr})
                nums = [x for x in re.split(r"[,\s]+", num.strip()) if x.isdigit()]
                if nums:
                    lst = p["N"].setdefault(team, [])
                    for x in nums:
                        if x not in lst:
                            lst.append(x)
                    p["j"] = nums[0]
                cols = [c.strip() for c in re.findall(r">([^<]+)</a>", colhtml) if c.strip()]
                if cols:
                    # keep EVERY school a player attended, not just the first one seen
                    have = p.get("C") or ([p["c"]] if p.get("c") else [])
                    for c in cols:
                        if c not in have:
                            have.append(c)
                    p["C"] = have
                    if not p["c"]:
                        p["c"] = have[0]

            # Cup-of-coffee sweep: the stats table catches players the roster table drops
            # (released 10-day signees) and records how many games each one actually played.
            for sname, games, spid in parse_stats_rows(html):
                skey = sname
                if spid:
                    ex = db["players"].get(sname)
                    if ex is not None and ex.get("id") and ex["id"] != spid:
                        skey = f"{sname} ({spid[-2:]})"
                sp = db["players"].get(skey)
                if sp is None:
                    sp = db["players"].setdefault(skey, {"s": [], "j": None, "c": None, "N": {}})
                    if spid:
                        sp["id"] = spid
                shit = next((x for x in sp["s"] if x["t"] == team and x["y1"] - 1 <= yr <= x["y2"] + 1), None)
                if shit:
                    shit["y1"] = min(shit["y1"], yr); shit["y2"] = max(shit["y2"], yr)
                else:
                    sp["s"].append({"t": team, "y1": yr, "y2": yr})
                if games is not None:
                    gm = sp.setdefault("G", {})
                    gm[team] = gm.get(team, 0) + games
            done.add(key)
        if n % 20 == 0 or n == len(todo):
            state["done"] = sorted(done)
            json.dump(db, open(DB_FILE, "w"))
            json.dump(state, open(STATE_FILE, "w"))
            print(f"  {n}/{len(todo)} ({team if html else key}) — {len(db['players'])} players so far", flush=True)

    state["done"] = sorted(done)
    json.dump(db, open(DB_FILE, "w"))
    json.dump(state, open(STATE_FILE, "w"))
    missed = [f"{c}:{y}" for c, y in seasons if f"{c}:{y}" not in done]
    if missed:
        print(f"\nWARNING — {len(missed)} team-season pages could not be fetched this run", flush=True)
        print("(rate limits). The database is INCOMPLETE. Run the same command again", flush=True)
        print("to fetch just the missing pages:  python3 " + os.path.basename(sys.argv[0]), flush=True)
    else:
        print(f"\nCOMPLETE — all {len(seasons)} team-seasons crawled.", flush=True)
    if offseason:
        espn_offseason(db, max_yr + 1)
        json.dump(db, open(DB_FILE, "w"))
    print(f"DONE — {len(db['players'])} players written to {DB_FILE}", flush=True)
    print("Import this file in the Database Builder (Import JSON button).", flush=True)


def fill_numbers():
    """
    Roster tables leave the No. cell blank for some players (mostly 1940s-60s seasons and
    10-day/one-game guys). Their OWN player page still lists the numbers they wore, so visit
    just those pages and backfill. Resumable, ~1.5s per player.
    """
    db = json.load(open(DB_FILE))
    P = db["players"]
    todo = [n for n, p in P.items() if not (p.get("N") or {})]
    print(f"{len(todo)} players missing jersey numbers \u2014 fetching their player pages (~{len(todo)*2//60+1} min)\u2026", flush=True)
    fixed = failed = 0
    for i, name in enumerate(todo, 1):
        p = P[name]
        bare = re.sub(r"\s*\([^)]*\)\s*$", "", name)
        try:
            pid = p.get("id")
            if not pid:
                # find the player page via Basketball-Reference search
                s = get("/search/search.fcgi?search=" + requests.utils.quote(bare))
                m = re.search(r'/players/[a-z]/([a-z0-9]+)\.html', s or "")
                pid = m.group(1) if m else None
            if not pid:
                failed += 1
                continue
            html = get(f"/players/{pid[0]}/{pid}.html")
            if not html:
                failed += 1
                continue
            nums = set()
            # "Jersey Number(s): 23, 6" block on the player page
            blk = re.search(r'Jersey Number[^<]*:?(.{0,400}?)</p>', html, re.S | re.I)
            if blk:
                for a in re.findall(r'>\s*#?(\d{1,2})\s*<', blk.group(1)):
                    nums.add(str(int(a)))
            # fallback: the per-season table's number column on their own page
            if not nums:
                for a in re.findall(r'data-stat="number_jersey"[^>]*>\s*#?(\d{1,2})\s*<', html):
                    nums.add(str(int(a)))
            if nums:
                # attach to their first team so the game can look them up
                team = (p.get("s") or [{}])[0].get("t")
                if team:
                    p.setdefault("N", {})[team] = sorted(nums, key=int)
                    fixed += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        if i % 20 == 0:
            json.dump(db, open(DB_FILE, "w"))
            print(f"  {i}/{len(todo)} \u2014 {fixed} filled, {failed} with no number on record", flush=True)
        time.sleep(1.6)
    json.dump(db, open(DB_FILE, "w"))
    print(f"DONE \u2014 {fixed} players given numbers, {failed} genuinely have none recorded anywhere.", flush=True)
    print("Next:  python3 ebk.py master   then   python3 ebk.py verify", flush=True)


# Players who went straight from high school (or overseas) to the NBA. Basketball-Reference
# lists a school for some of them anyway — a college they attended later, or for another sport
# (J.R. Smith played GOLF at North Carolina A&T) — which is not a valid game link.
PREPS_TO_PROS = {
    "J.R. Smith", "LeBron James", "Kobe Bryant", "Kevin Garnett", "Tracy McGrady",
    "Jermaine O'Neal", "Amar'e Stoudemire", "Dwight Howard", "Josh Smith", "Al Jefferson",
    "Monta Ellis", "Andrew Bynum", "Sebastian Telfair", "Shaun Livingston", "Robert Swift",
    "Martell Webster", "Gerald Green", "Andray Blatche", "Louis Williams", "C.J. Miles",
    "Ndudi Ebi", "Travis Outlaw", "James Lang", "Kendrick Perkins", "Darius Miles",
    "DeShawn Stevenson", "Korleone Young", "Leon Smith", "Moses Malone", "Darryl Dawkins",
    "Bill Willoughby", "Shawn Kemp", "Rashard Lewis", "Tyson Chandler", "Eddy Curry",
    "DeSagana Diop", "Kwame Brown", "Ousmane Cisse", "Lou Williams",
}


def build_master():
    """
    Ball Knowledge — MASTER database builder (v2).

    Scans this folder for every ball-knowledge-db*.json the crawler wrote,
    picks the richest one (most players + most countries), then:
      1. repairs garbled names (VarejÃ£o -> Varejão)
      2. merges duplicate players — including accent twins (Dončić / Doncic)
      3. fills country for no-college players (USA only when nothing recorded)
      4. writes ONE clean file: ball-knowledge-MASTER.json

    HOW TO RUN:
      cd ~/Downloads
      python3 ebk_master.py
    """
    import json, os, re, sys, glob, unicodedata

    candidates = sorted(glob.glob("ball-knowledge-db*.json"))
    if not candidates:
        sys.exit("No ball-knowledge-db*.json found here — run the crawler first (python3 ebk_countries.py).")

    best, best_score = None, -1
    for f in candidates:
        try:
            d = json.load(open(f, encoding="utf-8"))
            n = len(d.get("players", {}))
            co = sum(1 for p in d["players"].values() if p.get("co") and p["co"] != "USA")
            score = n + co * 10  # countries are the scarce resource — weight them
            print(f"  {f}: {n} players, {co} with real countries")
            if score > best_score:
                best, best_score, db = f, score, d
        except Exception as e:
            print(f"  {f}: unreadable ({e}) — skipped")

    players = db.get("players", {})
    print(f"Using {best} ({len(players)} players)")
    real_co = sum(1 for p in players.values() if p.get("co") and p["co"] != "USA")
    if real_co == 0:
        print("!! WARNING: this file has NO country data. The countries crawl output wasn't found in this folder.")
        print("!! Run  python3 ebk_countries.py  first, let it finish, then run this again.")

    MOJI = re.compile(r"[ÃÂ][\u0080-\u00BF]|Ã©|Ã¡|Ã³|Ã­|Ãº|Ã±|Ä|Å|Ã¶|Ã¼|Ã¤")

    def fix(s):
        if not s or not MOJI.search(s):
            return s
        try:
            r = s.encode("latin-1").decode("utf-8")
            return s if "\ufffd" in r else r
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s

    def canon(name):
        # accent-insensitive, punctuation-insensitive key for duplicate detection
        n = unicodedata.normalize("NFD", name)
        n = "".join(ch for ch in n if unicodedata.category(ch) != "Mn")
        return re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()

    def merge(a, b):
        a["s"] = (a.get("s") or []) + (b.get("s") or [])
        for k in ("c", "co", "j", "d", "v", "r"):
            if not a.get(k) and b.get(k):
                a[k] = b[k]
        Na, Nb = a.get("N") or {}, b.get("N") or {}
        for t, nums in Nb.items():
            Na[t] = sorted(set((Na.get(t) or []) + nums))
        a["N"] = Na
        return a

    fixed_names = fixed_fields = merged = 0
    out, keymap = {}, {}   # keymap: canon -> display name in out
    for name, p in players.items():
        if p.get("c"):
            c2 = fix(p["c"])
            if c2 != p["c"]: p["c"] = c2; fixed_fields += 1
        for st in p.get("s") or []:
            t2 = fix(st.get("t", ""))
            if t2 != st.get("t"): st["t"] = t2; fixed_fields += 1
        n2 = fix(name)
        if n2 != name: fixed_names += 1
        ck = canon(n2)
        if ck in keymap:
            existing = keymap[ck]
            # prefer the accented/richer spelling as the display name
            keep = n2 if (n2 != existing and any(ord(c) > 127 for c in n2)) else existing
            rec = merge(out.pop(existing), p)
            out[keep] = rec
            keymap[ck] = keep
            merged += 1
        else:
            out[n2] = p
            keymap[ck] = n2

    # preps-to-pros: strip the bogus school so the country becomes the link
    stripped = 0
    for name, p in out.items():
        if name in PREPS_TO_PROS and (p.get("c") or p.get("C")):
            p["c"] = None; p.pop("C", None); stripped += 1
    if stripped:
        print(f"Cleared bogus colleges for {stripped} preps-to-pros players", flush=True)

    usa = 0
    for p in out.values():
        if not p.get("c") and not p.get("co"):
            p["co"] = "USA"; usa += 1

    # merge suffix twins (Jimmy Butler / Jimmy Butler III) — overlapping careers = same person
    sfx = re.compile(r"^(.*?)\s+(Jr\.?|Sr\.?|II|III|IV|V)$", re.I)
    def _yrs(p):
        st = p.get("s") or []
        return (min(x["y1"] for x in st), max(x["y2"] for x in st)) if st else (9999, 0)
    for k in list(out.keys()):
        m = sfx.match(k)
        if not m or m.group(1) not in out:
            continue
        base = m.group(1)
        a1, a2 = _yrs(out[base]); b1, b2 = _yrs(out[k])
        if a1 > b2 or b1 > a2:
            continue  # no overlap — genuinely different people (Tim Hardaway Sr/Jr)
        tgt, src = out[base], out[k]
        for x in src.get("s", []):
            if not any(y["t"] == x["t"] and y["y1"] == x["y1"] and y["y2"] == x["y2"] for y in tgt.get("s", [])):
                tgt.setdefault("s", []).append(x)
        for t, ns in (src.get("N") or {}).items():
            tgt.setdefault("N", {}).setdefault(t, [])
            tgt["N"][t] = sorted(set(tgt["N"][t]) | set(ns))
        tgt["c"] = tgt.get("c") or src.get("c"); tgt["co"] = tgt.get("co") or src.get("co")
        if src.get("v") is not None and (tgt.get("v") is None or src["v"] > tgt["v"]):
            tgt["v"] = src["v"]
        del out[k]
        merged += 1

    # rename crawler-disambiguated keys — "Bobby Jones (02)" / "Name (rk)" — to career years "Bobby Jones (2006–2008)"
    for k in list(out.keys()):
        m = re.match(r"^(.*) \((?:\d\d|rk)\)$", k)
        if not m:
            continue
        st = out[k].get("s") or []
        yrs = f" ({min(x['y1'] for x in st)}\u2013{max(x['y2'] for x in st)})" if st else " (2)"
        nk = m.group(1) + yrs
        if nk not in out:
            out[nk] = out.pop(k)

    db["players"] = out
    db["masterClean"] = True
    json.dump(db, open("ball-knowledge-MASTER.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Repaired {fixed_names} garbled names + {fixed_fields} fields, merged {merged} duplicates")
    print(f"USA fallback for {usa} players · real countries kept: {sum(1 for p in out.values() if p.get('co') and p['co'] != 'USA')}")
    print(f"DONE — {len(out)} players written to ball-knowledge-MASTER.json")
    print("Import ball-knowledge-MASTER.json in the Database Builder.")


def run_rarity():
    import datetime as _dt
    _end = _dt.date.today().replace(day=1)
    _start = _end.replace(year=_end.year - 1)
    SPAN = f"{_start:%Y%m}0100/{_end:%Y%m}0100"
    """
    Ball Knowledge — rarity crawler (fixed edition).

    Scores every player by REAL Wikipedia searches/year, done correctly this time:
      - resolves each player via Wikipedia search WITH basketball context
        (no more Luka matching some other Doncic)
      - 1 request/sec with backoff on rate limits — slow (~3 h) but never garbage
      - resumable: stop any time, run again, it continues
      - sanity check at the end: prints the top 20 (must be LeBron/MJ/Curry-tier)
        and flags suspicious zeros instead of writing them

    HOW TO RUN (leave it going, e.g. overnight):
      cd ~/Downloads
      python3 ebk_rarity.py

    Writes rarity into ball-knowledge-MASTER.json and marks it trusted
    (rarityFixed) so the game switches from career-based to real search data.
    """
    import json, os, sys, time, urllib.request, urllib.parse

    SRC = "ball-knowledge-MASTER.json"
    STATE = "ebk_rarity_state.json"
    if not os.path.exists(SRC):
        sys.exit(f"{SRC} not found — run ebk_master.py first.")

    db = json.load(open(SRC, encoding="utf-8"))
    players = db["players"]
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}

    def get(url, tries=5):
        for i in range(tries):
            try:
                r = requests.get(url, headers={"User-Agent": "BallKnowledgeGame/1.0 (rarity scoring)"}, timeout=30)
                if r.status_code == 429:
                    time.sleep(60)
                    continue
                if r.status_code != 200:
                    return None
                return r.json()
            except Exception as e:
                wait = 5 * (i + 1)
                if "429" in str(e):
                    wait = 60
                time.sleep(wait)
        return None

    def resolve_title(name):
        # search WITH basketball context so namesakes lose
        q = urllib.parse.quote(name + " basketball player")
        j = get(f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&srlimit=3&format=json")
        if not j:
            return None
        hits = j.get("query", {}).get("search", [])
        lname = name.lower()
        def is_bball(h):
            blob = (h["title"] + " " + h.get("snippet", "")).lower()
            return any(w in blob for w in ("basketball", "nba", "aba "))
        # 1) exact name match that is verifiably a basketball article
        for h in hits:
            t = h["title"]
            tl = t.lower().replace("(basketball)", "").replace("(basketball player)", "").strip()
            if (tl == lname or tl.startswith(lname)) and is_bball(h):
                return t
        # 2) any basketball-context hit
        for h in hits:
            if is_bball(h):
                return h["title"]
        # 3) NOTHING basketball-related found: return None (unknown) — never a namesake
        # golfer/musician/politician page. Unknown > wrong: the game treats it as mid-rarity.
        return None

    def views_per_year(title):
        t = urllib.parse.quote(title.replace(" ", "_"), safe="")
        j = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{t}/monthly/" + SPAN)
        if not j or "items" not in j:
            return None
        return sum(m["views"] for m in j["items"])

    names = [n for n in players.items() if True]
    todo = [n for n in players if n not in state]
    print(f"{len(players)} players · {len(players) - len(todo)} already scored · {len(todo)} to go (~{len(todo)*2//60} min)")

    count = 0
    for name in todo:
        # disambiguated players ("Bobby Jones (1975–1986)") — search Wikipedia by the bare name
        search_name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
        try:
            time.sleep(1.0)
            title = resolve_title(search_name)
            v = views_per_year(title) if title else None
        except Exception as e:
            import traceback
            json.dump(state, open(STATE, "w"))
            print(f"\n!! CRASH on {name}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            sys.exit("Progress saved — send me the error above.")
        state[name] = {"t": title, "v": v}
        count += 1
        if count <= 5 or count % 25 == 0:
            json.dump(state, open(STATE, "w"))
            print(f"  {len(state)}/{len(players)}  ({name}: {v if v is not None else '??'} views/yr, page: {title})", flush=True)
    json.dump(state, open(STATE, "w"))

    # write into the database with sanity rules: a None/0 result is UNKNOWN, never "ultra rare"
    scored = {n: s["v"] for n, s in state.items() if s.get("v")}
    vals = sorted(scored.values())
    def tier(v):
        # percentile within the league
        import bisect
        pct = bisect.bisect_left(vals, v) / max(1, len(vals))
        return max(1, min(10, 10 - int(pct * 10)))
    for n, p in players.items():
        v = scored.get(n)
        if v:
            p["v"] = v
            p["r"] = tier(v)
        else:
            p.pop("v", None); p.pop("r", None)  # unknown -> game falls back to career-based

    db["rarityFixed"] = True
    json.dump(db, open(SRC, "w", encoding="utf-8"), ensure_ascii=False)

    top = sorted(scored.items(), key=lambda x: -x[1])[:20]
    print("\nSANITY CHECK — top 20 by searches (should be all-time greats):")
    for n, v in top:
        print(f"  {v:>10,}  {n}")
    zeros = sum(1 for n in players if n not in scored)
    print(f"\n{len(scored)} scored · {zeros} unknown (game uses career fallback for those)")
    print(f"DONE — rarity written into {SRC}. Re-import it in the Database Builder.")



def verify():
    import glob
    f = "ball-knowledge-MASTER.json" if os.path.exists("ball-knowledge-MASTER.json") else DB_FILE
    if not os.path.exists(f):
        sys.exit("No database file found here.")
    d = json.load(open(f, encoding="utf-8")); P = d["players"]
    print(f"file: {f} | players: {len(P)} | masterClean: {d.get('masterClean', False)} | rarityFixed: {d.get('rarityFixed', False)}")
    for n in ["Luka Don\u010di\u0107", "Luka Doncic", "Giannis Antetokounmpo", "LeBron James", "Nikola Joki\u0107", "Nikola Jokic", "Stephen Curry"]:
        p = P.get(n)
        if p:
            last = max((x["y2"] for x in p.get("s", [])), default=0)
            print(f"  {n:<26} country: {str(p.get('co')):<14} searches/yr: {str(p.get('v')):<10} tier: {str(p.get('r')):<4} last yr: {last}")
    noco = sum(1 for p in P.values() if not p.get("c") and not p.get("co"))
    usa = sum(1 for p in P.values() if not p.get("c") and p.get("co") == "USA")
    norar = sum(1 for p in P.values() if p.get("r") is None)
    print(f"  no college AND no country: {noco} (should be 0)")
    print(f"  no-college players marked USA: {usa}")
    print(f"  missing rarity: {norar}" + (" (run: python3 ebk.py rarity)" if norar else ""))
    bad = [n for n, p in P.items() if any(x["y2"] > 2027 or x["y1"] < 1946 for x in p.get("s", []))]
    print(f"  impossible year ranges: {len(bad)}" + (f" e.g. {bad[:3]}" if bad else ""))
    print("VERDICT: " + ("LOOKS GOOD — import this file in the Database Builder." if noco == 0 and len(P) > 5000 else "NOT READY — see numbers above."))

if __name__ == "__main__":
    _cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    print("=== EBK ALL-IN-ONE — FINAL ===", flush=True)
    if _cmd == "nettest":
        print("Testing Wikipedia connections (10s)…", flush=True)
        try:
            r = requests.get("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=LeBron+James+basketball&srlimit=1&format=json", headers={"User-Agent": "BallKnowledgeGame/1.0"}, timeout=15)
            print("  wikipedia search:", r.status_code, flush=True)
        except Exception as e:
            print("  wikipedia search FAILED:", type(e).__name__, str(e)[:200], flush=True)
        try:
            r2 = requests.get("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/LeBron_James/monthly/2025060100/2026060100", headers={"User-Agent": "BallKnowledgeGame/1.0"}, timeout=15)
            print("  pageviews api:", r2.status_code, r2.text[:80], flush=True)
        except Exception as e:
            print("  pageviews api FAILED:", type(e).__name__, str(e)[:200], flush=True)
        sys.exit(0)
    if _cmd == "numbers":
        fill_numbers()
    elif _cmd == "master":
        build_master()
    elif _cmd == "rarity":
        run_rarity()
    elif _cmd == "verify":
        verify()
    else:
        main()  # crawl (resumable); also: test <CODE> <YEAR>, rebuild, gaps, redo, offseason
        print("\nNext:  python3 ebk.py master   then   python3 ebk.py verify", flush=True)
