# Lets fly

Flight search for the `flights` Claude Code subagent: Google Flights and promociones-aereas.com.ar deals,
behind an MCP server, isolated in Docker.

## Conventions

- **Developer happiness**: tools read like the question, answers need no docs.
- **Convention over configuration**: no config files or env vars; defaults decide.
- **One way to do it**: no abstractions just in case.
- **Least surprise**: the price is what you pay, empty is not failed, unknown is `null`.
- **Small, readable code**: plain functions and dataclasses, comments only for a non-obvious why.
- **Sharp inside, strict at the edge**: sandboxed, allowlisted, polite (10 req/s to Google, 20 s between blog requests).
- **Dependencies earn their place**: curl_cffi and what it needs, nothing else.

## Use

    ./install.sh                         build the images, link flights, install the subagent and its skill
    flights search EZE MIA 2026-12-10    also: dates, airports; --json prints what the agent gets
    flights mcp                          the MCP server over stdio
    flights test                         offline tests inside the image
    flights selftest                     live check: searches, MCP, network blocking, cleanup
    flights audit                        live data checks against Google: pair prices, dates, a burst
    flights upstream                     what changed in fli since the commit we derive from, read only

## Isolation

Each session gets its own internal network, a read-only, unprivileged core container with nothing
mounted, and a proxy that only lets HTTPS through to www.google.com and promociones-aereas.com.ar.
Every egress attempt is logged to `~/.local/state/flights/egress.log`.

## Links

The tools hand out short local links, `file:///home/<you>/.local/state/flights/l/<id>.html`, instead of booking
links of about 300 characters, which the agent sometimes altered when it copied them. Each page redirects to the
real link. The core prints `LINK <id> <url>` on stderr, and `bin/flights` pipes it through `src/letsfly/links.py`,
which writes the page only when `<id>` is the hash of `<url>`. The container still mounts nothing.

## Tests

`tests/replay` keeps recorded pages (the `ds:1` blob and each row's aria-label) and the reference output
of the 4 tools. A refactor must reproduce it exactly; a behavior change updates `reference.json`.

## Credits

Part of the code derives from [fli](https://github.com/punitarani/fli) by Punit Arani (MIT), credited in
`LICENSE`. Airport data comes from [OurAirports](https://ourairports.com/data/) (public domain).
