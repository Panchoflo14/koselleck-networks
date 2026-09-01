# The Koselleck Machine's grounded-discovery layer.
#
# This package turns the existing measurement pipeline (network -> community ->
# metrics -> labels) into a conversational tool for historical scientific
# discovery, following docs/implementation_plan.md. Nothing here recomputes or
# grades the quantitative findings - migration_fraction, NMI, ARI and community
# membership stay the sole product of network.py / community.py / metrics.py.
# This layer only *retrieves* those findings, tags every fact with where it
# came from and how far to trust it (see evidence.py), and hands them to an LLM
# under a strict "cite or refuse" contract.
