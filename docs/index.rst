.. meta::
   :description: Benchmark case studies and shared benchmark tooling for HED (Hierarchical Event Descriptors) annotation and analysis
   :keywords: HED, hierarchical event descriptors, benchmarks, performance, search, neuroscience, BIDS, event annotation

HED benchmarks
==============

Welcome to the HED benchmarks documentation!
This repository holds benchmark case studies and shared benchmark tooling for
**Hierarchical Event Descriptors (HED)** - a standardized framework for
annotating events and experimental metadata in neuroscience and beyond.

The repository has two kinds of content:

* **Benchmark case studies**: self-contained benchmarks, one directory each
  under ``use_cases/`` - performance benchmarks for HED search now, and
  application-oriented benchmarks (sleep staging, language scoring, epilepsy
  scoring) as they are developed.
* **Shared tooling**: the ``hedbench`` package, which will run any case study
  through a standardized JSON input/output format validated with JSON Schema.

.. toctree::
   :maxdepth: 2

   User guide <user_guide>
   Use cases <use_cases/index>
   API <api/index>

* :ref:`genindex`
