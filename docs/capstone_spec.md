# RenovAI Capstone Spec — Agents for Good

## Problem statement
First-time and lower-income apartment buyers in Hungary lack access to
contractor-grade due diligence. They don't know what questions to ask
sellers, can't independently verify whether a renovation quote is fair,
and don't recognise red flags common in older Budapest housing stock
(kohósalak slag under floors, shared plumbing stacks requiring co-owner
approval, undocumented electrical work). This knowledge already exists —
inside fragmented Excel quotes and contractor know-how — but never
reaches the buyers who need it most before they sign a purchase contract.

## Agent role
RenovAI is a buyer-side agent that redistributes renovation due-diligence
knowledge for free, in Hungarian, before a purchase decision is made.
It does NOT represent the seller, the contractor, or the agent's
commission — its only loyalty is to the buyer.

## Why this must be an agent, not a static FAQ
Each apartment is different — the right questions, the right red flags,
and the right cost range depend on the building era, district, and
condition of that specific unit. A static FAQ cannot reason over 30
real historical quotes to find the 3 most comparable cases, cross-check
a buyer's planned scope against what got excluded in similar quotes, and
explain the gap in plain Hungarian. That requires retrieval, reasoning,
and tool composition — i.e., an agent.

## Harness requirements (this capstone submission)
1. MCP Server — exposes RenovAI's three capabilities (cost estimate,
   pre-purchase advisory, market data query) as MCP tools, so any MCP
   client (not just this agent) can use them. This is a deliberate
   "open infrastructure" decision for the Agents for Good track: a
   tenant union or civic-tech group could plug into this server later
   without touching RenovAI's codebase.
2. Agent Skills — the three capabilities are implemented as standard
   Agent Skills (SKILL.md + scripts/ + references/), so the orchestrator
   can route to them declaratively rather than via hardcoded if/else.
3. Terminal Sandboxing — when a buyer uploads their OWN quote (an XLSX
   from a contractor they're evaluating) for analysis, the ingestion
   pipeline runs inside a sandboxed subprocess with no network access
   and a read-only mount of the existing database. This protects
   vulnerable users' financial documents from any code-execution risk
   in a parser that processes untrusted, buyer-supplied spreadsheets.
4. (Stretch) ADK orchestrator agent — a thin routing agent that takes
   the buyer's free-text Hungarian question, decides which of the
   three skills applies (possibly more than one), calls them via the
   MCP server, and composes the final answer.

## Success criteria for the demo
- A buyer can ask in Hungarian: "55 m²-es lakást nézek a 8. kerületben,
  villany és vízvezeték is kell, mennyit költsek felújításra és mire
  figyeljek vásárlás előtt?" and receive: a cost range grounded in
  similar historical quotes, a due-diligence checklist with red flags,
  and citations back to which historical quote informed which answer.
- A buyer can upload their own contractor's XLSX quote and have it
  ingested through the sandboxed pipeline, then ask "is this quote
  reasonable for this scope?" and get a comparison against the
  existing 30-quote corpus.
- All of the above is demonstrable from a terminal calling the MCP
  server directly (proving the protocol works independent of any UI).

## Non-goals for this submission
- Video transcription pipeline (Whisper/NotebookLM) — already built,
  not central to the agent story, omit from the demo.
- Full FastAPI production deployment — keep but don't feature; Cloud
  Run deploy of the MCP server is the deployability story instead.
- 30-sample ML price model precision — already documented as a known
  limitation (n=30, missing floor-area data); the demo should be
  honest about this, framing the RAG-grounded similar-case comparison
  as the primary signal, the regression number as secondary.
