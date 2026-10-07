# TrainWatcher

A personal Telegram bot that watches Italian high-speed train fares (Trenitalia Frecce, Italo) over time and tells its owner when prices drop.

## Language

### Watching

**Watch**:
A standing request from the Owner to track fares for one route over a set of days, with optional filters (fare, class, operators, max price). One-way watches have one Leg; round-trip watches have two.
_Avoid_: alert (that's a message), search, subscription

**Cheapest watch**:
A Watch with no Fare selected: it tracks the lowest price over every Fare the Passenger type can buy.
_Avoid_: simple watch, any-fare watch

**Fare watch**:
A Watch tracking only the Fares the Owner selected (e.g. FrecciaYOUNG); it never falls back to other Fares.
_Avoid_: specific watch, filtered watch

**Past watch**:
A Watch whose last departure has passed; it is no longer checked, but its history stays viewable.
_Avoid_: expired, archived, deleted

**Leg**:
One direction of a Watch: origin, destination, its travel days (one day, a month, or a custom period of up to 92 days) and an optional departure-time window.
_Avoid_: segment, journey (operator API terms)

**Passenger type**:
The single traveller a Watch is priced for: adult, young or senior. It decides which Fares can be offered; a young or senior traveller can also buy every adult Fare.
_Avoid_: profile, age group, pax

**Owner**:
The person who runs the bot: the only one with no limits, who invites and removes Friends and receives every maintenance notice.
_Avoid_: user, admin

**Friend**:
Someone the Owner allowed after they opened the Invite link. Friends have their own Watches (at most 5) and a limit on searches per hour; the Owner can remove them, which stops their Watches.
_Avoid_: guest, member, user

**Invite link**:
The one link that lets a person ask the Owner for access. Each person asks once; the Owner allows or declines. A new link replaces the old one.
_Avoid_: invitation code, referral

**Max price**:
The Owner's price ceiling for a Watch: per Leg for one-way watches, total for round trips. It gates Drop alerts of Cheapest watches only; Fare watch alerts are just marked under or above it, and the Status view ignores it.
_Avoid_: budget, threshold, target

### Prices

**Operator**:
A rail company whose fares are tracked: Trenitalia or Italo.
_Avoid_: carrier, provider

**Train**:
One scheduled departure, identified by operator, train number and departure date-time.
_Avoid_: solution, connection, service

**Offer**:
One purchasable price for a Train, in a specific Class and Fare, possibly with a count of seats left.
_Avoid_: ticket, tariff

**Fare**:
The commercial product of an Offer (e.g. Base, Super Economy, FrecciaYOUNG, Italo Low Cost, A/R same day).
_Avoid_: offer type, tariff, price level

**Class**:
The comfort level of an Offer (e.g. Standard, Business, Smart, Prima, Club).
_Avoid_: service level, product class, coach

**A/R same day**:
A Fare that exists only when both Legs of a round trip travel on the same day and are bought together.
_Avoid_: return ticket, round-trip discount

**Best price**:
For one Train, the lowest Offer matching the Watch's filters.

**Lowest price**:
For a Leg, the minimum Best price across its Trains.
_Avoid_: cheapest fare (ambiguous between Train and Leg)

**Tie**:
Two or more Trains whose Best price equals the Lowest price; all of them are always reported.

**Observation**:
A recorded change in an Offer's price or availability at a point in time; history is built from Observations.
_Avoid_: snapshot, sample

### Telling the Owner

**Status view**:
The on-request report of a Watch: the 5 cheapest Trains plus any further Ties, shown regardless of Max price.
_Avoid_: summary, overview

**Alert**:
An unsolicited message sent when a Watch's prices move in a way the Owner cares about. Kinds: Drop, Rise (Fare watches, or when switched on), Under max, Back in stock, Gone (sold out or purchase window closed — only after a live Check confirms it), Still on sale (past the purchase window but a live Check finds it) and Last day to buy.
_Avoid_: notification (generic), status

**Purchase window**:
The period in which a Fare can still be bought; young and senior Fares close 11 days before departure even if seats remain.
_Avoid_: booking deadline, cutoff (informal)

**Search**:
A one-off Check of a route and days asked by the Owner, answered once with a Status-view-style report; it creates no Watch unless the Owner taps "Watch this".
_Avoid_: query, lookup, quick watch

**Check**:
One fetch of current Offers for a Watch's Legs from the Operators.
_Avoid_: poll, scan, refresh

**Run**:
One execution of the price-checking job, during which commands are answered and due Checks are made.
_Avoid_: tick, cycle, job (the code, not the execution)
