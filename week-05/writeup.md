## What I Built
I built a supplier-response team in `week-05/`. `supply_mcp_server.py` is an MCP server (official Python `mcp` package) with three tools: `get_part` returns a part's stock and scheduled arrivals, `get_orders_for_part` lists the open orders that use it with parts needed from the bill of materials, and `check_coverage` fills orders in ship-day order and reports which ones ship complete, how many units each short order is missing, and its earliest full ship day. A short order holds up every later order. `specialists.py` holds four shared specialists on `claude-sonnet-5`, each using the company's own prompt file. The analyst uses the MCP tools (at most 10 calls) and returns a JSON brief. The supplier writer gets the email and a brief with customer names, contacts, and order IDs removed. The customer writer gets only one order's facts. The reviewer returns APPROVE or REVISE. Every draft is reviewed, with up to two revisions, and code also checks for customer names in supplier drafts, supplier names in customer drafts, partial-shipment claims, and replies that choose a supplier option. Drafts are saved to `week-05/drafts/` (not committed) and never sent.

There are two coordinators. `pipeline.py` always runs the same steps: analyst, a notice for each order at risk, then the supplier reply. `orchestrator.py` gives a coordinating agent the company's `orchestrator.txt` prompt and four tools (`run_supply_analyst`, `draft_supplier_reply`, `draft_customer_notice`, `finish`) and lets it decide which specialists an email needs, within 10 tool calls. All four scripts take `--org`, which defaults to Brookfield. With `--org week-05/copperline-coffee`, the same code reads Copperline's CSVs and prompts, and the server detects Copperline's different column names (`product`, `units`, `per_unit`, `ship_by_day`) from the headers.

I saved eight runs in `week-05/runs/`: Copperline emails 1 to 4, each through both coordinators. Reviewing them led to fixes. Customer notices now say the whole order ships together, the orchestrator gets the supplier draft text so its summary is accurate, and when a supplier offers options, the reply can't choose one and any customer notice describes the delay as conditional. I reran the affected emails after each fix.

## Why This Approach
Task A: Agent, because it has to look up the order and the warranty as needed, and what it finds decides the next lookup, before it can draft a reply.

Task B: Team, because tracing which machines shipped and then preparing different communications for customers and for a regulator are separate jobs that each need their own specialist.

Task C: Pipeline, because every wholesale order follows the same steps in the same order, so a fixed sequence is cheaper and easier to check.

Task D: Orchestrator, because different kinds of inbox messages need different specialists, so something has to read each message and route it.

## Example Run
```text
Coordinator: orchestrator   Company: Copperline Coffee Machines (copperline-coffee)   Model: claude-sonnet-5
Email file: 04-pelham-maintenance-closure.eml
From: Owen Brady <o.brady@pelhamfittings.example>
Subject: Plant maintenance closure - PO-5540 not affected
API key source: ANTHROPIC_API_KEY environment variable

========================================================================
ORCHESTRATOR
========================================================================
[tokens] orchestrator turn 1: input=1500 output=255 stop_reason=tool_use
Orchestrator: This email explicitly states PO-5540 is not affected and no action is needed. There's no change to delivery date or quantity that would impact customer orders, so no analyst, supplier reply, or customer notice is warranted.

ORCHESTRATOR CHOICE 1: finish {"summary": "<shown under ORCHESTRATOR FINISH>", "needs_person_decision": false}
RESULT 1: Finished.

========================================================================
ORCHESTRATOR FINISH
========================================================================
Summary for the purchasing manager: Pelham Fittings notified us of a one-week plant maintenance closure starting in 4 weeks. They explicitly confirmed our open order PO-5540 (60 SW-3 steam wands) ships before the closure and will arrive on schedule, with no action needed on our side. Since no customer orders are affected, no analysis or draft communications were required.
A person must make a decision: no

========================================================================
SUMMARY
========================================================================
Drafts saved: 0
Drafts marked for a person: 0
Decisions for a person: 0
Token usage by specialist:
  - orchestrator: input=1500 output=255 (1 call(s))
Total tokens: input=1500 output=255 total=1755
```

## Reflection
Email 1 reading: Matches. Diane wrote, "The other 160 will follow 15 days later," and the original delivery was day 4. The `what_changed` line in the Email 1 pipeline log says "only 40 will arrive on day 4 as originally scheduled, with the remaining 160 delayed to day 19." Day 4 plus 15 days is day 19, which is the day the brief gave.

Email 1 customer names: No. The final Email 1 pipeline supplier reply named no customers. Diane asked for a list of the customer orders with each customer's name and quantity. The reply said, "We're not able to share customer names or order details," and instead gave only the impact: 3 orders affected, 45 units short, and delays of 7, 4, and 2 days. It offered to confirm units short and days delayed per order, and asked whether any of the 160 pumps could ship sooner.

Email 4 orchestrator: None. The saved Email 4 orchestrator called no specialists. Its only tool call was `finish`, because Pelham's email said PO-5540 was not affected and no action was needed, and it reported that no person needed to make a decision. The pipeline handled the same email by running its fixed steps anyway. The analyst made three tool calls (`get_part`, `get_orders_for_part`, and `check_coverage` for SW-3) and confirmed there was no change: the 60 steam wands still arrive on day 11, before the closure starting on day 28, with no orders at risk. The pipeline drafted no customer notices. The supplier writer returned "NO REPLY NEEDED," the reviewer approved it, and it was saved as a draft. The pipeline used 10,311 tokens against the orchestrator's 1,755. It checked the data instead of trusting the supplier, but cost about six times as much to reach the same result.
