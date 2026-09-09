# Sleep annotation search correctness

## Scientific question

At which 30-second epochs do human consensus, PSG-based AI, and headband-based AI annotations contain N2 or an unavailable-data state, and which source produced each matching annotation?

## Fixture and interpretation

- Fixture: `synthetic_cc0_boas_structure` (14 rows, `CC0-1.0`).
- Source structure: `ds005555` version `1.1.1`.
- HED schema: `score_2.1.0`.
- The fixture is synthetic and contains no participant data.
- No annotation source is treated as ground truth.
- The case tests semantic retrieval, not sleep-stage agreement or model performance.

## Correctness checks

- Fixture contract is valid: **True**
- Sidecar and expanded table validate: **True**
- All engines match direct label oracles: **True**
- Source-specific masks are distinct: **True**
- Broad masks are strict supersets: **True**

## Query results

| Query                     | Source            | Expected count | Expected onsets (s)            | Engines correct                                     |
| ------------------------- | ----------------- | -------------: | ------------------------------ | --------------------------------------------------- |
| `any_n2`                  | `any`             |              7 | 30, 60, 90, 120, 150, 180, 210 | Basic search, String search (lookup), Object search |
| `human_n2`                | `human-consensus` |              4 | 30, 120, 150, 210              | Basic search, String search (lookup), Object search |
| `psg_ai_n2`               | `psg-ai`          |              4 | 60, 120, 180, 210              | Basic search, String search (lookup), Object search |
| `headband_ai_n2`          | `headband-ai`     |              4 | 90, 150, 180, 210              | Basic search, String search (lookup), Object search |
| `any_unavailable`         | `any`             |              5 | 240, 270, 300, 330, 360        | Basic search, String search (lookup), Object search |
| `human_unavailable`       | `human-consensus` |              3 | 240, 330, 360                  | Basic search, String search (lookup), Object search |
| `psg_ai_unavailable`      | `psg-ai`          |              2 | 270, 330                       | Basic search, String search (lookup), Object search |
| `headband_ai_unavailable` | `headband-ai`     |              3 | 300, 330, 360                  | Basic search, String search (lookup), Object search |

## Analysis boundary

HED search finds annotations by meaning, keeps source identity, and returns matching rows and onsets. Alignment, disagreement, transition context, agreement statistics, and scientific interpretation remain explicit downstream Python analyses for the later real-data tutorial.

## Reproduce

```text
python use_cases/sleep/src/sleep_case.py
python use_cases/sleep/src/report.py
```
