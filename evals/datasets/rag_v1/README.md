# Aurora Relay RAG evaluation dataset (`rag_v1`)

Public synthetic project knowledge base for retrieval, citation, and stale-document
blocking evaluations.

## Contents

- README, ADR, runbook, guide, hard-negative, inactive, and distractor documents
- Mixed Korean/English documents
- train / dev / test case splits with labeled document and chunk IDs

## Policy

- Test labels must not be used for retrieval tuning.
- Fixture lexical results are regression evidence, not production embedding quality.
- No personal, company, or live user documents are included.
