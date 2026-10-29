---
name: broad-search
description: Strategies for a broad cheapest-trip search (airport groups, flexible dates, one-ways vs round trip, positioning flights, open-jaw, nearby hubs, deals from promociones-aereas.com.ar). Preloaded by the flights subagent; apply it when a flight request is open-ended or the user asks for a "búsqueda ampliada".
---

# Broad search

Apply this only when the request is open-ended ("lo más barato", "desde cualquier lugar", "en marzo", "a Colombia") or the user says "búsqueda ampliada". For a specific flight on specific dates, just search that. Budget: about 30 tool calls, a few in parallel at most; stop early when the extra strategies stop moving the price.

Strategies, cheapest to try first:

1. Airport groups. Search Buenos Aires as EZE,AEP and the destination country as all its airports at once (Dominican Republic PUJ,SDQ,STI,POP; Colombia CTG,BOG,MDE,BAQ,SMR,ADZ; Brazil northeast REC,SSA,FOR,NAT,MCZ). Then narrow down to what came back cheapest.
2. Flexible dates. cheapest_dates over the whole window; if the stay is not fixed, compare trip_days 7, 10 and 14.
3. Round trip vs two one-ways. Price each direction with cheapest_dates without trip_days and add them up; low-cost carriers often price one-ways the same as half the round trip.
4. Positioning. From an interior city (COR, ROS, MDZ, TUC, NQN, BRC...), compare flying direct against a cheap hop to EZE/AEP, SCL, LIM or GRU and the international flight from there. These are separate tickets: leave a long buffer, and AEP to EZE is a change of airport across the city.
5. Open-jaw. Arrive in one city and leave from another (Cartagena in, San Andrés out) as two one-ways, when the trip moves around.
6. Nearby hub at the destination. Land in a bigger airport near the destination and add a short domestic hop (MDE then CTG, BOG then ADZ) when the direct route is expensive.
7. Published deals. Start this one first, in parallel with strategy 1: promociones-aereas for the destination with details=false to scan titles. Then fetch details only for the one or two deals worth it (narrow query, limit 1 or 2, month and stay_days). For a flight deal, run search_flights on two or three of its dates to get today's real price per date; its "a partir de" is a snapshot from when the post was written. Package deals can't be priced per date: give the dates and the Despegar links.

Judgment, not rules: discard itineraries that glue an unlikely carrier onto a short hop (a "Bek Air" leg PUJ-SDQ turned out to be bad data), and say so. Flag self_transfer, change_of_airport, overnight layovers and fares whose bags show no carry-on.

Report a short ranked list: price, which strategy produced it, route and airlines, dates, the caveat that matters, and the link. End with the one option you would pick and why.
