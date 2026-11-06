---
name: broad-search
description: Strategies for a broad flight search when the dates, airports or destination are open (airport groups, flexible dates, one-ways vs round trip, positioning flights, nearby hubs, open-jaw), for any objective (price, nonstop, duration, schedule). Preloaded by the flights subagent; apply it when a flight request is open-ended or the user asks for a "broad search".
---

# Broad search

Apply this only when the request is open-ended ("from anywhere", "in March", "to Colombia", "the best flight") or the user asks for a "broad search", in any language. For a specific flight on specific dates, just search that. Budget: about 30 tool calls, a few in parallel at most; stop early when the extra strategies stop improving the result for the objective.

Start from the objective and the constraints the agent prompt defines. Each strategy says what it serves: skip any that breaks a constraint, and skip the price-only ones when the objective is not price.

For any objective, cheapest to try first:

1. Airport groups. Search Buenos Aires as EZE,AEP and the destination country as all its airports at once (Dominican Republic PUJ,SDQ,STI,POP; Colombia CTG,BOG,MDE,BAQ,SMR,ADZ; Brazil northeast REC,SSA,FOR,NAT,MCZ). Then narrow down to what came back best.
2. Flexible dates. cheapest_dates over the whole window; if the stay is not fixed, compare trip_days 7, 10 and 14.

For price only, when no constraint rules out what they add:

3. Round trip vs two one-ways. Price each direction with cheapest_dates without trip_days and add them up; low-cost carriers often price one-ways the same as half the round trip. Adds separate tickets.
4. Positioning. From an interior city (COR, ROS, MDZ, TUC, NQN, BRC...), compare flying direct against a cheap hop to EZE/AEP, SCL, LIM or GRU and the international flight from there. Adds a leg on its own ticket: leave a long buffer, and AEP to EZE is a change of airport across the city.
5. Nearby hub at the destination. Land in a bigger airport near the destination and add a short hop (MDE then CTG, BOG then ADZ) when the direct route is expensive. Adds a leg.

For the itinerary, when the trip moves around or the user asks for it:

6. Open-jaw. Arrive in one city and leave from another (Cartagena in, San Andres out) as two one-ways.

Judgment, not rules: discard itineraries that glue an unknown or unlikely carrier onto a short hop, which is usually bad data, and say so. Flag self_transfer, change_of_airport, overnight layovers and fares whose bags show no carry-on.

price_reference belongs to one search: its numbers don't compare across routes or dates. Use it to say whether the price you picked is unusual for its own route.

Report a short list ranked as the agent prompt's "Done when" says: price, which strategy produced it, route and airlines, dates, the caveat that matters, and the link. End with the one option you would pick and why.
