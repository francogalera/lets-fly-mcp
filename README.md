# Lets fly

Very fast flight search for your terminal, and an MCP server for your agent: real Google Flights fares and the
cheapest dates in a range, every seller's fares from a flight metasearch that compares agencies and airlines, with
short links to the booking pages. Over MCP, Claude Code, Claude Desktop, Codex and ChatGPT Desktop also get the deals
published on promociones-aereas.com.ar. On Linux and macOS. Secure by default: it runs sandboxed in Docker and reaches
only the sites it reads, with no API keys and no accounts.

![The two ways to use it, then a search in the terminal and a question to the flights subagent](https://github.com/user-attachments/assets/b45f5abd-4dca-4269-90bb-22a927404c6e)

## Install

    git clone https://github.com/francogalera/lets-fly-mcp.git ~/lets-fly && ~/lets-fly/install.sh

It needs Docker, git, curl and python3. Then search from any terminal:

    $ flights search EZE JFK 2026-12-10
    USD 831  American
      EZE-JFK  2026-12-10 20:40 > 2026-12-11 05:30  10.8 h  AA954
      http://127.0.0.1:18375/lmzcjcev

`flights dates`, `flights airports`, `flights meta` and `flights meta-dates` work the same way, and `--json` prints
what the agent gets.

Or ask a new agent session for flights in plain words: the `flights` subagent searches the dates and airports
you allow and answers with the best options, their bags, layovers and links.

## Tools

- `search_flights`: flights on a date, or round trips, cheapest first, with bags, layovers (overnight, airport
  changes) and self-transfers.
- `cheapest_dates`: the lowest fare per departure date, up to 93 dates.
- `find_airports`: airports by code, city (Spanish names too) or name; a city code lists all its airports.
- `metasearch_flights`: every seller's live fares for one trip (one way, round trip or multi-city), with the 3
  cheapest sellers per itinerary, bag, change and refund fees, and a summary per airline and stops. It spends 1 search
  of an hourly budget per IP, and says so in every reply.
- `metasearch_dates`: the lowest fares the metasearch saw, per month or per day, for free.
- `promociones-aereas`: the deals on promociones-aereas.com.ar (flights, packages, hotels), with their dates and
  links.

## Conventions

- **Secure by default**: each run is a read-only, unprivileged container with nothing mounted that reaches only the sites it reads; every other attempt is blocked and logged. No API keys, no accounts.
- **Developer happiness**: tools read like the question, answers need no docs.
- **Convention over configuration**: no config files or env vars; defaults decide.
- **One way to do it**: no abstractions just in case.
- **Least surprise**: the price is what you pay, empty is not failed, unknown is `null`.
- **Small, readable code**: plain functions and dataclasses, comments only for a non-obvious why.
- **Sharp inside, strict at the edge**: inputs checked where they enter, outputs kept inside their schemas, polite to the sources (10 req/s to Google, 5 s between blog requests, one metasearch search at a time).
- **Dependencies earn their place**: curl_cffi and what it needs, nothing else.

## Use

    ./install.sh                         install this folder: images, command, clients and link server
    flights --help                       every command
    flights search EZE JFK 2026-12-10    also: dates, airports, meta, meta-dates; --json prints what the agent gets
    flights mcp                          the MCP server over stdio
    flights links                        the link server, which runs as a service of the user (see Links)
    flights test                         offline tests inside the image, the launcher on bash 3.2, --from with git
    flights selftest                     live check: searches, filters, MCP and its schemas, network blocking, cleanup
    flights audit [filters]              live data checks against Google: pair prices, dates, a burst, the filters
    flights update [--from PATH]         install the latest version, or a checkout to try it (see Update)
    flights rollback                     back to the previous version, without building
    flights build                        rebuild both images after changing src/ or docker/
    flights image                        tag of the core image, which follows the content of src/ and docker/
    flights images                       tags of both images

## Isolation

Each session gets its own internal network, a read-only, unprivileged core container with nothing
mounted, and a proxy that only lets HTTPS through to the hosts in `ALLOW` in `bin/flights`.
Every egress attempt is logged to `~/.local/state/flights/egress.log`. The proxy exits once the core is gone, so a
client that kills `flights mcp` (Claude Code allows half a second) leaves no containers or networks behind.

## Clients

Every install sets up Claude Code (the subagent and the `broad-search` skill in `~/.claude`), Claude Desktop and Codex
when they are installed, and a plugin in the personal marketplace, from which ChatGPT Desktop installs it. A client that
fails is skipped with a warning, and the next update tries again.

## Links

The tools hand out short links, `http://127.0.0.1:18375/<id>`, because the agent sometimes altered booking links of
about 300 characters when it copied them. The id is the hash of the link, so it can only open its own link.

The container still mounts nothing: it prints each link on stderr, and `bin/flights` keeps it in
`~/.local/state/flights/links` when the id matches. `flights links` redirects each id, only on 127.0.0.1, as a user
service that every install sets up and restarts (systemd on Linux, launchd on macOS). The port never changes and the
files last, so links in old answers keep working.

## Update

`flights update` installs the latest version in `~/.local/share/flights`, apart from your clone, which you can delete
if no client failed. If the build fails, nothing changes. Only the current and the previous version stay.

`flights rollback` goes back to the previous version without building. `flights update --from .` installs a checkout to
try a branch: each try replaces the last one, and rollback returns to the version you were on before trying.

## Tests

`flights test` runs offline. A few `ds:1` rows written by hand pin the decode: the pair price, the operator, the
bags, the airport change, `overnight`, `self_transfer` and `price_reference`; metasearch answers written by hand pin its
flow, its decoder, blocks, the cache and the budget. The output of the 6 tools and every error kind are
checked against the published schemas, which `flights selftest` also does with live replies.

Every PR runs `flights test` on GitHub Actions. `gh workflow run release.yml -f version=vX.Y.Z` tags main and publishes
a release only if `flights test` and `flights selftest` pass. A release is only a label: `flights update` installs the
latest commit on main.

## Credits

Airport data comes from [OurAirports](https://ourairports.com/data/) (public domain).
