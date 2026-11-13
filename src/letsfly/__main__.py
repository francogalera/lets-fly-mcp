"""The flights terminal: the same searches as the MCP, as text or JSON."""
import argparse
import json

from letsfly import audit, tools


def main():
    cli = argparse.ArgumentParser(prog="flights", description="Searches flights on Google Flights and a metasearch.")
    sub = cli.add_subparsers(dest="command", required=True)
    f = sub.add_parser("search", help="flights on one date, with an optional return")
    f.add_argument("origin"), f.add_argument("destination"), f.add_argument("date")
    f.add_argument("return_date", nargs="?")
    f.add_argument("--limit", type=int, default=5)
    d = sub.add_parser("dates", help="lowest price per departure date in a range")
    d.add_argument("origin"), d.add_argument("destination"), d.add_argument("start_date"), d.add_argument("end_date")
    d.add_argument("trip_days", nargs="?", type=int)
    d.add_argument("--limit", type=int, default=10)
    a = sub.add_parser("airports", help="finds airports by code or name")
    a.add_argument("query")
    m = sub.add_parser("meta", help="every seller's fares for one trip on the metasearch (spends 1 search)")
    m.add_argument("origin"), m.add_argument("destination"), m.add_argument("date")
    m.add_argument("return_date", nargs="?")
    m.add_argument("--limit", type=int, default=5)
    md = sub.add_parser("meta-dates", help="lowest fares the metasearch saw, per month or per day (free)")
    md.add_argument("origin"), md.add_argument("destination")
    md.add_argument("--month", help="YYYY-MM: per day of that month")
    md.add_argument("--outbound", help="YYYY-MM-DD: the returns for that departure")
    md.add_argument("--one-way", dest="one_way", action="store_true")
    u = sub.add_parser("audit", help="live checks of the data against Google: pair prices, dates, a burst, the filters")
    u.add_argument("only", nargs="?", choices=["filters"], help="only whether Google honors the filters")
    for p in (f, d, a, m, md):
        p.add_argument("--json", action="store_true", help="the reply exactly as the agent gets it")
    args = vars(cli.parse_args())
    if args["command"] == "audit":
        return audit.main(args["only"])
    command, as_json = args.pop("command"), args.pop("json")
    out = {"search": tools.search_flights, "dates": tools.cheapest_dates, "airports": tools.find_airports,
           "meta": tools.metasearch_flights, "meta-dates": tools.metasearch_dates}[command](**args)
    if as_json or "error" in out:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print("\n".join(TEXT[command](out)))
    if out.get("missing_dates"):
        print(f"Unknown, their page failed to load: {', '.join(out['missing_dates'])}")
    if out.get("note"):
        print(out["note"])
    if "budget" in out:
        budget = out["budget"]
        whose = "" if budget["shared"] else " (this session only: the proxy could not count the others)"
        print(f"Cost: {out['cost']} search; streak of {budget['streak_seconds']} s, of a safe "
              f"{budget['safe_streak_seconds']} s{whose}")
        if budget.get("warning"):
            print(f"Warning: {budget['warning']}")


def _flights(out):
    for row in out["flights"]:
        price = f"{out['currency']} {row['price']:.0f}" if row["price"] is not None else "price unknown"
        seller = f"  from {row['sellers'][0]['name']}" if row.get("sellers") else ""
        yield f"{price}  {row['airlines']}{seller}" + ("  (separate tickets)" if row.get("self_transfer") else "")
        for leg in (row["out"], row.get("back")):
            if leg:
                stops = ", ".join(f"{x['airport']} {x['minutes'] // 60}h{x['minutes'] % 60:02d}" for x in leg["layovers"])
                yield f"  {leg['route']}  {leg['depart']} > {leg['arrive']}  {leg['hours']} h  {leg['flights']}" + (f"  layovers: {stops}" if stops else "")
        if row.get("link"):
            yield f"  {row['link']}"


def _dates(out):
    for row in out["dates"]:
        yield f"{row['date']}" + (f" > {row['return']}" if "return" in row else "") + f"  {out['currency']} {row['price']:.0f}"


def _airports(out):
    for a in out["airports"]:
        yield f"{a['code']}  {a['name']}"


def _seen(out):
    for row in out["prices"]:
        yield f"{row.get('date') or row['month']}  {out['currency']} {row['price']:.0f}" + ("  low" if row.get("low") else "")
    yield out["seen"]


TEXT = {"search": _flights, "dates": _dates, "airports": _airports, "meta": _flights, "meta-dates": _seen}

if __name__ == "__main__":
    main()
