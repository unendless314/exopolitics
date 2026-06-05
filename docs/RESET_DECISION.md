# Reset Decision

**Status:** Active note

This repository has entered a documentation reset phase.

Rules:

- `docs/` contains the new active planning set
- `docs_archive/2026-06-reset/` contains superseded planning and decisions
- archived documents remain useful as reference, but they are not active contracts
- implementation changes should wait until the rewritten top-level contracts are stable

Why this reset happened:

- previous docs mixed valid high-level structure with weak storage/input contracts
- raw feed content and downstream classify input were not clearly separated
- rewriting the planning layer is cheaper than forcing incremental fixes onto unstable contracts
