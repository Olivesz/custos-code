# Eval

`custos-code eval` runs the gold set through the regex baseline and the full ladder, and prints
per-class precision/recall/F1, Cohen's kappa (each labeller vs majority; system vs majority), and
the cost per session. CI gate: contradicted precision >= 0.90 (mocked judge in PR CI; live nightly).

Study artefacts for the user evidence live under `eval/study/` (participants.csv with ids only;
per-participant sessions are gitignored). Protocol: docs/EVIDENCE_PLAN.md. Owner: Anush (metrics), Ananya (study).
