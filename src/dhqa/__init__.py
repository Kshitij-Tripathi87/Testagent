"""dhqa — DataHub Contract QA Agent.

Reads DataHub for schema/lineage/ownership, generates pipeline code and
tests from it, root-causes failing checks via the lineage graph, and
writes results back into DataHub.
"""

__version__ = "0.1.0"
