"""Rebuilds src/letsfly/data/airports.csv from OurAirports (public domain) and prints which codes came and went.

Keeps airports with an IATA code, the way Google Flights names them: when OurAirports moves a code (PBI became
DJT in 2026), Google follows. Codes an airport used to have (PBI, KPBI) become its aliases when they point to that
airport alone. Usage: airports.py REPO_ROOT
"""
import collections
import csv
import io
import re
import sys
import urllib.request

SOURCE = "https://davidmegginson.github.io/ourairports-data"
SIZE = {"large_airport": 3, "medium_airport": 2, "small_airport": 1}
# IATA city codes: OurAirports lists some as a keyword of one airport in the group, but BUE is EZE and AEP
METRO = set("BAK BER BHZ BJS BUE BUH CAS CHI DTT EAP JKT LON MIL MOW NYC OSA PAR REK RIO ROM SAO SEL SPK STO TYO "
            "WAS YEA YMQ YTO QDF QHO QLA QMI QSF".split())


def download(name):
    with urllib.request.urlopen(f"{SOURCE}/{name}", timeout=60) as response:
        return list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))


def main():
    target = f"{sys.argv[1]}/src/letsfly/data/airports.csv"
    countries = {row["code"]: row["name"] for row in download("countries.csv")}
    best = {}
    for row in download("airports.csv"):
        code = row["iata_code"].strip().upper()
        if len(code) != 3 or not code.isalpha() or row["type"] == "closed":
            continue
        entry = (code, row["icao_code"].strip().upper(), row["name"].strip(), row["municipality"].strip(),
                 countries.get(row["iso_country"], row["iso_country"]), SIZE.get(row["type"], 0),
                 int(row["scheduled_service"] == "yes"), row["keywords"])
        if code not in best or entry[6:4:-1] > best[code][6:4:-1]:
            best[code] = entry
    taken = set(best) | {entry[1] for entry in best.values()}
    claims = collections.defaultdict(set)
    for code, entry in best.items():
        for word in re.split(r"[,;]\s*", entry[7]):
            if re.fullmatch(r"[A-Z]{3,4}", word.strip()) and word.strip() not in taken | METRO:
                claims[word.strip()].add(code)
    aliases = collections.defaultdict(list)
    for alias, codes in claims.items():
        if len(codes) == 1:
            aliases[codes.pop()].append(alias)
    best = {code: entry[:7] + (" ".join(sorted(aliases[code])),) for code, entry in best.items()}
    with open(target, newline="", encoding="utf-8") as f:
        before = {row["iata"] for row in csv.DictReader(f)}
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["iata", "icao", "name", "city", "country", "size", "scheduled", "aliases"])
        writer.writerows(sorted(best.values()))
    print(f"{len(best)} airports; added {sorted(set(best) - before) or 'none'}; removed {sorted(before - set(best)) or 'none'}")


main()
