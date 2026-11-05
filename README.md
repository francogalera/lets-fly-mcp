# Lets fly

Very fast flight search for your terminal, and an MCP server for your agent: real Google Flights fares and the
cheapest dates in a range, with short links to the booking pages. Over MCP, Claude Code, Claude Desktop, Codex and
ChatGPT Desktop also get the deals published on promociones-aereas.com.ar. On Linux and macOS. Secure by default: it
runs sandboxed in Docker and reaches only the two sites it reads, with no API keys and no accounts.

## Install

    git clone https://github.com/francogalera/lets-fly-mcp.git ~/lets-fly && ~/lets-fly/install.sh

It needs Docker and python3. Then search from any terminal:

    $ flights search EZE JFK 2026-12-10
    USD 831  American
      EZE-JFK  2026-12-10 20:40 > 2026-12-11 05:30  10.8 h  AA954
      http://127.0.0.1:18375/lmzcjcev
    USD 837  American
      EZE-MIA-JFK  2026-12-10 10:10 > 2026-12-10 22:59  14.8 h  AA982 AA1357  layovers: MIA 2h20
      http://127.0.0.1:18375/zguyebih

`flights dates` and `flights airports` work the same way, and `--json` prints what the agent gets.

Or ask a new agent session for flights in plain words: the `flights` subagent searches the dates and airports
you allow and answers with the best options, their bags, layovers and links.

## Tools

- `search_flights`: flights on a date, or round trips, cheapest first, with bags, layovers (overnight, airport
  changes) and self-transfers.
- `cheapest_dates`: the lowest fare per departure date, up to 93 dates.
- `find_airports`: airports by code, city (Spanish names too) or name; a city code lists all its airports.
- `promociones-aereas`: the deals on promociones-aereas.com.ar (flights, packages, hotels), with their dates and
  links.

## Conventions

- **Secure by default**: each run is a read-only, unprivileged container with nothing mounted that reaches only the
  two sites it reads; every other attempt is blocked and logged. No API keys, no accounts.
- **Developer happiness**: tools read like the question, answers need no docs.
- **Convention over configuration**: no config files or env vars; defaults decide.
- **One way to do it**: no abstractions just in case.
- **Least surprise**: the price is what you pay, empty is not failed, unknown is `null`.
- **Small, readable code**: plain functions and dataclasses, comments only for a non-obvious why.
- **Sharp inside, strict at the edge**: inputs checked where they enter, outputs kept inside their schemas, polite
  to the sources (10 req/s to Google, 5 s between blog requests).
- **Dependencies earn their place**: curl_cffi and what it needs, nothing else.

## Use

    ./install.sh                         build the images, link flights, set up the clients and the link server
    flights --help                       every command
    flights search EZE JFK 2026-12-10    also: dates, airports; --json prints what the agent gets
    flights mcp                          the MCP server over stdio
    flights links                        the link server, which install.sh runs as a service (see Links)
    flights test                         offline tests inside the image, and the launcher on bash 3.2
    flights selftest                     live check: searches, MCP and its schemas, network blocking, cleanup
    flights audit                        live data checks against Google: pair prices, dates, a burst
    flights update                       pull this repo and reinstall: images, agent, skill and clients
    flights build                        rebuild both images after changing src/ or docker/
    flights image                        tag of the core image, which follows the content of src/ and docker/

## Isolation

Each session gets its own internal network, a read-only, unprivileged core container with nothing
mounted, and a proxy that only lets HTTPS through to www.google.com and promociones-aereas.com.ar.
Every egress attempt is logged to `~/.local/state/flights/egress.log`. The proxy exits once the core is gone, so a
client that kills `flights mcp` (Claude Code allows half a second) leaves no containers or networks behind.

## Clients

`install.sh` copies the subagent and the `broad-search` skill to `~/.claude` for Claude Code, adds `flights` to
Claude Desktop and to Codex (`codex mcp add`, plus both skills in `~/.agents/skills`) when they are installed, and
puts a plugin with the server and both skills in the personal marketplace (`~/.agents/plugins/marketplace.json`),
from which ChatGPT Desktop installs it.
Every client gets the server's `instructions`: the rules that matter most fit in their first 512 characters.

## Links

The tools hand out short links, `http://127.0.0.1:18375/<id>`, because the agent sometimes altered booking links of
about 300 characters when it copied them. The id is the hash of the link, so it can only open its own link.

The container still mounts nothing: it prints each link on stderr, and `bin/flights` keeps it in
`~/.local/state/flights/links` when the id matches. `flights links` redirects each id, only on 127.0.0.1, as a user
service that `install.sh` sets up (systemd on Linux, launchd on macOS). The port never changes and the files last,
so links in old answers keep working.

On macOS, keep the repo outside `~/Documents`, `~/Desktop` and `~/Downloads`, which a launchd agent cannot read (not
yet tried on a Mac).

## Schemas

Each tool publishes an `outputSchema` built from the `TypedDict` it returns, with a description per field, plus
the error object every tool can return instead. Claude Code (checked on 2.1.316) shows the model only the tool's
description and input schema, and let a result that broke its `outputSchema` through untouched. So each tool's
docstring says what the agent needs to read the answer, and the schema is the contract for clients that do
check results, as MCP asks them to: output objects accept extra fields, and the tests keep every output inside it.

## Tests

`flights test` runs offline. A few `ds:1` rows written by hand pin the decode: the pair price, the operator, the
bags, the airport change, `overnight`, `self_transfer` and `price_reference`. The output of the 4 tools and every error kind are
checked against the published schemas, which `flights selftest` also does with live replies.

## Credits

Airport data comes from [OurAirports](https://ourairports.com/data/) (public domain).
