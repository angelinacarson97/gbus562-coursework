# gbus562-coursework

## About

This repository contains my coursework for GBUS562, including the Week 1 assignment.

## Weekly Work

1. Week 1: an AI-coded Snake game, built with Claude Code.
2. Week 2: Built an AI Prompt Lab using Claude Code and the Anthropic API to explore zero-shot, one-shot, few-shot, and chain-of-thought prompting through four business tasks for Wildframe Coffee Roasters.
3. Week 3: Built a retrieval-augmented generation (RAG) question-answering tool, `week-03/ask.py`, that reads Copperline Manufacturing's finance policies (HTML and PDF), retrieves relevant chunks with Chroma, and answers with Claude using source citations.
4. Week 4: an AI agent that prepares replies to HR emails using tools from an MCP server, built with Claude Code.
5. Week 5: a supplier-response team of AI specialists (supply analyst, supplier writer, customer writer, and reviewer) that reads a supplier's delay email, uses an MCP server (`week-05/supply_mcp_server.py`) to check stock, scheduled arrivals, and which customer orders can still ship complete, and drafts a reviewed reply to the supplier and a notice to each affected customer. It runs either as a fixed pipeline (`week-05/pipeline.py`) or under a coordinating agent (`week-05/orchestrator.py`) for Brookfield Bicycle Company or Copperline Coffee Machines. Drafts are saved for a person to check and are never sent.
