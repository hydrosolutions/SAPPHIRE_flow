# Independent external-data-contract review — Plan 300

Reviewer: independent Codex subagent, commissioned for the additional contract-focused pass.
Date: 2026-09-16.
Repository base: `f2dc569ccb513ac913126d3d24d58ad2d1b18d84`.
Reviewed plan SHA-256: `a239f76eae2ec27b8e6ab18ecba91810ead0d2b852eeec95d4c18dab89d65605`.
The following is the reviewer's report, preserved without changing the plan.

---

- **P2 — Short-page termination lacks a completeness guarantee** — `docs/plans/300-dhm-observation-adapter.md:180` (T2). Stopping when a page contains fewer records than the configured `page_size`, despite a non-null `next`, conflicts with the requirement to fetch complete bounded histories. The captures and schema do not establish that the server always honors the requested page size or that every short page is terminal. A provider-capped short page could therefore terminate an earlier window prematurely; subsequent windows can advance the stored watermark beyond omitted readings. **Smallest correction:** remove short-page termination, follow validated continuations until empty results or absent `next`, and add a short-nonterminal-page regression test.

Verification limitations: read-only review at `f2dc569ccb513ac913126d3d24d58ad2d1b18d84`; all nine capture hashes match the manifest. No network requests or tests were run. DHM/BIPAD request/response equivalence was accepted as authoritative owner input.
