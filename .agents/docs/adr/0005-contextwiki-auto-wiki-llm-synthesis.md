# ADR 0005: ContextWiki Auto Wiki LLM Synthesis Boundary

## Status

accepted

## Date

2026-05-24

## Context

Phase C adds Auto Wiki generation over active ContextWiki evidence. The deterministic
path can generate citation-backed Markdown locally, but users may want more
natural prose and structure when an LLM provider is available.

The retrieved evidence can contain private source content from Notion, Tistory,
GitHub, website/docs, PDFs, or future connectors. LLM synthesis is therefore an
external integration and secrets/configuration boundary: enabling it may send
retrieved chunk text and source metadata outside the local process.

## Decision

Auto Wiki LLM synthesis is opt-in. The application only builds an external LLM
synthesizer when `CONTEXTWIKI_WIKI_LLM_ENABLED=true`, the configured provider is
supported, and the runtime API key is available through the configured
environment variable. The initial provider is OpenAI, implemented behind
`wiki.synthesis.OpenAIWikiSynthesizer` and wired into `WikiGenerationService` as
an optional dependency.

The MCP `generate_wiki_page` response shape remains stable. The service always
builds deterministic citations, backlinks, sections, and Markdown first. If an
LLM synthesizer is configured, it receives only citation-ready evidence and must
return a structured page using the provided citation markers. The service rejects
provider output that is malformed, omits required citation markers, references
unknown citation markers, has section marker metadata that does not match section
content, or includes substantive uncited sentences. Rejected or failed provider
output falls back to deterministic local Markdown.

Before prompt construction, secret-like strings in evidence metadata and text are
redacted. This redaction is a defense-in-depth guard and does not make it safe to
send arbitrary private content without user opt-in.

Smoke tests and deterministic validation keep LLM synthesis disabled by default.
Live LLM validation requires explicit user approval beyond ordinary fake or
GitHub live smoke.

## Consequences

- Auto Wiki can produce more natural pages when explicitly enabled, while local
  deterministic behavior remains the default.
- Source evidence may still leave the machine when the feature is enabled; users
  must opt in and provide runtime credentials.
- Secret-like evidence values are redacted before LLM prompts, but redaction is
  best-effort and does not replace source-side secret hygiene.
- Future providers must preserve the same opt-in, redaction, citation validation,
  deterministic fallback, and no-secret-logging constraints.
- Reviewers should treat changes to LLM prompt payloads, provider wiring,
  citation validation, or secret redaction as security-relevant.

## Alternatives Considered

- Always enable LLM synthesis when `OPENAI_API_KEY` exists: rejected because it
  could send private indexed content externally without explicit product opt-in.
- Only keep deterministic Auto Wiki output: rejected because it does not satisfy
  the requirement for more natural LLM-based summary and structure.
- Trust provider citations without local validation: rejected because provider
  output can omit citations or add unsupported claims.

## Related

- `.agents/docs/architecture.md`
- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `wiki/service.py`
- `wiki/synthesis.py`
