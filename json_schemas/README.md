# Benchmark JSON schemas

This directory will hold the JSON Schema definitions for the standardized benchmark input/output format, so that the tooling in `hedbench/` can run and report on any case study without per-case code.

The format is being designed. The document kinds expected here:

- a **case descriptor** - what a benchmark case study is and how to invoke it,
- a **run input** - parameters and data references for one benchmark run,
- a **run output** - timings, scores, and environment provenance from one run.

Until the first schema lands, each case study under `use_cases/` defines its own input and output, and `hedbench.validation` is a stub.
