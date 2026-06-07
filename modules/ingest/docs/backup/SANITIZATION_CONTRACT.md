# Sanitization Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines the minimum contract for transforming raw feed input into sanitized working text.

The purpose of sanitization is not to create publish-ready prose. The purpose is to create a predictable downstream working representation for classification, review support, and later workflow decisions.

---

## 2. Input And Output

Input:

- raw feed title
- raw feed summary or description when present
- raw embedded HTML fragments when present

Output:

- sanitized working text
- sanitization metrics and flags

Important rule:

- the sanitized output must be clearly distinguishable from raw source fields in storage and in downstream contracts

---

## 3. Minimum Transformation Expectations

Sanitization must at minimum:

- strip obvious HTML noise
- remove script and style content when present
- decode or flatten common HTML entities when needed
- normalize whitespace
- remove repeated empty blocks and obvious boilerplate fragments when feasible
- apply a controlled length cap when needed

This module does not need to solve perfect article extraction in MVP.

---

## 4. Output Quality Goals

Sanitized working text should be:

- readable enough for downstream model input
- stable enough that downstream prompts can rely on it
- conservative enough not to invent new content

Sanitization must not:

- rewrite source meaning
- summarize source content as a substitute for cleaning
- produce site-ready editorial copy

---

## 5. Required Metrics And Flags

The long-term contract should preserve lightweight sanitization observability fields such as:

- raw text length
- sanitized text length
- reduction ratio when measurable
- whether HTML was detected
- sanitization method or version
- whether truncation occurred
- whether the item became low-context after cleaning

These fields help evaluate sanitization quality even when raw payload retention expires.

---

## 6. Low-Context Boundary

Sanitization may produce a low-context outcome.

That means:

- cleaning can succeed technically
- but the resulting usable text may still be too thin for confident classification

`ingest` may persist a low-context sanitization signal, but it must not perform the classification decision itself.

---

## 7. Non-Goals

This contract does not require:

- full-page readability extraction for every item
- source-specific custom cleaners for every feed in MVP
- editorial rewriting
- publication formatting
