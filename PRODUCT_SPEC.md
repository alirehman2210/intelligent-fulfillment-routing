# Product Spec — Intelligent Fulfillment Routing Engine

[README.md](README.md) — project overview · [DECISIONS.md](DECISIONS.md) — product decisions and trade-offs

## How I used this document

I wrote the first version of this spec before building anything. As I implemented it, I came back and updated a few sections with what I measured and what I got wrong. I kept the updates in the same document because I was using the spec as a working build guide, not a frozen handoff.

The loop was: write the spec, build, learn, update the spec.

Sections marked **Updated after building** contain things I measured or changed during implementation. Everything else is the product thinking that guided the build.

Throughout this document, **FC** means fulfillment center, which is just a warehouse that ships customer orders. **SKU** means one specific product.

---

## 1. The business problem

A merchant keeps stock in several warehouses. An order comes in, and something has to decide which warehouse ships it.

That decision looks simple and is not. Three things pull against each other:

- **The delivery promise.** The shopper was shown a date at checkout. They are expecting the package then.
- **Shipping cost.** The merchant pays it, and it varies a lot depending on which warehouse the package leaves from.
- **Available stock.** Warehouses hold different amounts, and the stock is shared across every order coming in that day.

Shipping from the cheapest warehouse does not always get the package there on time. Shipping from the fastest warehouse often costs more than the situation needed. And when stock is tight, filling one order changes what is possible for the next one.

The hard part is not picking a warehouse. It is picking one without quietly breaking a promise or quietly overspending, and being able to show which one you did and why.

## 2. Who this is for

**The merchant** is the primary user. They sell the product, they pay the shipping bill, and they carry the relationship with the shopper. They want their customer to get the package on time without paying more than the situation required. They also want to understand why a particular warehouse was chosen, because a decision they cannot inspect is a decision they cannot challenge.

**The shopper** is affected but never sees the engine. They see a promised date at checkout and they either get the package by then or they do not.

**The operations team** running the fulfillment network is the third audience. When a promise is missed, they need to know whether the problem was the promise, the inventory placement, or the routing rule, because those three need completely different fixes.

## 3. User story

> As a merchant, I want the platform to choose the best fulfillment center for each order, so my customer receives it by the promised date without me spending more on shipping than necessary. I also want to understand why that fulfillment center was chosen.

The last sentence is doing real work. A routing engine that gets good answers but cannot explain them is hard to trust and impossible to improve.

## 4. Product objectives

**Primary objective: protect the shopper's promised delivery date.**

**Secondary objective: keep shipping cost down.**

The order is fixed. First find the warehouses that have enough stock and can still make the promised date. Then pick the lowest-cost option among those.

Promise-first is my hypothesis for this prototype, not a universal rule. Shipping cost is easy to measure. The business effect of a late delivery is harder to see — it shows up through support contacts, refunds, the merchant's own view of the service, and whether that shopper orders again. My reasoning is that if the easy number wins by default, the hard one never gets counted.

**What this claim is not:** the prototype measures the shipping cost difference between the two policies exactly. It says nothing about how customers actually react to a late delivery, because it has no data on that. A merchant selling low-margin commodity goods might reasonably prefer the cheaper route and accept more lateness. In production this should be a merchant setting, not my opinion.

## 5. Success measures

### Primary outcome

Promises kept among the nine valid orders evaluated.

There are 10 sample orders. One fails validation and is rejected before any warehouse is priced, so **nine** is the denominator for every result in this document. It is not a hit rate among fulfillable orders, because a valid order can still come back as unroutable.

### Cost measures

- Total shipping cost for the batch.
- Extra shipping cost above the cheapest warehouse that had enough stock, recorded per order with a reason.
- Cost per additional promise kept, when comparing one strategy against another.

### Correctness checks

The prototype should be treated as wrong, and not taken further, if any of these happen:

- Inventory does not balance. What a shelf started with must equal what shipped from it plus what is left.
- A warehouse is selected that did not have enough stock for the full order.
- The same unit is allocated twice.
- An order ships late even though, at the moment it was routed, a warehouse had enough stock and could have delivered it on time.
- A decision cannot be reconstructed from its saved record.

I did not set percentage thresholds for a production rollout. Those would need to be set against a real network's current routing performance, real merchant economics, and real delivery data, none of which this prototype has.

## 6. Functional requirements

### Inputs

Three record types. All sample data is synthetic.

| Record | Fields |
| --- | --- |
| Order | order ID, destination, SKU, quantity, order date, promised delivery date |
| Fulfillment center | FC ID, location, available inventory by SKU |
| Shipping lane | FC, destination region, shipping cost, transit time |

Cost and transit time belong to the **lane**, not the warehouse, because shipping from Dallas to Los Angeles is not the same as shipping from Dallas to New York.

Shipping prices are stored as whole cents rather than decimals. The engine compares prices that are sometimes only 40 cents apart, and decimal rounding errors could genuinely flip a decision. Whole numbers cannot drift. The display still shows $8.00.

### Delivery date calculation

- An order ships on the next working day.
- Transit time is counted in working days.
- Weekends are skipped.
- Public holidays are not modeled. This is a known shortcut.

Worked through: an order placed Friday August 14 ships Monday August 17, and with 3 working days of transit it arrives Thursday August 20.

### Input validation

| What is wrong | Reason recorded |
| --- | --- |
| Quantity is zero or negative | `INVALID_QUANTITY` |
| The product does not exist | `UNKNOWN_SKU` |
| The destination is not in the region map | `UNKNOWN_DESTINATION` |
| The promised date is before the order date | `INVALID_PROMISE_DATE` |
| The date is not a real date, such as 2026-02-30 | `INVALID_DATE_FORMAT` |

Only the first problem found is reported.

One judgment call here: a promise for the same day as the order date is **valid input**. It may well be impossible to meet, but that makes it a difficult promise, not bad data. The engine should route it as well as it can and report honestly that it will be late. Rejecting it would hide a real problem behind a validation error.

Unknown destinations are rejected rather than guessed at. Guessing the region would mean inventing a transit time and promising a date the engine cannot back up.

## 7. Routing rules

The router works through these in a fixed order. It is a sequence of rules, not a weighted score.

| # | Rule |
| --- | --- |
| 1 | Reject invalid order data |
| 2 | Remove warehouses without enough stock for the full order |
| 3 | Calculate shipping cost and expected delivery date for every remaining warehouse |
| 4 | If one or more can meet the promise, keep only the on-time options |
| 5 | If none can meet the promise, keep only the options tied for the fewest days late |
| 6 | Find the cheapest option in what is left |
| 7 | Build a $0.50 price band from that cheapest option |
| 8 | Inside the band, prefer more stock remaining, then lower cost, then shorter transit, then FC ID |
| 9 | Allocate stock only after a warehouse is selected |
| 10 | Save the selection, the alternatives, the expected delivery, the cost, and the reason |

FC ID is the last tie-break so identical options always resolve the same way and the same inputs always produce the same output.

### The $0.50 rule

Once the shortlist is narrowed to either the on-time options or the options tied for the fewest days late, the router finds the cheapest one and lets any option within 50 cents join it in a price band. Inside that band it prefers the warehouse that leaves more stock behind.

The reasoning is that a very small saving is not worth taking a warehouse down to its last units, because those units might be needed by the next order near that location.

Because the band is only built **after** the lateness filter, inventory preservation can break a close tie on cost, but it can never accept an extra day of lateness.

**$0.50 is a prototype assumption.** It is not based on real economics and nothing in this data says 50 cents is right. I picked it because it makes the behavior visible in a small demo and it is a single named value in one place, so it is easy to change. A real threshold would depend on demand for that product at that warehouse, the cost of running out there, the orders expected next, the order value, and merchant policy.

### Batch sequencing

Before routing anything, the engine works out how much room each order has:

> **Delivery flexibility = promised delivery date − the earliest delivery date the network can currently achieve**

The code calls this *routing slack*. Then:

1. Push any order that no warehouse can fill to the end of the queue, so it cannot disturb orders that can actually ship.
2. Process the tightest orders first, meaning the smallest delivery flexibility.
3. If two orders tie, use the order ID.

The subtraction matters. Flexibility is measured against the soonest date the network could actually manage, not against today. A five-day promise sounds relaxed, but if the only warehouse holding the item is five days away, there is no room in it at all.

## 8. Acceptance criteria

The prototype is working if all of these hold, and all of them are covered by the test suite:

- An order is routed to a warehouse that has enough stock for the whole order, every time.
- A cheaper option that arrives late loses to a more expensive option that arrives on time.
- When two options both arrive on time, the cheaper one wins.
- When every option is late, the least-late one wins.
- Stock is reduced after a selection, and later orders in the batch see the reduced amount.
- Inventory balances across the whole batch.
- The same orders and the same starting stock always produce the same decisions.
- Every order ends in exactly one of four statuses: `ROUTED`, `ROUTED_LATE`, `UNROUTABLE`, `INVALID_ORDER`.
- An unroutable order is marked `OUT_OF_STOCK` when the network has nothing, or `SPLIT_REQUIRED` when the network holds enough in total but no single warehouse does.
- Every decision stores the alternatives considered, each with a reason it lost.
- Every decision where we did not take the cheapest option stores the extra amount and why it was spent.

## 9. Important edge cases

**A promise nobody could ever have kept.** The order is fine and the stock exists, but no route reaches the shopper by that date, and that was true before the batch started. This is a promise-setting problem, not a routing failure. The engine still routes the order as well as it can and reports it as late.

**A promise that an earlier order made impossible.** The order had a workable path at the start of the batch, then an earlier order consumed the stock it needed. This points at inventory placement or batch sequencing. Telling this apart from the case above is the single most important distinction in the engine, and getting it wrong is covered in section 11.

**The routing rule itself chose badly.** An on-time option existed when the order was routed, and the policy picked a late one anyway. This never happens under promise-first, which drops late options whenever an on-time one exists. It happens once under cost-first, and the report names it rather than burying it.

**The order can never be routed.** No single warehouse holds enough. The promised date never came into it. This is a stock problem that no routing rule can solve. The engine separates two versions: nothing available anywhere, versus enough units in the network but spread across warehouses. The second one detects that a split shipment would be needed. The prototype does not build split shipments.

**Two orders fighting over the last unit.** Covered in section 7 and section 11. This is the case that made me change the design.

**An order the engine should not reject.** A same-day promise. Difficult, but valid input.

## 10. Validation results

**Updated after building.**

After building it, I ran the same 10 synthetic orders under three strategies to see how much the choices actually mattered. Every run starts from a fresh copy of the original inventory, otherwise the comparison would be meaningless while still looking perfectly sensible.

| Strategy | Promises kept, of 9 valid orders | Total days late | Shipping cost |
| --- | ---: | ---: | ---: |
| Promise-first, tightest orders first | 6 | 1 | $47.70 |
| Promise-first, file order | 5 | 2 | $47.70 |
| Cost-first, tightest orders first | 5 | 4 | $39.30 |

**Sequencing only.** The first two rows use the same routing objective. Only the processing order changes. Tightest-first keeps one more promise for exactly the same $47.70. There is no tradeoff here — the bill is identical to the penny, so file order was simply worse for no saving.

**Objective only.** The first and third rows use the same tightest-first sequence, so the difference comes from the objective. Promise-first keeps one more promise and cuts total lateness from four days to one, but spends $8.40 more. Because the difference is a single promise, the extra spend and the cost per additional promise kept are the same number. That is one finding, not two.

The engine does not decide whether $8.40 is good or bad. It makes the tradeoff visible so a merchant can answer it.

**Where the $8.40 comes from.** Not all of it protects promises, which is why the engine records a reason alongside every extra dollar:

| Order | Extra spend | Reason | Chose | Over |
| --- | ---: | --- | --- | --- |
| ORD-1004 | $5.00 | `REDUCE_LATENESS` | Los Angeles | Philadelphia |
| ORD-1001 | $3.00 | `PROTECT_PROMISE` | Dallas | Philadelphia |
| ORD-1002 | $0.40 | `PRESERVE_INVENTORY` | Dallas | Chicago |

The biggest single piece was spent on an order that was going to be late no matter what, to make it one day late instead of three.

**Test coverage.** 64 tests across 22 classes, all passing. They cover the correctness checks in section 5 plus the behaviors that matter most: a cheaper late option loses to an on-time one, cost decides between on-time options, a warehouse without enough stock is never selected, the least-late option wins when everything is late, the two unroutable reasons are returned in the right cases, and inventory is conserved across a batch. Most of the suite exists to lock in behavior that had already broken once.

**A limit worth stating plainly.** I designed this sample data to contain routing conflicts on purpose, because without them every strategy picks the same warehouse and there is nothing to compare. These results show how the prototype behaves on a batch built to stress it. They are not evidence that promise-first would win on a real fulfillment network, where the rates, transit times, and inventory positions are nothing like these.

## 11. What changed after testing

**Updated after building.**

The most useful thing I learned is that *"was this promise ever possible?"* has to be answered at two different moments, not one.

**What I originally built.** My first version measured each order's delivery flexibility once, at the moment the order was routed. If that number came back negative, the engine reported the promise as having been impossible from the start.

**What the test showed.** One order in the sample batch starts with exactly 0 days of flexibility. Tight, but possible. Routed in file order, an earlier order takes the warehouse unit it needed, and by the time its turn comes the number is −1.

The first version read that −1 and reported the promise as never achievable. So the engine was telling the merchant to go fix their checkout, for a date the engine itself had made impossible.

Nothing about the output looked broken. The report was internally consistent and every number added up. That is what made it worth fixing properly rather than patching.

**What I changed.** I now measure flexibility twice and store both numbers on every order: `slack_days_at_batch_start` is how much room the order had before any routing happened, and `slack_days_when_routed` is how much room was left when its turn came.

Together they separate *never achievable* from *our earlier decisions made it unachievable*. Those two need completely different fixes — one is a promise-setting problem, the other is an inventory placement or sequencing problem. An engine that cannot tell them apart will confidently send people to the wrong problem, and will report its own mistakes as somebody else's.

**The general lesson:** define the problem, build the rule, run it on working sample data, notice a result that does not sit right, work out why, then change the design rather than the symptom.

The second thing testing changed was the batch sequencing itself. I had assumed processing order was a detail. Comparing the two runs showed it was worth a delivery promise at zero cost, which is the cheapest improvement in the whole project.

## 12. Remaining limitations

### Deliberate simplifications in the prototype

- The $0.50 inventory-preservation threshold is invented.
- Transit times are fixed and treated as certain. A 3-day lane always takes exactly 3 days.
- One shipping service per warehouse-to-region route. No ground versus overnight choice.
- Three broad regions instead of real postal zones. Some cities land in a region that does not suit them.
- Recorded inventory is assumed fully available. Nothing is reserved, inbound, damaged, or held as safety stock.
- One product per order.
- No public holidays, so the engine will happily promise a delivery on Christmas Day.
- Priority is calculated once at the start of the batch, not recalculated after each decision.
- Orders are routed one at a time. The engine does not search every possible order-to-warehouse combination, so a better overall answer may exist that it will not find.

### Not built, and important before production

**Inventory and allocation:** safe concurrent reservations, batch-wide optimal assignment, incoming inventory, real safety stock logic.

**Orders and products:** split shipments, multi-product routing, warehouse product-capability restrictions, kits and lots and expiry rules.

**Shipping and operations:** real carrier pricing, warehouse capacity and cutoff times, public holidays and carrier disruptions, international shipping.

**Order lifecycle:** rerouting after a warehouse fails to pick, address-change rerouting, cancellation release, protection against duplicate routing requests.

### What I would build next, in order

1. **Safe inventory reservations.** Two orders arriving at the same moment must not both be able to take the same last unit. This is first because it is a correctness problem, not an optimization, and everything below depends on being right about what stock exists.
2. **Split shipments and multi-product orders.** Harder than it looks: two packages means two costs and two arrival dates, the promise is only kept if both land in time, and "cheapest" becomes a comparison of combinations.
3. **Probability of hitting the promised date,** from how that lane and carrier have actually performed, instead of one assumed transit time.
4. **Carrier and service-level selection,** so upgrading the service becomes an option alongside changing warehouse.
5. **Warehouse capacity, cutoff times, and holiday calendars,** all of which currently make the engine more optimistic than it should be.
6. **Merchant-configurable policy,** so the acceptable extra cost to protect a promise is the merchant's number.
7. **Decision logging and monitoring,** comparing what the router predicted against what actually happened. Every decision is already recorded in full, so this is mostly a matter of keeping them and measuring.
8. **Feeding routing feasibility back into checkout.** The promised date is currently an input the engine can only react to. One order in the sample batch had a date no warehouse was ever going to hit, and the engine can see that — but only after the sale, when the best it can do is pay $5.00 extra to arrive one day late instead of three.

That last one is the real product insight. Good routing helps keep delivery promises. Good promise-setting prevents impossible promises from being made in the first place. This prototype only builds the routing side.
