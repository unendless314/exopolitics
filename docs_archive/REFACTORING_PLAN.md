# Refactoring Plan: Renaming Review Module to Curate

**Document version:** v1.2  
**Updated:** 2026-06-15  
**Status:** Pending Review  

---

## 1. Rationale & Objectives

During the active planning phase of the processing pipeline, a semantic conflict was identified regarding the term `review`. Currently, `review` is used to represent both:
1. The automated, LLM-based triage, routing, and format selection of raw, incoming RSS feed items.
2. The final manual approval or revision of edited drafts before publication.

To eliminate long-term semantic ambiguity and maintain a clean boundary separation:
* The third module is renamed to **`curate`**. Its purpose is redefined as "editorial curation, triage, formatting, and routing decisions."
* The term `review` is reserved exclusively for draft verification and final human approval workflows (managed inside the `edit` or `revision` module).
* This rename aligns the grammatical verb structure of the processing pipeline:
  `ingest` (verb) $\rightarrow$ `classify` (verb) $\rightarrow$ `curate` (verb) $\rightarrow$ `edit` (verb) $\rightarrow$ `publish` (verb) $\rightarrow$ `site` (presentation).

---

## 2. Naming & Semantic Preservation Principles

To prevent over-replacement and ensure consistent terminology, the following naming rules are established:

### 2.1 What is renamed to Curate/Curation
* **Module Path:** `modules/curate/` (active verb matching pipeline flow).
* **Database Tables:** `curation_decision`, `curation_output` (representing the noun-form curation outcomes).
* **Database Columns:** `curation_decision_id`, `curation_output_id`, `curate_status` (enum: `'approved'`, `'rejected'`, `'failed'`), `curated_at`.
* **Module Policy Document:** `CURATION_POLICY.md` (renamed from `REVIEW_POLICY.md`).
* **CLI Command Group:** `python -m modules.curate.src.cli`

### 2.2 What is preserved as Review (Do Not Rename)
* **Human Draft Sign-Off:** Any references to final human approval of edited drafts before publishing are kept as "manual review", "editorial review", or "draft review".
* **Downstream workflow references:** Conceptual transitions like `curation decision -> edit workflow -> human review decision` are preserved.

---

## 3. Scope of Impact & Refactoring Level

### 3.1 Aggressive Rename (DB Contract Alignment)
Because the `review` module currently has **no executable code, no database records in `canonical.db`, and no test fixtures**, we choose the **Aggressive Rename** path. 
* We will rewrite the DDL schema in the migration scripts directly to use `curation_decision` and `curation_output` from scratch.
* There is no legacy migration or database cutover requirement because the schema is in a pre-migration/greenfield state.
* **Caveat:** This assumption holds true because no files, scripts, or tests outside the top-level contract documents in `docs/` treat `review_*` tables as an active, implemented runtime dependency.

### 3.2 Path & CLI Command Map
The following translation list dictates the exact file and command renames:

| Original Path / Command | New Path / Command |
| :--- | :--- |
| `modules/review/` | `modules/curate/` |
| `modules/review/docs/*.md` | `modules/curate/docs/*.md` |
| `modules/review/config/*` | `modules/curate/config/*` |
| `modules/review/docs/REVIEW_POLICY.md` | `modules/curate/docs/CURATION_POLICY.md` |
| `modules/review/src/migrations/v001_initial_review_tables.sql` | `modules/curate/src/migrations/v001_initial_curate_tables.sql` |
| `python -m modules.review.src.cli migrate` | `python -m modules.curate.src.cli migrate` |
| `python -m modules.review.src.cli run` | `python -m modules.curate.src.cli run` |
| `python -m modules.review.src.cli status` | `python -m modules.curate.src.cli status` |

---

## 4. Execution Strategy: Side-by-Side Equivalent File Writing

To eliminate the risk of accidental text corruption during in-place replacements, we will use a **safe side-by-side creation and deletion strategy**:

1. **Step 1: Create the New Module Tree**
   We will create a fresh directory tree `modules/curate/` alongside `modules/review/`.
2. **Step 2: Write Clean Curate Documents**
   We will write the equivalent documentation directly into `modules/curate/docs/` from scratch, applying the new naming and contract terminology directly. This ensures the files are written cleanly without search-and-replace errors.
3. **Step 3: Update Top-Level Contracts**
   We will update the top-level planning documents in `docs/` (such as `MODULE_BOUNDARIES.md`, `DATA_LIFECYCLE.md`, `CANONICAL_ENTITY_CONTRACT.md`, `IMPLEMENTATION_ROADMAP.md`, `PRD.md`, `SYSTEM_OVERVIEW.md`, and `README.md`) to substitute references to the `review` module with the `curate` module.
   * **Crucial Control:** We must only substitute references that point specifically to the third processing pipeline module (e.g. "review module", "review DDL"). We must **not** substitute references to human draft verification (e.g. "draft review", "manual review", "editorial review"), preserving their intended workflow semantics.
4. **Step 4: Verification**
   Verify the new module documentation and the updated top-level documents side-by-side with the old `modules/review/` directory.
5. **Step 5: Deletion**
   Once verified, we will execute `git rm` on `modules/review/` to delete the old files cleanly.
