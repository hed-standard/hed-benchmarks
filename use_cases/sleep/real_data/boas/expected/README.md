# Independent numeric reference

`identity.json` binds the exact BOAS v1.1.1 metadata and 256-file event manifest reviewed at commit `0225bb258566172fa97a4f75dc2c2689243df2a2`.

`numeric_reference.json` was calculated directly from those numeric event tables by `recompute_numeric.py`, independently of HED and `src/boas_case.py`. It contains all context denominators, disagreements, stage strata, transition types, unavailable-code counts, and the participant-cluster bootstrap reference. The main runner must reproduce these values from HED-derived semantic columns. The source-code counts are independent numeric oracles for all 18 HED queries.

To inspect or reproduce the reference, write a new file outside the repository:

```text
python use_cases/sleep/real_data/boas/expected/recompute_numeric.py \
  --data-root /path/to/ds005555 --output /path/to/local/numeric_reference.json
```

Do not refresh the committed reference merely because a test fails. Investigate differences in source bytes, definitions, software, and numerical behavior first. The real-data integration test uses this reference with an absolute tolerance of 1e-12 for bootstrap floating-point results; counts and query masks must match exactly.
