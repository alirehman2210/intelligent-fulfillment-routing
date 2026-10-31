"""
WHAT THIS FILE IS FOR
    This is the order routing engine. Its one job is to answer a single question
    for each customer order: which warehouse should this order ship from?

    It answers that using four things - where the stock is, what shipping costs,
    how long shipping takes, and the delivery date we promised the shopper.

WHAT ROLE IT PLAYS IN THE PROTOTYPE
    This file is the brain. It holds all the decision making, and it holds the
    rules about what a good decision even means. Every judgement call the
    prototype makes happens somewhere in here.

    It is deliberately kept separate from main.py. This file decides things but
    never prints anything. main.py prints things but never decides anything.
    That split matters, because it means you can test the decisions on their own
    without having to read printed text to work out what happened.

WHAT IT READS
    Three data files in the data folder:
      - data/fulfillment_centers.json  the warehouses and how much stock each holds
      - data/shipping_routes.json      which area each city is in, plus the price
                                       and delivery time for every warehouse-to-area
                                       route
      - data/orders.json               the batch of customer orders to work through

WHAT IT RETURNS OR PRINTS
    It prints nothing at all. Every function here returns plain Python
    dictionaries and lists.

    The main thing it returns is a "decision record" for each order: a dictionary
    holding which warehouse was picked, what it cost, when it will arrive, whether
    that keeps the promise, and - just as importantly - which other warehouses were
    considered and the reason each one was turned down.

    We keep the rejected options on purpose. A decision you cannot explain is not
    much use to a merchant, and "we picked Dallas" is a far weaker answer than
    "we picked Dallas because the cheaper option would have arrived a day late".

A NOTE ON HOW THE DATA IS SHAPED
    Everything in here is plain dictionaries and lists rather than custom classes.
    That is a deliberate choice for a prototype. The decision record needs to be
    saved as JSON anyway, so keeping it as a dictionary from start to finish means
    there is no converting back and forth, and what you read in this file is
    exactly what comes out the other end.

THIS FILE IS BUILT IN FIVE PARTS
    Part 1  Setting up: the rules, loading the data, checking orders, working out dates
    Part 2  Scoring: what would each warehouse cost, and when would it arrive
    Part 3  Choosing: picking one warehouse and writing down why
    Part 4  Batches: working out spare time, and routing many orders in a row
    Part 5  Explaining: turning a decision record into readable English
"""

import json
import textwrap
from datetime import date, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# PART 1 - SETTING UP
# ---------------------------------------------------------------------------


# --- The rules of the engine, in one place ---------------------------------
#
# These are the numbers and labels the whole engine runs on. They live up here
# together, rather than being scattered through the code, so that anyone can see
# what the engine believes without reading any logic. If a merchant wanted to
# argue with one of our choices, this is the block they would point at.

# How close in price two warehouses have to be before we stop caring about the
# difference and start caring about stock instead.
#
# Why this exists: imagine one warehouse is 30 cents cheaper but it is down to
# its last unit, while another has plenty. Taking the 30 cents means burning
# that last unit, and the next customer who really needs that warehouse then has
# nothing. Thirty cents is not worth that. So any warehouse priced within 50
# cents of the cheapest is treated as "the same price, near enough", and we pick
# between them on how much stock they have left instead.
#
# The 50 cent figure is invented for this prototype. It is not any real
# rule. In a real system a merchant would set it themselves, because how much a
# spare unit is worth depends entirely on what they sell.
COST_TIE_BAND_CENTS = 50

# Saturday and Sunday. Python numbers the days of the week from 0 for Monday, so
# 5 and 6 are the weekend. We skip these when counting delivery days, because
# warehouses and delivery vans mostly do not run then.
WEEKEND_DAYS = (5, 6)

# The four ways an order can end up.
STATUS_ROUTED = "ROUTED"                # sorted, and it will arrive on time
STATUS_ROUTED_LATE = "ROUTED_LATE"      # sorted, but it will arrive late
STATUS_UNROUTABLE = "UNROUTABLE"        # the order is fine, we just cannot fill it
STATUS_INVALID_ORDER = "INVALID_ORDER"  # the order itself does not make sense

# Why an order was rejected before we even tried to route it.
REASON_INVALID_QUANTITY = "INVALID_QUANTITY"        # asked for zero or fewer items
REASON_UNKNOWN_SKU = "UNKNOWN_SKU"                  # we do not sell that product
REASON_UNKNOWN_DESTINATION = "UNKNOWN_DESTINATION"  # we do not recognise that city
REASON_INVALID_PROMISE_DATE = "INVALID_PROMISE_DATE"  # promised before it was ordered
REASON_INVALID_DATE_FORMAT = "INVALID_DATE_FORMAT"  # the date is not a real date

# Why we could not fill an order that was perfectly valid.
REASON_OUT_OF_STOCK = "OUT_OF_STOCK"        # not enough of it anywhere in the company
REASON_SPLIT_REQUIRED = "SPLIT_REQUIRED"    # enough in total, but spread across warehouses

# Why we turned down a particular warehouse.
NOT_SELECTED_MISSES_PROMISE = "MISSES_PROMISE"    # it would arrive too late
NOT_SELECTED_MORE_DAYS_LATE = "MORE_DAYS_LATE"    # everything is late, this one is later
NOT_SELECTED_HIGHER_COST = "HIGHER_COST"          # too far above the cheapest to compete
NOT_SELECTED_LOWER_STOCK = "LOWER_STOCK"          # close enough on price, but less stock left
NOT_SELECTED_HIGHER_COST_ON_TIE = "HIGHER_COST_ON_TIE"  # same stock left, so the cheaper one won
NOT_SELECTED_SLOWER = "SLOWER"                    # same price and stock, but slower
NOT_SELECTED_NO_STOCK = "NO_STOCK"                # it cannot fill the order at all
NOT_SELECTED_TIE_BREAK = "TIE_BREAK_ON_FC_ID"     # dead equal, so we went alphabetical

# These are listed in the exact order the engine checks them, and that is not just
# tidiness. The reason we report has to be the reason that ACTUALLY knocked a
# warehouse out. If this list and the choosing rules ever drift apart, the engine
# starts printing things that are true-ish but misleading, which is worse than
# printing nothing at all.

# The two sets of rules we compare.
#
# "Promise first" is the one we actually believe in. "Cost first" exists purely so
# we have something to measure it against. Saying "protecting promises costs money"
# means nothing unless you can show how much money, and the only way to show that
# is to run the same orders the greedy way and compare the two bills.
POLICY_PROMISE_FIRST = "PROMISE_FIRST"
POLICY_COST_FIRST = "COST_FIRST"

# The two ways of deciding which order to deal with FIRST.
#
# This is a separate question from which warehouse to use, and it is easy to miss.
# When several orders want the same last unit, whoever gets looked at first gets
# the unit. So the running order is itself a decision the engine is making, whether
# it admits to it or not.
#
# "First come first served" is what you get by doing nothing - just work down the
# list. It feels fair and it is what most systems do by accident.
#
# "Lowest slack first" means deal with the order that has the least spare time
# first, because that order has the fewest ways out. An order with plenty of spare
# time can survive being pushed onto a slower warehouse. An order with none cannot.
SEQUENCE_LOWEST_SLACK_FIRST = "LOWEST_SLACK_FIRST"
SEQUENCE_FIRST_COME_FIRST_SERVED = "FIRST_COME_FIRST_SERVED"

# Why we spent more than the cheapest option available.
#
# This matters more than it looks. The whole point of recording this is so a
# merchant can see honestly where their money went, so the reason has to be true.
PREMIUM_NONE = "NONE"                          # we spent nothing extra
PREMIUM_PROTECT_PROMISE = "PROTECT_PROMISE"    # cheaper option would have been late
PREMIUM_REDUCE_LATENESS = "REDUCE_LATENESS"    # everything was late, we paid to be less late
PREMIUM_PRESERVE_INVENTORY = "PRESERVE_INVENTORY"  # cheaper option was down to its last units

# A note on that last reason. The spec only listed the first three. While working
# through the sample orders we hit a case none of them described: two warehouses
# were both on time, and the engine paid 40 cents more to avoid draining the
# nearly empty one. Calling that "protecting the promise" would have been a lie,
# because the cheaper warehouse would have kept the promise perfectly well. So we
# added a fourth reason rather than log something misleading. A report that lies
# is worse than no report at all.

# Who is actually at fault when a promise gets broken.
#
# A single order's record cannot tell these apart, because they all look the same
# from the inside: late parcel, no time left. Only by comparing against the spare
# time the order had before the batch started can we say which one it was. And the
# difference matters enormously - one of these means the shop should fix its
# checkout, and the other two mean we should fix our engine.
LOST_NEVER_POSSIBLE = "NEVER_POSSIBLE"
# The promise could not be kept even with full shelves and our best warehouse.
# Somebody promised a date we were never going to hit. Not our fault.

LOST_TO_EARLIER_ORDERS = "EARLIER_ORDERS_TOOK_THE_STOCK"
# There was enough time when the day started. Another order we handled first took
# the only warehouse that could have made it. Our fault, and the fix is sequencing.

LOST_TO_OUR_OWN_RULE = "OUR_RULE_CHOSE_A_LATE_OPTION"
# An on-time warehouse was sitting right there with stock in it, and our rules
# picked a different one anyway. Our fault, and the fix is the rules. This is what
# chasing the cheapest price looks like when you write it down honestly.

# The three areas of the country we group cities into.
ZONES = ("WEST", "CENTRAL", "EAST")


# --- Loading the data ------------------------------------------------------


def load_network(data_dir="data"):
    """
    Reads the three data files and returns everything the engine needs as one
    dictionary.

    Takes: the folder the data files live in.
    Gives back: a dictionary with these keys:
        fulfillment_centers  the list of warehouses, straight from the file
        destination_zones    a lookup of city name -> area, e.g. "Boston, MA" -> "EAST"
        lanes                a lookup of (warehouse, area) -> price and delivery time
        known_skus           the set of products we sell
        starting_inventory   how much stock each warehouse began with

    Why lanes get reshaped into a lookup:
        In the file, lanes are stored as a list of 12 separate entries. That is
        easy for a human to read but awkward for the engine, which needs to ask
        "what does Dallas to WEST cost?" over and over. Searching a list every
        time would mean writing the same loop in several places. So we turn it
        into a lookup table once, here, keyed on the warehouse and area together.
        After this, answering that question is a single line anywhere in the code.

    Why we keep a separate copy of the starting stock:
        The engine reduces stock as it assigns orders, which means the numbers
        change while a batch runs. But we need the original numbers for two
        reasons. First, we run the same batch several times with different rules
        to compare them, and every run has to start from the same stock or the
        comparison is meaningless. Second, at the end we check that nothing went
        missing - stock we started with should equal stock we used plus stock
        left over. Neither is possible if the only copy has already been changed.
    """
    data_path = Path(data_dir)

    with open(data_path / "fulfillment_centers.json") as f:
        fc_file = json.load(f)
    with open(data_path / "shipping_routes.json") as f:
        routes_file = json.load(f)

    fulfillment_centers = fc_file["fulfillment_centers"]

    # Build the lane lookup. The key is a pair - (warehouse id, area) - because a
    # lane is only meaningful as a combination of the two. Dallas on its own has
    # no single price; Dallas-to-WEST does.
    lanes = {}
    for lane in routes_file["lanes"]:
        key = (lane["fc_id"], lane["destination_zone"])
        lanes[key] = {
            "shipping_cost_cents": lane["shipping_cost_cents"],
            "transit_days": lane["transit_days"],
        }

    # Work out which products we sell by looking at what the warehouses stock.
    #
    # This is a shortcut worth being honest about. We have no separate product
    # list file, so the warehouse stock records are the only place product names
    # appear. A product counts as real if at least one warehouse mentions it,
    # even with zero units - "we keep none here today" still tells us the product
    # exists. A real system would have a proper product catalogue, and would be
    # able to tell the difference between a product that does not exist and one
    # that is simply sold out everywhere.
    known_skus = set()
    for fc in fulfillment_centers:
        known_skus.update(fc["inventory"].keys())

    # Take our own copy of the stock numbers, so that changing them later cannot
    # accidentally change what the file said.
    starting_inventory = {
        fc["fc_id"]: dict(fc["inventory"]) for fc in fulfillment_centers
    }

    return {
        "fulfillment_centers": fulfillment_centers,
        "destination_zones": routes_file["destination_zones"],
        "lanes": lanes,
        "known_skus": known_skus,
        "starting_inventory": starting_inventory,
    }


def load_orders(data_dir="data"):
    """
    Reads the batch of customer orders.

    Takes: the folder the data files live in.
    Gives back: a list of order dictionaries, in the order they appear in the file.

    We keep the file's own order because one of the strategies we compare is
    "deal with them in the order they arrived", so the original sequence is
    itself a piece of information we need.
    """
    with open(Path(data_dir) / "orders.json") as f:
        return json.load(f)["orders"]


def fresh_inventory(network):
    """
    Hands back a brand new copy of the starting stock levels.

    Takes: the network dictionary from load_network().
    Gives back: a dictionary of warehouse -> product -> units.

    Call this at the start of every batch run. We run the same orders several
    times with different rules to see which rules do better, and each run must
    begin from identical stock. If run two started from whatever run one had left
    over, the comparison would be worthless - run two would look worse purely
    because it was handed emptier shelves.

    The copy is built fresh rather than handed out, so the caller can change it
    freely without ever touching the original.
    """
    return {
        fc_id: dict(stock) for fc_id, stock in network["starting_inventory"].items()
    }


# --- Working out dates -----------------------------------------------------
#
# Two small functions, but they decide whether every promise in the system is
# kept or broken, so they are worth getting exactly right.


def parse_date(text):
    """
    Turns a date written as text, like "2026-08-14", into a real date.

    Takes: a string in year-month-day form.
    Gives back: a date, or None if the text is not a usable date.

    We return None instead of crashing because a single bad row in a data file
    should not bring down a whole batch of orders. The order gets rejected with a
    clear reason and the engine carries on with the rest.
    """
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def next_business_day(from_date):
    """
    Finds the next working day after the date given.

    Takes: a date.
    Gives back: the next date that is a Monday to Friday.

    This is how we work out when a package actually leaves the building. An order
    placed today is not packed today - it goes out the next working day. So an
    order placed on Friday 14 August leaves on Monday 17 August, because the
    weekend is skipped.

    Note that it always moves forward at least one day, even if the date given is
    already a weekday. That is the point: an order placed on a Monday ships
    Tuesday, not the same Monday.
    """
    result = from_date + timedelta(days=1)
    while result.weekday() in WEEKEND_DAYS:
        result += timedelta(days=1)
    return result


def add_business_days(start_date, days):
    """
    Adds a number of working days to a date, skipping weekends.

    Takes: a date to start from, and how many working days to add.
    Gives back: the date you land on.

    This turns a shipping time into a real arrival date. If a package leaves on
    Monday 17 August and the route takes 3 working days, it arrives Thursday 20
    August - we count Tuesday, Wednesday, Thursday.

    Weekends are skipped because deliveries mostly do not happen then. Public
    holidays are NOT skipped, which is a shortcut. In a real system a public
    holiday would quietly push every delivery back a day and break promises the
    engine thought it was keeping, so this is one of the first things that would
    need fixing.
    """
    result = start_date
    remaining = days
    while remaining > 0:
        result += timedelta(days=1)
        if result.weekday() not in WEEKEND_DAYS:
            remaining -= 1
    return result


# --- Checking an order makes sense ----------------------------------------


def validate_order(order, network):
    """
    Checks an order for problems before we try to route it.

    Takes: one order dictionary, and the network dictionary from load_network().
    Gives back: None if the order is fine, or a short reason code saying what is
                wrong with it.

    There is an important distinction buried in here, and it is worth being clear
    about because it is easy to get wrong.

    A HARD order is not the same as a BAD order.

        Bad order:  the details themselves make no sense. Someone ordered minus
                    two items, or gave us a city we have never heard of. There is
                    nothing to route. We reject it.

        Hard order: the details are perfectly fine, we just cannot do a great job
                    of it. Somebody promised delivery by Tuesday when the fastest
                    possible option arrives Wednesday. That order still deserves
                    to be shipped as well as we can manage, and the shopper still
                    wants their parcel. We route it and flag that it will be late.

    Only bad orders get rejected here. Hard orders pass straight through this
    function and get dealt with properly by the routing logic.

    We report only the FIRST problem we find, not all of them. That keeps the
    output simple, and matches how the spec describes it. A real system aimed at
    a merchant fixing their data would be more helpful if it listed everything
    wrong at once.
    """
    # Did they order a sensible number of items? Zero or negative is meaningless.
    quantity = order.get("quantity")
    if not isinstance(quantity, int) or quantity <= 0:
        return REASON_INVALID_QUANTITY

    # Do we actually sell this product?
    if order.get("sku") not in network["known_skus"]:
        return REASON_UNKNOWN_SKU

    # Do we recognise the city?
    #
    # We refuse rather than guess. Guessing which area an unknown city belongs to
    # would mean inventing a shipping time, and then quoting the shopper a
    # delivery date built on a made-up number. Being honest that we do not know
    # is far safer than being confidently wrong.
    if order.get("destination") not in network["destination_zones"]:
        return REASON_UNKNOWN_DESTINATION

    # Are both dates real dates?
    order_date = parse_date(order.get("order_date"))
    promised_date = parse_date(order.get("promised_delivery_date"))
    if order_date is None or promised_date is None:
        return REASON_INVALID_DATE_FORMAT

    # Was the delivery promised for before the order was even placed? That is not
    # a hard promise, it is impossible, and it means something upstream is broken.
    if promised_date < order_date:
        return REASON_INVALID_PROMISE_DATE

    return None


# ---------------------------------------------------------------------------
# PART 2 - SCORING EVERY WAREHOUSE
# ---------------------------------------------------------------------------
#
# WHAT THIS PART IS FOR
#     Before the engine can choose a warehouse, it needs to know what choosing
#     each one would actually mean. So for one single order, this part walks
#     through all four warehouses and works out three things about each:
#
#         1. Can it fill the whole order from its own shelves?
#         2. How much would shipping cost from there?
#         3. What day would the parcel land on the doorstep?
#
#     Nothing gets chosen here. This part only gathers facts. Choosing happens in
#     Part 3. Keeping those two jobs apart is deliberate - it means we can look at
#     the facts on their own and check they are right, without the choosing rules
#     getting in the way and confusing things.
#
# WHY WE KEEP THE WAREHOUSES WE CANNOT USE
#     When a warehouse has no stock, the easy thing to do is drop it and move on.
#     We do not do that. We keep it in the list with a note saying it had no
#     stock.
#
#     The reason is that a merchant asking "why did my order ship from Dallas?"
#     is often really asking "why did it NOT ship from Chicago, which is round the
#     corner?" If we quietly threw Chicago away, we could not answer that. The
#     honest answer is "Chicago was empty", and we can only say that if we
#     remembered it.


def get_destination_zone(order, network):
    """
    Works out which part of the country an order is going to.

    Takes: one order, and the network from load_network().
    Gives back: "WEST", "CENTRAL" or "EAST".

    This is a simple lookup of the city name. It only works because the order has
    already been checked, so we know the city is one we recognise. If it were not,
    the order would have been rejected long before reaching here.
    """
    return network["destination_zones"][order["destination"]]


def units_available(fc_id, sku, inventory):
    """
    How many of a product a single warehouse is holding right now.

    Takes: the warehouse id, the product code, and the current stock levels.
    Gives back: a whole number, which may be zero.

    We use .get() with a fallback of zero because a warehouse that has never
    stocked a product simply has no entry for it. No entry and zero units mean
    the same thing to us here, so we treat them the same and avoid a crash.
    """
    return inventory.get(fc_id, {}).get(sku, 0)


def total_units_in_network(sku, inventory):
    """
    How many of a product the whole company is holding, added up across every
    warehouse.

    Takes: the product code, and the current stock levels.
    Gives back: a whole number.

    This is what lets us tell two very different failures apart. If we cannot
    fill an order, we need to know whether that is because the stock does not
    exist, or because it exists but is scattered in the wrong pattern. Those two
    problems have completely different answers, so we must not lump them together.
    """
    return sum(stock.get(sku, 0) for stock in inventory.values())


def diagnose_why_unfillable(order, inventory):
    """
    Explains why no single warehouse could fill an order.

    Takes: one order, and the current stock levels.
    Gives back: either "OUT_OF_STOCK" or "SPLIT_REQUIRED".

    Only call this once you already know no single warehouse can do the job. This
    function does not check that - it just explains it.

    THE TWO FAILURES ARE VERY DIFFERENT AND IT MATTERS

    Sold out. The company has fewer units than the customer asked for, full stop.
    Nothing clever will fix this. Somebody needs to make more of the product, or
    we need to tell the customer no. This is a supply problem.

    Needs splitting. We DO own enough units. They are just sitting in different
    buildings, and no single building has enough on its own. Say a customer wants
    3 units and we hold 2 in Dallas and 2 in Chicago. We own 4, which is plenty,
    but neither warehouse can send 3 by itself.

    We could fix that by sending two parcels from two warehouses. That is called
    splitting, and real fulfilment companies do it. But it means two boxes, two
    shipping labels, two lots of picking and packing work, and more money. Whether
    that extra cost is worth it is a genuine business decision, and the spec puts
    it out of scope for this prototype.

    So we do not split. But we do say clearly that splitting is what this order
    needed. That is a much more useful answer than "sold out", which would be
    plainly untrue - and a merchant reading "sold out" about a product they know
    they own four of would rightly stop trusting the whole report.
    """
    if total_units_in_network(order["sku"], inventory) < order["quantity"]:
        return REASON_OUT_OF_STOCK
    return REASON_SPLIT_REQUIRED


def score_all_warehouses(order, network, inventory):
    """
    Works out what shipping this order from each warehouse would mean.

    Takes: one order, the network from load_network(), and the current stock levels.
    Gives back: a dictionary with two lists inside it:
        "usable"    warehouses that can fill the whole order, each with its price
                    and arrival date worked out
        "no_stock"  warehouses that cannot fill it, kept so we can explain why
                    they were not used

    Each entry in "usable" holds:
        fc_id                  which warehouse
        shipping_cost_cents    what shipping costs, in whole cents
        transit_days           how many working days the journey takes
        ship_date              the day the parcel leaves the warehouse
        expected_delivery_date the day it should land
        promise_met            True if it lands on or before the promised day
        days_late              how many days past the promise, or 0 if on time
        inventory_before       units at that warehouse before this order
        inventory_after        units left there if we picked it

    WHY WE WORK OUT "STOCK LEFT AFTER" HERE
        It looks like an odd thing to include. The reason is the 50 cent rule.
        When two warehouses cost nearly the same, we pick the one with more stock
        left over afterwards. To compare that we need the after number, not the
        before number - because the order itself changes the picture. A warehouse
        with 3 units sending 2 ends up worse off than a warehouse with 3 units
        sending 1.

    WHY EVERY WAREHOUSE GETS THE SAME SHIP DATE
        In this prototype the parcel always leaves on the next working day, no
        matter which warehouse sends it. So the ship date is the same everywhere
        and the arrival dates differ only because the journeys differ.

        That is a shortcut. Real warehouses have different cut-off times for
        handing parcels to couriers, and they get busy, so a parcel from a
        stretched warehouse might sit for a day before it moves. That would change
        which warehouse wins. Worth knowing this is simpler than real life.

    WHY THE LIST COMES BACK SORTED BY WAREHOUSE NAME
        Sorting by name means the engine sees the warehouses in the same order
        every single time it runs. Without that, two runs on identical data could
        break a tie differently just because a list happened to be built in a
        different order, and the engine would look like it was guessing. A routing
        engine that gives different answers to the same question is one nobody
        will trust, so we remove the randomness at the source.
    """
    zone = get_destination_zone(order, network)
    sku = order["sku"]
    quantity = order["quantity"]

    # The parcel goes out on the next working day after the order was placed.
    # Worked out once, because it is the same for every warehouse.
    order_date = parse_date(order["order_date"])
    promised_date = parse_date(order["promised_delivery_date"])
    ship_date = next_business_day(order_date)

    usable = []
    no_stock = []

    for fc in sorted(network["fulfillment_centers"], key=lambda f: f["fc_id"]):
        fc_id = fc["fc_id"]
        on_hand = units_available(fc_id, sku, inventory)

        # First gate: can this warehouse fill the WHOLE order on its own?
        #
        # Note "whole". A warehouse with 1 unit is no use for an order of 2 in
        # this prototype, because we never split orders across warehouses. Half an
        # order is not a smaller success, it is a different kind of failure.
        if on_hand < quantity:
            no_stock.append({
                "fc_id": fc_id,
                "inventory_before": on_hand,
                "reason_not_selected": NOT_SELECTED_NO_STOCK,
            })
            continue

        # Look up what it costs and how long it takes to get from this warehouse
        # to this part of the country.
        lane = network["lanes"][(fc_id, zone)]
        transit_days = lane["transit_days"]

        # Add the journey time to the ship date to get the arrival date.
        expected_delivery_date = add_business_days(ship_date, transit_days)

        # Did we keep our word?
        #
        # Landing exactly ON the promised day counts as keeping it. We said "by
        # the 20th", and the 20th is by the 20th.
        promise_met = expected_delivery_date <= promised_date

        # If we are late, how late?
        #
        # This is counted in ordinary calendar days, NOT working days, and that is
        # a deliberate choice. Travel time uses working days because vans do not
        # drive at weekends. But lateness is about the customer, and a customer
        # waiting over a weekend feels every one of those days. Telling somebody
        # their parcel is "two working days late" when they have been waiting
        # since Thursday would be dressing up the truth.
        days_late = 0
        if not promise_met:
            days_late = (expected_delivery_date - promised_date).days

        usable.append({
            "fc_id": fc_id,
            "shipping_cost_cents": lane["shipping_cost_cents"],
            "transit_days": transit_days,
            "ship_date": ship_date,
            "expected_delivery_date": expected_delivery_date,
            "promise_met": promise_met,
            "days_late": days_late,
            "inventory_before": on_hand,
            "inventory_after": on_hand - quantity,
        })

    return {"usable": usable, "no_stock": no_stock}


# ---------------------------------------------------------------------------
# PART 3 - CHOOSING A WAREHOUSE AND WRITING DOWN WHY
# ---------------------------------------------------------------------------
#
# WHAT THIS PART IS FOR
#     Part 2 gathered the facts about every warehouse. This part makes the actual
#     decision, and then writes down a full record of it.
#
#     This is the heart of the whole prototype. Everything before it was
#     preparation, and everything after it is either reporting or comparing.
#
# THE RULE WE FOLLOW, IN ONE LINE
#     Protect the promise first. Save money where we can.
#
#     Said more fully: out of the warehouses that will get the parcel there on
#     time, pick the cheapest. Only when nothing can arrive on time do we switch
#     to picking whichever option is least late.
#
# WHY THE PROMISE COMES FIRST AND NOT THE MONEY
#     A late parcel costs more than the shipping money saved, but the cost is
#     spread out and hard to see. The customer emails support, which costs staff
#     time. The merchant may refund the shipping fee or send a voucher. The
#     customer is a bit less likely to buy again. If the merchant sells through a
#     marketplace, late deliveries can hurt their standing there.
#
#     Meanwhile the shipping saving is one clean number on one invoice. It is very
#     easy to see and very easy to be pleased about, which is exactly why it is so
#     tempting to chase.
#
#     So the engine treats the promise as the thing to protect, and money as the
#     thing to be careful with afterwards. But it never hides the cost of that
#     choice. Every decision records what it cost in extra shipping, so a merchant
#     can look at the total and decide for themselves whether our judgement was
#     right for their business.


def _sort_key_within_price_band(candidate):
    """
    The order we prefer warehouses in once price has stopped separating them.

    Takes: one scored warehouse from Part 2.
    Gives back: a tuple Python can sort by. Smaller sorts first.

    Once two warehouses count as the same price, we choose between them like this:

        1. More stock left over afterwards  (the minus sign flips it, so bigger wins)
        2. Cheaper
        3. Faster journey
        4. Warehouse name, alphabetically

    Why stock comes first:
        Speed we do not need is worth nothing. Both of these warehouses already
        arrive on time, so getting there even earlier wins us no extra credit -
        we promised a date, not a race. Spare stock, on the other hand, is worth
        something real, because it is what lets us say yes to the next customer.

    Why price comes back in at step 2, when we just said price was tied:
        Because "close enough to ignore" is not the same as "free". The 50 cent
        window exists so that a small price gap cannot force us to empty a shelf.
        It was never meant to say the money does not exist.

        So once stock is level, there is nothing left to buy with that money, and we
        should keep it. Skipping this step was a real bug in an earlier version: two
        warehouses that both dropped to zero stock would be split on speed, and the
        engine would happily pay 40 cents for a faster delivery it had no use for -
        and then record the 40 cents as "protecting stock", which was simply untrue.

    Why the last rule is just the name, alphabetically:
        It looks arbitrary because it is. That is the point. When two options are
        genuinely identical on everything we care about, we still have to pick one,
        and we need to pick the SAME one every single time. Sorting on the name
        guarantees that. Without it the engine could give two different answers to
        the same question on two different days, which would make it impossible to
        test and impossible to trust.
    """
    return (
        -candidate["inventory_after"],
        candidate["shipping_cost_cents"],
        candidate["transit_days"],
        candidate["fc_id"],
    )


def choose_promise_first(candidates):
    """
    Picks a warehouse using our real rule: keep the promise, then spend as little
    as possible.

    Takes: the list of warehouses that can fill the order, from Part 2.
    Gives back: a small dictionary holding three things:
        "winner"                   the warehouse we picked
        "price_band_anchor_cents"  the price the 50 cent window was measured FROM
        "price_band_width_cents"   how wide that window was

    WHY IT HANDS BACK THE WINDOW AND NOT JUST THE WINNER
        Because the part of the engine that explains rejections needs to know where
        the window actually sat, and it cannot work that out for itself.

        This caused a real bug in an earlier version. The explaining code guessed
        the window from the WINNER's price instead. Usually those are the same
        number, so it looked fine. But when we pick a slightly pricier warehouse to
        protect stock, the winner is no longer the cheapest, so the guessed window
        sits in the wrong place - and a warehouse that was genuinely thrown out for
        being too expensive got told "we flipped a coin" instead. Passing the real
        number along removes the guesswork.

    HOW IT WORKS

    Step 1 - split the options into on time and late.

    Step 2 - if ANY option is on time, we stop caring about the late ones
             completely. They are not slightly worse options, they are failures at
             the one job that matters most. Then among the on-time options we take
             the cheapest.

    Step 3 - the 50 cent softener. Rather than blindly taking the single cheapest,
             we treat everything within 50 cents of it as "the same price, near
             enough", and choose inside that group on stock instead.

             Here is why. Suppose the cheapest warehouse is 30 cents cheaper but
             it is down to its final unit, while another has plenty. Taking the 30
             cents empties that shelf, and the next customer who needs that
             warehouse gets nothing. Thirty cents is not worth that.

    Step 4 - if NOTHING can arrive on time, the question changes. It is no longer
             "who keeps the promise" but "who breaks it by the least". So we find
             the smallest number of days late, keep only the options tied at that,
             and run the same cheapest-then-stock rules inside that group.

             The order matters here. Fewest days late comes before price. We would
             rather pay a bit more to be one day late than pay less to be three
             days late, because the customer feels the days and the merchant only
             feels the pennies.
    """
    on_time = [c for c in candidates if c["promise_met"]]

    if on_time:
        # At least one warehouse keeps the promise, so we only look at those.
        shortlist = on_time
    else:
        # Nobody can make it. Switch to damage control: keep only the options that
        # are the least late, and choose between those.
        fewest_days_late = min(c["days_late"] for c in candidates)
        shortlist = [c for c in candidates if c["days_late"] == fewest_days_late]

    # Now find the cheapest on the shortlist, and treat anything within 50 cents
    # of it as tied on price.
    cheapest_cost = min(c["shipping_cost_cents"] for c in shortlist)
    price_band = [
        c for c in shortlist
        if c["shipping_cost_cents"] <= cheapest_cost + COST_TIE_BAND_CENTS
    ]

    # Everything left is close enough on price that a small gap cannot force us to
    # empty a shelf, so stock decides first, then the leftover money, then speed,
    # then the name.
    winner = sorted(price_band, key=_sort_key_within_price_band)[0]

    return {
        "winner": winner,
        "price_band_anchor_cents": cheapest_cost,
        "price_band_width_cents": COST_TIE_BAND_CENTS,
    }


def choose_cost_first(candidates):
    """
    Picks the cheapest warehouse and ignores the promised date entirely.

    Takes: the list of warehouses that can fill the order, from Part 2.
    Gives back: the same shape of dictionary as choose_promise_first, so the rest of
                the engine does not have to care which rule was used.

    Note that the window it reports back is ZERO cents wide. That is not a
    placeholder, it is the truth about this rule: there is no "near enough the same
    price" here. One cent more expensive means you lose. Reporting a 50 cent window
    would make the explanations claim a kindness this rule does not have.

    THIS IS NOT THE RULE WE BELIEVE IN. It exists to be measured against.

    Claiming "protecting delivery promises costs money" is worth nothing on its
    own. To make it a real statement you have to say how much money. The only
    honest way to work that out is to run the exact same orders with the exact
    same stock the greedy way, and compare the two bills at the end.

    So this deliberately does the naive thing: cheapest wins, and the promised
    date never enters into it. It will keep fewer promises and it will produce a
    smaller shipping bill. The gap between the two is the number a merchant
    actually needs to see.

    Note that this ignores the 50 cent stock rule too. That is on purpose. If it
    used half of our thinking it would be a blurred comparison, and we would not
    be able to say which of our ideas caused which part of the difference. Being
    plainly, purely greedy makes it a clean control to measure against.
    """
    winner = sorted(
        candidates,
        key=lambda c: (c["shipping_cost_cents"], c["transit_days"], c["fc_id"]),
    )[0]

    return {
        "winner": winner,
        "price_band_anchor_cents": winner["shipping_cost_cents"],
        "price_band_width_cents": 0,
    }


def explain_why_not_selected(candidate, winner, band_anchor_cents, band_width_cents):
    """
    Works out, in one short code, why we turned a particular warehouse down.

    Takes: the warehouse we rejected, the one we picked, and the price window the
           choosing rule actually used (where it sat, and how wide it was).
    Gives back: a short reason code such as "MISSES_PROMISE" or "HIGHER_COST".

    The checks below run in the same order the choosing rules ran. That is what
    makes the answer honest. We report the reason that ACTUALLY knocked this
    warehouse out, not just any true statement about it.

    An example of why that matters. Pennsylvania is usually both the cheapest and
    the slowest. When we turn it down, saying "it was more expensive" would be
    false, and saying "it was slower" would be beside the point. The real reason it
    lost is that it would have arrived late. If the reasons did not follow the same
    order as the rules, we could easily end up printing something that is true but
    misleading.

    WHY THE PRICE WINDOW IS PASSED IN RATHER THAN WORKED OUT HERE
        Because this function cannot see the other warehouses, so it cannot know
        where the window sat, and an earlier version of it guessed. It measured the
        window from the WINNER's price.

        Most of the time that guess is harmless, because the winner usually IS the
        cheapest. But when the engine deliberately pays a little more to protect
        stock, the winner is not the cheapest any more, and the guessed window
        lands in the wrong place. A warehouse that was genuinely thrown out for
        costing too much then slipped past the price check and came out the other
        end labelled "we flipped a coin". That is not a small wording problem. The
        one thing this engine promises is that it can explain itself, and it was
        quietly making things up.

        Being handed the real number costs nothing and removes the guess entirely.
    """
    # Did it simply miss the promise while our pick kept it? That beats everything
    # else, so it is checked first.
    if winner["promise_met"] and not candidate["promise_met"]:
        return NOT_SELECTED_MISSES_PROMISE

    # If everything was late, was this one later than our pick?
    if candidate["days_late"] > winner["days_late"]:
        return NOT_SELECTED_MORE_DAYS_LATE

    # Was it priced right out of the running?
    #
    # Measured from where the window really sat, not from what we happened to pay.
    if candidate["shipping_cost_cents"] > band_anchor_cents + band_width_cents:
        return NOT_SELECTED_HIGHER_COST

    # If we get this far, this warehouse was inside the price window, and may even
    # have been cheaper than the one we chose. So it lost on one of the later rules.
    if candidate["inventory_after"] < winner["inventory_after"]:
        return NOT_SELECTED_LOWER_STOCK

    # Level on stock, so the spare money had nothing left to buy and we kept it.
    if candidate["shipping_cost_cents"] > winner["shipping_cost_cents"]:
        return NOT_SELECTED_HIGHER_COST_ON_TIE

    if candidate["transit_days"] > winner["transit_days"]:
        return NOT_SELECTED_SLOWER

    # Dead equal on everything we care about, so the alphabet settled it.
    return NOT_SELECTED_TIE_BREAK


def work_out_premium(winner, candidates):
    """
    Works out how much extra our decision cost, and why we spent it.

    Takes: the warehouse we picked, and every warehouse that could have filled the
           order.
    Gives back: a dictionary with four things in it -
                  "cheapest_fc_id"       which warehouse was cheapest
                  "cheapest_cost_cents"  what that warehouse would have cost
                  "premium_cents"        how much more than that we actually spent
                  "premium_reason"       a short code saying why we spent it

    WHY WE MEASURE IT THIS WAY
        It would be tempting to call this "the cost of protecting the promise".
        We do not, because sometimes we miss the promise anyway, and in that case
        calling the extra money "protecting the promise" would be nonsense - we
        did not protect anything.

        So we measure something simpler and always true: the gap between what we
        spent and the cheapest thing we could have spent. Then we say separately
        why we spent it. That way the number is honest whatever happened.

    THE FOUR REASONS
        Nothing extra spent   We picked the cheapest option anyway.
        Protect the promise   The cheaper option would have arrived late.
        Reduce lateness       Everything was late, and we paid to be less late.
        Preserve stock        The cheaper option was on time too, but it was down
                              to its last units, so we paid a little to leave them
                              alone.

        That last reason was not in the original spec. We added it because a real
        order in our own sample batch needed it, and none of the other three were
        true for it. Writing down a reason that is not true would defeat the whole
        purpose of writing one down at all.
    """
    # The cheapest thing we could have done, whether or not it kept the promise.
    cheapest = sorted(
        candidates,
        key=lambda c: (c["shipping_cost_cents"], c["fc_id"]),
    )[0]

    cheapest_cost = cheapest["shipping_cost_cents"]
    premium = winner["shipping_cost_cents"] - cheapest_cost

    if premium == 0:
        # We took the cheapest option, so there is nothing to explain.
        reason = PREMIUM_NONE
    elif winner["promise_met"] and not cheapest["promise_met"]:
        # We paid more and got the parcel there on time. The cheap option would
        # have been late. This is the main case the engine exists for.
        reason = PREMIUM_PROTECT_PROMISE
    elif winner["days_late"] < cheapest["days_late"]:
        # Everything was going to be late. We paid to be less late than we had to
        # be. Not a win, but better than the alternative.
        reason = PREMIUM_REDUCE_LATENESS
    else:
        # Both options were equally good on timing, so the extra money went on
        # something else - protecting stock at a nearly empty warehouse.
        reason = PREMIUM_PRESERVE_INVENTORY

    return {
        # We hand back the NAME of the cheapest warehouse as well as its price,
        # and that is not tidiness - it is the difference between a number a
        # reader can check and one they have to take on trust.
        #
        # The report says "$3.00 more than the cheapest option". On its own that
        # leaves the reader hunting for where $3.00 came from, and there is no row
        # in the table costing $3.00, because it is a subtraction rather than a
        # price. With the name we can write "$3.00 more than FC-PENNSYLVANIA at
        # $5.00", and both numbers in that sum are then sitting on the page where
        # anyone can check them.
        #
        # The sort above already settles ties by warehouse name, so this is the
        # same cheapest warehouse every single run, not whichever one happened to
        # come out first.
        "cheapest_fc_id": cheapest["fc_id"],
        "cheapest_cost_cents": cheapest_cost,
        "premium_cents": premium,
        "premium_reason": reason,
    }


def route_order(order, network, inventory, policy=POLICY_PROMISE_FIRST):
    """
    Routes one single order from start to finish.

    Takes: one order, the network from load_network(), the current stock levels,
           and which set of rules to use.
    Gives back: one decision record - a dictionary describing what happened and why.

    IMPORTANT: THIS CHANGES THE STOCK LEVELS
        If the order gets routed, this takes the units off that warehouse's shelf
        straight away, by editing the inventory you passed in.

        That is on purpose, and it is how the batch works. The next order must see
        the shelves as they are now, not as they were this morning, or we would
        cheerfully promise the same last unit to five different customers.

        Orders we reject or cannot fill take nothing off any shelf.

    THE FOUR POSSIBLE OUTCOMES
        Rejected      The order details make no sense, so there is nothing to do.
        Cannot fill   The order is fine, but no single warehouse can supply it.
        On time       Routed, and it will arrive by the promised date.
        Late          Routed, but it will arrive after the promised date. Still the
                      best we could manage.
    """
    # --- First, is this even a sensible order? ---
    #
    # If not, we stop here. There is no point working out shipping prices for an
    # order asking for minus two items.
    problem = validate_order(order, network)
    if problem is not None:
        return {
            "order_id": order.get("order_id"),
            "status": STATUS_INVALID_ORDER,
            "policy": policy,
            "reason": problem,
            # What the customer asked for, copied down exactly as it arrived.
            #
            # These are NOT checked facts, and that is the point. The only reason
            # we are in here is that something about this order did not make sense,
            # so one of these four values is probably the culprit. We use .get()
            # because a broken order might be missing a field completely.
            #
            # We keep them because "we do not ship to Miami, FL" is something a
            # human can go and fix, while "UNKNOWN_DESTINATION" on its own is not.
            # The bad value is the most useful thing on the whole record.
            "destination": order.get("destination"),
            "sku": order.get("sku"),
            "quantity": order.get("quantity"),
            "promised_delivery_date": order.get("promised_delivery_date"),
            # The order date is only ever needed by one of the five rejection
            # reasons: the one where a delivery was promised for BEFORE the order
            # was placed. Explaining that reason without both dates side by side
            # would mean saying "the dates are the wrong way round" and leaving
            # the reader to take our word for which way round they were.
            "order_date": order.get("order_date"),
            "selected_fc": None,
            "alternatives": [],
        }

    # --- Work out what each warehouse would mean for this order ---
    scored = score_all_warehouses(order, network, inventory)
    usable = scored["usable"]

    # --- Can anybody actually fill it? ---
    #
    # If not, we still explain ourselves properly. We say which of the two very
    # different failures this is, and we list every warehouse we looked at along
    # with how little stock it had. A merchant seeing "sold out" wants to know
    # whether that means zero everywhere or just spread too thin.
    if not usable:
        return {
            "order_id": order["order_id"],
            "status": STATUS_UNROUTABLE,
            "policy": policy,
            "reason": diagnose_why_unfillable(order, inventory),
            # We write down the city AND the shipping area, because they answer two
            # different questions. The area is what the engine actually used to look
            # up prices and delivery times. The city is what a human recognises.
            # Printing "EAST" at somebody is not an explanation.
            "destination": order["destination"],
            "destination_zone": get_destination_zone(order, network),
            "sku": order["sku"],
            "quantity": order["quantity"],
            # Kept so the printout can say what we failed to hit. An order we could
            # not fill still had a date promised to it, and that promise is exactly
            # the thing that just got broken.
            "promised_delivery_date": order["promised_delivery_date"],
            "units_in_network": total_units_in_network(order["sku"], inventory),
            "selected_fc": None,
            "alternatives": [
                {
                    "fc_id": entry["fc_id"],
                    "inventory_before": entry["inventory_before"],
                    "reason_not_selected": entry["reason_not_selected"],
                }
                for entry in scored["no_stock"]
            ],
        }

    # --- Choose one, using whichever set of rules we were asked for ---
    #
    # Both rules hand back the same shape of answer: the winner, plus where the
    # price window sat. We need that window to explain the rejections honestly.
    if policy == POLICY_COST_FIRST:
        choice = choose_cost_first(usable)
    else:
        choice = choose_promise_first(usable)

    winner = choice["winner"]
    band_anchor = choice["price_band_anchor_cents"]
    band_width = choice["price_band_width_cents"]

    premium_info = work_out_premium(winner, usable)

    # --- How much breathing room did this order have, at the moment we routed it? ---
    #
    # Spare time means: the promised date, minus the soonest date we could manage
    # with the stock sitting on the shelves RIGHT NOW.
    #
    # We do NOT use "promised date minus order date", which would be the obvious
    # thing to reach for. That only tells us how generous the promise sounded, not
    # whether we can actually keep it. A five day promise sounds relaxed, but if
    # our fastest option takes five days then there is no room in it at all.
    #
    # READ THE FIELD NAME CAREFULLY: "when routed".
    #
    # This number is measured after any earlier orders in the batch have already
    # taken their stock, so a negative number here does NOT mean the promise was
    # impossible from the start. It might have been perfectly achievable this
    # morning, and we might be the ones who ruined it.
    #
    # An earlier version of this code called the field plain "routing_slack_days"
    # and said in its own docstring that a negative value meant the promise was
    # impossible before we ever saw the order. In our own sample batch that was
    # flatly untrue: ORD-1008 starts the day with exactly enough time, loses its
    # only fast warehouse to an order we handled first, and then gets filed as
    # "never possible". The engine was blaming the shop for its own decision.
    #
    # route_batch fills in the other half of the story - what the spare time was
    # before the batch started - so the two numbers together tell the truth.
    promised_date = parse_date(order["promised_delivery_date"])
    soonest_possible = min(c["expected_delivery_date"] for c in usable)
    slack_days_when_routed = (promised_date - soonest_possible).days

    # --- Build the list of options we turned down ---
    #
    # Cheapest first, because the most obvious question a merchant will ask is
    # "why didn't you use the cheap one?", and this puts that answer at the top.
    # Warehouses with no stock go last, since there was never a real choice there.
    alternatives = []
    for candidate in sorted(usable, key=lambda c: (c["shipping_cost_cents"], c["fc_id"])):
        if candidate["fc_id"] == winner["fc_id"]:
            continue
        alternatives.append({
            "fc_id": candidate["fc_id"],
            "shipping_cost_cents": candidate["shipping_cost_cents"],
            "transit_days": candidate["transit_days"],
            "expected_delivery_date": candidate["expected_delivery_date"].isoformat(),
            "promise_met": candidate["promise_met"],
            "days_late": candidate["days_late"],
            "inventory_after": candidate["inventory_after"],
            "reason_not_selected": explain_why_not_selected(
                candidate, winner, band_anchor, band_width
            ),
        })

    for entry in scored["no_stock"]:
        alternatives.append({
            "fc_id": entry["fc_id"],
            "inventory_before": entry["inventory_before"],
            "reason_not_selected": entry["reason_not_selected"],
        })

    # --- Take the stock off the shelf ---
    #
    # This happens last, once we are certain the order is going ahead. It has to
    # happen before the next order is looked at, so that the next order sees the
    # truth about what is left.
    inventory[winner["fc_id"]][order["sku"]] -= order["quantity"]

    # --- Write down everything about the decision ---
    #
    # Dates get turned into plain text here so the whole record can be saved
    # straight to a file with no extra work.
    return {
        "order_id": order["order_id"],
        "status": STATUS_ROUTED if winner["promise_met"] else STATUS_ROUTED_LATE,
        "policy": policy,
        "destination": order["destination"],
        "destination_zone": get_destination_zone(order, network),
        "sku": order["sku"],
        "quantity": order["quantity"],
        "selected_fc": winner["fc_id"],
        "ship_date": winner["ship_date"].isoformat(),
        "transit_days": winner["transit_days"],
        "expected_delivery_date": winner["expected_delivery_date"].isoformat(),
        "promised_delivery_date": order["promised_delivery_date"],
        "promise_met": winner["promise_met"],
        "days_late": winner["days_late"],
        "slack_days_when_routed": slack_days_when_routed,
        "shipping_cost_cents": winner["shipping_cost_cents"],
        # The cheapest warehouse we could have used, by name as well as by price.
        # Keeping the name is what lets the printed report say "$3.00 more than
        # FC-PENNSYLVANIA at $5.00" instead of "$3.00 more than the cheapest
        # option", which leaves the reader with a number they cannot trace to
        # anything on the page.
        "cheapest_available_fc": premium_info["cheapest_fc_id"],
        "cheapest_available_cost_cents": premium_info["cheapest_cost_cents"],
        "premium_over_cheapest_cents": premium_info["premium_cents"],
        "premium_reason": premium_info["premium_reason"],
        "inventory_before": winner["inventory_before"],
        "inventory_after": winner["inventory_after"],
        "alternatives": alternatives,
    }


# ---------------------------------------------------------------------------
# PART 4 - RUNNING A WHOLE BATCH OF ORDERS
# ---------------------------------------------------------------------------
#
# WHAT THIS PART IS FOR
#     Part 3 routes one order really well. This part runs a whole batch, and deals
#     with the problem that only shows up once there is more than one order:
#     the orders are competing with each other for the same stock.
#
# THE PROBLEM
#     There are only 4 units of the rare product in the entire company. Five
#     different orders want it. Whoever gets looked at first gets a unit, and
#     whoever is looked at last gets an apology.
#
#     So the running order is a decision the engine is making, whether or not it
#     admits to it. Doing nothing and working down the list top to bottom is not
#     "no decision" - it is the decision to let the order form's timestamp decide
#     who gets a late parcel. That is not a rule anybody chose. It is just a rule
#     nobody noticed.
#
# THE IDEA
#     Deal with the tightest order first.
#
#     "Tightest" means the least spare time between the date we promised and the
#     soonest date we could actually manage. Call that spare time SLACK.
#
#     Why this works: an order with lots of slack has options. Push it onto a
#     slower warehouse and it still arrives in time. An order with zero slack has
#     exactly one option, and if something else takes it, that promise is gone.
#     So we should let the order with choices give way to the order without any.
#
#     It costs nothing to do this. We are not buying anything or upgrading any
#     shipping. We are just deciding who goes first, and choosing sensibly instead
#     of by accident.
#
# WHY IT MATTERS HERE
#     Two orders in our sample batch both want the last rare unit at Chicago.
#     ORD-1007 was promised the 20th and has three warehouses that work.
#     ORD-1008 was promised the 18th and Chicago is the ONLY warehouse fast enough.
#
#     Work down the list and ORD-1007 goes first, takes Chicago because it is
#     cheapest, and ORD-1008 is late. That decision looked completely reasonable at
#     the time it was made. The harm did not appear until the next order.
#
#     Deal with ORD-1008 first and it gets Chicago, and ORD-1007 shrugs, takes
#     slower Dallas, and still lands inside its own promise. Both promises kept.
#     Same orders, same stock, same rules. One promise saved for free.


def earliest_possible_delivery(order, network, inventory):
    """
    Works out the soonest we could possibly get this order delivered.

    Takes: one order, the network from load_network(), and the current stock levels.
    Gives back: a date, or None if there is no way to ship this order at all.

    HOW
        Look at every warehouse that has enough stock right now, work out when each
        one's parcel would land, and take the earliest of those dates.

    WHY IT HAS TO RESPECT STOCK
        The temptation is to answer this from the shipping table alone - find the
        fastest lane and use that. That would be a fantasy. A one day lane from an
        empty warehouse is not fast, it is unavailable.

        So the answer depends on the stock we are holding at this exact moment,
        which means the answer changes as the batch runs. That is correct and it is
        the whole point: this is the soonest we could manage in the real world as
        it stands, not the soonest in a perfect one.

    None MEANS "NO WAY AT ALL"
        Either the order details do not make sense, or nobody has the stock. Either
        way there is no delivery date to talk about, so there is no spare time to
        talk about either.
    """
    # An order that does not make sense has no delivery date, so do not pretend.
    if validate_order(order, network) is not None:
        return None

    scored = score_all_warehouses(order, network, inventory)
    if not scored["usable"]:
        return None

    return min(c["expected_delivery_date"] for c in scored["usable"])


def routing_slack_days(order, network, inventory):
    """
    Works out how much spare time an order has.

    Takes: one order, the network from load_network(), and the current stock levels.
    Gives back: a whole number of days, or None if the order cannot be shipped.

    Spare time = the date we promised MINUS the soonest date we could manage.

        3   comfortable, we could lose three days and still be fine
        0   no room at all, exactly one thing works and it had better work
       -1   the promise was already impossible before we saw the order

    WHY NOT JUST "PROMISED DATE MINUS ORDER DATE"
        That is the obvious thing to reach for, and it is the wrong number. It tells
        you how generous the promise SOUNDED, not whether we can keep it.

        A five day promise sounds relaxed. But if the fastest warehouse holding the
        item takes five days to reach that customer, there is no room in it at all -
        one hiccup and the promise is broken. Meanwhile a two day promise from a
        warehouse an hour away is comfortable. Only measuring against what we can
        actually do tells us which orders are really in trouble.

    A NEGATIVE ANSWER IS NOT A BUG
        It means somebody at the checkout promised a date we were never going to
        hit. That is real, it happens, and the engine needs to see it clearly rather
        than treating it as an error. We still ship the order. We just cannot win.
    """
    soonest = earliest_possible_delivery(order, network, inventory)
    if soonest is None:
        return None

    promised = parse_date(order["promised_delivery_date"])
    return (promised - soonest).days


def slack_for_every_order(orders, network, inventory):
    """
    Works out the spare time for every order in one go.

    Takes: the list of orders, the network, and the stock levels as they are RIGHT
           NOW, before any of these orders has been handled.
    Gives back: a dictionary of order ID -> spare time in days, or None for orders
                that cannot be shipped at all.

    WHY THIS IS A SEPARATE FUNCTION
        Two different parts of the engine need these numbers, and they must both get
        the SAME numbers.

        The sorting needs them to decide who goes first. The reporting needs them to
        answer a question a merchant will absolutely ask: "was this promise ever
        possible, or did you break it yourself?"

        If each part worked them out on its own, they would be reading the shelves at
        different moments and would quietly disagree. So we take one snapshot,
        before anything has been shipped, and both parts use it.
    """
    return {
        order.get("order_id", ""): routing_slack_days(order, network, inventory)
        for order in orders
    }


def sort_orders_by_slack(orders, slack_by_order_id):
    """
    Puts the orders in the order we should deal with them: tightest first.

    Takes: the list of orders, and the spare-time numbers from
           slack_for_every_order().
    Gives back: a new list with the same orders in a different order. The list you
                passed in is left alone.

    THE SORTING RULES, IN ORDER
        1. Orders we cannot ship at all go to the very end. They are not competing
           for anything, so they should not be allowed to take stock from an order
           that could actually use it.
        2. Then least spare time first. Fewest options means fewest chances to
           recover, so go first.
        3. Then order ID, alphabetically, to break exact ties. This is arbitrary on
           purpose - what matters is that it is the SAME arbitrary choice every
           time, so two runs on the same data always match.

    THE SHORTCUT WORTH KNOWING ABOUT
        We work out everybody's spare time ONCE, using the stock we are holding at
        the start, and then we do not touch the running order again.

        That is not quite right, and it is worth being straight about. Stock drops
        as the batch runs, so by order number seven the real spare times have moved,
        and an order we sorted as comfortable may have quietly become tight.

        A better version would work out the spare times again after every single
        order and always pick the tightest one still waiting. We did not do that
        here for two reasons. It is much harder to follow, and it is not needed to
        make the point - one sort at the start already saves a promise in our
        sample batch. It is on the list of things to build next.
    """
    def sort_key(order):
        order_id = order.get("order_id", "")
        slack = slack_by_order_id.get(order_id)
        if slack is None:
            # Cannot be shipped, so send it to the back of the queue.
            return (1, 0, order_id)
        return (0, slack, order_id)

    return sorted(orders, key=sort_key)


def summarise_batch(decisions, order_count):
    """
    Adds up what happened across a whole batch.

    Takes: the list of decision records from a batch, and how many orders went in.
    Gives back: a dictionary of totals.

    WHY THESE PARTICULAR NUMBERS
        These are the numbers that let somebody argue with us, which is the point.
        Two of them matter most and they pull in opposite directions:

            promises_kept          how many customers got what they were told
            shipping_cost_cents    what the whole batch cost to ship

        If we only reported the first, we would look great and be hiding the bill.
        If we only reported the second, cheapest would always look best and nobody
        would see the damage. Printing both together is what turns "we protect
        promises" from a slogan into a claim somebody can check.

    WHY WE ALSO COUNT TOTAL DAYS LATE
        Counting broken promises alone treats one day late and five days late as
        the same failure, and customers absolutely do not. Two batches can break
        the same number of promises and feel completely different to the people
        waiting. So we add up the days as well.

    A DELIBERATE CHOICE ABOUT WHAT COUNTS AS A BROKEN PROMISE
        An order we could not ship at all is counted separately from one that
        shipped late. To the customer both are failures, and we could fairly lump
        them together. We keep them apart because they need completely different
        fixes: a late parcel is a routing problem, an unshippable order is a
        buy-more-stock problem. Rolling them into one number would hide which
        lever to pull.
    """
    on_time = [d for d in decisions if d["status"] == STATUS_ROUTED]
    late = [d for d in decisions if d["status"] == STATUS_ROUTED_LATE]
    unroutable = [d for d in decisions if d["status"] == STATUS_UNROUTABLE]
    invalid = [d for d in decisions if d["status"] == STATUS_INVALID_ORDER]

    shipped = on_time + late

    return {
        "order_count": order_count,
        "valid_orders": order_count - len(invalid),
        "routed_on_time": len(on_time),
        "routed_late": len(late),
        "unroutable": len(unroutable),
        "invalid": len(invalid),
        "promises_kept": len(on_time),
        "promises_broken": len(late),
        "total_days_late": sum(d["days_late"] for d in late),
        # Now the same failures split by whose fault they were. This is the most
        # useful part of the whole summary, because each line points at a different
        # fix, and one of them is not a fix we can make.
        #
        # These use .get() because an order routed on its own has no batch to compare
        # itself against, so the field simply is not there.
        "lost_never_possible": sum(
            1 for d in decisions if d.get("promise_lost_because") == LOST_NEVER_POSSIBLE
        ),
        "lost_to_earlier_orders": sum(
            1 for d in decisions if d.get("promise_lost_because") == LOST_TO_EARLIER_ORDERS
        ),
        "lost_to_our_own_rule": sum(
            1 for d in decisions if d.get("promise_lost_because") == LOST_TO_OUR_OWN_RULE
        ),
        # The headline: how many broken promises were ours rather than the shop's.
        "promises_we_broke_ourselves": sum(
            1 for d in decisions
            if d.get("promise_lost_because") in (LOST_TO_EARLIER_ORDERS, LOST_TO_OUR_OWN_RULE)
        ),
        "shipping_cost_cents": sum(d["shipping_cost_cents"] for d in shipped),
        "premium_over_cheapest_cents": sum(
            d["premium_over_cheapest_cents"] for d in shipped
        ),
    }


def check_inventory_adds_up(starting_inventory, ending_inventory, decisions):
    """
    Checks we did not invent or lose any stock while running the batch.

    Takes: the stock levels before the batch, the stock levels after, and every
           decision the batch made.
    Gives back: nothing if all is well. Raises an error and stops if it is not.

    THE SUM IT CHECKS, FOR EVERY WAREHOUSE AND PRODUCT SEPARATELY
        what that shelf started with  ==  what we shipped from it  +  what is left

    WHY IT HAS TO BE PER WAREHOUSE AND NOT COMPANY-WIDE
        This is the mistake the first version of this function made, and it is a
        neat example of a test that looks like it works.

        It only added up company-wide totals. So imagine Chicago quietly goes to
        MINUS one unit while Dallas somehow gains one. Company-wide the total is
        still correct, the sum balances, the check passes, and it prints nothing.
        And minus one unit on a shelf is exactly the double-booking this function
        exists to catch - the same unit sold to two people.

        A check that passes on the very bug it was written for is worse than having
        no check, because it makes you stop looking. So now we check every shelf on
        its own, and we also refuse to accept any negative number anywhere.

    WHY BOTHER
        This is the one mistake in a routing engine that nobody notices until it is
        expensive. Every wrong answer here looks fine on screen.

        Count stock twice and you promise the same unit to two customers, take both
        their money, and find out at the packing bench. Lose stock and you turn
        away orders you could have filled, which costs sales you never even know
        you missed. Neither shows up as an error message. Both show up as an angry
        phone call weeks later.

        So we check it after every batch. It is a handful of additions and it makes
        an entire family of silent bugs impossible to ship.

    WHY IT STOPS THE PROGRAM INSTEAD OF PRINTING A WARNING
        If this sum does not balance, every number the batch produced is suspect,
        including the promise counts and the shipping bill. Carrying on and printing
        a tidy report built on broken arithmetic would be worse than crashing,
        because somebody might believe it.
    """
    # Add up every unit we said we would ship, kept per warehouse AND per product.
    shipped = {}
    for d in decisions:
        if d["status"] in (STATUS_ROUTED, STATUS_ROUTED_LATE):
            key = (d["selected_fc"], d["sku"])
            shipped[key] = shipped.get(key, 0) + d["quantity"]

    # Collect every warehouse-and-product pair that appears anywhere: in the before
    # picture, in the after picture, or in something we shipped.
    #
    # Reading only the "before" picture would leave a hole. A product that somehow
    # appeared out of nowhere during the batch would exist only in the "after"
    # picture, so we would never look at it, and inventing stock would go unnoticed.
    pairs = set()
    for fc_id, levels in starting_inventory.items():
        pairs.update((fc_id, sku) for sku in levels)
    for fc_id, levels in ending_inventory.items():
        pairs.update((fc_id, sku) for sku in levels)
    pairs.update(shipped.keys())

    for fc_id, sku in sorted(pairs):
        started = starting_inventory.get(fc_id, {}).get(sku, 0)
        remaining = ending_inventory.get(fc_id, {}).get(sku, 0)
        sent = shipped.get((fc_id, sku), 0)

        if started != sent + remaining:
            raise AssertionError(
                f"Stock does not add up at {fc_id} for {sku}: "
                f"started with {started}, shipped {sent}, {remaining} left over. "
                f"Shipped plus left over should equal what we started with."
            )

        # A shelf can be empty. It cannot hold less than nothing. If it does, we
        # have promised the same unit to more than one customer.
        if remaining < 0:
            raise AssertionError(
                f"{fc_id} has {remaining} units of {sku}, which is impossible. "
                f"This means the same stock was promised to more than one order."
            )


def route_batch(orders, network, inventory,
                policy=POLICY_PROMISE_FIRST,
                sequencing=SEQUENCE_LOWEST_SLACK_FIRST):
    """
    Runs a whole batch of orders through the engine.

    Takes: the list of orders, the network from load_network(), a set of stock
           levels from fresh_inventory(), which warehouse rules to use, and which
           running order to use.
    Gives back: a dictionary holding three things:
        "summary"     the totals for the whole batch
        "decisions"   one record per order, in the order they were handled
        "sequence"    the order IDs in the order they were handled

    IMPORTANT: THIS USES UP THE STOCK YOU PASS IN
        Every routed order takes units off a shelf, and the next order sees the
        shelves as they are then. That is exactly the behaviour we want, because it
        is what creates the competition between orders.

        It also means you cannot run two batches on the same stock levels and
        compare them. The first run would eat the stock and the second would be
        routing an empty warehouse. Every comparison run has to start from its own
        fresh_inventory(network).

    WHY THE DECISIONS COME BACK IN THE ORDER THEY WERE HANDLED
        Because that IS the story. Seeing that ORD-1008 was dealt with second and
        ORD-1007 sixth is what explains why both promises were kept. Sorting the
        output back into tidy order-number order would throw away the one piece of
        information this part of the engine exists to show.
    """
    # Take a snapshot of the shelves before we touch anything, so we can check the
    # arithmetic at the end. This has to be a copy, not the live stock - comparing
    # the live stock against itself would prove nothing.
    starting_snapshot = {
        fc_id: dict(levels) for fc_id, levels in inventory.items()
    }

    # Work out everybody's spare time BEFORE anything has shipped.
    #
    # This snapshot does two jobs. It decides who goes first, and later it lets us
    # say honestly whether a broken promise was ever keepable in the first place.
    slack_at_start = slack_for_every_order(orders, network, inventory)

    # Decide who goes first.
    if sequencing == SEQUENCE_LOWEST_SLACK_FIRST:
        queue = sort_orders_by_slack(orders, slack_at_start)
    else:
        # First come first served: just work down the list exactly as it arrived.
        queue = list(orders)

    # Route them one at a time. Each one sees the stock the previous ones left.
    decisions = [
        route_order(order, network, inventory, policy=policy)
        for order in queue
    ]

    # --- Now say who is actually to blame for each broken promise ---
    #
    # This is the bit that stops the engine quietly passing the buck. A single record
    # cannot tell these three situations apart, because from the inside they look
    # identical - late parcel, no time left:
    #
    #   the shop promised a date nobody could have hit      not our fault
    #   we had the time and spent it on another order        our fault: sequencing
    #   an on-time warehouse was there and we skipped it     our fault: our rules
    #
    # The only way to separate them is to compare against the spare time the order
    # had before the batch started, which is why we took that snapshot.
    #
    # This is not a rounding error. It is the difference between telling a merchant
    # to fix their checkout and admitting we dropped one ourselves.
    for d in decisions:
        started_with = slack_at_start.get(d["order_id"])
        d["slack_days_at_batch_start"] = started_with
        d["promise_was_keepable_at_batch_start"] = (
            started_with is not None and started_with >= 0
        )

        if d["status"] not in (STATUS_ROUTED_LATE, STATUS_UNROUTABLE):
            # Nothing was broken, so there is nobody to blame.
            d["promise_lost_because"] = None

        elif not d["promise_was_keepable_at_batch_start"]:
            # Either no warehouse could fill it at all, or even our best option was
            # already too slow before we touched anything. Genuinely not our doing.
            d["promise_lost_because"] = LOST_NEVER_POSSIBLE

        elif d["status"] == STATUS_UNROUTABLE:
            # It was fillable this morning and it is not fillable now, so something
            # we shipped earlier used up the stock it needed.
            d["promise_lost_because"] = LOST_TO_EARLIER_ORDERS

        elif d["slack_days_when_routed"] < 0:
            # It still had time when the day started, but by the time we got to it
            # every fast enough warehouse had been emptied by an earlier order.
            d["promise_lost_because"] = LOST_TO_EARLIER_ORDERS

        else:
            # The most uncomfortable one. There was still an on-time warehouse with
            # stock in it when we routed this order, and our rules walked past it.
            # This is exactly what shows up when the engine chases the lowest price.
            d["promise_lost_because"] = LOST_TO_OUR_OWN_RULE

    # Prove we did not invent or lose stock along the way.
    check_inventory_adds_up(starting_snapshot, inventory, decisions)

    summary = summarise_batch(decisions, len(orders))
    summary["policy"] = policy
    summary["sequencing"] = sequencing

    return {
        "summary": summary,
        "decisions": decisions,
        "sequence": [d["order_id"] for d in decisions],
    }


# ---------------------------------------------------------------------------
# PART 5 - EXPLAINING A DECISION IN PLAIN ENGLISH
# ---------------------------------------------------------------------------
#
# WHAT THIS PART IS FOR
#     Parts 1 to 4 make decisions and write them down. Nothing so far produces a
#     single line a human would want to read. That is this part's job.
#
# THE ONE RULE THIS PART OBEYS
#     Every function in here reads ONLY the saved decision record. It never works
#     anything out again, and it never looks back at the original order.
#
#     That rule sounds fussy and it is the most important thing in this part.
#     If the explaining code were allowed to recalculate, the printed page could
#     quietly drift away from the decision that was actually made. Imagine the
#     engine misread a promised date and chose badly because of it. If printing
#     went and fetched the correct date from the order, the page would look
#     perfectly sensible and you would never see the disagreement.
#
#     So: if a fact is not on the record, the engine did not use it, and we do not
#     print it. That is only worth anything if the record is complete, which is
#     why the failure records in Part 3 carry the city and the promised date even
#     though the routing never got far enough to need them.
#
# THESE FUNCTIONS RETURN TEXT, THEY DO NOT PRINT IT
#     Nothing in this file prints, including here. Every function below hands back
#     a string or a list of strings, and main.py decides what to do with them.
#
#     Two reasons. Text you can return is text you can test - a test can check the
#     exact words without having to capture screen output. And the same record can
#     then be rendered more than one way without touching any of the logic.


# --- Small formatting helpers ----------------------------------------------
#
# These four are dull on purpose. Each one exists so that a decision about how
# something looks is made in exactly ONE place.
#
# The reason that matters: money and dates get printed in a dozen spots below.
# If each spot formatted its own, sooner or later two of them would disagree -
# one saying "$8.00" and another "800 cents" for the same number - and a report
# that contradicts itself is worse than no report, because the reader has to
# stop and work out which half to believe.


def money(cents):
    """
    Turns a whole number of cents into a price people recognise.

    Takes: a whole number of cents, for example 800. None is allowed.
    Gives back: a string like "$8.00". None becomes "-".

    WHY IT DOES THE SUM WITH WHOLE NUMBERS
        The engine keeps every price as a whole number of cents from start to
        finish, so that comparing 480 against 520 is exact and can never drift.
        The obvious way to print it is cents / 100, but that turns the number
        into a decimal at the very last moment and undoes that care.

        So we split it with whole-number division instead: 800 becomes 8 and 0,
        which we write as "$8.00". No decimals are ever involved.

    WHY IT ACCEPTS None
        An order we rejected has no price, because we never got as far as looking
        one up. The table still needs something to put in that column, and "-"
        is the honest answer. Crashing on the rejected rows would mean the
        failures - the interesting cases - are the ones we cannot show.
    """
    if cents is None:
        return "-"

    # Handle the minus sign separately so the split below works on a plain
    # positive number and we never end up printing something like "$-3.-50".
    sign = "-" if cents < 0 else ""
    amount = abs(cents)

    return f"{sign}${amount // 100}.{amount % 100:02d}"


def money_gap(cents):
    """
    Turns a DIFFERENCE between two prices into words, the way a person would say it.

    Takes: a whole number of cents, which may be negative. None is allowed.
    Gives back: "50c" for anything under a dollar, otherwise "$3.00".

    WHY THIS IS SEPARATE FROM money()
        Because a price and a gap between prices get spoken differently, and the
        output is meant to read like somebody explaining a decision out loud.

        Nobody says "the fifty cent rule" as "the zero point five zero rule".
        The core rule of this whole engine is called the 50 cent rule everywhere -
        in the data notes, in the README, in this file. Printing "$0.50" would
        stop the output matching the thing it is trying to explain.

        Above a dollar the plain price form reads better, so we hand back to
        money() and stop having an opinion.
    """
    if cents is None:
        return "-"

    amount = abs(cents)
    if amount < 100:
        sign = "-" if cents < 0 else ""
        return f"{sign}{amount}c"

    return money(cents)


def pretty_date(value):
    """
    Turns a date into something readable, without ever crashing.

    Takes: a date object, or a date written as text like "2026-08-20", or None.
    Gives back: a string like "Thu 20 Aug".
                None becomes "-".
                Text it cannot read comes straight back unchanged.

    WHY THE DAY NAME IS THERE
        Because half the decisions in this engine turn on weekends. "Fri 21 Aug"
        instantly explains why the next possible day was the 24th and not the
        22nd. "2026-08-21" makes the reader go and find a calendar, and most
        will not bother, so they end up taking the engine's word for it. The
        point of this part is that they should not have to.

    WHY IT NEVER RAISES AN ERROR ON BAD TEXT
        This is the important bit. A rejected order can be holding a date the
        customer typed wrongly - that may be the exact reason it was rejected.
        The row we most need to show is the row with the broken value in it.

        So unreadable text is handed straight back as-is. The report then says
        the promised date was "next tuesday", which is not a date, and that is a
        far more useful thing to read than a crash.

    A SHORTCUT WE ARE TAKING
        No year is shown. Every order in this prototype falls inside one week of
        August 2026, so a year on every line would be noise. A real system would
        need it, because "Thu 20 Aug" is genuinely ambiguous once a batch can
        straddle New Year.
    """
    if value is None:
        return "-"

    # Already a real date object, which is what Part 1 and Part 2 pass around.
    if isinstance(value, date):
        return value.strftime("%a %d %b")

    # Text with nothing in it gets the same "-" as a missing value. Handing back
    # the empty string would leave a blank space in the table, and a blank reads
    # as a hole in the report rather than as "the customer sent us nothing", which
    # is what it actually means.
    if not str(value).strip():
        return "-"

    # Saved records store dates as text, so read it back. parse_date hands back
    # None rather than raising if the text is not a date, which is what lets us
    # show the bad value instead of falling over on it.
    parsed = parse_date(value)
    if parsed is None:
        return str(value)

    # The day number is kept zero padded - "07 Aug", not "7 Aug" - so that every
    # date is the same width and the table columns line up underneath each other.
    return parsed.strftime("%a %d %b")


def plural(count, word):
    """
    Puts a number and a word together with the right ending.

    Takes: a whole number, and a word in its singular form like "day".
    Gives back: "1 day", "2 days", "0 days".

    WHY BOTHER WITH SOMETHING THIS SMALL
        Because "1 days late" is the sort of tiny wrongness that quietly costs
        you the reader. Somebody who spots the engine cannot count to one starts
        wondering what else it got wrong, and this report is asking to be
        trusted on much harder claims than that.

    WHAT IT CANNOT DO
        It just adds an "s". That is fine here, because the only words we ever
        hand it are "day" and "unit". Anything irregular would need a proper
        lookup, and we do not have any.
    """
    if count == 1:
        return f"1 {word}"
    return f"{count} {word}s"


# --- Turning the engine's codes into English -------------------------------
#
# Every decision the engine makes gets stored as a short code: MISSES_PROMISE,
# PROTECT_PROMISE, SPLIT_REQUIRED. Codes are the right thing to store, because
# code can compare them and a test can check them exactly. But nobody wants to
# read them, and a merchant handed a page of MISSES_PROMISE has been given a
# database dump rather than an explanation.
#
# WHY THESE ARE LOOKUP TABLES AND NOT A CHAIN OF IF STATEMENTS
#     Because a table can be checked for holes and a chain of ifs cannot.
#
#     With a table, adding a new code to Part 1 and forgetting to write its
#     English shows up immediately: the lookup misses and the page says so out
#     loud. With a long if-elif-else, a forgotten code silently falls through to
#     whatever the last else happens to say - which is exactly the bug we spent
#     so long removing from Part 3, where a missing case made the engine print a
#     confident reason that was not the real one.
#
# WHY A MISSING CODE PRINTS A COMPLAINT AND NOT THE RAW CODE
#     If an unknown code just got printed as-is, the page would read
#     "MISSES_PROMISE" and a reader would assume somebody meant it to look like
#     that. Printing "(no plain English written for ...)" cannot be mistaken for
#     a design choice. It reads as what it is: unfinished work.
#
# THE RULE ABOUT NUMBERS
#     Several of these phrases are far more useful with a number in them.
#     "costs 80c more" beats "costs more" every time, because the reader can
#     check it against the two prices already sitting in front of them.
#
#     But we only ever add a number the record actually contains. If the facts
#     needed for the sum are not there, we fall back to the plain phrase instead
#     of guessing. A sentence with an invented number in it is worse than a vague
#     one, because it looks precise.


# Why a warehouse was not chosen. These go in a narrow table column, so they are
# kept short. The order here matches the order the engine checks them in.
WHY_NOT_CHOSEN_PLAIN = {
    NOT_SELECTED_MISSES_PROMISE: "arrives after the promised date",
    NOT_SELECTED_MORE_DAYS_LATE: "arrives later than ours",
    NOT_SELECTED_HIGHER_COST: "costs more",
    NOT_SELECTED_LOWER_STOCK: "would leave less stock behind",
    NOT_SELECTED_HIGHER_COST_ON_TIE: "ties on stock, but costs more",
    NOT_SELECTED_SLOWER: "same price and stock, but slower",
    NOT_SELECTED_NO_STOCK: "not enough stock to fill the order",
    NOT_SELECTED_TIE_BREAK: "identical on every rule, chosen by name order",
}

# Why we spent more than the cheapest option. These finish a sentence that
# starts with the amount, so they read as "$3.00 more than the cheapest
# option, spent to hit the promised date".
PREMIUM_REASON_PLAIN = {
    PREMIUM_NONE: "the cheapest option available",
    PREMIUM_PROTECT_PROMISE: "spent to hit the promised date",
    PREMIUM_REDUCE_LATENESS: "spent to be less late than we had to be",
    PREMIUM_PRESERVE_INVENTORY: "spent to leave a nearly empty shelf alone",
}

# Who or what broke the promise. This is the most important table in this part.
# These three sentences point at three completely different things to go and
# fix, and the whole reason the engine tracks spare time twice is to be able to
# tell them apart honestly.
PROMISE_LOST_PLAIN = {
    LOST_NEVER_POSSIBLE: (
        "this date could not have been met from any warehouse, "
        "even before the batch started"
    ),
    LOST_TO_EARLIER_ORDERS: (
        "we could have met this date when the batch started, "
        "but an earlier order took the stock first"
    ),
    LOST_TO_OUR_OWN_RULE: (
        "an on-time warehouse still had stock, and our own rule walked past it"
    ),
}


def _look_up(table, code, missing="no reason recorded"):
    """
    Reads a phrase out of one of the tables above, and complains if it is missing.

    Takes: one of the PLAIN tables, a code to look up, and what to say when there
           is no code at all.
    Gives back: the English phrase, or a visible complaint if there is not one.

    This is the only place any of those tables gets read. That is deliberate -
    it means the "what if the code is unknown" question is answered once, the
    same way everywhere, instead of being forgotten in one spot out of nine.

    WHY THE "NOTHING RECORDED" WORDING CAN BE CHANGED
        Most of these tables hold reasons, so "no reason recorded" is the right
        thing to say when the field is empty. The batch summary uses the same
        function to look up which rule the run followed, and "Goal: no reason
        recorded" would read like a bug rather than a missing field. Same one
        lookup path, wording that suits the caller.
    """
    if code is None:
        return missing
    return table.get(code, f"(no plain English written for code {code})")


def _gap(theirs, ours):
    """
    Subtracts two numbers, but gives back None if the sum cannot honestly be done.

    Takes: two numbers, either of which may be None.
    Gives back: the difference, or None if either input is missing or the
                difference is not positive.

    WHY IT REFUSES TO RETURN ZERO OR A NEGATIVE
        Everything this is used for phrases the answer as "X more" or "X later".
        If the gap came out as zero or below, that sentence would be false, so
        the caller needs to know not to write it. Handing back None says "do not
        put a number in this sentence" in a way the caller cannot ignore by
        accident.
    """
    if theirs is None or ours is None:
        return None
    difference = theirs - ours
    if difference <= 0:
        return None
    return difference


def describe_why_not_chosen(alternative, decision):
    """
    Says in English why one warehouse lost, with the numbers filled in.

    Takes: one entry from a record's "alternatives" list, and the whole decision
           record it came from (which is where the winning warehouse's numbers
           live, so we can work out the gap).
    Gives back: a short string for the "Why not chosen" column.

    A NOTE ON THE TWO SHAPES OF ALTERNATIVE
        Rejected warehouses come in two kinds and they carry different facts.
        A warehouse that could have filled the order has a price, an arrival date
        and a stock-after figure. A warehouse without enough stock has none of
        those, because the engine never got as far as pricing it - all it has is
        how much stock it did have. So everything below uses .get() and checks
        before it does arithmetic.
    """
    code = alternative.get("reason_not_selected")
    plain = _look_up(WHY_NOT_CHOSEN_PLAIN, code)

    # --- The warehouses that simply did not have the goods ---
    #
    # Careful wording here. A shelf showing zero might be a product this
    # warehouse never carries, or one it normally carries and has sold out of
    # today. Our data genuinely cannot tell those apart, so we say only what we
    # know for certain: there is nothing on the shelf right now. Writing "never
    # stocks this" would be inventing a fact we do not have.
    if code == NOT_SELECTED_NO_STOCK:
        had = alternative.get("inventory_before")
        needed = decision.get("quantity")
        sku = decision.get("sku")
        if had is None or sku is None:
            return plain
        if had == 0:
            return f"no {sku} on the shelf"
        if needed is None:
            return f"only {plural(had, 'unit')} on the shelf"
        # The word "only" is itself a claim: that what was on the shelf was less
        # than what the order needed. That is always true today, because a
        # warehouse only lands in this list when its stock is short.
        #
        # We check it anyway rather than trust it. If a later version learns to
        # fill one order from two warehouses, this list could hold a warehouse
        # that had plenty, and "only 5 on the shelf, needs 2" is a sentence that
        # argues with itself. Falling back to the plain phrase says less, but
        # everything it says stays true.
        if had >= needed:
            return plain
        return f"only {plural(had, 'unit')} on the shelf, needs {needed}"

    # --- Beaten on price, by enough to fall outside the 50 cent window ---
    #
    # We measure the gap against the warehouse we CHOSE, not against the cheapest
    # one the rule actually used as its anchor. That is on purpose. Both prices
    # are sitting in the table right in front of the reader, so this is the one
    # subtraction they can check with their own eyes.
    if code == NOT_SELECTED_HIGHER_COST:
        gap = _gap(alternative.get("shipping_cost_cents"),
                   decision.get("shipping_cost_cents"))
        return plain if gap is None else f"costs {money_gap(gap)} more"

    # --- Level on stock, so the tie fell to price ---
    if code == NOT_SELECTED_HIGHER_COST_ON_TIE:
        gap = _gap(alternative.get("shipping_cost_cents"),
                   decision.get("shipping_cost_cents"))
        return plain if gap is None else f"ties on stock, costs {money_gap(gap)} more"

    # --- Everything was going to be late, and this one was later ---
    if code == NOT_SELECTED_MORE_DAYS_LATE:
        gap = _gap(alternative.get("days_late"), decision.get("days_late"))
        return plain if gap is None else f"{plural(gap, 'day')} later than ours"

    # --- Would have emptied a thinner shelf ---
    if code == NOT_SELECTED_LOWER_STOCK:
        theirs = alternative.get("inventory_after")
        ours = decision.get("inventory_after")
        if theirs is None or ours is None:
            return plain
        return f"leaves {theirs} behind, ours leaves {ours}"

    # --- Same money, same stock, just slower ---
    if code == NOT_SELECTED_SLOWER:
        gap = _gap(alternative.get("transit_days"), decision.get("transit_days"))
        return plain if gap is None else f"same price and stock, {plural(gap, 'day')} slower"

    # Missing the promise, and the pure alphabetical tie-break, have no useful
    # number to add. The arrival dates are already in the table beside them.
    return plain


def describe_premium(decision):
    """
    Says what the choice cost us over the cheapest option, and why we spent it.

    Takes: one decision record for an order that shipped.
    Gives back: a sentence fragment like "$3.00 more than the cheapest option,
                spent to hit the promised date", or "the cheapest option
                available" when nothing extra was spent. Empty string if the
                record has no premium on it, which is the case for orders that
                never shipped.

    WHY THE "NOTHING EXTRA" CASE STILL GETS SAID OUT LOUD
        Because it is the only proof the engine is not just buying the fast
        expensive option every time. An engine that always overspent would look
        completely identical on the hard orders. The only place the difference
        shows is on the easy ones, where a restrained engine says plainly that it
        took the cheapest thing on the table.
    """
    premium = decision.get("premium_over_cheapest_cents")
    reason = decision.get("premium_reason")

    if premium is None or reason is None:
        return ""

    phrase = _look_up(PREMIUM_REASON_PLAIN, reason)

    if premium == 0:
        return phrase

    # Name the warehouse we are measuring against, and its price, so the reader
    # can do the subtraction themselves against two rows of the table.
    #
    # Without the name this said "$3.00 more than the cheapest option", and that
    # sent people hunting for a warehouse costing $3.00. There is never one,
    # because $3.00 is the gap between two prices rather than a price. Naming
    # FC-PENNSYLVANIA at $5.00 turns an assertion into something checkable.
    cheapest_fc = decision.get("cheapest_available_fc")
    cheapest_cost = decision.get("cheapest_available_cost_cents")

    # Older records may not carry the name. Say the vaguer thing rather than
    # guess which warehouse it was.
    if cheapest_fc is None or cheapest_cost is None:
        return f"{money_gap(premium)} more than the cheapest option, {phrase}"

    return (
        f"{money_gap(premium)} more than {cheapest_fc} "
        f"at {money(cheapest_cost)}, {phrase}"
    )


def describe_promise_lost(decision):
    """
    Says who is actually to blame for a broken promise.

    Takes: one decision record.
    Gives back: a sentence, or an empty string if this order's promise was kept.

    WHY THIS IS THE MOST USEFUL SENTENCE THE ENGINE PRODUCES
        Because the three answers send you to three different places, and getting
        it wrong wastes real work.

            Never possible          Go and fix the checkout. It is quoting dates
                                    the warehouse network cannot reach. Routing
                                    can do nothing about it.
            Earlier orders took it  Go and fix the running order. Costs nothing,
                                    and our own batch proves it works.
            Our own rule            Go and fix the rule. It walked past a
                                    warehouse that would have been on time.

        An earlier version of the engine could not tell the first two apart, and
        reported the second as the first. Anyone acting on that would have gone
        off to rewrite the checkout while the real cause sat untouched in the
        routing code.
    """
    cause = decision.get("promise_lost_because")
    if cause is None:
        return ""

    # "Never possible" covers two situations that need different words, and using
    # one sentence for both sends the reader to the wrong place.
    #
    # Normally it means the order was perfectly shippable and the promised date
    # was simply tighter than our fastest warehouse can manage. That is ORD-1004:
    # promised the 18th, and nothing on the west coast lands before the 19th. The
    # calendar really is the story there, and the checkout really is what to fix.
    #
    # But it also catches orders we could not ship AT ALL, where the date never
    # entered into it. ORD-1005 asks for 25 units and no single warehouse holds
    # that many. Telling somebody "this date could not have been met" about that
    # order points them at the checkout, when the promise was actually a
    # comfortable week away and the thing to fix is that we cannot split an order
    # across two sites. The stock is the story, not the calendar.
    if cause == LOST_NEVER_POSSIBLE and decision.get("status") == STATUS_UNROUTABLE:
        return (
            "no warehouse could fill this order at all, "
            "so the promised date never came into it"
        )

    return _look_up(PROMISE_LOST_PLAIN, cause)


def describe_cannot_fill(decision):
    """
    Says why no warehouse could fill an order, for the two very different reasons.

    Takes: one decision record with status UNROUTABLE.
    Gives back: a sentence.

    WHY THE TWO REASONS ARE KEPT APART
        They look the same on a screen - the order did not ship - and they are
        nothing alike underneath.

            Out of stock     We do not own enough. The fix is buying more, or
                            telling the shopper sooner.
            Split required   We DO own enough, it is just spread across sites.
                            The fix is code: allow one order to ship in two
                            boxes. Nothing needs buying at all.

        Reporting both as "out of stock" would hide a problem you could solve for
        free behind one that costs money.
    """
    reason = decision.get("reason")
    sku = decision.get("sku")
    units = decision.get("units_in_network")
    needed = decision.get("quantity")

    if reason == REASON_OUT_OF_STOCK:
        if units == 0:
            return f"we have no {sku} left anywhere in the network"
        if units is None or needed is None:
            return f"we do not have enough {sku} anywhere in the network"
        return (
            f"we own {plural(units, 'unit')} of {sku} in total, "
            f"and this order needs {needed}"
        )

    if reason == REASON_SPLIT_REQUIRED:
        if units is None or needed is None:
            return (
                f"we own enough {sku} in total, but it is spread across warehouses "
                f"and no single one can fill this order"
            )
        return (
            f"we own {plural(units, 'unit')} of {sku} in total, but no single "
            f"warehouse holds {needed}. Filling this would mean splitting the "
            f"order across two sites, which this prototype does not do"
        )

    return f"(no plain English written for code {reason})"


def describe_bad_order(decision):
    """
    Says what was wrong with an order we refused to even try routing.

    Takes: one decision record with status INVALID_ORDER.
    Gives back: a sentence.

    WHY IT ECHOES THE BAD VALUE BACK
        Because the bad value IS the explanation. "UNKNOWN_DESTINATION" tells you
        a rule fired. "we do not ship to Miami, FL" tells you what to go and do
        about it. One is a log line, the other is an answer.

    WHY MISSING AND WRONG ARE WORDED DIFFERENTLY
        A field that was left out and a field filled in wrongly need different
        fixing, so they get different sentences. Printing the word None at
        somebody, which is what would happen if we just dropped the raw value
        into the sentence, tells them neither.
    """
    reason = decision.get("reason")

    if reason == REASON_INVALID_QUANTITY:
        quantity = decision.get("quantity")
        if quantity is None:
            return "no quantity was given"
        return f"the quantity ({quantity}) is not a whole number above zero"

    if reason == REASON_UNKNOWN_SKU:
        sku = decision.get("sku")
        if sku is None:
            return "no product code was given"
        return f"we do not stock {sku} at any warehouse"

    if reason == REASON_UNKNOWN_DESTINATION:
        destination = decision.get("destination")
        if destination is None:
            return "no destination was given"
        return (
            f'we do not ship to "{destination}". It is not one of the cities we '
            f"have shipping rates for, and guessing which area it belongs to "
            f"would mean quoting a delivery date built on a made up number"
        )

    if reason == REASON_INVALID_DATE_FORMAT:
        return (
            f"we could not read the dates on this order - it says it was ordered "
            f'"{decision.get("order_date")}" and promised '
            f'"{decision.get("promised_delivery_date")}"'
        )

    if reason == REASON_INVALID_PROMISE_DATE:
        return (
            f"delivery was promised for {pretty_date(decision.get('promised_delivery_date'))}, "
            f"which is before the order was even placed on "
            f"{pretty_date(decision.get('order_date'))}"
        )

    return f"(no plain English written for code {reason})"


# --- Laying out one order as a readable block -------------------------------
#
# WHAT THIS SECTION BUILDS
#     One block of text per order: a heading line saying what was ordered, a
#     small table of every warehouse we looked at, and a verdict underneath.
#
# WHY A TABLE AND NOT A PARAGRAPH
#     Because the point being made is a comparison. "We chose Dallas because
#     Pennsylvania was cheaper but slower" is a sentence you have to trust. Four
#     rows with the prices and dates in them is something you can check, and
#     checking is the whole difference between an explanation and a claim.
#
#     It also shows the options we turned DOWN, which is the part most systems
#     throw away. A decision only looks reasonable next to the alternatives.

REPORT_WIDTH = 78

DECISION_TABLE_HEADINGS = ("Warehouse", "Cost", "Arrives", "Status", "Why not chosen")

# One letter per column: l for left, r for right. Prices are right aligned so the
# dollar amounts stack up underneath each other and can be compared at a glance,
# which is the entire reason the cost column is there.
DECISION_TABLE_ALIGN = "lrlll"


def _tidy(text, capitalise=True):
    """
    Tidies a phrase up into a proper sentence.

    Takes: a phrase, and whether to put a capital letter on the front.
    Gives back: the phrase with a capital letter and a full stop, or an empty
                string if there was nothing to tidy.

    WHY THE CAPITAL IS OPTIONAL
        The describe_ functions all hand back lowercase phrases, because most of
        them end up in a narrow table cell where a capital letter would look odd.
        But the same phrases also get used in prose, where a lowercase start
        looks like a mistake. Rather than keep two versions of every phrase, we
        keep the lowercase one and dress it up at the point of use.

        The capital gets turned off when the phrase lands mid-sentence, after
        something like "Why the promise broke:".
    """
    text = text.strip()
    if not text:
        return ""
    if capitalise:
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def _wrap(text, indent="  "):
    """
    Breaks a long sentence over several lines so it fits a terminal.

    Takes: a piece of text, and what to put at the start of every line.
    Gives back: a list of lines, already indented. An empty list for empty text.

    Some of the explanations are genuinely long - the one about splitting an order
    across two warehouses runs to about thirty words - because the short version
    would leave out the reason. Wrapping keeps them readable without shortening
    them into something vague.
    """
    if not text.strip():
        return []
    return textwrap.wrap(
        text,
        width=REPORT_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def _lay_out_table(headings, rows, align, indent="  "):
    """
    Turns rows of cells into lines of text with the columns lined up.

    Takes: the column headings, the rows (each a list of cells), a string of
           l and r letters saying how to line each column up, and an indent.
    Gives back: a list of lines - headings, a row of dashes, then the rows.

    WHY THE WIDTHS ARE MEASURED RATHER THAN FIXED
        Every column is made as wide as its widest cell, worked out from the
        actual contents each time. Hard coding widths would look fine today and
        then quietly break the moment somebody adds a warehouse with a longer
        name, or the day a shipping cost goes over $100. A misaligned table is a
        small thing that makes a reader trust the numbers in it less.

        The last column is not padded when it is left aligned, because nothing
        sits to the right of it and trailing spaces are just invisible mess.

        It IS padded when it is right aligned, because right aligning something
        means nothing without a width to align it inside. The comparison table in
        5e ends in a column of money, and those figures have to stack up under
        each other like every other money column in this report.
    """
    column_count = len(headings)

    widths = []
    for index in range(column_count):
        cells = [str(headings[index])] + [str(row[index]) for row in rows]
        widths.append(max(len(cell) for cell in cells))

    def build(cells):
        parts = []
        for index, cell in enumerate(cells):
            text = str(cell)
            if index == column_count - 1 and align[index] != "r":
                parts.append(text)
            elif align[index] == "r":
                parts.append(text.rjust(widths[index]))
            else:
                parts.append(text.ljust(widths[index]))
        return (indent + "  ".join(parts)).rstrip()

    lines = [build(headings)]
    lines.append(build(["-" * width for width in widths]))
    lines.extend(build(row) for row in rows)
    return lines


def _order_headline(decision):
    """
    Builds the one line at the top of an order's block.

    Takes: any decision record.
    Gives back: a string like
                "ORD-1001   Los Angeles, CA   1 x SKU-A   promised Thu 20 Aug".

    WHY EVERY PIECE IS CHECKED BEFORE IT IS USED
        A rejected order can be missing almost anything - that may be exactly why
        it was rejected. So each piece is checked, and anything absent is named as
        absent rather than printed as the word None. "(no destination)" tells a
        reader what to go and fix. "None" tells them our code leaked.
    """
    pieces = [decision.get("order_id") or "(no order id)"]

    destination = decision.get("destination")
    pieces.append(destination if destination else "(no destination)")

    quantity = decision.get("quantity")
    sku = decision.get("sku")
    if quantity is not None and sku:
        pieces.append(f"{quantity} x {sku}")
    elif sku:
        pieces.append(str(sku))
    elif quantity is not None:
        pieces.append(f"{quantity} x (no product)")
    else:
        pieces.append("(nothing recorded)")

    pieces.append("promised " + pretty_date(decision.get("promised_delivery_date")))

    return "   ".join(pieces)


def _status_cell(alternative):
    """
    Works out the Status cell for one warehouse we did not choose.

    Takes: one entry from a record's "alternatives" list.
    Gives back: a short string - "no stock", "on time", or "2 days late".

    This column exists so the timing can be read without doing date arithmetic in
    your head. The Arrives column already holds the date, but nobody wants to work
    out whether Friday the 21st beats a Thursday the 20th promise while reading
    down a list of four warehouses.
    """
    # "No stock" and "not enough" are different things and the column has to say
    # which. On ORD-1005 Los Angeles is holding 19 units and still cannot fill an
    # order for 25. Printing "no stock" beside a shelf with 19 units on it is
    # simply untrue, and it hides the actual problem: we own plenty, it is just
    # spread across four buildings.
    if alternative.get("reason_not_selected") == NOT_SELECTED_NO_STOCK:
        had = alternative.get("inventory_before")
        if had is None or had == 0:
            return "no stock"
        return "not enough"

    if alternative.get("promise_met"):
        return "on time"

    days_late = alternative.get("days_late")
    if days_late is None:
        return "-"

    return f"{plural(days_late, 'day')} late"


def _table_rows(decision):
    """
    Builds every row of one order's comparison table.

    Takes: a decision record.
    Gives back: a list of rows, each a list of five cells matching
                DECISION_TABLE_HEADINGS. Empty if there is nothing to compare.

    WHY THE CHOSEN WAREHOUSE GOES FIRST
        Because the first question anybody asks is "which one did we use". Sorting
        it in with the others by price would be tidier and would bury the answer.

    WHY THE CHEAPEST ROW GETS LABELLED
        The verdict underneath says something like "$3.00 more than
        FC-PENNSYLVANIA at $5.00". That sum has to be checkable against the table,
        so the row it refers to is marked. Without the label the reader is left
        looking for a warehouse that costs $3.00, and there is never one, because
        $3.00 is a gap between two prices rather than a price.

        The label only goes on when we actually did spend more. If we took the
        cheapest option ourselves there is nothing to compare against, and
        labelling some other row "cheapest" while the verdict says we paid nothing
        extra would just read as a contradiction.
    """
    rows = []

    if decision.get("selected_fc"):
        rows.append([
            decision["selected_fc"],
            money(decision.get("shipping_cost_cents")),
            pretty_date(decision.get("expected_delivery_date")),
            "CHOSEN",
            "-",
        ])

    premium = decision.get("premium_over_cheapest_cents") or 0
    cheapest_fc = decision.get("cheapest_available_fc")

    for alternative in decision.get("alternatives", []):
        why = describe_why_not_chosen(alternative, decision)

        if premium > 0 and alternative.get("fc_id") == cheapest_fc:
            why = f"cheapest, but {why}"

        rows.append([
            alternative.get("fc_id", "(unnamed)"),
            money(alternative.get("shipping_cost_cents")),
            pretty_date(alternative.get("expected_delivery_date")),
            _status_cell(alternative),
            why,
        ])

    return rows


def _verdict_lines(decision):
    """
    Builds the sentences underneath an order's table.

    Takes: a decision record.
    Gives back: a list of lines.

    There is one branch per outcome, because the four outcomes are genuinely
    different pieces of news and flattening them into one shape would hide that.
    An order that shipped late and an order that never shipped at all are not
    variations on a theme.
    """
    status = decision.get("status")
    lines = []

    if status == STATUS_INVALID_ORDER:
        lines += _wrap("REJECTED - " + _tidy(describe_bad_order(decision)))

    elif status == STATUS_UNROUTABLE:
        lines += _wrap("NOT SHIPPED - " + _tidy(describe_cannot_fill(decision)))

    else:
        warehouse = decision.get("selected_fc")
        paid = money(decision.get("shipping_cost_cents"))
        money_phrase = describe_premium(decision)
        premium = decision.get("premium_over_cheapest_cents") or 0

        if status == STATUS_ROUTED:
            opening = f"PROMISE KEPT - shipped from {warehouse} for {paid}"
        else:
            days_late = decision.get("days_late") or 0
            opening = (
                f"LATE BY {plural(days_late, 'DAY').upper()} - shipped from "
                f"{warehouse} for {paid}, arriving "
                f"{pretty_date(decision.get('expected_delivery_date'))} against a "
                f"{pretty_date(decision.get('promised_delivery_date'))} promise"
            )

        # When nothing extra was spent, the money phrase is a short tag like "the
        # cheapest option available", so it reads better hung on the end of the
        # sentence than left standing on its own as a line.
        if premium == 0 and money_phrase:
            lines += _wrap(_tidy(f"{opening}, {money_phrase}"))
        else:
            lines += _wrap(_tidy(opening))
            if money_phrase:
                lines += _wrap(_tidy(f"That is {money_phrase}", capitalise=False))

    # Whose fault a broken promise was. This deliberately comes last, because it
    # is the sentence somebody should act on, and it is the one worth leaving a
    # reader with.
    lost = describe_promise_lost(decision)
    if lost:
        lines += _wrap("Why the promise broke: " + _tidy(lost, capitalise=False))

    return lines


def format_decision(decision):
    """
    Turns one decision record into a block of readable text.

    Takes: one decision record, of any of the four outcomes.
    Gives back: a list of lines. It does NOT print them - main.py does that.

    WHY IT HANDS BACK LINES INSTEAD OF PRINTING
        A list of lines can be tested. A test can check that the words
        "PROMISE KEPT" and "FC-DALLAS" appear for ORD-1001 without having to
        capture anything off the screen, which means the wording of this report is
        as testable as the routing rules underneath it.

        It also keeps the promise the top of this file makes: nothing in
        router.py prints. Deciding and displaying stay separate jobs.

    WHAT A BLOCK LOOKS LIKE
        ORD-1001   Los Angeles, CA   1 x SKU-A   promised Thu 20 Aug

          Warehouse          Cost  Arrives     Status    Why not chosen
          ---------------  ------  ----------  --------  --------------
          FC-DALLAS         $8.00  Thu 20 Aug  CHOSEN    -
          FC-PENNSYLVANIA   $5.00  Fri 21 Aug  1 day...  cheapest, but arrives...
          ...

          PROMISE KEPT - shipped from FC-DALLAS for $8.00.
          That is $3.00 more than FC-PENNSYLVANIA at $5.00, spent to hit the
          promised date.
    """
    lines = [_order_headline(decision)]

    rows = _table_rows(decision)

    # A rejected order has no table at all, and that is the honest thing to show.
    # We never worked out a single price for it, so inventing an empty table with
    # dashes in it would suggest we looked at four warehouses and found nothing,
    # when the truth is we never got as far as looking.
    if rows:
        lines.append("")
        lines += _lay_out_table(DECISION_TABLE_HEADINGS, rows, DECISION_TABLE_ALIGN)

    verdict = _verdict_lines(decision)
    if verdict:
        lines.append("")
        lines += verdict

    return lines


# --- 5d. The batch summary --------------------------------------------------
#
# WHAT THIS BLOCK IS FOR
#     5c explains one order. This explains a whole run of them in one screen.
#
# WHY A RUN NEEDS ITS OWN PAGE
#     Ten separate order blocks tell you ten separate stories and no overall
#     result. The thing worth arguing about is not "was Dallas the right call for
#     ORD-1001", it is "did following this rule for a whole day leave us better
#     off than following a different one". You cannot see that one order at a time.
#
# THE TWO NUMBERS THIS PAGE EXISTS TO PUT SIDE BY SIDE
#     How many customers got the date they were told, and what the batch cost.
#     Those two pull against each other, and any engine that reports only one of
#     them is flattering itself. Printed together they turn "we protect promises"
#     into something a reader can price.
#
# WHY ZEROS ARE STILL PRINTED
#     The most useful line on this page is often the one showing nothing went
#     wrong in a particular way. "0  our own rule chose a late option" looks like
#     filler until you run the cost-first policy and watch that same line climb.
#     A report that hides its zeros cannot be compared against another run, and
#     comparing runs is the entire point.

# The three causes of a broken promise again, shortened to fit a column.
#
# PROMISE_LOST_PLAIN holds full sentences, which is right when a single order is
# explaining itself in prose and far too wide when three counts have to line up
# next to each other. So there are two wordings of the same three ideas.
#
# The duplication is a real cost and worth naming: these two tables must stay in
# step. Same three keys, and the short version must never claim anything the long
# version does not. If a fourth cause is ever added, it has to be added twice.
PROMISE_LOST_SHORT = {
    LOST_NEVER_POSSIBLE: "never possible from any warehouse",
    LOST_TO_EARLIER_ORDERS: "an earlier order took the stock",
    LOST_TO_OUR_OWN_RULE: "our own rule chose a late option",
}

# What each of the two goals actually means, in words rather than a code.
# Printing "PROMISE_FIRST" at somebody is not telling them anything they could
# disagree with. Spelling the rule out is what makes the result checkable.
POLICY_PLAIN = {
    POLICY_PROMISE_FIRST: (
        "protect the promised delivery date first, then keep the cost down"
    ),
    POLICY_COST_FIRST: (
        "take the cheapest warehouse that has the stock, promise or no promise"
    ),
}

# And what order the orders were worked through in. This matters more than it
# sounds: on this data it is worth one extra kept promise for no extra money.
SEQUENCE_PLAIN = {
    SEQUENCE_LOWEST_SLACK_FIRST: (
        "tightest promise first - whichever order has the least spare time goes next"
    ),
    SEQUENCE_FIRST_COME_FIRST_SERVED: (
        "first come first served - straight down the list as the orders arrived"
    ),
}


def _lay_out_counts(rows, indent="    "):
    """
    Lays out "number then label" rows with the numbers lined up.

    Takes: a list of rows. Each row is either a (count, label) pair, or the plain
           string "rule", which draws a short line of dashes under the numbers.
    Gives back: a list of lines.

    WHY THE NUMBERS ARE RIGHT ALIGNED
        So that 6, 1, 2, 1 and the total 10 all finish in the same column. A
        column of figures that do not line up is much harder to add up by eye,
        and adding them up by eye is the first thing anybody checking this report
        will try to do. Making that easy is the difference between a summary that
        gets trusted and one that gets skimmed.
    """
    numbers = [str(row[0]) for row in rows if row != "rule"]
    width = max(len(number) for number in numbers) if numbers else 1

    lines = []
    for row in rows:
        if row == "rule":
            lines.append(indent + "-" * width)
        else:
            count, label = row
            lines.append(f"{indent}{str(count).rjust(width)}   {label}")
    return lines


def _lay_out_money(rows, indent="    "):
    """
    Lays out "label then amount" rows with the amounts lined up on the right.

    Takes: a list of (label, cents) pairs.
    Gives back: a list of lines.

    The amounts are the opposite way round from _lay_out_counts - label first,
    figure last - because these are a short list of totals being read as a bill,
    and a bill puts the money in the right hand column. Same reason as before
    though: the figures stack up so they can be compared without effort.
    """
    labels = [label for label, _ in rows]
    amounts = [money(cents) for _, cents in rows]

    label_width = max(len(label) for label in labels)
    amount_width = max(len(amount) for amount in amounts)

    return [
        f"{indent}{label.ljust(label_width)}   {amount.rjust(amount_width)}"
        for label, amount in zip(labels, amounts)
    ]


def format_batch_summary(summary, sequence=None):
    """
    Turns the totals from one batch run into a readable block.

    Takes: the summary dictionary route_batch hands back, and optionally the list
           of order ids in the order they were actually worked through.
    Gives back: a list of lines. Like everything else in Part 5, it prints nothing.

    WHY THE ORDER SEQUENCE IS OPTIONAL
        The summary on its own says which sequencing rule was used. It cannot
        show what that rule actually did. Two runs claiming different sequencing
        rules and producing the same running order would be a real bug, and the
        only way to see it is to print the running order. But it is a long line
        and not every caller wants it, so it is offered rather than forced.

    WHY THIS FUNCTION CHECKS ITS OWN ARITHMETIC
        Twice below, it adds up numbers that came from somewhere else and
        compares the answer against a total that also came from somewhere else.
        If those disagree it prints a warning line instead of quietly showing
        both.

        This is the same argument as the stock check in Part 4. A summary whose
        numbers do not add up is worse than no summary, because somebody will
        read the two figures they care about and never notice the other four do
        not support them. A visible complaint costs three lines and makes a whole
        family of silent counting bugs impossible to hide.
    """
    lines = ["BATCH SUMMARY", ""]

    lines += _wrap(
        "Goal: " + _look_up(
            POLICY_PLAIN, summary.get("policy"), missing="(no goal recorded)"
        )
    )
    lines += _wrap(
        "Order we worked in: " + _look_up(
            SEQUENCE_PLAIN, summary.get("sequencing"), missing="(no sequencing recorded)"
        )
    )

    # Joined with commas rather than arrows. Arrows read better on one line and
    # badly on two, because a wrapped line then starts with "-> ", which looks
    # like a bullet point rather than a continuation. The label already says this
    # is a running order, so the commas lose nothing.
    if sequence:
        lines += _wrap("Which worked through them in this order: " + ", ".join(sequence))

    # --- What happened to every order -------------------------------------
    #
    # These four are deliberately kept apart rather than rolled into "7 fine, 3
    # not". Each one needs a completely different fix. A late parcel is a routing
    # problem. An order we could not fill is a buy-more-stock problem. A rejected
    # order is a broken shop page. One combined failure count would hide which of
    # the three levers to pull.
    on_time = summary.get("routed_on_time", 0)
    late = summary.get("routed_late", 0)
    unroutable = summary.get("unroutable", 0)
    invalid = summary.get("invalid", 0)
    order_count = summary.get("order_count", 0)

    lines.append("")
    lines.append("  WHAT HAPPENED TO EVERY ORDER")
    lines += _lay_out_counts([
        (on_time, "shipped, arriving on or before the promised date"),
        (late, "shipped, but arriving after the promised date"),
        (unroutable, "not shipped - no single warehouse could fill it"),
        (invalid, "rejected before we priced anything, because the order was wrong"),
        "rule",
        (order_count, "orders went into the batch"),
    ])

    counted = on_time + late + unroutable + invalid
    if counted != order_count:
        lines += _wrap(
            f"WARNING: those four lines add up to {counted}, but {order_count} "
            "orders went in. Some order finished in a state this summary does "
            "not know how to count, so do not trust the figures below either.",
        )

    # --- The promise ------------------------------------------------------
    #
    # promises_kept and promises_broken are read from the record by name rather
    # than worked out again here, because they are the two numbers the whole
    # engine is judged on. Recalculating them in the printing code would let the
    # page and the record drift apart on exactly the claim that matters most.
    #
    # Worth knowing what promises_broken does NOT include: an order we could not
    # ship at all. Part 4 counts that separately on purpose, so it gets its own
    # line here rather than being quietly folded in. A customer would call both a
    # broken promise, and the line below says so in words.
    kept = summary.get("promises_kept", 0)
    broken_by_shipping_late = summary.get("promises_broken", 0)
    valid = summary.get("valid_orders", 0)
    days_late = summary.get("total_days_late", 0)

    lines.append("")
    lines.append("  THE PROMISE")
    lines += _lay_out_counts([
        (kept, f"promises kept, out of {valid} orders we could actually act on"),
        (broken_by_shipping_late, "broken by shipping late"),
        (unroutable, "broken by not shipping at all"),
    ])

    # Days late as well as broken promises, because one order five days late and
    # five orders one day late break the same number of promises and are not the
    # same failure to the people waiting.
    lines += _wrap(
        f"Total lateness across the batch: {plural(days_late, 'day')}."
    )

    # --- The bill ---------------------------------------------------------
    bill = summary.get("shipping_cost_cents", 0)
    extra = summary.get("premium_over_cheapest_cents", 0)

    lines.append("")
    lines.append("  THE BILL")
    lines += _lay_out_money([
        ("What the batch cost to ship", bill),
        ("Of that, spent above the cheapest option", extra),
    ])

    if extra == 0:
        lines += _wrap(
            "Nothing extra was spent - every order went out on the cheapest "
            "warehouse that could fill it."
        )
    elif bill > 0:
        # Integer division on purpose, like every other sum in this engine, so no
        # decimal ever creeps into money. It rounds down, so the figure is a floor
        # rather than a flattering round-up.
        share = extra * 100 // bill
        # THE SENTENCE THIS USED TO SAY, AND WHY IT WAS WRONG
        #     It used to end "each order block above says what its own share of
        #     that bought". That was true when the summary followed a full set of
        #     order blocks and false the moment a caller printed the totals on
        #     their own - which is exactly what main.py does for the two runs it
        #     only wants to measure. The page then pointed at detail that was
        #     never printed.
        #
        #     A formatting function cannot know what else got printed around it,
        #     so it has no business referring to it. It now names the three things
        #     the money can buy and says the record holds the answer, which is
        #     true wherever this block ends up.
        #
        #     All three reasons have to be listed, not just the obvious one. In
        #     the sample batch this figure is made of one payment to hit a date,
        #     one to be less late than we had to be, and one to leave a thin shelf
        #     alone. "Spent to protect promised dates" would have been a tidier
        #     sentence and would have misdescribed two thirds of the money.
        lines += _wrap(
            f"So about {share}% of the bill was a deliberate choice rather than "
            "the cheapest route - spent to hit a promised date, to be less late "
            "than we had to be, or to leave a nearly empty shelf alone. Each "
            "order's own record says which of the three it was."
        )

    # --- Whose fault the broken promises were -----------------------------
    #
    # This is the most useful section on the page and the least flattering, which
    # is why it goes last. The first line is not our fault and we cannot fix it in
    # the router. The other two are ours.
    #
    # Note this section counts BOTH kinds of broken promise - shipped late and not
    # shipped at all - so its total is larger than promises_broken above. That is
    # why the heading counts them out loud instead of leaving a reader to assume
    # the two sections are about the same set of orders.
    never = summary.get("lost_never_possible", 0)
    earlier = summary.get("lost_to_earlier_orders", 0)
    our_rule = summary.get("lost_to_our_own_rule", 0)
    ours = summary.get("promises_we_broke_ourselves", 0)

    not_kept = late + unroutable

    # A REJECTED ORDER IS NOT COUNTED IN THIS SECTION, AND THAT NEEDS SAYING
    #     promise_lost_because is only ever filled in for an order we actually
    #     tried to route. A rejected order never got that far, so it has no cause
    #     recorded and cannot appear below.
    #
    #     Which means the counts here are honest about routing and quietly
    #     misleading about customers. Somebody was told a date for ORD-1010 too,
    #     and they are getting nothing. Saying "every promise was kept" on a batch
    #     containing a rejected order would be a flat lie, and it is exactly the
    #     kind of lie that survives because the arithmetic all checks out.
    #
    #     So the wording below always says which orders it is talking about, and
    #     when there are rejected orders it names them rather than leaving them
    #     out of the story.
    lines.append("")

    if not_kept == 0:
        if invalid == 0:
            lines.append("  Every promise in this batch was kept.")
        else:
            who = (
                "That customer was given a date too, and is still waiting"
                if invalid == 1
                else "Those customers were given a date too, and are still waiting"
            )
            lines += _wrap(
                "Every promise we could act on was kept. "
                f"{plural(invalid, 'order')} never got that far, though - see the "
                f"rejected block above. {who} for somebody to fix the order."
            )
        return lines

    lines.append(
        "  WHY 1 ROUTED PROMISE WAS NOT KEPT" if not_kept == 1
        else f"  WHY {not_kept} ROUTED PROMISES WERE NOT KEPT"
    )
    lines += _lay_out_counts([
        (never, _look_up(PROMISE_LOST_SHORT, LOST_NEVER_POSSIBLE)),
        (earlier, _look_up(PROMISE_LOST_SHORT, LOST_TO_EARLIER_ORDERS)),
        (our_rule, _look_up(PROMISE_LOST_SHORT, LOST_TO_OUR_OWN_RULE)),
        "rule",
        (never + earlier + our_rule, "promises not kept, late or not shipped"),
    ])

    if never + earlier + our_rule != not_kept:
        lines += _wrap(
            f"WARNING: {not_kept} promises were not kept but only "
            f"{never + earlier + our_rule} of them have a cause recorded. One of "
            "them is unaccounted for."
        )

    # The line to judge the engine on. The first cause is the shop's problem; a
    # router cannot invent time that was never there. These two are ours.
    lines += _wrap(
        f"{ours} of those {'was' if ours == 1 else 'were'} down to us rather than "
        "to the promise itself - the bottom two lines. Those are the ones a better "
        "engine would save."
    )

    # And the customers this section left out. Named, not hidden.
    if invalid > 0:
        lines += _wrap(
            f"Not counted above: {plural(invalid, 'order')} we rejected outright. "
            "There is no warehouse to blame because we never routed it, but that "
            "customer was given a date as well and is not getting a parcel either."
        )

    return lines


# --- 5e. Comparing whole runs against each other ----------------------------
#
# WHAT THIS BLOCK IS FOR
#     5d explains one run. This puts several of them side by side.
#
# WHY THE PROTOTYPE NEEDS THIS AT ALL
#     Everything up to here can only answer "what did the engine do". None of it
#     can answer "was that rule worth following", and that is the actual question
#     the brief asks. A single run has nothing to be measured against, so on its
#     own it can only ever be described, never judged.
#
#     The way out is to build a rule we do NOT believe in - take the cheapest
#     warehouse, promise or no promise - run the same orders through it, and print
#     both results next to each other. If protecting promises is worth doing, the
#     difference has to be visible in numbers rather than argued for in prose.
#
# WHY THE CHEAP RULE IS RUN HONESTLY RATHER THAN SET UP TO FAIL
#     A comparison is worthless if one side is rigged. The cost-first rule uses
#     the same warehouses, the same prices, the same stock and the same order
#     sequence, and it genuinely does come out cheaper - which is exactly why the
#     tradeoff is real and worth showing. Making it look bad would prove nothing.

# The short names for the two goals and the two sequencing rules, for a column
# heading or a legend line. Same warning as PROMISE_LOST_SHORT further up: these
# are second wordings of ideas that already have a table each, and they have to
# stay in step with POLICY_PLAIN and SEQUENCE_PLAIN.
POLICY_SHORT = {
    POLICY_PROMISE_FIRST: "promise first",
    POLICY_COST_FIRST: "cost first",
}

SEQUENCE_SHORT = {
    SEQUENCE_LOWEST_SLACK_FIRST: "tightest promise first",
    SEQUENCE_FIRST_COME_FIRST_SERVED: "first come first served",
}

# The rows of the comparison table, in the order they are printed.
#
# Promises first, money last, on purpose. Whichever number sits at the top of a
# table is the one a reader treats as the point of it, and the point of this
# engine is the promise. The bill goes underneath where it cannot be missed
# either, because a promise count with no price next to it is just boasting.
#
# Each row is (label, which key to read, whether it is money).
COMPARISON_ROWS = (
    ("Promises kept", "promises_kept", False),
    ("Shipped late", "routed_late", False),
    ("Not shipped at all", "unroutable", False),
    ("Total days late", "total_days_late", False),
    ("Broken by us, not by the promise", "promises_we_broke_ourselves", False),
    ("Shipping bill", "shipping_cost_cents", True),
    ("Spent above cheapest", "premium_over_cheapest_cents", True),
)


def _dominates(better, worse):
    """
    Says whether one run beat another on every measure at once.

    Takes: two summary dictionaries.
    Gives back: True if the first is at least as good as the second on all five
                measures below, and strictly better on at least one.

    THE FIVE MEASURES, AND WHY TOTAL DAYS LATE IS ONE OF THEM
        promises kept, orders shipped late, total days late, orders not shipped at
        all, and the shipping bill. Those are the five things this engine controls
        and this report prints.

        The first version of this check left total days late out, and that was a
        genuine bug rather than a rough edge. Without it, the cost-first run came
        out "at least as good" as the first-come-first-served run, because the two
        tie on every count and cost-first is cheaper. The page then printed "there
        is no case for run B" directly underneath a table row showing run B leaves
        customers waiting 2 days in total and run C leaves them waiting 4.

        A page that argues with a table printed four lines above it is worse than
        one that says nothing. The rule this taught: anything the table shows has
        to be something the conclusion looked at. If a measure is worth printing,
        it is worth counting.

    WHAT THIS STILL DOES NOT COVER
        Beating another run on these five does NOT mean beating it on everything.
        We do not model how hard each warehouse is working, whether a warehouse
        can physically pack that many boxes today, what packaging costs, or what a
        customer would rather have. A run could win all five and still be the
        wrong thing to do. So the sentence this feeds into names the measures it
        is talking about rather than claiming one run is simply better.

    WHY THIS IS WORTH COMPUTING AT ALL
        Because it turns up the strongest result in the prototype. Two runs
        follow the same goal and differ only in what order they work through the
        orders. One of them beats the other on the promise and matches it to the
        penny on cost. That is not a tradeoff, it is just free, and it would be
        easy to miss in two separate summary pages.
    """
    measures = (
        ("promises_kept", "more_is_better"),
        ("routed_late", "less_is_better"),
        ("total_days_late", "less_is_better"),
        ("unroutable", "less_is_better"),
        ("shipping_cost_cents", "less_is_better"),
    )

    strictly_better_somewhere = False
    for key, direction in measures:
        mine = better.get(key, 0)
        theirs = worse.get(key, 0)
        if direction == "more_is_better":
            if mine < theirs:
                return False
            if mine > theirs:
                strictly_better_somewhere = True
        else:
            if mine > theirs:
                return False
            if mine < theirs:
                strictly_better_somewhere = True

    return strictly_better_somewhere


def _comparison_findings(runs):
    """
    Reads the finished table and writes down what it shows.

    Takes: the same list of (label, summary) pairs the table was built from.
    Gives back: a list of sentences. Possibly an empty list.

    WHY THE FINDINGS ARE WORKED OUT FROM THE NUMBERS AND NOT TYPED IN
        Because a hand written conclusion is a conclusion that can quietly go
        stale. Change one price in the data, rerun, and a sentence like "run A
        keeps one more promise for the same money" is still sitting there being
        wrong. Every sentence below is built from the figures in the table
        directly above it, so it cannot disagree with them.

    WHY IT DOES NOT MARK A WINNER IN THE TABLE ITSELF
        A star against the best number in each row would be the engine grading
        its own homework, and it would hide the whole point: the rows pull in
        opposite directions. Most promises kept and smallest bill are different
        runs. Which one is right is a business decision, not a sum, so the job
        here is to state the tradeoff clearly and leave the choosing to a human.
    """
    findings = []

    # Which runs we have already named as beating another outright. Used further
    # down to avoid saying the same thing twice: if run X has just been shown to
    # beat run Y on every measure, adding "and X was also cheapest and kept the
    # most promises" tells a reader nothing they did not read one line earlier.
    already_named_as_winner = set()

    # 1. Did any run beat another outright? Same goal, better result, no cost.
    #    This is checked before the tradeoff sentence, because a run that wins
    #    for free is not a tradeoff at all and should not be described as one.
    for better_label, better in runs:
        for worse_label, worse in runs:
            if better_label == worse_label:
                continue
            if not _dominates(better, worse):
                continue

            already_named_as_winner.add(better_label)

            same_money = (
                better.get("shipping_cost_cents", 0)
                == worse.get("shipping_cost_cents", 0)
            )
            gap = better.get("promises_kept", 0) - worse.get("promises_kept", 0)

            if same_money and gap > 0:
                findings.append(
                    f"Run {better_label} beat run {worse_label} for free. Same "
                    f"bill to the penny, {gap} extra "
                    f"{'promise' if gap == 1 else 'promises'} kept. When two runs "
                    "follow the same goal and differ only in what order they work "
                    "through the orders, a difference like this is not a tradeoff "
                    "- it is a decision we were getting wrong for nothing."
                )
            else:
                findings.append(
                    f"Run {better_label} was at least as good as run "
                    f"{worse_label} on all five of promises kept, orders late, "
                    "total days late, orders not shipped and total bill, and "
                    "better on at least one. On those five measures there is no "
                    f"case for run {worse_label}."
                )

    # 2. The tradeoff. Which run keeps the most promises, which is cheapest, and
    #    what the gap between them actually costs in both directions.
    #
    #    Ties are settled by position in the list so the same three runs always
    #    produce the same sentence, run after run.
    most_promises = runs[0]
    cheapest = runs[0]
    for candidate in runs[1:]:
        if candidate[1].get("promises_kept", 0) > most_promises[1].get("promises_kept", 0):
            most_promises = candidate
        if candidate[1].get("shipping_cost_cents", 0) < cheapest[1].get("shipping_cost_cents", 0):
            cheapest = candidate

    kind_label, kind = most_promises
    cheap_label, cheap = cheapest

    if kind_label == cheap_label:
        if kind_label not in already_named_as_winner:
            findings.append(
                f"Run {kind_label} kept the most promises AND had the smallest "
                "bill, so on this batch there was no tradeoff to make at all."
            )
        return findings

    money_saved = kind.get("shipping_cost_cents", 0) - cheap.get("shipping_cost_cents", 0)
    promises_given_up = kind.get("promises_kept", 0) - cheap.get("promises_kept", 0)
    extra_waiting = cheap.get("total_days_late", 0) - kind.get("total_days_late", 0)

    trade = (
        f"The tradeoff, in one line: run {cheap_label} saves "
        f"{money_gap(money_saved)} against run {kind_label}"
    )
    if promises_given_up > 0:
        trade += f" and keeps {plural(promises_given_up, 'promise')} fewer"
    if extra_waiting > 0:
        trade += f", leaving customers waiting {plural(extra_waiting, 'day')} longer in total"
    trade += "."
    findings.append(trade)

    # 3. And what that saving works out at per broken promise. This is the number
    #    that makes the decision concrete rather than a matter of taste. Somebody
    #    can look at it and say yes or no, which is the whole point of printing it.
    if promises_given_up > 0:
        # "about" on the plural version only. Dividing whole cents by a whole
        # number of promises rounds down, so with three promises and an odd saving
        # the figure is a penny or two light. On one promise there is no division
        # happening at all and the number is exact, where hedging it would just
        # make a precise figure look uncertain.
        each = money_gap(money_saved // promises_given_up)
        sold = (
            f"sold one delivery promise for {each}"
            if promises_given_up == 1
            else f"sold {promises_given_up} delivery promises at about {each} each"
        )
        findings.append(
            f"Put another way, run {cheap_label} {sold}. Whether that is a good "
            "price is a business decision, not a routing one - but nobody can "
            "make it until the number is on the page."
        )

    return findings


def format_run_comparison(runs):
    """
    Builds the side by side comparison of several whole runs.

    Takes: a list of (label, summary) pairs. The label is a short name for the
           column such as "A". The summary is what route_batch handed back.
    Gives back: a list of lines. It prints nothing.

    WHY THE LABELS ARE THE CALLER'S BUT NOTHING ELSE IS
        The column headings are the only thing on this page that main.py gets to
        invent, and even those are only names. Every fact underneath comes out of
        the summary records.

        That matters because a hand typed label is the one thing here that can
        lie. Put "cost first" over a promise-first run and the whole page is
        wrong. So the legend prints each run's goal and sequencing rule READ FROM
        ITS OWN RECORD, directly under its label. A mislabelled column disagrees
        with its own legend line, on screen, where somebody will see it.

    WHY IT CHECKS THE RUNS ARE COMPARABLE
        Comparing "6 promises kept" against "3 promises kept" says nothing if one
        run had ten orders and the other had four. Two runs are only comparable if
        the same orders went into both, so if the order counts differ the page
        says so loudly instead of inviting a reader to draw a conclusion the
        numbers cannot support.
    """
    if not runs:
        return ["HOW THE RUNS COMPARE", "", "  No runs were given, so there is nothing to compare."]

    if len(runs) == 1:
        only_label = runs[0][0]
        return [
            "HOW THE RUNS COMPARE",
            "",
            *_wrap(
                f"Only run {only_label} was given. A single run cannot be judged, "
                "because there is nothing to measure it against. Run the same "
                "orders through a different rule and compare the two."
            ),
        ]

    lines = ["HOW THE RUNS COMPARE", ""]
    lines += _wrap(
        "Same orders, same warehouses, same stock and same prices in every run. "
        "The only thing that changed is the rule."
    )
    lines.append("")

    # The legend: what each column actually is, taken from its own record.
    label_width = max(len(label) for label, _ in runs)
    for label, summary in runs:
        goal = _look_up(POLICY_SHORT, summary.get("policy"), missing="(no goal recorded)")
        order = _look_up(
            SEQUENCE_SHORT, summary.get("sequencing"), missing="(no sequencing recorded)"
        )
        lines.append(f"  {label.ljust(label_width)}   {goal}, {order}")

    lines.append("")

    # The table. Labels down the left, one column per run, numbers right aligned
    # so a reader can compare down a column and across a row without effort.
    headings = [""] + [label for label, _ in runs]
    align = "l" + "r" * len(runs)

    rows = []
    for label, key, is_money in COMPARISON_ROWS:
        cells = [label]
        for _, summary in runs:
            value = summary.get(key, 0)
            cells.append(money(value) if is_money else str(value))
        rows.append(cells)

    lines += _lay_out_table(headings, rows, align)

    # Are these runs even comparable? Asked after the table rather than before,
    # so a reader has already seen the numbers the warning is about.
    counts = {summary.get("order_count") for _, summary in runs}
    if len(counts) > 1:
        lines.append("")
        lines += _wrap(
            "WARNING: these runs did not all get the same number of orders "
            f"({', '.join(str(c) for c in sorted(counts, key=lambda c: (c is None, c)))}). "
            "The rows above cannot be compared, because a run with fewer orders "
            "has fewer promises available to keep or break."
        )
        return lines

    findings = _comparison_findings(runs)
    if findings:
        lines.append("")
        lines.append("  WHAT THE TABLE SHOWS")
        for finding in findings:
            lines += _wrap(finding, indent="    ")
            lines.append("")
        lines.pop()

    return lines


# --- 5f. Describing the data before any routing happens ---------------------
#
# WHY THIS COUNTS AS PART 5 RATHER THAN AS PART OF main.py
#     It is the same job as everything else in this part: take something the
#     engine loaded and turn it into lines a person can read. Doing it here means
#     main.py stays a file that only prints, and it means the layout helpers get
#     used rather than a second hand rolled version of them appearing elsewhere.


def format_network_overview(network, orders):
    """
    Describes what is actually in the data files, before anything is routed.

    Takes: the network from load_network(), and the list of orders.
    Gives back: a list of lines. It prints nothing.

    WHY A REPORT NEEDS THIS AT THE TOP
        Every result further down is a claim about this data, and none of those
        claims mean anything without it. "We kept six promises" needs to be read
        against ten orders. "No warehouse could fill it" only makes sense once you
        know there are four warehouses holding four rare units between them.

    WHY EVERY FIGURE HERE IS COUNTED RATHER THAN TYPED
        A hand written "four warehouses, ten orders" would sit there being wrong
        the first time somebody added a fifth warehouse, and it would look exactly
        as authoritative as it does now. Counting them means this section moves
        with the data instead of slowly becoming a lie about it.
    """
    warehouses = sorted(network["fulfillment_centers"], key=lambda w: w["fc_id"])
    inventory = network["starting_inventory"]
    skus = sorted(network["known_skus"])

    lines = _lay_out_counts([
        (len(warehouses), "warehouses"),
        (len(network["lanes"]), "shipping lanes, one per warehouse and area pair"),
        (len(network["destination_zones"]), "cities we have shipping rates for"),
        (len(orders), "customer orders to route"),
    ], indent="  ")

    # The totals go in a sentence above the table rather than in a row at the
    # bottom of it. They are the important numbers - four rare units in the whole
    # company is the fact that drives half the interesting decisions in this batch
    # - and a figure buried in the last row of a table gets skimmed past.
    totals = ", ".join(
        f"{plural(total_units_in_network(sku, inventory), 'unit')} of {sku}"
        for sku in skus
    )

    lines.append("")
    lines += _wrap(
        f"Stock on hand at the start of every run: {totals}. Spread out like this:"
    )
    lines.append("")

    rows = [
        [warehouse["fc_id"]]
        + [str(inventory[warehouse["fc_id"]].get(sku, 0)) for sku in skus]
        for warehouse in warehouses
    ]

    # Warehouses sorted by name and products sorted too, so this comes out
    # identical every run. Same reason as everywhere else in the engine: if the
    # order can wobble, you cannot tell a real change from a reshuffle.
    lines += _lay_out_table(
        ["Warehouse"] + skus,
        rows,
        "l" + "r" * len(skus),
        indent="    ",
    )

    return lines
