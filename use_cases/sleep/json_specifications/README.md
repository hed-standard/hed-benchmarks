# Sleep case format observations

This directory does not define a universal benchmark schema yet. The working sleep case exposes fields that can inform that design after comparison with the synthetic search case:

- case, fixture, license, and schema identity;
- semantic question, query expression, and source scope;
- independent expected count and onsets;
- engine-specific observed count, onsets, and correctness;
- source-file hashes and explicit analysis boundaries;
- optional timing and environment metadata kept separate from deterministic correctness output.

The shared JSON Schema should be designed only after at least two working cases reveal which fields are genuinely common.
