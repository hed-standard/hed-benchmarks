"""Validate benchmark input and output documents against the JSON Schemas in json_schemas/.

The standardized format is not designed yet; see json_schemas/README.md for the
current state and the planned document kinds.
"""

from __future__ import annotations


def validate_document(document, kind):
    """Validate one benchmark JSON document against its schema.

    Parameters:
        document: The parsed JSON document (dict).
        kind: Which schema to validate against, e.g. "case", "input", "output".

    Returns:
        A list of validation issues; empty if the document is valid.
    """
    raise NotImplementedError("Waiting on the standardized benchmark JSON format; see json_schemas/README.md.")
