# Pillar 4 of docs/implementation_plan.md: the grounding & honesty eval.
#
# A discovery instrument is only worth trusting if it can be shown not to make
# things up. This package checks exactly that, in two layers:
#
#   checks.py  - deterministic, model-free grounding checks over one engine
#                result: every number and period the answer states must appear
#                in the Evidence the tools returned; a no-data question must be
#                refused, not answered; and a claim resting on unreliable
#                (OCR / Structural) evidence must carry its caveat. These are
#                the cheap, hard checks - they catch a fabricated statistic
#                outright, and they need neither a model nor the corpus.
#   judge.py   - an optional LLM-as-judge faithfulness score, for the softer
#                "is every sentence supported by the evidence" reading the
#                deterministic checks can't fully capture. A reading aid, never
#                a gate - and never pointed at the quantitative findings
#                themselves (see the plan's "harmful" note on LLM-as-judge).
#
# run.py drives the engine over cases.py and reports both. The engine run needs
# a live model; the checks do not, and are unit-tested against synthetic
# results so the honesty logic itself is verified offline.
