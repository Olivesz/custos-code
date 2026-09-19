"""Per-session token and dollar accounting by stage and tier (`receipts cost`).

Records: claims settled per tier, judge requests per session, input/cached/output tokens per
request, and the price-list lookup from config. Produces the chart: judge-everything vs ladder
vs ladder+compressor on the same sessions, with kappa beside each (EVIDENCE_PLAN, Token Company).
NEEDS-DECISION(anush): where the price list lives and how it is dated.

Owner: Anush.
"""
