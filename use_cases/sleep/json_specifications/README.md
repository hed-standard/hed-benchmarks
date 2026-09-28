# Sleep case format observations

This directory does not define a universal benchmark schema yet. The synthetic search fixture and the BOAS real-data extension expose fields that can inform that design:

- case, fixture, license, and schema identity;
- semantic question, query expression, and source scope;
- independent expected count and onsets;
- engine-specific observed count, onsets, and correctness;
- source-file hashes and explicit analysis boundaries;
- optional timing and environment metadata kept separate from deterministic correctness output.

The BOAS case adds several real-data requirements:

- pinned dataset accession, version, DOI, license, and source revision;
- recording count and the participant-level clustering identifier;
- a deterministic manifest of every input table;
- explicit valid-analysis populations and exclusion counts;
- HED-to-original-label oracle counts;
- downstream estimates separated from HED retrieval results; and
- uncertainty method, resampling unit, seed, requested samples, and completed samples.

These observations are inputs to a later shared JSON Schema, not a final cross-domain contract.
