"""Pipeline coordinator: a fixed sequence of the shared specialists for one supplier email.

    1. The analyst works out how the email affects customer orders (MCP data tools).
    2. For each order at risk, the customer writer drafts a notice and the reviewer reviews it.
    3. The supplier writer drafts the reply to the supplier and the reviewer reviews it.

Nothing is sent. Drafts are saved under week-05/drafts/<company-folder>/.

Usage (from the repository root):
    .venv/bin/python week-05/pipeline.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
    .venv/bin/python week-05/pipeline.py --org week-05/copperline-coffee \\
        --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml \\
        --save week-05/runs/copperline-01-pipeline.txt
"""

from specialists import Team, run_cli


async def run_pipeline(team: Team) -> None:
    team.out.section("STEP 1: SUPPLY ANALYST")
    brief = await team.run_analyst(source="pipeline")
    team.print_brief()

    orders = list(dict.fromkeys(str(e.get("order_id")) for e in brief.get("orders_at_risk", []) if isinstance(e, dict)))
    team.out.section(f"STEP 2: CUSTOMER NOTICES ({len(orders)} order(s) at risk)")
    for order_id in orders:
        if team.at_risk_entry(order_id) is None or order_id.upper() not in team.data.orders_by_id:
            team.out(f"Skipping {order_id}: not a known order in orders.csv.")
            team.extra_decisions.append(f"Check order {order_id} named in the analyst brief")
            continue
        await team.draft_customer_notice(order_id, source="pipeline")

    team.out.section("STEP 3: SUPPLIER REPLY")
    await team.draft_supplier_reply(source="pipeline")


if __name__ == "__main__":
    run_cli("Draft and review the responses to one supplier email with a fixed pipeline.",
            "pipeline", run_pipeline)
