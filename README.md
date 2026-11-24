# Lets fly

Very fast flight search for your terminal, and an MCP server for your agent: real Google Flights fares and the cheapest dates in a range, every seller's fares from a flight metasearch that compares agencies and airlines, with short links to the booking pages. On Linux and macOS. Secure by default: it runs sandboxed in Docker and reaches only the sites it reads, with no API keys and no accounts.

![An MCP server for your agent in Claude Desktop, then flight search in the terminal](https://github.com/user-attachments/assets/19d72cf7-3349-45ba-b9f8-ff53cff19ce3)

## Install

    curl -fsSL https://github.com/francogalera/lets-fly-mcp/releases/latest/download/release.py | python3 -I - update

It needs Docker, curl and python3. It installs the latest release with its ready-made images, the `flights` command in `~/.local/bin`, the link server, Claude Code (the subagent and the `broad-search` skill), Claude Desktop and Codex when they are installed, and a plugin in the personal marketplace, from which ChatGPT Desktop installs it. A client that fails is skipped with a warning, and the next update tries again.

## Use

Ask a new agent session for flights in plain words: the `flights` MCP searches the dates and airports you allow and answers with the best options, their bags, layovers and links.

Or search from any terminal:

    flights search EZE JFK 2026-12-10    flights on a date, with an optional return date
    flights dates EZE JFK START END      lowest price per departure date, with an optional trip length
    flights airports QUERY               airports by code, city or name
    flights meta EZE JFK 2026-12-10      every seller's fares on the metasearch (spends 1 search)
    flights meta-dates EZE JFK           lowest fares the metasearch saw, per month or per day (free)
    flights update                       install the latest release (see Update)
    flights rollback                     back to the previous version
    flights --help                       every command

Each search command takes `--json`, which prints what the agent gets.

## Tools

- `search_flights`: flights on a date, or round trips, cheapest first, with bags, layovers (overnight, airport changes) and self-transfers.
- `cheapest_dates`: the lowest fare per departure date, up to 93 dates.
- `find_airports`: airports by code, city (Spanish names too) or name; a city code lists all its airports.
- `metasearch_flights`: every seller's live fares for one trip (one way, round trip or multi-city), with one price per itinerary from its cheapest seller, bag, change and refund fees, and a summary per airline and stops. Each call spends 1 search, counted across every session on this machine's IP, and the reply warns to ask first when another one could get the IP blocked.
- `metasearch_dates`: the lowest fares the metasearch saw, per month or per day, for free.

## Links

The tools hand out short links, `http://127.0.0.1:18375/<id>`, because the agent sometimes altered booking links of about 300 characters when it copied them. The id is the hash of the link, so it can only open its own link.

The core still mounts nothing: it prints each link on stderr, and the launcher keeps it on the host when the id matches. `flights links` redirects each id, only on 127.0.0.1, as a user service that every install sets up and restarts (systemd on Linux, launchd on macOS). The port never changes and the files last, so links in old answers keep working.

## Security

Each session gets its own internal network and two read-only, unprivileged containers:

- **Core**: letsfly, the MCP server and the terminal commands, with no network of its own and nothing mounted.
- **Proxy**: the only way out. It lets HTTPS through only to the allowed hosts and logs every attempt. Its one mount is a read-only file where the host logs each metasearch search, block and query (time, egress IP, session), so every session on this machine shares one budget per IP. It learns that IP from the metasearch's edge, with a request that spends no search and is logged like the rest.

The proxy exits once the core is gone, so a client that kills `flights mcp` (Claude Code allows half a second) leaves no containers or networks behind.

## Update

`flights update` installs the latest release in its own folder. It downloads the release's commit and pulls its two images from GitHub's container registry by the digests the release declares in `images.json`, so it runs the images that passed the release's checks and builds nothing. If anything does not match, nothing changes. Only the current and the previous version stay.

`flights rollback` goes back to the previous version without building.

## Development

Conventions:

- **Secure by default**: each run is a read-only, unprivileged container with nothing mounted that reaches only the sites it reads; every other attempt is blocked and logged. No API keys, no accounts.
- **Developer happiness**: tools read like the question, answers need no docs.
- **Convention over configuration**: no config files or env vars; defaults decide.
- **One way to do it**: no abstractions just in case.
- **Least surprise**: the price is what you pay, empty is not failed, unknown is `null`.
- **Small, readable code**: plain functions and dataclasses, comments only for a non-obvious why.
- **Sharp inside, strict at the edge**: inputs checked where they enter, outputs kept inside their schemas, polite to the sources (10 req/s to Google, one metasearch search at a time).
- **Dependencies earn their place**: curl_cffi and what it needs, nothing else.

Commands:

    flights mcp                          the MCP server over stdio
    flights links                        the link server, which runs as a service of the user (see Links)
    flights update --from PATH           build and install a checkout to try it
    flights build                        rebuild both images after changing the code
    flights test                         offline tests inside the image, the launcher on bash 3.2, --from with git
    flights selftest [--no-filters]      live check: searches, filters, MCP and its schemas, network blocking, cleanup
    flights audit [filters]              live data checks against Google: pair prices, dates, a burst, the filters
    flights image                        tag of the core image, which follows its code
    flights images                       tags of both images

To work on a clone, which needs git too, run its `./install.sh`: it builds both images and installs the checkout as a stable version. `flights update --from .` installs a checkout to try a branch: each try replaces the last one, and rollback returns to the version you were on before trying.

### Tests

`flights test` runs offline. A few `ds:1` rows written by hand pin the decode: the pair price, the operator, the bags, the airport change, `overnight`, `self_transfer` and `price_reference`; metasearch answers written by hand pin its flow, its decoder, blocks, the cache and the budget. The output of the 5 tools and every error kind are checked against the published schemas, which `flights selftest` also does with live replies.

Every PR runs `flights test` on GitHub Actions, and main only takes changes through a PR that passed it. The repository admin can bypass this.

### Releases

`gh workflow run release.yml -f version=vX.Y.Z` builds both images on amd64 and arm64 and runs `flights test` and `flights selftest --no-filters` on each. Only then does it push those same images and publish the release with their digests. The release skips the filter checks, which test Google rather than the image, and retries the selftest once after two minutes, since Google throttles shared runner IPs. For the filter checks, run `flights selftest` or `flights audit filters` locally.

## Credits

Airport data comes from [OurAirports](https://ourairports.com/data/) (public domain).
