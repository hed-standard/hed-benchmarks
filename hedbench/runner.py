"""Discover and run benchmark case studies through the standardized JSON interface.

Each case study under use_cases/ will declare itself with a JSON descriptor
validated against the schemas in json_schemas/. Until that format is designed
(see json_schemas/README.md), case studies are run directly by their own scripts.
"""

from __future__ import annotations


def run_case(case_dir, run_input=None):
    """Run one benchmark case study from its descriptor.

    Parameters:
        case_dir: Path to the case-study directory under use_cases/.
        run_input: Optional path to a run-input JSON document.

    Returns:
        The run-output document as a dict.
    """
    raise NotImplementedError("Waiting on the standardized benchmark JSON format; see json_schemas/README.md.")
