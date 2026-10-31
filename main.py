"""
WHAT THIS FILE IS FOR
    This is the file you run. It prints the whole demonstration in one go:
    every routing decision explained, the totals for the batch, and the same
    ten orders put through three different rules so the rules can be compared.

WHAT ROLE IT PLAYS IN THE PROTOTYPE
    It is the front door, and it is deliberately the least clever file here.

    router.py decides things and never prints. This file prints things and never
    decides. That split is the whole reason main.py exists as a separate file at
    all, and it is worth being strict about, because the moment printing code is
    allowed to work something out for itself, the page can quietly stop matching
    the decision it claims to be describing.

    So there is no routing logic below. Not a price, not a date, not a
    comparison. Every number printed was worked out by router.py and handed over
    already finished. All this file chooses is what order to show things in.

WHAT IT READS
    The three files in the data folder, through router.load_network() and
    router.load_orders():
        data/fulfillment_centers.json   the four warehouses and their stock
        data/shipping_routes.json       which city is in which area, and the
                                        cost and transit time of every lane
        data/orders.json                the ten customer orders

WHAT IT RETURNS OR PRINTS
    It returns nothing. It prints a report of about 430 lines to the screen, in
    five sections:
        1  what is in the data
        2  run A, with all ten orders explained one at a time
        3  runs B and C, totals only
        4  all three runs side by side, and what that shows
        5  one decision record printed raw, exactly as the engine stored it

HOW TO RUN IT
    python3 main.py

    It takes no options. One command prints everything. There is no way to ask
    for half the report, because a routing decision shown without the options it
    beat is not an explanation, and the whole point of this prototype is that
    every number can be traced to the numbers underneath it.
"""

import json
import textwrap
from pathlib import Path

import router


# WHY THE DATA FOLDER IS WORKED OUT FROM THIS FILE'S OWN LOCATION
#     router.load_network() defaults to the folder "data", which means "data
#     inside whatever folder you happen to be standing in". That is fine when you
#     are already inside the project and it breaks the moment you are not:
#
#         cd routing-engine && python3 main.py      works
#         python3 routing-engine/main.py            crashes, file not found
#
#     Somebody unzipping this and running the second one gets a confusing error
#     about a missing file rather than a report. So we build the path from where
#     THIS file is, which does not change no matter where it is run from.
DATA_DIR = Path(__file__).resolve().parent / "data"

# Matches the width Part 5 wraps its sentences to, so the dividers line up with
# the text underneath them instead of being their own arbitrary length.
LINE_WIDTH = router.REPORT_WIDTH


def banner(title):
    """
    Prints a heavy divider with a title in it.

    Takes: the title to show.
    Gives back: nothing. It prints.

    The report is long and it is mostly numbers. Without obvious breaks in it a
    reader loses track of which run they are looking at, and a promise count read
    against the wrong run is worse than no promise count.
    """
    print()
    print("=" * LINE_WIDTH)
    print(f" {title}")
    print("=" * LINE_WIDTH)
    print()


def say(text):
    """
    Prints a paragraph of this file's own words, wrapped to the report width.

    Takes: a paragraph as one long string.
    Gives back: nothing. It prints.

    WHY NOT JUST WRITE THE LINE BREAKS IN BY HAND
        Because hand broken lines end up a different width from the ones Part 5
        wraps, and the whole report then looks like two documents stapled
        together. The first version of this file wrapped its own paragraphs at
        about seventy characters while every explanation underneath ran to
        seventy-eight, and the ragged edge between them was obvious on screen.

        Doing it here also means the paragraphs below can be edited as sentences
        rather than as carefully counted lines.
    """
    print(textwrap.fill(
        " ".join(text.split()),
        width=LINE_WIDTH,
        initial_indent="  ",
        subsequent_indent="  ",
    ))


def show_lines(lines):
    """
    Prints a list of lines.

    Takes: the list of lines a Part 5 function handed back.
    Gives back: nothing. It prints.

    This exists so that the printing happens in exactly one place. Every block of
    text in this report arrives as a list of strings, and if each caller printed
    it in its own way they would drift apart on spacing.
    """
    for line in lines:
        print(line)


def do_run(orders, network, policy, sequencing):
    """
    Does one complete run of the batch and hands back the result.

    Takes: the orders, the network, which goal to follow, and which order to work
           through the orders in.
    Gives back: the result dictionary from router.route_batch().

    WHY EVERY RUN STARTS FROM fresh_inventory()
        Because routing an order takes stock off a shelf. If run B started from
        the shelves run A left behind, it would be routing a batch of orders
        against stock that had already been sold, and the comparison at the end
        of this report would be meaningless while still looking perfectly
        sensible.

        This is the single easiest way to get a wrong answer out of this whole
        prototype, and it would show up as runs B and C looking mysteriously
        worse than run A rather than as any kind of error.
    """
    return router.route_batch(
        orders,
        network,
        router.fresh_inventory(network),
        policy=policy,
        sequencing=sequencing,
    )


def main():
    """
    Prints the whole report.

    Takes: nothing.
    Gives back: nothing. It prints.
    """
    network = router.load_network(DATA_DIR)
    orders = router.load_orders(DATA_DIR)

    # ---------------------------------------------------------------------
    # 1. What is in the data
    # ---------------------------------------------------------------------
    banner("INTELLIGENT FULFILLMENT ROUTING ENGINE - PROTOTYPE")
    say("""
        Given an order and a set of warehouses, decide which warehouse it ships
        from. The thing being protected is the delivery date the customer was
        promised. The cost of protecting it is printed next to it every single
        time, so the tradeoff is never hidden.
    """)
    print()
    show_lines(router.format_network_overview(network, orders))

    # ---------------------------------------------------------------------
    # 2. Run A, in full
    # ---------------------------------------------------------------------
    # Only run A gets its orders explained one at a time. Printing all ten for
    # all three runs would be thirty blocks and about four hundred lines, and
    # nobody reads the twenty-first block. The other two runs are here to be
    # measured, not read, so they get their totals only.
    run_a = do_run(
        orders, network,
        router.POLICY_PROMISE_FIRST,
        router.SEQUENCE_LOWEST_SLACK_FIRST,
    )

    banner("RUN A - EVERY DECISION, ONE ORDER AT A TIME")
    say("""
        Each block below lists every warehouse we considered, what it would have
        cost, when it would have arrived, and why it lost. The warehouse we
        picked is the row marked CHOSEN. The goal and the working order are
        printed with the totals at the end of this section.
    """)

    for decision in run_a["decisions"]:
        print()
        print("-" * LINE_WIDTH)
        show_lines(router.format_decision(decision))

    banner("RUN A - THE TOTALS")
    show_lines(router.format_batch_summary(run_a["summary"], run_a["sequence"]))

    # The stock check already ran inside route_batch and would have stopped the
    # program if it failed. Saying so out loud matters because a check that only
    # speaks up when it breaks is invisible on a good day, and a reader has no
    # way of knowing it was ever there.
    print()
    say("""
        Stock check passed: for every warehouse and every product on its own,
        what the shelf started with equals what we shipped from it plus what is
        left. If that sum had not balanced, the program would have stopped
        instead of printing this report.
    """)

    # ---------------------------------------------------------------------
    # 3. Runs B and C, totals only
    # ---------------------------------------------------------------------
    run_b = do_run(
        orders, network,
        router.POLICY_PROMISE_FIRST,
        router.SEQUENCE_FIRST_COME_FIRST_SERVED,
    )
    run_c = do_run(
        orders, network,
        router.POLICY_COST_FIRST,
        router.SEQUENCE_LOWEST_SLACK_FIRST,
    )

    # Runs B and C get their totals only, no order blocks. Thirty blocks of four
    # hundred lines is not a report anybody finishes, and these two runs are here
    # to be measured against run A rather than read through.
    banner("RUN B - SAME GOAL, BUT STRAIGHT DOWN THE LIST")
    say("""
        Identical to run A in every way except one: it works through the orders
        in the sequence they arrived instead of dealing with the tightest promise
        first. Same stock, same prices, same rules for picking a warehouse.
        Totals only below - the point of this run is the comparison at the end.
    """)
    print()
    show_lines(router.format_batch_summary(run_b["summary"], run_b["sequence"]))

    banner("RUN C - THE RULE WE DO NOT BELIEVE IN")
    say("""
        Always take the cheapest warehouse that has the stock, and ignore the
        promised date completely.
    """)
    print()
    say("""
        This run exists to be argued with. Without it, "protecting promises is
        worth it" is an opinion rather than a result. It gets the same
        warehouses, the same prices and the same stock as the other two, and it
        genuinely does come out cheaper - which is exactly why the tradeoff it
        shows is a real one and not a rigged comparison.
    """)
    print()
    show_lines(router.format_batch_summary(run_c["summary"], run_c["sequence"]))

    # ---------------------------------------------------------------------
    # 4. All three side by side
    # ---------------------------------------------------------------------
    banner("ALL THREE RUNS SIDE BY SIDE")
    show_lines(router.format_run_comparison([
        ("A", run_a["summary"]),
        ("B", run_b["summary"]),
        ("C", run_c["summary"]),
    ]))

    # ---------------------------------------------------------------------
    # 5. One record, raw
    # ---------------------------------------------------------------------
    # WHY THIS IS WORTH THE SPACE
    #     Everything above is a tidy table, and a tidy table is exactly what
    #     somebody would produce if they were making the numbers up. A reader has
    #     no way to tell a rendering of real data from a nicely typed fiction.
    #
    #     So here is one decision exactly as the engine stored it, every field,
    #     nothing removed. The pretty block for the same order is printed further
    #     up, and every figure in it can be found in here. That turns the whole
    #     report from something you have to trust into something you can check.
    #
    #     ORD-1001 is the one chosen because it is the interesting case: three
    #     warehouses could hit the promised date, the cheapest of all four could
    #     not, and we paid more on purpose.
    banner("ONE DECISION RECORD, EXACTLY AS THE ENGINE STORED IT")
    say("""
        Every table above is built from records like this one and nothing else.
        No number anywhere in this report is worked out at printing time. This is
        ORD-1001, the same order explained near the top of run A - every figure
        in that block can be found somewhere in here.
    """)
    print()

    example = next(
        (d for d in run_a["decisions"] if d["order_id"] == "ORD-1001"),
        run_a["decisions"][0],
    )
    print(json.dumps(example, indent=2, sort_keys=True))

    banner("END OF REPORT")


if __name__ == "__main__":
    main()
