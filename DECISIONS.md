# Decisions

The [README](README.md) explains what the engine does. [PRODUCT_SPEC.md](PRODUCT_SPEC.md) covers the requirements and the rules.

This file is about judgment: what I was actually trying to solve, what else I could have done, why I picked what I picked, what happened when I tested it, and what I would change.

Each decision below follows the same five questions.

---

## 1. Protect the delivery promise before optimizing cost

**The problem.** Two things cost the merchant money: the shipping bill, and the fallout from a late delivery. Only one of them arrives as an invoice. If I let the engine optimize whatever is easiest to measure, it will optimize the shipping bill and quietly spend the other one.

**Alternatives I considered.**

- *Cost-first.* Always take the cheapest warehouse with stock. Simple, obviously defensible to a finance team, and it ignores the date the shopper was shown.
- *A weighted score.* Give cost, speed, and stock each a weight and take the highest total. This is the conventional answer.
- *Promise-first.* Treat the promised date as a hard filter, then optimize cost inside what survives.

**Why I chose promise-first.** The promised date is the only part of this the shopper ever sees. Shipping cost shows up on one invoice on one day. The cost of breaking a promise is spread across support contacts, refunds and goodwill credits, the merchant's own view of the platform, their standing on any marketplace they sell through, and whether that shopper comes back. Some of that is measurable and some is not, but none of it lands on the shipping invoice.

I rejected the weighted score for a different reason, covered in decision 4.

**What happened when I tested it.** I built cost-first as a real second mode and ran both. Cost-first came out $8.40 cheaper across the batch, kept one promise fewer, and left customers waiting 4 days in total instead of 1. So the tradeoff is real and I can put a number on it rather than asserting that promise-first is better.

It is worth being precise about that $8.40, because it is tempting to call it the price of protecting promises and it is not. It breaks down as $5.00 to be less late on a promise that was already impossible, $3.00 to actually protect a promise, and $0.40 to avoid emptying a shelf. Only the middle one bought a delivery date.

**What I would improve.** This should be a merchant setting, not my opinion baked into the code. A merchant selling low-margin commodity goods might reasonably accept more lateness for a cheaper bill. A merchant selling dated gifts would not. The honest limit of this prototype is that it measures the shipping difference exactly and says nothing about how customers actually react to a late delivery, because it has no data on that. The acceptable premium should come from merchant economics and measured delivery outcomes.

---

## 2. Process the tightest orders first

**The problem.** When several orders need the same scarce stock, the one processed first changes what is possible for the rest. Each decision can be individually correct and the batch result still worse than it needed to be.

The clearest case in the sample batch: two orders both need the one rare unit sitting in Chicago. ORD-1007 is going to Chicago, promised August 20. ORD-1008 is going to Minneapolis, promised August 18, and Chicago is the only warehouse that can hit that date, at $4.80 on a one-day transit. The next best for Minneapolis is Dallas at $5.20, arriving August 19.

ORD-1007 comes first in the file, so in file order it takes the Chicago unit — even though its own promise is two days later and Dallas would have suited it fine. ORD-1008 then gets Dallas and misses a promise it could have kept.

Nothing went wrong in that first decision. ORD-1007 took the cheapest warehouse that met its promise, exactly as the rules say.

**Alternatives I considered.**

- *File order.* Whatever sequence the orders arrive in. Simple and defensible as first-come-first-served.
- *Promised date, soonest first.* The obvious proxy for urgency.
- *Delivery flexibility, tightest first.* Promised date minus the soonest date the network could actually manage.
- *Batch-wide optimization.* Search every order-to-warehouse combination and pick the best overall assignment.

**Why I chose tightest-first on flexibility.** Sorting by promised date alone would have been wrong here, and that is the interesting part. A five-day promise sounds relaxed, but if the only warehouse holding the item is five days away there is no room in it at all. Measuring against what the network can actually achieve, rather than against today, is what makes the sort work.

I skipped batch-wide optimization because it is a much bigger piece of work and it would have buried the thing I was trying to show. It is listed honestly as a gap.

One extra rule: any order that no warehouse can fill goes to the back of the queue, so it cannot disturb orders that can actually ship.

**What happened when I tested it.** Tightest-first kept 6 promises against file order's 5, for an identical $47.70. The pair of competing orders costs $10.00 either way — the two warehouses simply swap, and ORD-1007 still arrives a day early from Dallas.

That result is unusual and worth naming. When two runs follow the same objective and differ only in sequencing, a difference like this is not a tradeoff. It is a decision that was being got wrong for free.

**What I would improve.** The sort runs once, at the start, using the inventory position at that moment. Routing the first order changes the flexibility of everything after it, so a stricter version would recalculate after each decision. I skipped that on the assumption it changes little at this scale — which I have not proved, and which is exactly the kind of assumption that turns out to matter on a bigger batch.

---

## 3. Protect scarce stock when two options cost nearly the same

**The problem.** Sometimes the cheapest warehouse is cheaper by a few cents and taking it would leave that warehouse on its last unit. That saving is real but tiny, and the unit might be exactly what the next order near that location needs.

**Alternatives I considered.**

- *Always take the cheapest.* Clean and easy to explain, and it empties thin shelves for pennies.
- *Add stock level to a weighted score.* Rejected along with scoring generally.
- *A price band.* Treat options within a small range as effectively tied on cost, and break the tie on remaining stock.

**Why I chose the price band.** It keeps cost as the primary rule and only lets stock matter when the cost difference is genuinely trivial. Crucially, the band is built **after** the lateness filter has run. So the band can break a close tie on price, but it can never accept an extra day of lateness to preserve stock. That ordering is the whole safety of the rule.

Inside the band the preference order is remaining stock, then cost, then transit time, then warehouse ID. Cost comes back in deliberately, so that when two options leave the same stock behind we still take the cheaper one. Warehouse ID is last so identical options always resolve the same way.

**What happened when I tested it.** It fires once in the batch, on ORD-1002. Chicago costs $4.80, arrives a day earlier, and would drop to 1 unit left. Dallas costs $5.20 and leaves 3. Dallas wins. Chicago is cheaper *and* faster and still loses, and the record says plainly that the 40 cents was spent to avoid emptying a shelf.

I also noted what the batch does **not** exercise: ORD-1004 is routed late, but only one warehouse is in its least-late shortlist, so the band never gets to change a decision on the late path. That path is covered by tests rather than by the sample data.

**What I would improve.** The 50-cent threshold is arbitrary and I want to be blunt about that. I picked it because it makes the behavior visible in a small demo and because it is a single named value in one place, so it is easy to change. Nothing in this data says 50 cents is right. A real threshold should come from something meaningful: demand for that product at that warehouse, the cost of running out there, the orders expected next, the order value, or a limit the merchant sets. As it stands the rule demonstrates the shape of the tradeoff and nothing more.

---

## 4. Ordered rules instead of a weighted score

**The problem.** The engine has to combine four things — stock, cost, transit time, and the promised date — into one choice. There is a conventional way to do that: give each one a weight, add up the points, take the highest.

**Alternatives I considered.** A weighted score, or a fixed sequence of rules where each one filters the options for the next.

**Why I chose ordered rules.** A score is hard to argue with. If a warehouse loses by 0.3 points, you cannot tell anyone why it lost, and you cannot tell whether the weights are wrong or the data just fell that way. With ordered rules the answer is always a specific sentence: "it arrived after the promised date," or "it cost 80 cents more."

That matters more than usual here because the whole point of the prototype is showing its reasoning. A decision that commits stock and spends money should be arguable by the person whose money it is.

**What happened when I tested it.** The rules held up, and the unexpected benefit was in the tests. Each rule is separately testable, so when behavior broke I could find which rule did it. 64 tests across 22 classes, and most of them exist to lock in behavior that had already broken once.

**What I would improve.** I am not claiming scoring is wrong in general. A production system with service-level choices, split shipments, and real probability of on-time arrival may well need actual optimization, and a solver could beat hand-ordered rules on results. For a prototype whose job is explaining itself, ordered rules fit better. I would revisit this the moment split shipments arrive, because "cheapest" then becomes a comparison of combinations and hand-ordered rules get awkward fast.

---

## 5. Fixed rules rather than an AI model for the decision itself

**The problem.** I built this quickly with AI assistance, so the obvious question is whether an AI model should make the routing call too.

**Alternatives I considered.** A language model choosing the warehouse and explaining itself; a model as a tie-breaker on close calls; or no model in the decision path at all.

**Why I chose no model in the decision path.** This decision commits a unit of someone's stock, spends their money, and sets a date a shopper is waiting on. For that I want two properties. The same inputs should always produce the same answer, so the behavior can be tested and trusted. And the reason should be inspectable afterwards — a stored record a person can read, not an explanation generated after the fact that may or may not reflect what actually happened.

A model that is usually right and occasionally different is a bad fit for a decision you have to be able to audit.

**What happened when I tested it.** The determinism is tested directly: the same orders and starting stock produce identical decisions every run. That test is cheap to write precisely because there is no model in the path, and it caught real problems while I was changing the sequencing logic.

**What I would improve.** None of this rules AI out of the wider product. It could help a merchant understand why an order routed the way it did, investigate a run of exceptions, spot patterns in what keeps going late, or turn a plain-English policy into settings the router understands. Those all sit around the decision. The decision itself stays a fixed rule.

I would also say plainly that AI did a lot of the building here. It wrote code, reviewed it, and helped generate the test suite. That is how I got a working prototype with 64 tests in about half a day. The split I care about is between building with AI and deciding with AI.

---

## 6. Record the reason for every decision, including the ones we did not take

**The problem.** "Dallas was chosen" is not much use on its own. Nobody can agree or disagree with it. And if a merchant asks why their shipping bill looks the way it does, an answer reconstructed after the fact is not evidence.

**Alternatives I considered.** Store the winner only; store the winner plus a text explanation; or store the full comparison the router actually used.

**Why I chose the full comparison.** The policy contains a business tradeoff, so anyone reading the record should see both halves of it. For every order the engine stores the warehouse selected and what it cost, the cheapest warehouse that could have filled the order and what that would have cost, the difference between them, a short code for why the more expensive option won, and every warehouse considered with its cost, arrival date, whether it hit the promise, and why it lost.

"Dallas was chosen, Philadelphia would have saved $3.00, and the $3.00 bought a delivery date" is enough to agree or disagree with the policy. That is the standard I was aiming at.

**What happened when I tested it.** Two things came out of this that I did not plan.

First, every table in the report is built from these records and nothing else. No number is worked out at printing time. That turned out to be a real constraint worth enforcing — the report cannot drift away from the decision it claims to describe, and there are tests that check the printed summary never overstates the result.

Second, the report prints one complete record raw at the end, every field, nothing removed. A tidy table is exactly what somebody would produce if they were making the numbers up. Printing one record in full means a reader can check the pretty version against the stored one.

**What I would improve.** Right now the records exist for the length of one run. In production they should be stored and compared against what actually happened, so the rules can be corrected against reality rather than against my assumptions. The data is already in the right shape for it, so this is mostly a matter of keeping it.

---

## 7. Measure the extra cost of the more expensive route, and say what it bought

**The problem.** Promise-first spends more than the minimum sometimes. If I hide that in a total, I am doing exactly what I criticized cost-first for — optimizing the number that is easy to look at and ignoring the other one.

**Alternatives I considered.** Report the total shipping bill only; report the total extra spend as one figure; or record the extra spend per order with a reason attached.

**Why I chose per-order with a reason.** Because I found that not every premium protects a promise, and lumping them together would have been misleading in a way that flattered the policy. So the engine records one of four reasons on every order: nothing extra spent, the extra kept the promised date, the extra reduced how late an already-late order would be, or the extra avoided taking a warehouse down to its last units.

**What happened when I tested it.** This is the finding I would lead with if someone asked what measuring actually changed. Across the batch the premium is $8.40, and it is tempting to describe that as the price of protecting delivery promises. The per-order breakdown says otherwise:

| Order | Extra | Reason |
| --- | ---: | --- |
| ORD-1004 | $5.00 | reduce lateness on an already-impossible promise |
| ORD-1001 | $3.00 | protect the promise |
| ORD-1002 | $0.40 | preserve inventory |

The largest single piece — $5.00, more than half the total — was spent on an order that was going to be late no matter what, to make it one day late instead of three. Only $3.00 actually bought a delivery date.

If I had only tracked the total I would have reported a true number with a false story attached to it.

**What I would improve.** Whether $5.00 is worth two fewer days of lateness on a promise that was already broken is a genuinely open question, and the engine currently assumes it is. That should be a merchant setting too. There is a reasonable argument that once a promise is broken, the cheapest route is the right route.

---

## 8. Tell an impossible promise apart from one we made impossible

**The problem.** "Shipped late" does not tell anyone what to fix. A late delivery can mean the checkout promised a date the network was never going to hit, or it can mean the routing engine spent the stock that would have made it. Those need completely different fixes, and a single late count hides the difference.

So the engine separates four outcomes, each with a real example from the sample batch:

1. **The promise was impossible from the start.** The network could fill the order, but no route could reach the shopper by that date when the batch began. ORD-1004 ships a day late. *Points at: promise-setting.*
2. **Earlier routing decisions made it impossible.** The promise could have been kept at the start of the batch, but an earlier order used the stock this one needed. ORD-1009 ends up with nothing to ship. *Points at: inventory placement or sequencing.*
3. **The routing policy chose badly.** An on-time option existed when the order was routed and the rules took a late one anyway. This never happens under promise-first. It happens once under cost-first, and the report names it rather than burying it. *Points at: the routing policy.*
4. **The order could never be routed at all.** No single warehouse held enough. ORD-1005 needs 25 units when no warehouse has that many, so the promised date never came into it. *Points at: inventory availability.*

Status and reason are stored as two separate fields. The status says what happened; the reason says why. Even an unroutable order gets a reason, because "nothing here could ever fill this" and "we could have filled this at the start of the batch" are different problems.

**Alternatives I considered.** One late flag; a late flag plus a free-text note; or distinct outcomes derived from stored facts.

**Why I chose distinct outcomes.** Because the four cases route to four different teams. An engine that reports them as one thing is an engine that sends people to the wrong problem.

**What happened when I tested it — and what I got wrong.** This is the part worth reading, because my first version of it was broken and the output looked fine.

My first version measured each order's delivery flexibility once, at the moment it was routed. A negative number meant the promise had been impossible from the start.

The batch proved that wrong. ORD-1008 starts with exactly 0 days of flexibility — tight, but possible. Routed in file order, an earlier order takes the warehouse it needed, and by the time we reach it the number is −1. My first version read that −1 and reported the promise as never achievable. So the engine was telling the merchant to go fix their checkout, for a date the engine itself had made impossible.

Nothing about the output looked broken. The report was internally consistent and every number added up. That is what made it worth fixing the design rather than patching the symptom.

The fix was to measure flexibility twice and store both numbers on every order: `slack_days_at_batch_start` for how much room the order had before any routing happened, and `slack_days_when_routed` for how much was left when its turn came. Together they separate outcome 1 from outcome 2. With one number the engine cannot tell them apart, and it will report its own mistakes as somebody else's.

That sequence is the general lesson, and it is the one I would point at if someone asked how I work: **define the problem, build it, test it, find the issue, improve the design.** The build is the cheap part. Noticing that a correct-looking report was quietly wrong is the part that took judgment.

**What I would improve.** The two-clock approach tells me *that* an earlier order consumed the stock. It does not tell me *which* order did it. Storing that link would turn the diagnosis into something actionable — not just "an earlier decision cost you this promise," but "this specific order took the unit, and here is what it would have cost to route it elsewhere."

---

## 9. Where I knowingly took shortcuts

These are listed in full in [PRODUCT_SPEC.md](PRODUCT_SPEC.md#12-remaining-limitations). Two are worth a note here because they involved a real judgment call rather than just running out of time.

**No split shipments.** An order fails if no single warehouse can fill it, even when the network holds enough in total. In the sample batch an order for 25 units fails while we hold 34 across four warehouses, which looks silly and is. I left it out because splitting is more work than it looks — two packages means two costs and two arrival dates, the promise is only kept if both land in time, and "cheapest" becomes a comparison of combinations rather than a sort. It is the largest functional gap here.

But I would not build it first. Inventory reservations come first, because there is no point adding routing options while two orders can still take the same unit. Correctness before optimization.

**Simplified inventory.** A single count of available units per product per warehouse. Nothing is reserved, inbound, damaged, or held as safety stock. This means an empty shelf and "we have units but they are all committed" look identical to the engine, and operationally they are not remotely the same thing. This is the assumption most likely to make the prototype look better than it is.
