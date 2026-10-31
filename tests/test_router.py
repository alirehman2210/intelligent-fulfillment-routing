"""
WHAT THIS FILE IS FOR
    It checks that the routing engine actually does what the README says it does.
    Each test sets up one small situation with a known right answer, runs the
    engine on it, and fails loudly if the answer changed.

WHAT ROLE IT PLAYS IN THE PROTOTYPE
    It is the part that stops the engine quietly rotting.

    router.py is 3,400 lines and the rules inside it lean on each other. The 50
    cent stock rule only makes sense once the promise rule has already run; the
    rejection reasons are only honest because they are checked in the same order
    the choosing happened. A small tidy-up in one place can change an answer
    somewhere else and still print a perfectly confident, perfectly wrong report.

    So several of these tests are not here to prove the engine works. They are
    here because that exact thing already went wrong once, and this is what makes
    sure it stays fixed. Those tests name the bug they are guarding.

WHAT IT READS
    Mostly nothing. Almost every test builds its own tiny world in memory - two
    or three warehouses and one customer - so the situation being tested is
    visible in the test itself.

    A few tests do read the real files in the data folder, and they say so. Those
    are the ones checking that the shipped prototype gives the answers the README
    quotes.

WHAT IT RETURNS OR PRINTS
    It returns nothing and prints nothing of its own. unittest prints the
    results: a dot for each test that passed, and a full report of what was
    expected against what actually happened for each one that failed.

HOW TO RUN IT
    python3 -m unittest discover tests -v

    Nothing needs installing first. unittest comes with Python.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

# WHY THE PROJECT FOLDER IS ADDED TO THE IMPORT PATH BY HAND
#     router.py sits one folder up from this file, and Python does not look there
#     on its own. Without this line the tests only run if you happen to be stood
#     in exactly the right folder, and anywhere else they fail with "no module
#     named router" - which reads like the engine is missing rather than like the
#     command was run from the wrong place.
#
#     main.py had the same problem with its data folder and it is fixed the same
#     way: work the path out from where THIS file is, which never changes.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import router  # noqa: E402  (the path line above has to run first)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# BUILDING A SMALL WORLD TO TEST IN
# ---------------------------------------------------------------------------
#
# WHY THE TESTS DO NOT JUST USE THE REAL DATA FILES
#     Because a test should say what it is testing.
#
#     The real network has four warehouses, twelve lanes and ten orders, all
#     tuned so the sample report shows something interesting. Testing the 50 cent
#     rule against it would mean writing "ORD-1002 should pick Dallas" - which is
#     true, but says nothing about why, and a reader cannot tell whether it passed
#     for the right reason. Worse, changing one price in the data file to improve
#     the report would then break tests that have nothing to do with that price.
#
#     So each test below builds exactly the world it needs, with only the numbers
#     that matter to it. "Two warehouses, both on time, 40 cents apart, one nearly
#     empty" is a sentence you can check against the rule it is testing.
#
# THE WORLD IS ALWAYS ONE CITY
#     Every made-up world ships to a single city in a single area. That is not a
#     limitation worth working around: which area a city is in is a plain lookup,
#     and it gets its own test later. Collapsing it to one city here means a lane
#     is just a price and a number of days, so the setup stays readable.
#
# EVERY MADE-UP ORDER IS PLACED ON FRIDAY 14 AUGUST 2026
#     Which means the parcel always leaves on Monday 17 August, because the
#     weekend is skipped. That one fixed ship date makes every arrival date in
#     these tests something you can work out in your head:
#
#         1 working day  -> Tuesday 18 August
#         2 working days -> Wednesday 19 August
#         3 working days -> Thursday 20 August
#         4 working days -> Friday 21 August
#         5 working days -> Monday 24 August   (the weekend is skipped again)
#
#     It is also the date the real orders use, so the numbers here and the numbers
#     in the report are talking about the same week.

TEST_CITY = "Testville, TS"
TEST_ZONE = "TEST"
FRIDAY = "2026-08-14"       # every made-up order is placed on this day
SHIP_DATE = "2026-08-17"    # so every parcel leaves on this day


def make_network(warehouses, lanes):
    """
    Builds a made-up network in the same shape load_network() returns.

    Takes: warehouses, as {warehouse id: {product: units}}
           lanes, as {warehouse id: (price in cents, working days)}
    Gives back: a network dictionary the engine will accept.

    Every warehouse needs an entry in lanes, because the engine looks up a lane
    for any warehouse that has stock and would crash on a missing one. That is
    correct behaviour - a warehouse we cannot ship from is a hole in the data, not
    a routing decision - so this helper does not paper over it.
    """
    return {
        "fulfillment_centers": [
            {"fc_id": fc_id, "inventory": dict(stock)}
            for fc_id, stock in warehouses.items()
        ],
        "destination_zones": {TEST_CITY: TEST_ZONE},
        "lanes": {
            (fc_id, TEST_ZONE): {
                "shipping_cost_cents": cost,
                "transit_days": days,
            }
            for fc_id, (cost, days) in lanes.items()
        },
        "known_skus": {sku for stock in warehouses.values() for sku in stock},
        "starting_inventory": {
            fc_id: dict(stock) for fc_id, stock in warehouses.items()
        },
    }


def make_order(promised, quantity=1, sku="SKU-A", order_id="ORD-TEST",
               destination=TEST_CITY, order_date=FRIDAY):
    """
    Builds one made-up customer order.

    Takes: the promised delivery date, and anything else worth changing.
    Gives back: an order dictionary in the same shape as the ones in orders.json.

    The promised date has no default on purpose. It is the single most important
    number in every test in this file - it decides whether a warehouse is a
    success or a failure - so it should never be something a reader has to come
    up here to look up.
    """
    return {
        "order_id": order_id,
        "sku": sku,
        "quantity": quantity,
        "destination": destination,
        "order_date": order_date,
        "promised_delivery_date": promised,
    }


def route_one(order, network):
    """
    Routes a single order against fresh stock and hands back the decision record.

    Takes: one order and one network.
    Gives back: the decision record from router.route_order().

    Fresh stock every time, because route_order() takes units off the shelf as a
    side effect. Two tests sharing one inventory would mean the second one
    running against whatever the first one left behind, and it would pass or fail
    depending on the order the tests happened to run in.
    """
    return router.route_order(order, network, router.fresh_inventory(network))


def reason_for(decision, fc_id):
    """
    Digs out why one particular warehouse was not chosen.

    Takes: a decision record, and the warehouse to look up.
    Gives back: the reason code, or None if that warehouse is not listed.

    Checking the reason codes matters as much as checking the winner. The engine's
    whole claim is that it can explain itself, and it is perfectly possible to
    pick the right warehouse for a reason it then describes wrongly - that has
    happened here before.
    """
    for alternative in decision["alternatives"]:
        if alternative["fc_id"] == fc_id:
            return alternative["reason_not_selected"]
    return None


# ---------------------------------------------------------------------------
# PIECE 1 - THE RULES THAT PICK A WAREHOUSE
# ---------------------------------------------------------------------------


class TestPromiseBeatsPrice(unittest.TestCase):
    """
    The main rule: if a cheaper warehouse would arrive late, we do not use it.

    This is the one thing the whole prototype is built to do, so it gets tested
    from both sides. Doing the expensive thing when the promise needs it is only
    half the job. The other half is NOT doing it when the promise does not.
    """

    def test_we_pay_more_when_the_cheap_warehouse_would_be_late(self):
        # Near is dear and quick, Far is cheap and slow. The customer was
        # promised Wednesday, so Far's Monday arrival is five days too late.
        network = make_network(
            warehouses={"FC-NEAR": {"SKU-A": 10}, "FC-FAR": {"SKU-A": 10}},
            lanes={"FC-NEAR": (900, 1), "FC-FAR": (400, 5)},
        )
        decision = route_one(make_order(promised="2026-08-19"), network)

        self.assertEqual(decision["selected_fc"], "FC-NEAR")
        self.assertEqual(decision["status"], router.STATUS_ROUTED)
        self.assertEqual(decision["expected_delivery_date"], "2026-08-18")

        # The extra $5.00 is recorded, named, and pinned to the warehouse we
        # turned down. A merchant should never have to take "we spent more" on
        # trust - the cheaper option and its price are both on the record.
        self.assertEqual(decision["premium_over_cheapest_cents"], 500)
        self.assertEqual(decision["premium_reason"], router.PREMIUM_PROTECT_PROMISE)
        self.assertEqual(decision["cheapest_available_fc"], "FC-FAR")
        self.assertEqual(decision["cheapest_available_cost_cents"], 400)

        # And the reason given for skipping Far is the reason that actually
        # knocked it out, not merely something true about it. Far was also
        # slower, but slowness is not why it lost.
        self.assertEqual(
            reason_for(decision, "FC-FAR"), router.NOT_SELECTED_MISSES_PROMISE
        )

    def test_we_do_not_pay_more_when_the_cheap_warehouse_already_makes_it(self):
        # THIS IS THE TEST THAT CATCHES A LAZY ENGINE.
        #
        # Identical warehouses to the test above. The only thing that changed is
        # the promised date: the customer will wait until Monday, and Far gets
        # there Monday. Nothing needs protecting, so nothing should be spent.
        #
        # An engine that simply always grabbed the fastest warehouse would sail
        # through the previous test and fail this one. Passing both is what makes
        # "protect the promise" a real rule rather than a fast-shipping habit
        # with a nice name.
        network = make_network(
            warehouses={"FC-NEAR": {"SKU-A": 10}, "FC-FAR": {"SKU-A": 10}},
            lanes={"FC-NEAR": (900, 1), "FC-FAR": (400, 5)},
        )
        decision = route_one(make_order(promised="2026-08-24"), network)

        self.assertEqual(decision["selected_fc"], "FC-FAR")
        self.assertEqual(decision["status"], router.STATUS_ROUTED)
        self.assertEqual(decision["expected_delivery_date"], "2026-08-24")

        # Arriving exactly ON the promised day counts as keeping the promise. We
        # said "by Monday", and Monday is by Monday. Getting this wrong by one
        # day would make the engine buy speed it was never asked for.
        self.assertTrue(decision["promise_met"])
        self.assertEqual(decision["days_late"], 0)

        self.assertEqual(decision["premium_over_cheapest_cents"], 0)
        self.assertEqual(decision["premium_reason"], router.PREMIUM_NONE)
        self.assertEqual(
            reason_for(decision, "FC-NEAR"), router.NOT_SELECTED_HIGHER_COST
        )


class TestTheFiftyCentStockRule(unittest.TestCase):
    """
    When two warehouses cost nearly the same, we protect the emptier shelf.

    The rule: anything within 50 cents of the cheapest option counts as the same
    price, and inside that group we prefer whichever leaves more stock behind.

    Both sides of the window are tested. A window that never lets anything in is
    the same as having no rule; a window that lets everything in is a licence to
    overspend. The pair of tests below is what holds it to 50.
    """

    def test_forty_cents_apart_counts_as_the_same_price_so_stock_decides(self):
        # 40 cents apart, so inside the window. Cheap is down to 2 units, Dearer
        # has 20. We pay the 40 cents and leave the nearly empty shelf alone.
        network = make_network(
            warehouses={"FC-CHEAP": {"SKU-A": 2}, "FC-DEARER": {"SKU-A": 20}},
            lanes={"FC-CHEAP": (480, 1), "FC-DEARER": (520, 1)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["selected_fc"], "FC-DEARER")
        self.assertEqual(decision["premium_over_cheapest_cents"], 40)

        # The reason has to be PRESERVE_INVENTORY and nothing else. Both options
        # arrived on time, so calling this 40 cents "protecting the promise"
        # would be a lie about money we actually spent - and it is the kind of
        # lie that adds up into a total nobody can account for.
        self.assertEqual(decision["premium_reason"], router.PREMIUM_PRESERVE_INVENTORY)
        self.assertEqual(
            reason_for(decision, "FC-CHEAP"), router.NOT_SELECTED_LOWER_STOCK
        )

    def test_eighty_cents_apart_is_a_real_gap_so_the_cheaper_one_wins(self):
        # Same shelves, same shortage at Cheap. The only change is that the gap
        # is now 80 cents, which is outside the window. Dearer never gets
        # considered on stock at all, and Cheap's last two units go.
        #
        # That is the rule working, not failing. 50 cents is the point where we
        # stop calling a price difference small, and a rule that bent for 80
        # cents would bend for any number.
        network = make_network(
            warehouses={"FC-CHEAP": {"SKU-A": 2}, "FC-DEARER": {"SKU-A": 20}},
            lanes={"FC-CHEAP": (480, 1), "FC-DEARER": (560, 1)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["selected_fc"], "FC-CHEAP")
        self.assertEqual(decision["premium_over_cheapest_cents"], 0)
        self.assertEqual(decision["premium_reason"], router.PREMIUM_NONE)
        self.assertEqual(
            reason_for(decision, "FC-DEARER"), router.NOT_SELECTED_HIGHER_COST
        )

    def test_the_window_is_read_from_the_constant_not_typed_into_the_engine(self):
        # The two tests above hard-code 40 and 80 because a reader needs to see
        # real numbers to follow them. This one is the safety net for that
        # choice: if somebody changes the band to 25 cents, the tests above
        # become wrong, and this test is what says so out loud instead of
        # leaving two confusing failures with no explanation.
        self.assertEqual(
            router.COST_TIE_BAND_CENTS, 50,
            "The two tests above are written around a 50 cent window. If the "
            "band has deliberately changed, their 40 and 80 cent gaps need "
            "changing with it.",
        )


class TestTheLastTwoTieBreakers(unittest.TestCase):
    """
    What happens when price and stock have both run out of things to say.

    The full order of preference, once two warehouses count as the same price:

        1. more stock left over afterwards
        2. cheaper
        3. faster
        4. warehouse name, alphabetically

    Rules 3 and 4 almost never get used. Nothing in the real sample batch reaches
    them, which is exactly why they are worth a test - a rule nothing exercises is
    a rule nobody has ever checked.

    Rule 4 looks arbitrary because it is. That is the point of it. When two options
    are genuinely identical on everything we care about, we still have to pick one,
    and we have to pick the SAME one every time. Sorting on the name guarantees
    that. Without it the engine could answer the same question two different ways
    on two different days.
    """

    def test_when_everything_else_is_level_the_faster_one_wins(self):
        # Same price, same stock left over, different journey times.
        network = make_network(
            warehouses={"FC-QUICK": {"SKU-A": 10}, "FC-PLODDING": {"SKU-A": 10}},
            lanes={"FC-QUICK": (500, 1), "FC-PLODDING": (500, 3)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["selected_fc"], "FC-QUICK")
        self.assertEqual(decision["premium_over_cheapest_cents"], 0)

        # Only now is "it was slower" the honest reason. Everywhere else in the
        # engine, slowness is either the thing that broke the promise (a different
        # reason) or something we did not care about, because we promised a date
        # and not a race.
        self.assertEqual(
            reason_for(decision, "FC-PLODDING"), router.NOT_SELECTED_SLOWER
        )

    def test_a_dead_heat_is_settled_by_the_alphabet(self):
        # Two warehouses identical in every way that matters: same price, same
        # journey, same stock before and after. There is no reason to prefer
        # either, so the name decides and Aardvark wins.
        network = make_network(
            warehouses={"FC-ZEBRA": {"SKU-A": 10}, "FC-AARDVARK": {"SKU-A": 10}},
            lanes={"FC-ZEBRA": (500, 2), "FC-AARDVARK": (500, 2)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["selected_fc"], "FC-AARDVARK")

        # And the record admits it was a coin toss settled by the alphabet, rather
        # than inventing a reason that sounds better. "We went alphabetical" is a
        # slightly embarrassing thing to print and it is the truth.
        self.assertEqual(
            reason_for(decision, "FC-ZEBRA"), router.NOT_SELECTED_TIE_BREAK
        )

    def test_the_alphabet_gives_the_same_answer_whatever_order_the_file_lists(self):
        # The same dead heat, with the two warehouses written into the data the
        # other way round. The answer must not change.
        #
        # This is the test that would catch the engine reading warehouses in
        # whatever order they happened to appear. On a dead heat, "first in the
        # file" and "first in the alphabet" give different winners, and only one of
        # those is a rule.
        one_way = make_network(
            warehouses={"FC-ZEBRA": {"SKU-A": 10}, "FC-AARDVARK": {"SKU-A": 10}},
            lanes={"FC-ZEBRA": (500, 2), "FC-AARDVARK": (500, 2)},
        )
        other_way = make_network(
            warehouses={"FC-AARDVARK": {"SKU-A": 10}, "FC-ZEBRA": {"SKU-A": 10}},
            lanes={"FC-AARDVARK": (500, 2), "FC-ZEBRA": (500, 2)},
        )
        order = make_order(promised="2026-08-21")

        self.assertEqual(
            route_one(order, one_way)["selected_fc"],
            route_one(order, other_way)["selected_fc"],
        )


class TestWarehousesThatCannotFillTheOrder(unittest.TestCase):
    """
    A warehouse is only a candidate if it can supply the WHOLE order by itself.

    We never split an order across warehouses in this prototype, so a warehouse
    holding one unit is no help at all to an order for two. Half an order is not
    a smaller success - it is a different kind of failure.
    """

    def test_a_warehouse_without_enough_stock_is_never_chosen(self):
        # Empty holds nothing, Thin holds one, and the order is for two. Both of
        # them are cheaper than Full, and neither can have the order.
        network = make_network(
            warehouses={
                "FC-EMPTY": {"SKU-A": 0},
                "FC-THIN": {"SKU-A": 1},
                "FC-FULL": {"SKU-A": 10},
            },
            lanes={
                "FC-EMPTY": (300, 1),
                "FC-THIN": (350, 1),
                "FC-FULL": (900, 1),
            },
        )
        decision = route_one(
            make_order(promised="2026-08-21", quantity=2), network
        )

        self.assertEqual(decision["selected_fc"], "FC-FULL")
        self.assertEqual(decision["status"], router.STATUS_ROUTED)

        # We paid $9.00 with a $3.00 lane sitting right there, and the premium is
        # still correctly zero. That is because the premium measures us against
        # the cheapest thing we could ACTUALLY have done, and shipping from an
        # empty warehouse was never one of those. Measuring against a price that
        # was never on offer would invent a cost we never had the option to save.
        self.assertEqual(decision["premium_over_cheapest_cents"], 0)
        self.assertEqual(decision["cheapest_available_fc"], "FC-FULL")
        self.assertEqual(decision["premium_reason"], router.PREMIUM_NONE)

    def test_warehouses_we_could_not_use_are_still_listed_with_their_stock(self):
        # The warehouses that lost are kept on the record rather than dropped.
        #
        # This is here because of a real bug in the printed report. The column
        # said "no stock" for every warehouse in this group, and one of them was
        # holding 19 units against an order for 25 - true that it could not fill
        # the order, false about the shelf, and it hid the actual problem, which
        # was that we owned plenty and it was spread across four buildings.
        #
        # The fix reads inventory_before, so inventory_before has to be there and
        # has to be right. Nought and one are different facts about the world.
        network = make_network(
            warehouses={
                "FC-EMPTY": {"SKU-A": 0},
                "FC-THIN": {"SKU-A": 1},
                "FC-FULL": {"SKU-A": 10},
            },
            lanes={
                "FC-EMPTY": (300, 1),
                "FC-THIN": (350, 1),
                "FC-FULL": (900, 1),
            },
        )
        decision = route_one(
            make_order(promised="2026-08-21", quantity=2), network
        )

        listed = {a["fc_id"]: a for a in decision["alternatives"]}
        self.assertEqual(set(listed), {"FC-EMPTY", "FC-THIN"})

        self.assertEqual(
            listed["FC-EMPTY"]["reason_not_selected"], router.NOT_SELECTED_NO_STOCK
        )
        self.assertEqual(listed["FC-EMPTY"]["inventory_before"], 0)

        self.assertEqual(
            listed["FC-THIN"]["reason_not_selected"], router.NOT_SELECTED_NO_STOCK
        )
        self.assertEqual(
            listed["FC-THIN"]["inventory_before"], 1,
            "A shelf with one unit on it must not be recorded as empty - that is "
            "the bug this test exists for.",
        )


# ---------------------------------------------------------------------------
# PIECE 2 - THE FOUR THINGS THAT CAN HAPPEN TO AN ORDER
# ---------------------------------------------------------------------------
#
# Every order comes out of the engine as exactly one of these:
#
#     ROUTED         we are sending it, and it will arrive in time
#     ROUTED_LATE    we are sending it, and it will arrive late
#     UNROUTABLE     the order is fine, we just cannot fill it
#     INVALID_ORDER  the order itself makes no sense, so there is nothing to send
#
# Piece 1 above already covers ROUTED, because every test in it ends with a
# parcel arriving on time. The three below are the awkward ones.
#
# WHY THE AWKWARD ONES GET THE MOST ATTENTION
#     Because they are where an engine is tempted to lie. Saying "sold out" is
#     easier than working out that we own plenty and it is in the wrong buildings.
#     Dropping a broken order silently is easier than saying which field is
#     broken. Both shortcuts still produce a report that looks finished.


class TestWhenNothingCanArriveOnTime(unittest.TestCase):
    """
    Sometimes the promised date was never reachable. The order still ships.

    A promise we cannot keep is not a reason to send nothing. The customer still
    wants their parcel. So the question changes from "who keeps the promise" to
    "who breaks it by the least", and we answer that one instead.
    """

    def test_we_pick_the_least_late_option_even_though_it_costs_more(self):
        # The customer was promised Tuesday. Nothing gets there by Tuesday.
        #   Slow      $4.00, 5 working days -> Monday 24th, six days late
        #   Less Slow $7.00, 3 working days -> Thursday 20th, two days late
        network = make_network(
            warehouses={"FC-SLOW": {"SKU-A": 10}, "FC-LESS-SLOW": {"SKU-A": 10}},
            lanes={"FC-SLOW": (400, 5), "FC-LESS-SLOW": (700, 3)},
        )
        decision = route_one(make_order(promised="2026-08-18"), network)

        self.assertEqual(decision["status"], router.STATUS_ROUTED_LATE)
        self.assertEqual(decision["selected_fc"], "FC-LESS-SLOW")
        self.assertFalse(decision["promise_met"])
        self.assertEqual(decision["days_late"], 2)

        # Days late come before money here, and on purpose. We would rather pay
        # $3.00 more to be two days late than save it and be six days late. The
        # customer feels the days. The merchant only feels the pennies.
        self.assertEqual(decision["premium_over_cheapest_cents"], 300)
        self.assertEqual(decision["premium_reason"], router.PREMIUM_REDUCE_LATENESS)

        # The reason for skipping Slow must be MORE_DAYS_LATE, not MISSES_PROMISE.
        # Slow did miss the promise - but so did the warehouse we chose, so that
        # cannot be why it lost. Printing MISSES_PROMISE here would be true about
        # Slow and still a wrong account of the decision.
        self.assertEqual(
            reason_for(decision, "FC-SLOW"), router.NOT_SELECTED_MORE_DAYS_LATE
        )

    def test_the_spare_time_goes_negative_when_the_promise_was_impossible(self):
        # Spare time is the promised date minus the soonest we could manage.
        # Promised the 18th, soonest possible is the 20th, so spare time is -2.
        #
        # A negative number here is the engine admitting the promise was already
        # broken before it did anything. That matters later: piece 4 uses this to
        # work out whether a broken promise was our fault or never possible.
        network = make_network(
            warehouses={"FC-SLOW": {"SKU-A": 10}, "FC-LESS-SLOW": {"SKU-A": 10}},
            lanes={"FC-SLOW": (400, 5), "FC-LESS-SLOW": (700, 3)},
        )
        decision = route_one(make_order(promised="2026-08-18"), network)

        self.assertEqual(decision["slack_days_when_routed"], -2)


class TestWhenWeCannotFillTheOrderAtAll(unittest.TestCase):
    """
    Two very different failures that must never be confused with each other.

    Sold out      We own fewer units than the customer asked for. Nothing clever
                  fixes this. Somebody has to make more of the product.
    Needs splitting
                  We own plenty. It is sitting in different buildings, and no
                  single building has enough on its own.

    The second one is not a stock problem at all - it is a rule of this prototype
    (we never send two parcels for one order) turning into a failure. Calling it
    "sold out" would be false, and a merchant who knows they own four of something
    would rightly stop believing the rest of the report.
    """

    def test_sold_out_is_reported_as_sold_out(self):
        # Two warehouses with one unit each. The customer wants five. We own two.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 1}, "FC-TWO": {"SKU-A": 1}},
            lanes={"FC-ONE": (400, 1), "FC-TWO": (500, 1)},
        )
        decision = route_one(
            make_order(promised="2026-08-21", quantity=5), network
        )

        self.assertEqual(decision["status"], router.STATUS_UNROUTABLE)
        self.assertEqual(decision["reason"], router.REASON_OUT_OF_STOCK)
        self.assertIsNone(decision["selected_fc"])

        # We own two, they asked for five. Fewer than asked for, so: sold out.
        self.assertEqual(decision["units_in_network"], 2)

    def test_needs_splitting_is_reported_as_needing_splitting(self):
        # Two warehouses with two units each. The customer wants three.
        #
        # We own four. That is MORE than they asked for. Neither warehouse can
        # send three by itself, so the order fails - but nothing is sold out.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 2}, "FC-TWO": {"SKU-A": 2}},
            lanes={"FC-ONE": (400, 1), "FC-TWO": (500, 1)},
        )
        decision = route_one(
            make_order(promised="2026-08-21", quantity=3), network
        )

        self.assertEqual(decision["status"], router.STATUS_UNROUTABLE)
        self.assertEqual(decision["reason"], router.REASON_SPLIT_REQUIRED)

        # The proof that "sold out" would have been a lie, written down as a
        # number: we are holding four units and the order was for three.
        self.assertEqual(decision["units_in_network"], 4)
        self.assertGreater(decision["units_in_network"], decision["quantity"])

    def test_a_failed_order_still_records_what_we_failed_to_hit(self):
        # This test exists because of a real gap. Failure records used to be
        # written without the city or the promised date, and the printed report
        # then had to say "we could not fill this order" with no mention of who
        # was waiting or what date they had been given.
        #
        # An order we could not fill still had a date promised to it, and that
        # promise is exactly the thing that just got broken. Leaving it off the
        # record made the failure look smaller than it was.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 1}, "FC-TWO": {"SKU-A": 1}},
            lanes={"FC-ONE": (400, 1), "FC-TWO": (500, 1)},
        )
        decision = route_one(
            make_order(promised="2026-08-21", quantity=5), network
        )

        self.assertEqual(decision["promised_delivery_date"], "2026-08-21")
        self.assertEqual(decision["destination"], TEST_CITY)
        self.assertEqual(decision["sku"], "SKU-A")
        self.assertEqual(decision["quantity"], 5)

        # The city AND the area are both kept, because they answer different
        # questions. The area is what the engine used to look up prices. The city
        # is what a human recognises. Printing "TEST" at somebody explains nothing.
        self.assertEqual(decision["destination_zone"], TEST_ZONE)

        # Every warehouse we looked at is still listed with how little it had.
        # "Sold out" is a claim, and this is the working behind it.
        self.assertEqual(len(decision["alternatives"]), 2)
        for alternative in decision["alternatives"]:
            self.assertEqual(
                alternative["reason_not_selected"], router.NOT_SELECTED_NO_STOCK
            )
            self.assertEqual(alternative["inventory_before"], 1)


class TestOrdersThatMakeNoSense(unittest.TestCase):
    """
    A bad order is not the same thing as a hard order, and the difference matters.

    Bad order   The details themselves are wrong. Someone asked for minus two
                items, or gave a city we have never heard of. There is nothing to
                route, so we reject it and say which field is wrong.
    Hard order  The details are fine, we just cannot do a good job. Somebody
                promised Tuesday and our fastest option is Wednesday. That order
                still gets shipped, flagged as late.

    Only bad orders get rejected. The tests above this class are all hard orders,
    and every one of them ships.
    """

    def setUp(self):
        # One warehouse with plenty of stock, so nothing below can fail for any
        # reason other than the broken field it is testing.
        self.network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 50}},
            lanes={"FC-ONE": (400, 1)},
        )

    def test_each_kind_of_broken_order_is_named_correctly(self):
        # All five ways an order can be rejected. Each is checked on its own so a
        # failure says which kind stopped being detected.
        cases = [
            (
                "nobody can order nothing",
                make_order(promised="2026-08-21", quantity=0),
                router.REASON_INVALID_QUANTITY,
            ),
            (
                "a negative order is meaningless",
                make_order(promised="2026-08-21", quantity=-2),
                router.REASON_INVALID_QUANTITY,
            ),
            (
                "a quantity has to be a whole number, not the word two",
                make_order(promised="2026-08-21", quantity="two"),
                router.REASON_INVALID_QUANTITY,
            ),
            (
                "we do not sell that product",
                make_order(promised="2026-08-21", sku="SKU-NOBODY-SELLS"),
                router.REASON_UNKNOWN_SKU,
            ),
            (
                "we have no shipping rates for that city",
                make_order(promised="2026-08-21", destination="Atlantis, XX"),
                router.REASON_UNKNOWN_DESTINATION,
            ),
            (
                "the 32nd of August is not a day",
                make_order(promised="2026-08-32"),
                router.REASON_INVALID_DATE_FORMAT,
            ),
            (
                "delivery promised four days before the order was placed",
                make_order(promised="2026-08-10"),
                router.REASON_INVALID_PROMISE_DATE,
            ),
        ]

        for description, order, expected_reason in cases:
            with self.subTest(description):
                decision = router.route_order(
                    order, self.network, router.fresh_inventory(self.network)
                )
                self.assertEqual(decision["status"], router.STATUS_INVALID_ORDER)
                self.assertEqual(decision["reason"], expected_reason)
                self.assertIsNone(decision["selected_fc"])

                # Nothing was considered, because there was nothing to consider.
                # An empty list here is the honest answer, and it is what stops
                # the report from printing a table of warehouses for an order it
                # never looked at.
                self.assertEqual(decision["alternatives"], [])

    def test_the_broken_value_itself_is_kept_on_the_record(self):
        # "UNKNOWN_DESTINATION" tells a person nothing they can act on. "We have
        # no rates for Atlantis, XX" tells them exactly what to go and fix.
        #
        # So the values are copied onto the record exactly as they arrived, wrong
        # ones included. On a rejected order the wrong value is the single most
        # useful thing on the whole record.
        order = make_order(promised="2026-08-21", destination="Atlantis, XX")
        decision = router.route_order(
            order, self.network, router.fresh_inventory(self.network)
        )

        self.assertEqual(decision["destination"], "Atlantis, XX")

    def test_both_dates_are_kept_when_they_are_the_wrong_way_round(self):
        # One rejection reason needs both dates to be explainable: the one where
        # delivery was promised before the order was placed. Without both dates
        # side by side the report can only say "the dates are the wrong way
        # round" and leave the reader to take our word for which way round.
        order = make_order(promised="2026-08-10", order_date="2026-08-14")
        decision = router.route_order(
            order, self.network, router.fresh_inventory(self.network)
        )

        self.assertEqual(decision["reason"], router.REASON_INVALID_PROMISE_DATE)
        self.assertEqual(decision["order_date"], "2026-08-14")
        self.assertEqual(decision["promised_delivery_date"], "2026-08-10")

    def test_only_the_first_problem_is_reported(self):
        # This order is wrong in three separate ways: no quantity, a product we
        # do not sell, and a city we do not ship to. Only the quantity is
        # reported, because that is the first thing checked.
        #
        # That is a deliberate shortcut and this test pins it down so the README
        # can describe it honestly. A real system helping a merchant fix their
        # data would list everything wrong at once, so they could do one round of
        # fixes instead of three.
        order = make_order(
            promised="2026-08-21",
            quantity=0,
            sku="SKU-NOBODY-SELLS",
            destination="Atlantis, XX",
        )
        decision = router.route_order(
            order, self.network, router.fresh_inventory(self.network)
        )

        self.assertEqual(decision["reason"], router.REASON_INVALID_QUANTITY)


# ---------------------------------------------------------------------------
# PIECE 3 - RUNNING A WHOLE BATCH
# ---------------------------------------------------------------------------
#
# Everything above routes one order on its own. A batch adds one problem, and it
# is the problem this whole part of the engine exists for:
#
#     the orders are competing with each other for the same stock.
#
# There are only so many units. Whoever gets looked at first gets one, and
# whoever gets looked at last gets an apology. So the running order is a decision
# the engine is making whether it admits to it or not.
#
# WHAT THESE TESTS COVER
#     Stock really coming off the shelf, and off the right shelf.
#     The sum at the end proving nothing was invented or lost.
#     The running order changing the answer.
#     The same input always giving the same answer.


class TestStockMovesOffTheShelf(unittest.TestCase):
    """
    Routing an order changes the stock. That is not a side effect to tidy up
    later - it is how the competition between orders happens at all.

    If stock did not come off the shelf, the engine would cheerfully promise the
    same last unit to five different customers and every single one of those
    decisions would look correct on its own.
    """

    def setUp(self):
        # WHY THE WAREHOUSE WE CHOOSE IS DELIBERATELY NOT THE CHEAPEST ONE
        #     The obvious setup is a cheap warehouse with stock and a dear one
        #     without, so the cheapest option wins. That setup cannot catch a
        #     subtraction aimed at the wrong shelf, because the chosen shelf and
        #     the cheapest shelf are the same shelf, so the mistake is invisible.
        #
        #     I know that because it happened. The first version of this test used
        #     that setup, I broke the engine on purpose to point the subtraction at
        #     the cheapest warehouse instead of the chosen one, and the test passed
        #     happily.
        #
        #     So here the dearer warehouse wins, on the 50 cent stock rule: they
        #     are 40 cents apart, and FC-CHEAPER would be emptied by this order
        #     while FC-CHOSEN has plenty. Now "the shelf we chose" and "the
        #     cheapest shelf" are two different shelves and the test has teeth.
        self.network = make_network(
            warehouses={"FC-CHOSEN": {"SKU-A": 10}, "FC-CHEAPER": {"SKU-A": 3}},
            lanes={"FC-CHOSEN": (520, 1), "FC-CHEAPER": (480, 1)},
        )

    def test_the_units_come_off_the_warehouse_we_chose_and_no_other(self):
        inventory = router.fresh_inventory(self.network)
        order = make_order(promised="2026-08-21", quantity=3)

        decision = router.route_order(order, self.network, inventory)

        # Confirm the setup really is the awkward one before trusting the rest:
        # we paid 40 cents more than we had to, so the cheapest warehouse is not
        # the one we are shipping from.
        self.assertEqual(decision["selected_fc"], "FC-CHOSEN")
        self.assertEqual(decision["cheapest_available_fc"], "FC-CHEAPER")

        self.assertEqual(inventory["FC-CHOSEN"]["SKU-A"], 7)

        # The warehouse we did not use must be untouched. This is worth checking
        # separately: a subtraction pointed at the wrong shelf still produces
        # totals that add up, so the stock check at the end of a batch would not
        # notice, and the report would look perfect.
        self.assertEqual(inventory["FC-CHEAPER"]["SKU-A"], 3)

        # The record and the shelf have to agree. These are two separate numbers
        # written by the same line of code, and if they ever disagree then the
        # report is describing a shipment that did not happen.
        self.assertEqual(decision["inventory_before"], 10)
        self.assertEqual(decision["inventory_after"], 7)

    def test_an_order_we_cannot_fill_leaves_every_shelf_alone(self):
        # Nobody can send 25 units, so nothing should move anywhere.
        inventory = router.fresh_inventory(self.network)
        order = make_order(promised="2026-08-21", quantity=25)

        decision = router.route_order(order, self.network, inventory)

        self.assertEqual(decision["status"], router.STATUS_UNROUTABLE)
        self.assertEqual(inventory, {"FC-CHOSEN": {"SKU-A": 10},
                                     "FC-CHEAPER": {"SKU-A": 3}})

    def test_a_rejected_order_leaves_every_shelf_alone(self):
        # A broken order must not quietly reserve stock on its way out. If it did,
        # the units would be gone and no shipment would ever explain where to.
        inventory = router.fresh_inventory(self.network)
        order = make_order(promised="2026-08-21", quantity=0)

        decision = router.route_order(order, self.network, inventory)

        self.assertEqual(decision["status"], router.STATUS_INVALID_ORDER)
        self.assertEqual(inventory, {"FC-CHOSEN": {"SKU-A": 10},
                                     "FC-CHEAPER": {"SKU-A": 3}})

    def test_the_second_order_sees_what_the_first_one_left(self):
        # One warehouse, one unit, two customers who each want it.
        #
        # The first order gets it. The second must be told we cannot fill it. Both
        # answers are correct, and they are different answers to the same
        # question, which is exactly the point of running a batch.
        network = make_network(
            warehouses={"FC-ONLY": {"SKU-A": 1}},
            lanes={"FC-ONLY": (400, 1)},
        )
        inventory = router.fresh_inventory(network)

        first = router.route_order(
            make_order(promised="2026-08-21", order_id="ORD-FIRST"),
            network, inventory,
        )
        second = router.route_order(
            make_order(promised="2026-08-21", order_id="ORD-SECOND"),
            network, inventory,
        )

        self.assertEqual(first["status"], router.STATUS_ROUTED)
        self.assertEqual(first["selected_fc"], "FC-ONLY")

        self.assertEqual(second["status"], router.STATUS_UNROUTABLE)
        self.assertEqual(second["reason"], router.REASON_OUT_OF_STOCK)
        self.assertEqual(inventory["FC-ONLY"]["SKU-A"], 0)


class TestTheStockCheck(unittest.TestCase):
    """
    After every batch the engine proves it did not invent or lose any stock.

    The sum, for every warehouse and every product separately:

        what the shelf started with  ==  what we shipped from it  +  what is left

    This is the one kind of mistake in a routing engine that nobody notices until
    it is expensive. Count stock twice and you sell the same unit to two people
    and find out at the packing bench. Lose stock and you turn away orders you
    could have filled. Neither shows up as an error. Both show up as a phone call
    weeks later.
    """

    def test_it_complains_when_a_shelf_does_not_add_up(self):
        # Started with 5, shipped 1, and somehow still have 5.
        with self.assertRaises(AssertionError) as caught:
            router.check_inventory_adds_up(
                starting_inventory={"FC-A": {"SKU-A": 5}},
                ending_inventory={"FC-A": {"SKU-A": 5}},
                decisions=[{
                    "status": router.STATUS_ROUTED,
                    "selected_fc": "FC-A",
                    "sku": "SKU-A",
                    "quantity": 1,
                }],
            )

        # The message has to name the warehouse and the product. "Stock does not
        # add up" on its own leaves somebody reading 3,400 lines of code to find
        # out where.
        self.assertIn("FC-A", str(caught.exception))
        self.assertIn("SKU-A", str(caught.exception))

    def test_it_checks_each_shelf_on_its_own_not_just_the_company_total(self):
        # THIS IS THE BUG THE FIRST VERSION OF THE CHECK HAD.
        #
        # It only added up company-wide totals. So this exact situation passed:
        # one warehouse quietly drops to minus one unit while another gains one.
        # Company-wide the total is still 10, the sum balances, and the check
        # prints nothing.
        #
        # Minus one unit on a shelf is the very double-booking the check exists to
        # catch. A check that passes on the bug it was written for is worse than
        # no check at all, because it stops you looking.
        starting = {"FC-A": {"SKU-A": 5}, "FC-B": {"SKU-A": 5}}
        ending = {"FC-A": {"SKU-A": -1}, "FC-B": {"SKU-A": 11}}

        # Proof that a company-wide check really would have been fooled: the
        # totals before and after are identical, and nothing was shipped.
        self.assertEqual(
            sum(levels["SKU-A"] for levels in starting.values()),
            sum(levels["SKU-A"] for levels in ending.values()),
        )

        with self.assertRaises(AssertionError) as caught:
            router.check_inventory_adds_up(starting, ending, decisions=[])

        self.assertIn("FC-A", str(caught.exception))

    def test_it_refuses_a_shelf_holding_less_than_nothing(self):
        # A shelf can be empty. It cannot hold less than nothing.
        #
        # Here the sum balances perfectly - started with minus one, shipped
        # nothing, minus one left over - so the arithmetic check has no complaint.
        # This is what the second guard is for. The number is impossible whatever
        # the sum says, and it means the same unit was promised twice.
        with self.assertRaises(AssertionError) as caught:
            router.check_inventory_adds_up(
                starting_inventory={"FC-A": {"SKU-A": -1}},
                ending_inventory={"FC-A": {"SKU-A": -1}},
                decisions=[],
            )

        self.assertIn("impossible", str(caught.exception))

    def test_it_says_nothing_when_everything_balances(self):
        # Started with 5, shipped 2, 3 left. No complaint, no return value.
        result = router.check_inventory_adds_up(
            starting_inventory={"FC-A": {"SKU-A": 5}},
            ending_inventory={"FC-A": {"SKU-A": 3}},
            decisions=[{
                "status": router.STATUS_ROUTED,
                "selected_fc": "FC-A",
                "sku": "SKU-A",
                "quantity": 2,
            }],
        )
        self.assertIsNone(result)


class TestWhoGoesFirstChangesTheAnswer(unittest.TestCase):
    """
    The running order is the cheapest lever in the whole engine.

    We are not buying anything or upgrading any shipping. We are only deciding
    who gets dealt with first. Doing nothing and working down the list top to
    bottom is not "no decision" - it is the decision to let the order form's
    timestamp choose who gets a late parcel. That is not a rule anybody picked.
    It is a rule nobody noticed.

    THE SITUATION BELOW
        One fast warehouse with a single unit, one slow warehouse with plenty.
        The fast one is also the cheaper one, which is what makes the trap.

            FC-FAST  1 unit,  $4.00, 1 working day  -> Tuesday 18th
            FC-SLOW  10 units, $7.00, 3 working days -> Thursday 20th

        Two customers, one unit each:

            ORD-LOOSE promised Thursday 20th - either warehouse works
            ORD-TIGHT promised Tuesday 18th - only the fast one works

        The file lists Loose first, so working down the list hands the only fast
        unit to the customer who did not need it.
    """

    def setUp(self):
        self.network = make_network(
            warehouses={"FC-FAST": {"SKU-A": 1}, "FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-FAST": (400, 1), "FC-SLOW": (700, 3)},
        )
        self.orders = [
            make_order(promised="2026-08-20", order_id="ORD-LOOSE"),
            make_order(promised="2026-08-18", order_id="ORD-TIGHT"),
        ]

    def run_with(self, sequencing):
        return router.route_batch(
            self.orders, self.network, router.fresh_inventory(self.network),
            sequencing=sequencing,
        )

    def test_first_come_first_served_breaks_a_promise_it_did_not_have_to(self):
        result = self.run_with(router.SEQUENCE_FIRST_COME_FIRST_SERVED)

        # Loose is dealt with first and takes the fast unit, because it is also
        # the cheapest and Loose has no reason to refuse it. Every step of that is
        # reasonable. The harm only appears on the next order.
        self.assertEqual(result["sequence"], ["ORD-LOOSE", "ORD-TIGHT"])

        by_id = {d["order_id"]: d for d in result["decisions"]}
        self.assertEqual(by_id["ORD-LOOSE"]["selected_fc"], "FC-FAST")
        self.assertEqual(by_id["ORD-TIGHT"]["status"], router.STATUS_ROUTED_LATE)
        self.assertEqual(by_id["ORD-TIGHT"]["days_late"], 2)

        self.assertEqual(result["summary"]["promises_kept"], 1)
        self.assertEqual(result["summary"]["routed_late"], 1)

    def test_tightest_promise_first_keeps_both(self):
        result = self.run_with(router.SEQUENCE_LOWEST_SLACK_FIRST)

        # Tight has no spare time at all, so it goes first and gets the fast unit.
        # Loose then shrugs, takes the slow warehouse, and still lands on the day
        # it was promised.
        self.assertEqual(result["sequence"], ["ORD-TIGHT", "ORD-LOOSE"])

        by_id = {d["order_id"]: d for d in result["decisions"]}
        self.assertEqual(by_id["ORD-TIGHT"]["selected_fc"], "FC-FAST")
        self.assertEqual(by_id["ORD-LOOSE"]["selected_fc"], "FC-SLOW")

        self.assertEqual(result["summary"]["promises_kept"], 2)
        self.assertEqual(result["summary"]["routed_late"], 0)

    def test_the_saved_promise_costs_nothing(self):
        # THIS IS THE RESULT WORTH THE WHOLE OF PART 4.
        #
        # Both runs ship the same two parcels from the same two warehouses. All
        # that changed is which customer got which one. So the bill is identical
        # to the penny, and one of the two runs keeps an extra promise.
        #
        # Nothing was bought. No shipping was upgraded. The promise was saved by
        # putting one order before another, and that is free.
        fifo = self.run_with(router.SEQUENCE_FIRST_COME_FIRST_SERVED)["summary"]
        tightest = self.run_with(router.SEQUENCE_LOWEST_SLACK_FIRST)["summary"]

        self.assertEqual(
            fifo["shipping_cost_cents"], tightest["shipping_cost_cents"],
            "If these two bills ever stop matching, the free-promise claim in "
            "the README is no longer true and needs rewriting.",
        )
        self.assertGreater(tightest["promises_kept"], fifo["promises_kept"])

    def test_orders_we_cannot_ship_go_to_the_back_of_the_queue(self):
        # An order nobody can fill is not competing for anything, so it must not
        # be allowed to go ahead of an order that could actually use the stock.
        #
        # Its spare time comes back as None, meaning "there is no delivery date to
        # measure against", and None has to sort to the end rather than sorting as
        # zero - which would put it right at the front, ahead of every real order.
        orders = [
            make_order(promised="2026-08-21", quantity=99, order_id="ORD-HOPELESS"),
            make_order(promised="2026-08-18", order_id="ORD-TIGHT"),
            make_order(promised="2026-08-20", order_id="ORD-LOOSE"),
        ]
        spare_time = router.slack_for_every_order(
            orders, self.network, router.fresh_inventory(self.network)
        )

        self.assertIsNone(spare_time["ORD-HOPELESS"])

        queue = router.sort_orders_by_slack(orders, spare_time)
        self.assertEqual(
            [o["order_id"] for o in queue],
            ["ORD-TIGHT", "ORD-LOOSE", "ORD-HOPELESS"],
        )


class TestTheSameInputAlwaysGivesTheSameAnswer(unittest.TestCase):
    """
    Two runs on the same data must match exactly, down to the last field.

    This one is not about being tidy. A routing engine that answers the same
    question two different ways cannot be tested, cannot be explained to a
    merchant, and will not be trusted by anybody who catches it doing so.

    The risk is real and quiet. Several decisions here come down to a tie, and a
    tie broken by whatever order a list happened to be built in looks exactly like
    a tie broken by a rule. That is why warehouses are sorted by name and orders
    by ID before anything is chosen.

    These tests use the REAL data files, because this is a claim about the
    prototype that gets sent off, not about a made-up world.
    """

    def setUp(self):
        self.network = router.load_network(DATA_DIR)
        self.orders = router.load_orders(DATA_DIR)

    def run_real_batch(self, policy, sequencing):
        return router.route_batch(
            self.orders, self.network, router.fresh_inventory(self.network),
            policy=policy, sequencing=sequencing,
        )

    def test_two_runs_of_the_real_batch_are_identical(self):
        first = self.run_real_batch(
            router.POLICY_PROMISE_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST
        )
        second = self.run_real_batch(
            router.POLICY_PROMISE_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST
        )

        # Every field of every decision, not just the winners and the totals. A
        # reason code that wobbles between runs is the same failure as a warehouse
        # that wobbles, because the explanation is the thing we are selling.
        self.assertEqual(first["decisions"], second["decisions"])
        self.assertEqual(first["summary"], second["summary"])
        self.assertEqual(first["sequence"], second["sequence"])

    def test_all_three_runs_in_the_report_pass_the_stock_check(self):
        # route_batch runs the stock check itself and stops the program if it
        # fails, so these three calls not raising IS the test.
        #
        # It is worth doing for all three settings rather than one. The check is
        # about stock moving, and the two policies pick different warehouses while
        # the two sequencings visit orders in different orders - so each pair
        # moves stock around in its own pattern.
        settings = [
            (router.POLICY_PROMISE_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST),
            (router.POLICY_PROMISE_FIRST, router.SEQUENCE_FIRST_COME_FIRST_SERVED),
            (router.POLICY_COST_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST),
        ]
        for policy, sequencing in settings:
            with self.subTest(f"{policy} / {sequencing}"):
                result = self.run_real_batch(policy, sequencing)
                self.assertEqual(
                    result["summary"]["order_count"], len(self.orders)
                )

    def test_every_order_gets_exactly_one_decision(self):
        # No order may be dropped and none may be handled twice. A dropped order
        # is a customer nobody ever looks at again, and it would not show up
        # anywhere in the totals - the counts would simply be smaller and still
        # add up perfectly.
        result = self.run_real_batch(
            router.POLICY_PROMISE_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST
        )

        handled = [d["order_id"] for d in result["decisions"]]
        expected = [o["order_id"] for o in self.orders]

        self.assertEqual(len(handled), len(expected))
        self.assertEqual(sorted(handled), sorted(expected))

        summary = result["summary"]
        self.assertEqual(
            summary["routed_on_time"] + summary["routed_late"]
            + summary["unroutable"] + summary["invalid"],
            len(expected),
            "The four outcomes must account for every order and nothing else.",
        )


# ---------------------------------------------------------------------------
# PIECE 4 - THE BUGS THAT ALREADY HAPPENED
# ---------------------------------------------------------------------------
#
# Every test below is here because the engine once got this exact thing wrong.
# None of them were written by imagining what might break. They were written
# after something did.
#
# WHY THIS IS THE MOST VALUABLE PIECE IN THE FILE
#     A bug that has happened once is far more likely to happen again than one
#     nobody has ever seen. The conditions that produced it are still in the code:
#     the same two numbers that have to agree, the same wording that has to match
#     what got printed elsewhere.
#
# WHAT THEY ALL HAVE IN COMMON
#     Not one of these bugs crashed anything. Every single one produced a report
#     that looked finished, read confidently, and was wrong. Several of them made
#     the engine sound BETTER than it was, which is the direction that does real
#     damage in a demonstration.


class TestThePriceWindowIsMeasuredFromTheRightPlace(unittest.TestCase):
    """
    Two bugs, both in the 50 cent rule, both invisible on screen.

    The 50 cent window is measured from the CHEAPEST option. Not from the price we
    ended up paying. Those two are usually the same number, which is what made
    this so easy to get wrong and so hard to spot.
    """

    def test_the_window_is_measured_from_the_cheapest_not_from_what_we_paid(self):
        # THE BUG
        #     The code that explains rejections used to work the window out for
        #     itself, from the winner's price. Usually harmless, because the winner
        #     usually IS the cheapest.
        #
        #     It stops being harmless the moment we deliberately pay a little more
        #     to protect a shelf. The winner is then not the cheapest, so the
        #     guessed window sits 40 cents too high - and a warehouse that was
        #     genuinely priced out slipped through and came out labelled "same
        #     price, we kept the change" instead of "too expensive".
        #
        #     That is not a wording problem. The one thing this engine promises is
        #     that it can explain itself, and it was quietly making things up.
        #
        # THE SETUP
        #     FC-CHEAP  $4.80, 3 units - this order would empty it
        #     FC-CHOSEN $5.20, 20 units - wins, 40 cents inside the window
        #     FC-DEAR   $5.60, 20 units - 80 cents out, so priced out
        network = make_network(
            warehouses={
                "FC-CHEAP": {"SKU-A": 3},
                "FC-CHOSEN": {"SKU-A": 20},
                "FC-DEAR": {"SKU-A": 20},
            },
            lanes={
                "FC-CHEAP": (480, 1),
                "FC-CHOSEN": (520, 1),
                "FC-DEAR": (560, 1),
            },
        )
        decision = route_one(
            make_order(promised="2026-08-21", quantity=3), network
        )

        # First confirm this is the awkward case: we paid more than the cheapest.
        self.assertEqual(decision["selected_fc"], "FC-CHOSEN")
        self.assertEqual(decision["premium_over_cheapest_cents"], 40)

        # The real window runs from $4.80 to $5.30. FC-DEAR at $5.60 is outside
        # it, so it was priced out and the record has to say so.
        #
        # The bug measured from $5.20 instead, giving a window up to $5.70, which
        # let FC-DEAR through the price check and labelled it HIGHER_COST_ON_TIE -
        # a claim that we treated the two as the same price. We did not.
        self.assertEqual(
            reason_for(decision, "FC-DEAR"), router.NOT_SELECTED_HIGHER_COST
        )
        self.assertEqual(
            reason_for(decision, "FC-CHEAP"), router.NOT_SELECTED_LOWER_STOCK
        )

    def test_we_do_not_buy_speed_we_have_no_use_for(self):
        # THE BUG
        #     Inside the 50 cent window the order of preference is stock, then
        #     price, then speed. The price step was missing.
        #
        #     So when two warehouses both dropped to zero stock, they were level on
        #     stock, and speed decided. The engine paid 40 cents for a faster
        #     delivery it had no use for - both options already arrived on time -
        #     and then wrote the 40 cents down as "protecting stock", which was
        #     flatly untrue, because the shelf it supposedly protected ended at
        #     zero either way.
        #
        # THE SETUP
        #     Both warehouses hold exactly one unit and the order is for one, so
        #     whichever we pick ends up empty. Both arrive in time. There is
        #     nothing left for the extra 40 cents to buy, so we keep it.
        network = make_network(
            warehouses={"FC-CHEAP": {"SKU-A": 1}, "FC-QUICK": {"SKU-A": 1}},
            lanes={"FC-CHEAP": (480, 3), "FC-QUICK": (520, 1)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["selected_fc"], "FC-CHEAP")
        self.assertEqual(decision["premium_over_cheapest_cents"], 0)
        self.assertEqual(decision["premium_reason"], router.PREMIUM_NONE)

        # The proof that "protecting stock" could not have been true: the shelf we
        # chose ends at zero. So does the other one. There was no stock to protect.
        self.assertEqual(decision["inventory_after"], 0)
        self.assertEqual(
            [a["inventory_after"] for a in decision["alternatives"]], [0]
        )

        # And the faster warehouse is recorded as losing on price, which is what
        # actually happened, rather than on anything to do with stock.
        self.assertEqual(
            reason_for(decision, "FC-QUICK"), router.NOT_SELECTED_HIGHER_COST_ON_TIE
        )


class TestWhoGetsTheBlameForABrokenPromise(unittest.TestCase):
    """
    THE BUG
        There used to be one spare-time number per order, and its own notes said a
        negative value meant the promise was impossible before we ever saw the
        order.

        In our own sample batch that was untrue. One order starts the day with
        exactly enough time, loses its only fast warehouse to an order we handled
        first, and was then filed as "never possible". The engine was blaming the
        shop for its own decision - and it read as a perfectly reasonable report.

    THE FIX
        Two numbers instead of one. The spare time before the batch started, and
        the spare time when we actually got to the order. On their own each is
        ambiguous. Together they say which of three things happened:

            never possible                 the shop promised a date nobody could hit
            an earlier order took the stock  we had the time and spent it elsewhere
            our own rule chose a late option we walked past a warehouse that worked

        Only the first one is somebody else's fault.
    """

    def test_a_promise_nobody_could_have_kept_is_not_blamed_on_us(self):
        # Promised Tuesday, and the fastest thing we have arrives Thursday. There
        # was never a version of this day where we won.
        network = make_network(
            warehouses={"FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-SLOW": (400, 3)},
        )
        result = router.route_batch(
            [make_order(promised="2026-08-18", order_id="ORD-DOOMED")],
            network, router.fresh_inventory(network),
        )
        decision = result["decisions"][0]

        self.assertEqual(decision["status"], router.STATUS_ROUTED_LATE)
        self.assertEqual(
            decision["promise_lost_because"], router.LOST_NEVER_POSSIBLE
        )
        self.assertFalse(decision["promise_was_keepable_at_batch_start"])
        self.assertEqual(result["summary"]["promises_we_broke_ourselves"], 0)

    def test_a_promise_lost_to_an_earlier_order_says_exactly_that(self):
        # THIS IS THE CASE THE OLD CODE GOT WRONG.
        #
        # One fast warehouse with a single unit, and it is also the cheapest. Work
        # down the list and the relaxed customer takes it, leaving the customer who
        # needed it with nothing fast enough.
        #
        # The tight order had exactly enough time when the day started. We spent
        # it on somebody else. That is ours.
        network = make_network(
            warehouses={"FC-FAST": {"SKU-A": 1}, "FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-FAST": (400, 1), "FC-SLOW": (700, 3)},
        )
        orders = [
            make_order(promised="2026-08-20", order_id="ORD-LOOSE"),
            make_order(promised="2026-08-18", order_id="ORD-TIGHT"),
        ]
        result = router.route_batch(
            orders, network, router.fresh_inventory(network),
            sequencing=router.SEQUENCE_FIRST_COME_FIRST_SERVED,
        )
        tight = {d["order_id"]: d for d in result["decisions"]}["ORD-TIGHT"]

        # The two numbers that together tell the truth. Either one on its own is
        # misleading, and the old code only had the second.
        self.assertEqual(
            tight["slack_days_at_batch_start"], 0,
            "It was possible this morning - zero spare days, but possible.",
        )
        self.assertEqual(
            tight["slack_days_when_routed"], -2,
            "By the time we got to it, it was not possible any more.",
        )
        self.assertTrue(tight["promise_was_keepable_at_batch_start"])

        self.assertEqual(
            tight["promise_lost_because"], router.LOST_TO_EARLIER_ORDERS
        )
        self.assertEqual(result["summary"]["lost_never_possible"], 0)
        self.assertEqual(result["summary"]["promises_we_broke_ourselves"], 1)

    def test_the_engine_names_itself_when_its_own_rule_broke_the_promise(self):
        # The most uncomfortable of the three, and the one that proves the blame
        # is not rigged in our favour.
        #
        # An on-time warehouse was sitting there with stock in it, and the
        # cheapest-wins rule walked straight past it. Nobody took anything, no
        # promise was impossible. We simply chose the late option.
        network = make_network(
            warehouses={"FC-NEAR": {"SKU-A": 10}, "FC-FAR": {"SKU-A": 10}},
            lanes={"FC-NEAR": (900, 1), "FC-FAR": (400, 5)},
        )
        result = router.route_batch(
            [make_order(promised="2026-08-19", order_id="ORD-SOLD-OUT-CHEAP")],
            network, router.fresh_inventory(network),
            policy=router.POLICY_COST_FIRST,
        )
        decision = result["decisions"][0]

        self.assertEqual(decision["selected_fc"], "FC-FAR")
        self.assertEqual(decision["status"], router.STATUS_ROUTED_LATE)

        # It was keepable at the start AND still keepable when we routed it. The
        # only thing that broke it was our own rule.
        self.assertTrue(decision["promise_was_keepable_at_batch_start"])
        self.assertGreaterEqual(decision["slack_days_when_routed"], 0)
        self.assertEqual(
            decision["promise_lost_because"], router.LOST_TO_OUR_OWN_RULE
        )

    def test_a_kept_promise_has_nobody_to_blame(self):
        # An empty blame field on a successful order, so the counts in the summary
        # cannot be quietly inflated by orders that went fine.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 10}},
            lanes={"FC-ONE": (400, 1)},
        )
        result = router.route_batch(
            [make_order(promised="2026-08-21", order_id="ORD-FINE")],
            network, router.fresh_inventory(network),
        )
        decision = result["decisions"][0]

        self.assertEqual(decision["status"], router.STATUS_ROUTED)
        self.assertIsNone(decision["promise_lost_because"])
        self.assertEqual(result["summary"]["promises_we_broke_ourselves"], 0)


class TestTheSummaryDoesNotOverstateHowWellWeDid(unittest.TestCase):
    """
    THE BUG
        The totals page printed "Every promise in this batch was kept" on a batch
        that contained a rejected order.

        It was not a counting error. Every number on the page was right. The blame
        breakdown is only ever filled in for orders we actually tried to route, so
        a rejected order can never appear in it - which made the page honest about
        routing and quietly misleading about customers. Somebody was promised a
        date for that order too, and they were getting nothing.

        Every arithmetic check in the engine stayed silent, because there was
        nothing wrong with the arithmetic.
    """

    def make_batch(self, orders):
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 10}},
            lanes={"FC-ONE": (400, 1)},
        )
        result = router.route_batch(
            orders, network, router.fresh_inventory(network)
        )
        text = "\n".join(
            router.format_batch_summary(result["summary"], result["sequence"])
        )
        return result["summary"], text

    def test_it_does_not_claim_a_clean_sweep_when_an_order_was_rejected(self):
        summary, text = self.make_batch([
            make_order(promised="2026-08-21", order_id="ORD-FINE-1"),
            make_order(promised="2026-08-21", order_id="ORD-FINE-2"),
            make_order(promised="2026-08-21", order_id="ORD-BROKEN", quantity=0),
        ])

        # The batch really is the situation that caused the bug: nothing shipped
        # late, nothing unshippable, one order thrown out.
        self.assertEqual(summary["routed_late"], 0)
        self.assertEqual(summary["unroutable"], 0)
        self.assertEqual(summary["invalid"], 1)

        self.assertNotIn("Every promise in this batch was kept", text)
        self.assertIn("never got that far", text)

    def test_it_still_says_so_when_the_sweep_really_is_clean(self):
        # The other half of the fix, and the reason this test exists at all.
        #
        # The lazy way to fix the bug above is to delete the sentence. Then the
        # engine can never overstate itself, because it can never state anything.
        # A batch where every promise genuinely was kept should say so plainly.
        summary, text = self.make_batch([
            make_order(promised="2026-08-21", order_id="ORD-FINE-1"),
            make_order(promised="2026-08-21", order_id="ORD-FINE-2"),
        ])

        self.assertEqual(summary["invalid"], 0)
        self.assertEqual(summary["promises_kept"], 2)
        self.assertIn("Every promise in this batch was kept", text)

    def test_the_blame_heading_says_routed_promises_not_all_promises(self):
        # Part of the same fix. The heading counts ROUTED promises, because that
        # is the only set of orders the breakdown underneath it can describe.
        # "Why 3 promises were not kept" over a list that only accounts for two of
        # them is where the original lie came from.
        network = make_network(
            warehouses={"FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-SLOW": (400, 3)},
        )
        result = router.route_batch(
            [make_order(promised="2026-08-18", order_id="ORD-LATE")],
            network, router.fresh_inventory(network),
        )
        text = "\n".join(
            router.format_batch_summary(result["summary"], result["sequence"])
        )

        self.assertIn("ROUTED PROMISE", text)


class TestTheBillSentenceIsTrueWhereverItIsPrinted(unittest.TestCase):
    """
    THE BUG, WHICH WAS ACTUALLY TWO BUGS IN ONE SENTENCE
        The bill paragraph used to end "each order block above says what its own
        share of that bought". True when the totals follow a full set of order
        blocks. False the moment a caller prints the totals on their own - which is
        exactly what the report does for the two runs it only wants to measure.
        The page pointed at detail that was never printed.

        Fixing that exposed the worse half. The same sentence named two reasons the
        extra money might have been spent. There are three, and the missing one was
        the largest share of the money in our own sample batch. "Spent to protect
        promised dates" was the tidier sentence and misdescribed two thirds of it.

    THE RULE THIS TAUGHT
        A formatting function cannot know what else got printed around it, so it
        must not refer to it.

    These tests use the REAL data, because the three-way split of the money is a
    fact about the shipped prototype rather than about a made-up world.
    """

    def setUp(self):
        network = router.load_network(DATA_DIR)
        result = router.route_batch(
            router.load_orders(DATA_DIR), network,
            router.fresh_inventory(network),
        )
        self.summary = result["summary"]
        self.text = "\n".join(
            router.format_batch_summary(self.summary, result["sequence"])
        )

    def test_the_money_really_is_made_of_all_three_reasons(self):
        # If this ever stops being true, the sentence tested below becomes wrong,
        # so the split is checked before the words are.
        network = router.load_network(DATA_DIR)
        result = router.route_batch(
            router.load_orders(DATA_DIR), network,
            router.fresh_inventory(network),
        )
        reasons = {}
        for d in result["decisions"]:
            if d.get("premium_over_cheapest_cents", 0) > 0:
                reasons[d["premium_reason"]] = (
                    reasons.get(d["premium_reason"], 0)
                    + d["premium_over_cheapest_cents"]
                )

        self.assertEqual(
            set(reasons),
            {
                router.PREMIUM_PROTECT_PROMISE,
                router.PREMIUM_REDUCE_LATENESS,
                router.PREMIUM_PRESERVE_INVENTORY,
            },
            "All three reasons must be in play, or the sentence naming three of "
            "them is describing money that was not spent.",
        )
        self.assertEqual(
            sum(reasons.values()), self.summary["premium_over_cheapest_cents"]
        )

    def test_the_sentence_names_all_three_and_points_at_nothing(self):
        self.assertGreater(self.summary["premium_over_cheapest_cents"], 0)

        # All three reasons, in plain words rather than codes.
        self.assertIn("promised date", self.text)
        self.assertIn("less late", self.text)
        self.assertIn("empty shelf", self.text)

        # And no pointing at things this function cannot see.
        self.assertNotIn("order block above", self.text)


class TestTheComparisonNeverArguesWithItsOwnTable(unittest.TestCase):
    """
    THE BUG
        The check for "this run beat that one on everything" looked at four
        measures. The table printed seven. Total days late was in the table and
        not in the check.

        So the cost-first run came out "at least as good" as the first-come-first-
        served run - they tie on every count and cost-first is cheaper - and the
        page printed "there is no case for run B" four lines under a table row
        reading: total days late, B 2, C 4.

        A page that argues with a table printed above it is worse than one that
        says nothing.

    THE RULE THIS TAUGHT
        If a measure is worth printing, it is worth counting. Anything the table
        shows has to be something the conclusion looked at.
    """

    # Which way is good, for every row the comparison table prints.
    MORE_IS_BETTER = {"promises_kept"}

    def test_a_cheaper_run_does_not_win_when_it_leaves_people_waiting_longer(self):
        # The exact pair of runs that produced the wrong sentence, as plain
        # numbers. Identical on every count. C is $8.40 cheaper and leaves
        # customers waiting twice as long in total.
        b = {"promises_kept": 5, "routed_late": 2, "unroutable": 2,
             "total_days_late": 2, "shipping_cost_cents": 4770}
        c = {"promises_kept": 5, "routed_late": 2, "unroutable": 2,
             "total_days_late": 4, "shipping_cost_cents": 3930}

        # Neither run beats the other. There is a real tradeoff here, and the
        # honest answer is to describe it rather than declare a winner.
        self.assertFalse(
            router._dominates(c, b),
            "This is the bug: cheaper is not the same as better when the cheap "
            "run leaves customers waiting two extra days.",
        )
        self.assertFalse(router._dominates(b, c))

    def test_it_can_still_declare_a_winner_when_there_really_is_one(self):
        # The mirror test again. A check that always says no is useless, and would
        # throw away the best result in the prototype - the run that keeps an
        # extra promise for exactly the same money.
        worse = {"promises_kept": 5, "routed_late": 2, "unroutable": 2,
                 "total_days_late": 2, "shipping_cost_cents": 4770}
        better = {"promises_kept": 6, "routed_late": 1, "unroutable": 2,
                  "total_days_late": 1, "shipping_cost_cents": 4770}

        self.assertTrue(router._dominates(better, worse))
        self.assertFalse(router._dominates(worse, better))

    def test_no_claimed_winner_loses_on_any_row_of_the_real_table(self):
        # A wider version of the same idea, run against the three real runs the
        # report prints.
        #
        # This one does not hard-code any numbers. It reads the table the report
        # prints, and for every run claimed to have beaten another, checks that
        # claim against every single printed row. If somebody adds an eighth row
        # to the table and forgets to count it, this test fails - which is the
        # whole class of mistake the original bug belonged to.
        network = router.load_network(DATA_DIR)
        orders = router.load_orders(DATA_DIR)
        settings = {
            "A": (router.POLICY_PROMISE_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST),
            "B": (router.POLICY_PROMISE_FIRST,
                  router.SEQUENCE_FIRST_COME_FIRST_SERVED),
            "C": (router.POLICY_COST_FIRST, router.SEQUENCE_LOWEST_SLACK_FIRST),
        }
        summaries = {
            label: router.route_batch(
                orders, network, router.fresh_inventory(network),
                policy=policy, sequencing=sequencing,
            )["summary"]
            for label, (policy, sequencing) in settings.items()
        }

        for winner in summaries:
            for loser in summaries:
                if winner == loser:
                    continue
                if not router._dominates(summaries[winner], summaries[loser]):
                    continue
                for _, key, _ in router.COMPARISON_ROWS:
                    with self.subTest(f"{winner} beats {loser} on {key}"):
                        mine = summaries[winner].get(key, 0)
                        theirs = summaries[loser].get(key, 0)
                        if key in self.MORE_IS_BETTER:
                            self.assertGreaterEqual(mine, theirs)
                        else:
                            self.assertLessEqual(mine, theirs)


# ---------------------------------------------------------------------------
# PIECE 5 - THE DATE MATHS
# ---------------------------------------------------------------------------
#
# WHY THIS GETS A PIECE OF ITS OWN
#     Every promise in this engine is a date. Whether we kept it is one date
#     compared against another. So if the date maths is out by a day, every single
#     number in the report is wrong together - the promises kept, the days late,
#     the spare time, which run won - and they are all wrong in the same
#     direction, which means nothing looks odd. There is no cross-check left,
#     because everything downstream is built on the same mistake.
#
#     It is also the easiest thing in the whole project to get wrong, because
#     "add three days" is not one question. It is three:
#
#         does the parcel go out today or tomorrow
#         do Saturday and Sunday count
#         and when we say two days late, late by whose calendar
#
# THE ONE IDEA THESE TESTS ARE ALL ABOUT
#     This engine runs two different clocks on purpose.
#
#         Travel time is counted in WORKING days, because vans do not drive at
#         weekends.
#
#         Lateness is counted in ORDINARY calendar days, because the customer is
#         sat at home all weekend waiting, and they feel every one of those days.
#
#     Those two clocks give different answers for the same parcel, and that is not
#     a bug, it is the point. A parcel that was due Friday and turns up Monday is
#     one working day late and three real days late. Telling that customer they are
#     "one day late" would be dressing up the truth.
#
#     Most of the tests below exist to pin down which clock is being used where.
#
# ONE TEST BELOW DELIBERATELY LOCKS IN A KNOWN SHORTCUT
#     Public holidays are ignored. That is a real weakness, it is named in the
#     README, and there is a test proving it happens rather than a comment hoping
#     somebody notices. When holidays are added, that test should fail. It is
#     written so that the failure explains itself.


class TestWhenTheParcelLeavesTheBuilding(unittest.TestCase):
    """
    An order is not packed the moment it is placed. It goes out the next working
    day. That single rule decides the starting point for every arrival date in the
    engine, so if it slips by a day, every promise slips with it.
    """

    def test_three_different_order_days_all_ship_on_the_same_monday(self):
        # The interesting part of the weekend rule. An order placed Friday
        # afternoon, one placed Saturday and one placed Sunday are all sat in the
        # same queue on Monday morning. Nothing happens at the weekend.
        for placed in ("2026-08-14", "2026-08-15", "2026-08-16"):
            with self.subTest(placed=placed):
                self.assertEqual(
                    router.next_business_day(date.fromisoformat(placed)),
                    date(2026, 8, 17),
                )

    def test_a_weekday_order_still_waits_until_the_next_day(self):
        # It always moves forward at least one day, even when today is already a
        # working day. A Monday order ships Tuesday, not that same Monday.
        #
        # Worth its own test because the obvious way to write this function is
        # "move forward until you land on a weekday", and that version leaves a
        # Monday order shipping on the Monday it arrived - which quietly hands the
        # engine a free extra day on four orders out of five.
        for placed, expected in (
            ("2026-08-17", "2026-08-18"),   # Monday    -> Tuesday
            ("2026-08-18", "2026-08-19"),   # Tuesday   -> Wednesday
            ("2026-08-19", "2026-08-20"),   # Wednesday -> Thursday
            ("2026-08-20", "2026-08-21"),   # Thursday  -> Friday
        ):
            with self.subTest(placed=placed):
                self.assertEqual(
                    router.next_business_day(date.fromisoformat(placed)),
                    date.fromisoformat(expected),
                )

    def test_which_days_count_as_the_weekend_is_read_from_the_constant(self):
        # Every date in this file was worked out by hand from a Saturday-Sunday
        # weekend. If that ever changes, the arithmetic in about twenty tests
        # silently stops matching the engine, so it is checked once, here, out loud.
        self.assertEqual(
            router.WEEKEND_DAYS, (5, 6),
            "Saturday and Sunday. Every date in this test file is worked out from "
            "that, so changing it means rechecking all of them by hand.",
        )


class TestHowLongTheJourneyTakes(unittest.TestCase):
    """
    Travel time is in working days, so a journey that runs over a weekend takes
    two extra days on the calendar without taking any extra days of driving.
    """

    def test_the_arrival_date_for_every_journey_length_out_of_one_monday(self):
        # The whole table in one test, because these five numbers are what every
        # other test in this file quietly depends on. If this table is right, the
        # made-up worlds elsewhere are all measuring what they claim to.
        #
        # Note the jump between 4 and 5. Four working days is Friday. Five is
        # Monday - three days later on the calendar, one more day of driving.
        monday = date(2026, 8, 17)
        for days, expected in (
            (1, "2026-08-18"),   # Tuesday
            (2, "2026-08-19"),   # Wednesday
            (3, "2026-08-20"),   # Thursday
            (4, "2026-08-21"),   # Friday
            (5, "2026-08-24"),   # Monday, the weekend skipped
            (10, "2026-08-31"),  # two clear weeks
        ):
            with self.subTest(working_days=days):
                self.assertEqual(
                    router.add_business_days(monday, days),
                    date.fromisoformat(expected),
                )

    def test_a_journey_of_no_days_lands_on_the_day_it_left(self):
        # A guard against being out by one. The obvious way to write a loop like
        # this is to step forward first and check afterwards, which turns "add
        # nothing" into "add one day" and pulls every arrival date in the engine
        # forward by a day.
        monday = date(2026, 8, 17)
        self.assertEqual(router.add_business_days(monday, 0), monday)

    def test_a_friday_departure_with_a_one_day_journey_lands_on_monday(self):
        # The smallest case where the weekend actually bites. One day of driving,
        # three days on the calendar.
        self.assertEqual(
            router.add_business_days(date(2026, 8, 21), 1), date(2026, 8, 24)
        )


class TestABadlyWrittenDateDoesNotBringDownTheBatch(unittest.TestCase):
    """
    Reading a date either works or hands back nothing. It never throws.

    WHY THAT MATTERS MORE THAN IT SOUNDS
        A crash here would take out the whole batch. Nine perfectly good orders
        would go unrouted because the tenth had a typo in it, and the error on
        screen would be about date formats rather than about the one order that
        needs fixing. So a bad date has to come back as "no date", which the
        checking step then turns into a clear rejection for that order alone.
    """

    def test_a_proper_date_is_read_as_a_proper_date(self):
        self.assertEqual(router.parse_date("2026-08-14"), date(2026, 8, 14))

    def test_every_kind_of_rubbish_comes_back_as_nothing(self):
        for rubbish, what_is_wrong in (
            ("2026-08-32", "there is no 32nd of August"),
            ("2026-13-01", "there is no 13th month"),
            ("14/08/2026", "the right day, written the British way round"),
            ("next Tuesday", "words instead of a date"),
            ("", "the field was left empty"),
            (None, "the field was missing altogether"),
            (20260814, "a number, not text"),
        ):
            with self.subTest(what_is_wrong):
                self.assertIsNone(router.parse_date(rubbish))


class TestTheTwoClocks(unittest.TestCase):
    """
    THE HEADLINE OF THIS WHOLE PIECE.

    Travel time is counted in working days. Lateness is counted in ordinary
    calendar days. The tests below are built so that those two clocks give
    different answers, which is the only way to prove which one is being used.
    """

    def test_a_parcel_due_friday_and_landing_monday_is_three_days_late(self):
        # THE CASE THAT SEPARATES THE TWO CLOCKS.
        #
        #     Parcel leaves Monday 17 August.
        #     The journey is 5 working days, so it lands Monday 24 August.
        #     We promised Friday 21 August.
        #
        # Count in working days and it is 1 day late.
        # Count in real days and it is 3 days late.
        #
        # The engine has to say 3. That customer sat in all weekend waiting for
        # something we said would be there on Friday. "One working day late" is
        # technically arguable and it is not what happened to them.
        network = make_network(
            warehouses={"FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-SLOW": (400, 5)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["ship_date"], "2026-08-17")
        self.assertEqual(decision["expected_delivery_date"], "2026-08-24")
        self.assertEqual(decision["status"], router.STATUS_ROUTED_LATE)
        self.assertFalse(decision["promise_met"])
        self.assertEqual(
            decision["days_late"], 3,
            "Three real days, not one working day. If this ever reads 1, the "
            "engine has started counting lateness on the delivery van's calendar "
            "instead of the customer's.",
        )

    def test_landing_exactly_on_the_promised_day_counts_as_keeping_the_promise(self):
        # We said "by the 21st". The 21st is by the 21st. Getting this wrong by a
        # single day would turn a batch of kept promises into a batch of broken
        # ones, and the engine would start paying extra money to fix a problem
        # that was never there.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 10}},
            lanes={"FC-ONE": (400, 4)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["expected_delivery_date"], "2026-08-21")
        self.assertTrue(decision["promise_met"])
        self.assertEqual(decision["days_late"], 0)
        self.assertEqual(decision["status"], router.STATUS_ROUTED)

    def test_arriving_early_is_recorded_exactly_the_same_as_arriving_on_time(self):
        # There is no credit for being early, and the engine must not invent any.
        # We promised a date, not a race. A parcel a day early and a parcel bang on
        # the day are both simply "promise kept, nothing late".
        #
        # This is the date-maths half of a rule that shows up all over the engine:
        # speed we did not need is worth nothing, so we should never pay for it.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 10}},
            lanes={"FC-ONE": (400, 3)},
        )
        decision = route_one(make_order(promised="2026-08-21"), network)

        self.assertEqual(decision["expected_delivery_date"], "2026-08-20")
        self.assertTrue(decision["promise_met"])
        self.assertEqual(decision["days_late"], 0)

    def test_the_batch_total_adds_up_real_days_not_working_days(self):
        # The clock has to survive being added up. It is no good getting each
        # order right and then totalling the wrong thing on the summary page,
        # because the totals page is what the comparison between runs is built on.
        #
        #     Both parcels leave Monday 17 and land Monday 24.
        #     One was promised Friday 21   -> 3 real days late (1 working day)
        #     One was promised Thursday 20 -> 4 real days late (2 working days)
        #
        # Real days total 7. Working days would total 3.
        network = make_network(
            warehouses={"FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-SLOW": (400, 5)},
        )
        result = router.route_batch(
            [
                make_order(promised="2026-08-21", order_id="ORD-FRIDAY"),
                make_order(promised="2026-08-20", order_id="ORD-THURSDAY"),
            ],
            network, router.fresh_inventory(network),
        )

        self.assertEqual(result["summary"]["routed_late"], 2)
        self.assertEqual(
            result["summary"]["total_days_late"], 7,
            "Seven real days. Three would mean the summary quietly switched to "
            "the van's calendar somewhere between the order and the total.",
        )

    def test_every_warehouse_ships_on_the_same_day_so_only_the_journey_differs(self):
        # A shortcut worth having a test for, because it is easy to forget it is a
        # shortcut. Every warehouse in this prototype gets the parcel out on the
        # same day, so the ONLY thing separating two arrival dates is the length of
        # the journey.
        #
        # A real network would not work like this. Cut-off times differ, a
        # warehouse three hours behind may miss today's van, and a busy site may be
        # a day behind on picking. All of that would change the departure day and
        # none of it is modelled here.
        network = make_network(
            warehouses={"FC-QUICK": {"SKU-A": 10}, "FC-SLOW": {"SKU-A": 10}},
            lanes={"FC-QUICK": (900, 1), "FC-SLOW": (400, 5)},
        )
        decision = route_one(make_order(promised="2026-08-18"), network)

        arrivals = {decision["selected_fc"]: decision["expected_delivery_date"]}
        for alternative in decision["alternatives"]:
            arrivals[alternative["fc_id"]] = alternative["expected_delivery_date"]

        self.assertEqual(decision["ship_date"], "2026-08-17")
        self.assertEqual(
            arrivals, {"FC-QUICK": "2026-08-18", "FC-SLOW": "2026-08-24"}
        )


class TestSpareTimeIsMeasuredOnTheCustomersCalendarToo(unittest.TestCase):
    """
    Spare time is the promised date minus the soonest date we could manage. It is
    counted in ordinary days, like lateness.

    THIS IS A KNOWN ROUGH EDGE, NOT A CLEAN DECISION
        It means spare time slightly overstates how much room we really have,
        because some of those spare days are weekends and no van moves on them.
        Three days of spare time across a weekend is really only one working day of
        room.

        It is left as it is for a reason. Spare time is only used to decide which
        order to deal with FIRST, and for that job all that matters is which order
        is tightest - the ranking, not the exact number. Every order in a batch is
        measured the same way, so the ranking comes out right either way.

        The test below pins the current behaviour on purpose, with the numbers
        written out, so this stays a decision somebody made rather than something
        nobody noticed. It is named in the README as a shortcut.
    """

    def test_spare_time_across_a_weekend_is_counted_in_real_days(self):
        #     Soonest we could manage is Friday 21 August (4 working days out).
        #     We promised Monday 24 August.
        #
        # Real days of room: 3.
        # Working days of room: 1. Only Monday itself.
        #
        # The engine says 3.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 10}},
            lanes={"FC-ONE": (400, 4)},
        )
        inventory = router.fresh_inventory(network)
        order = make_order(promised="2026-08-24")

        self.assertEqual(
            router.earliest_possible_delivery(order, network, inventory),
            date(2026, 8, 21),
        )
        self.assertEqual(
            router.routing_slack_days(order, network, inventory), 3,
            "Three real days of room, of which only one is a working day. The "
            "overstatement is deliberate and is written up in the README.",
        )

    def test_an_order_nobody_can_ship_has_no_spare_time_rather_than_none_left(self):
        # There is a difference between "no room at all" and "there is no number
        # here". An order nobody can fill has no delivery date to measure from, so
        # spare time is nothing rather than zero.
        #
        # Zero would be a lie in the worst possible direction: it would sort as
        # merely tight and push a hopeless order to the front of the queue, ahead
        # of orders we could actually still save.
        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 1}},
            lanes={"FC-ONE": (400, 1)},
        )
        inventory = router.fresh_inventory(network)
        order = make_order(promised="2026-08-24", quantity=99)

        self.assertIsNone(
            router.earliest_possible_delivery(order, network, inventory)
        )
        self.assertIsNone(router.routing_slack_days(order, network, inventory))


class TestTheHolidayShortcutIsRealAndThisTestProvesIt(unittest.TestCase):
    """
    Public holidays are ignored. Weekends are skipped, holidays are not.

    WHY THERE IS A TEST FOR SOMETHING THAT IS WRONG
        Because a comment saying "we ignore holidays" is a promise that somebody
        read it. A failing test is a promise that somebody has to deal with it.

        This is the shortcut in this engine most likely to break a real promise,
        and it breaks it in the quiet direction: the engine works out a delivery
        date, tells the customer it is safe, and no van drives that day. Every
        number in the report still adds up perfectly. Nothing looks wrong.

    WHAT TO DO WHEN HOLIDAYS GET ADDED
        This test will fail. That is correct and it is the whole point of it
        existing. Delete it and write the opposite one: that a parcel due to land
        on Christmas Day gets pushed to the next working day, and that any promise
        which depended on that day is reported as broken up front rather than
        discovered later.
    """

    def test_a_parcel_can_be_promised_for_christmas_day_and_nobody_objects(self):
        # Order placed Wednesday 23 December 2026.
        # Ships Thursday 24 December.
        # One working day of travel, so it lands Friday 25 December.
        #
        # Christmas Day. The engine calls that a working day, counts the promise as
        # kept, and moves on quite happily.
        self.assertEqual(
            date(2026, 12, 25).strftime("%A"), "Friday",
            "The maths below only makes the point if Christmas Day 2026 really is "
            "a weekday. It is.",
        )

        network = make_network(
            warehouses={"FC-ONE": {"SKU-A": 10}},
            lanes={"FC-ONE": (400, 1)},
        )
        decision = route_one(
            make_order(
                promised="2026-12-25",
                order_date="2026-12-23",
            ),
            network,
        )

        self.assertEqual(decision["ship_date"], "2026-12-24")
        self.assertEqual(decision["expected_delivery_date"], "2026-12-25")
        self.assertTrue(
            decision["promise_met"],
            "The engine believes it delivered on Christmas Day. It did not. This "
            "is the holiday shortcut, and when holidays are added this assertion "
            "is the one that should fail.",
        )
        self.assertEqual(decision["status"], router.STATUS_ROUTED)


if __name__ == "__main__":
    unittest.main()
