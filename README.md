# Intelligent Fulfillment Routing Engine

A working prototype that decides which warehouse should ship each customer order.

## Overview

When a customer places an order, a company with several warehouses has to pick one to ship it from. This engine takes an order, checks which warehouses hold enough stock, works out what each one would cost and when each one would arrive, and then picks one.

It also records why it picked that warehouse and why it rejected the others. Every number in its report can be traced back to the decision that produced it.

The data is synthetic. The routing logic is real, and you can run it.

## The Problem

Say you sell a product and you keep stock in four warehouses. An order comes in from Los Angeles. Four warehouses could fill it, and they all give you a different answer.

The cheapest one is in Philadelphia. It costs $5.00 to ship, but it takes four working days and the customer was promised the package by Thursday. It would arrive Friday.

The fastest one is in Los Angeles itself. It arrives a day early, and it costs $10.00.

So the cheapest warehouse breaks the promise, and the fastest one charges you double for a day the customer never asked for. Neither is the right answer. That is the problem in one order.

Now multiply it by a batch of orders that are competing for the same limited stock, and the problem gets harder. Filling one order can take the last unit that a later order needed. The second order then misses a delivery date it could have made, and nothing about the first decision looked wrong at the time.

## What I Built

A command-line prototype in Python that does the following. I have verified each of these by running it:

- **Checks inventory across warehouses.** A warehouse only stays in the running if it can fill the whole order on its own.
- **Calculates shipping cost** for each warehouse, based on the destination rather than just the origin. Dallas to Los Angeles is a different price from Dallas to New York.
- **Estimates the delivery date.** Packages leave the next working day and transit is counted in working days, so weekends are skipped.
- **Prioritizes the delivery promise.** If any warehouse can arrive by the promised date, only those warehouses stay in the running.
- **Compares the alternatives and records them.** Every warehouse that lost is stored with its cost, its arrival date, and a short reason it lost.
- **Processes a batch of orders** and reduces stock as it goes, so later orders see the inventory that earlier orders left behind.
- **Handles orders competing for scarce stock** by working out which orders have the least room to spare and routing those first.
- **Explains every decision**, including what the cheapest option would have cost and why the engine did not take it.

It also runs the same batch under three different rule sets so the rules can be compared against each other rather than just asserted.

## Key Product Decisions

The reasoning behind each of these is in [DECISIONS.md](DECISIONS.md). The short version:

**The delivery promise comes first, cost comes second.** Shipping cost is one number on one invoice. The cost of a late delivery is spread across support contacts, refunds, and whether that shopper buys again. If the easy number wins by default, the hard one gets ignored.

**Cost only decides between options that already work.** The engine finds the warehouses that can hit the promised date first, then takes the cheapest of those. Cost still matters. It just does not get to override the promise.

**The routing decision uses fixed rules, not an AI model.** This decision commits someone's stock and spends their money, so it has to give the same answer every time and a person has to be able to check it. I used AI tools to build and test the prototype faster. No AI model picks the warehouse.

**The order you process a batch in changes the result.** I found this while testing. Routing orders in the sequence they arrived cost me a delivery promise for no saving at all. Handling the tightest orders first fixed it.

**Every decision has to be explainable from what was saved.** Not an explanation written afterwards, but the actual facts the engine used, stored at the time.

## Results

I ran the same 10 synthetic orders three times, changing one rule at a time. Each run starts from a fresh copy of the original stock, otherwise the comparison would be meaningless.

One of the 10 orders is rejected as invalid before any warehouse is priced, so 9 orders are actually evaluated.

| Strategy | Promises kept | Total days late | Shipping cost |
| --- | ---: | ---: | ---: |
| A. Promise first, tightest orders first | 6 of 9 | 1 | $47.70 |
| B. Promise first, file order | 5 of 9 | 2 | $47.70 |
| C. Cost first, tightest orders first | 5 of 9 | 4 | $39.30 |

**A against B shows a free improvement.** Both runs follow the same goal. The only difference is the order the orders were worked through. Handling the tightest promises first kept one extra promise for exactly the same $47.70. There was no tradeoff to weigh here — the other way round was simply worse for no saving.

**A against C shows the real tradeoff.** Ignoring the promised date and always taking the cheapest warehouse saved $8.40. It also lost a delivery promise and left customers waiting 4 days in total instead of 1.

Whether $8.40 is a fair price for one delivery promise depends on a merchant's margins and their customers. The engine does not answer that. It puts the number on the page so somebody can.

### Where the $8.40 goes

The $8.40 is not all spent on protecting promises. It comes from three orders and three different reasons:

| Order | Extra spend | Why |
| --- | ---: | --- |
| ORD-1004 | $5.00 | To arrive one day late instead of three, on a promise no warehouse could ever have met |
| ORD-1001 | $3.00 | To keep a promised delivery date |
| ORD-1002 | $0.40 | To avoid taking a warehouse down to its last unit |

Only the $3.00 was spent to keep a promise. The largest piece, $5.00, was spent reducing lateness on an order that was already going to be late whatever we did.

**These numbers describe synthetic data, not real customers.** I designed the sample batch to contain genuine conflicts, because without them every strategy picks the same warehouse and there is nothing to compare. The results show how the prototype behaves on a batch built to stress it. They are not evidence about how any real fulfillment network would perform.

## How It Works

For a single order:

1. **Check the order makes sense.** A real quantity, a product we sell, a city we have rates for, and a promised date that is not before the order date. If any check fails, the order is rejected with a reason and no warehouse is priced.
2. **Drop the warehouses that cannot fill it.** The prototype does not split orders, so a warehouse with 1 unit is no help for an order of 2.
3. **Price the rest.** Look up the cost and transit time to that part of the country, then work out what date the package would land.
4. **If anything can arrive on time, keep only those options.** The late ones are out.
5. **If nothing can arrive on time, keep only the ones tied for the fewest days late.** We cannot save the promise, so we limit the damage.
6. **Take the cheapest of what is left.**
7. **Check for a near-tie on price.** If another option is within 50 cents, prefer the one that leaves more stock behind. A few cents saved is not worth emptying a shelf that the next order might need. This can break a close tie on price, but it can never accept an extra day of lateness.
8. **Subtract the stock** from the warehouse we picked, so the next order sees the truth.

For a batch, there is one extra step before any of that. The engine works out how much room each order has before it would miss its promised date, and handles the tightest orders first. That room is the promised date minus the soonest date the network could actually manage — not the promised date minus today. A five-day promise sounds relaxed, but if the only warehouse holding the item is five days away, there is no room in it at all.

### Worked example

ORD-1001. One unit to Los Angeles, promised August 20.

| Warehouse | Cost | Expected delivery | Result |
| --- | ---: | --- | --- |
| Philadelphia | $5.00 | Aug 21 | 1 day late |
| Dallas | $8.00 | Aug 20 | On time |
| Chicago | $8.80 | Aug 20 | On time |
| Los Angeles | $10.00 | Aug 19 | On time |

Dallas wins. It is the cheapest warehouse that still arrives by August 20. Philadelphia is cheaper but misses the date, so it is dropped at step 4. Los Angeles arrives a day early, which earns no credit, because the promise was a specific date rather than the fastest possible delivery.

The engine spends $3.00 more than the cheapest route to keep the promise, and it records that $3.00 against the order rather than hiding it in the total.

## Technology

Python 3 and the standard library. Nothing to install, no dependencies, no configuration.

I used AI tools to help build, review, and test this prototype faster than I could have alone. The routing decision itself never calls an AI model. It is fixed rules, written down and testable, and the same inputs always produce the same answer.

## How to Run

Run the full report:

```bash
python3 main.py
```

That prints about 430 lines: what is in the data, all ten orders explained one at a time, the batch totals, the three strategies side by side, and one decision record exactly as the engine stored it.

Run the tests:

```bash
python3 -m unittest discover tests
```

64 tests across 22 classes. They cover warehouse selection, inventory updates, the failure cases, batch sequencing, and the date calculations.

## Limitations and Next Steps

This is a prototype built to demonstrate a routing decision, not a production system.

**What it does not handle:** split shipments or multi-product orders, so an order fails if no single warehouse can fill it even when the network holds enough in total. Transit times are treated as fixed and certain. There is one shipping service per route, no public holidays, no warehouse capacity or cutoff times, and no safe reservations, so two orders arriving at the same moment could both take the last unit.

**What I would do first for production,** in order:

1. **Inventory reservations.** Two orders arriving at the same moment must not both be able to take the same last unit. This is a correctness problem, and everything else depends on being right about what stock exists.
2. **Split shipments and multi-product orders.** The largest functional gap.
3. **Probability of hitting the promised date,** based on how that route and carrier have actually performed, instead of one assumed transit time.
4. **Carrier and service-level choice,** since upgrading the service is often a cheaper way to save a promise than shipping from a further warehouse.
5. **Merchant-configurable policy,** so the limit on acceptable extra cost is the merchant's number rather than my assumption.

## Project Files

| File | What's in it |
| --- | --- |
| [README.md](README.md) | This page. What the project does, the results, and how to run it. |
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | How I approached the problem: the requirements, the routing rules, and what changed after testing. |
| [DECISIONS.md](DECISIONS.md) | The eight decisions that shaped the engine, the alternatives I compared, and what testing showed. |
| [main.py](main.py) | The file you run. It prints the report and makes no decisions of its own. |
| [router.py](router.py) | The engine. All the routing logic lives here, and it never prints anything. |
| [data/](data/) | The synthetic warehouses, shipping rates, and sample orders. Each file explains its own numbers. |
| [tests/](tests/) | The 64 tests covering warehouse selection, inventory, dates, and batch sequencing. |

The split between `main.py` and `router.py` is deliberate. One decides and never prints; the other prints and never decides. That way the report can't drift away from the decision it claims to describe.
