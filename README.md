# Lets fly

Flight search for the `flights` Claude Code subagent, Claude Desktop, Codex and ChatGPT Desktop: Google Flights and
promociones-aereas.com.ar deals, behind an MCP server, isolated in Docker. Runs on Linux and macOS.

## Conventions

- **Developer happiness**: tools read like the question, answers need no docs.
- **Convention over configuration**: no config files or env vars; defaults decide.
- **One way to do it**: no abstractions just in case.
- **Least surprise**: the price is what you pay, empty is not failed, unknown is `null`.
- **Small, readable code**: plain functions and dataclasses, comments only for a non-obvious why.
- **Sharp inside, strict at the edge**: sandboxed, allowlisted, polite (10 req/s to Google, 5 s between blog requests).
- **Dependencies earn their place**: curl_cffi and what it needs, nothing else.

## Use

    ./install.sh                         build the images, link flights, set up the clients and the link server
    flights --help                       every command
    flights search EZE MIA 2026-12-10    also: dates, airports; --json prints what the agent gets
    flights mcp                          the MCP server over stdio
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

The tools hand out short links, `http://127.0.0.1:18375/<id>`, instead of booking links of about 300 characters,
which the agent sometimes altered when it copied them and which filled answers with several flights. The id is the
hash of the link, so it can only open its own link. `file://` links are no way out: each client decides what to do
with them, and Claude Code (2.1.316) shows the file in the file manager instead of opening it.

The core prints `LINK <id> <url>` on stderr, and `bin/flights` pipes it through `src/letsfly/links.py record`,
which keeps it only when `<id>` is the hash of `<url>`, in one file per session (for the MCP, a client's session):
`local/flight-links/YYYY/MM/DD/HHMMSS-<command>-<pid>.tsv`, one `<id>`, tab, `<url>` per line.
`links.py serve` answers each id with a redirect to its link and anything else with a 404, only on 127.0.0.1. It
runs as a service of the user that `install.sh` sets up (systemd on Linux, launchd on macOS) and `bin/flights`
starts when nothing answers. The files are what lasts: a link handed out while the server was down opens once it
runs, and the port never changes, so links in old answers keep working. The container still mounts nothing.

## Schemas

Each tool publishes an `outputSchema` built from the `TypedDict` it returns, with a description per field, plus
the error object every tool can return instead. Claude Code (checked on 2.1.316) shows the model only the tool's
description and input schema, and let a result that broke its `outputSchema` through untouched. So each tool's
docstring says what the agent needs to read the answer, and the schema is the contract for clients that do
check results, as MCP asks them to: output objects accept extra fields, and the tests keep every output inside it.

## Tests

`flights test` runs offline. A few `ds:1` rows written by hand pin the decode: the pair price, the operator, the
bags, the airport change, `overnight` and `self_transfer`. The output of the 4 tools and every error kind are
checked against the published schemas, which `flights selftest` also does with live replies.

## Credits

Airport data comes from [OurAirports](https://ourairports.com/data/) (public domain).
