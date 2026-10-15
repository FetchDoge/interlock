# Intake Ledger — sources behind the Interlock transfer

Per-source reading depth for the Boston Dynamics study that this project
transfers. Fluency is not proof; this ledger is the difference.

## Read deeply

- **Spot API protos, SDK 5.2.0** — all 152 `.proto` files under `bosdyn/api`
  read in full, 2026-09-26/27. Source of: leases with keepalives, e-stop,
  fault taxonomy, command + lease + clock admission, robot-perceived vs.
  client-asserted objects. Study notes are kept privately and not published.
- **`bosdyn/api/bddf.proto`** — read directly from the repository,
  2026-09-26. Source of: the ledger's self-describing structure (format
  descriptor first, per-series schema, timestamps, closing index).
- **Spot SDK conceptual documentation** — full first-pass crawl of
  `dev.bostondynamics.com`, 2026-09-26/27. Source of: the documentation
  register used in `docs/concepts/` (ALL-CAPS noun-phrase H1s, workflow
  stages, narrative closers, hedged safety language). The style guide
  is kept privately and not published.
- **Payload developer documentation** — craft analysis, 2026-09-26. Source
  of: interface records (mechanical → electrical → software; guarantees,
  constraints, ownership, failure behavior at every boundary).
- **Spot SDK repository management** — local clone analyzed, 2026-09-26.
  Source of: `scripts/release.sh` discipline (single VERSION source,
  release commits, tags, changelog). Proprietary license verified; NOT
  copied — Interlock is Apache-2.0.

## Skimmed

- **Boston Dynamics University catalog** — 16 videos cataloged; no
  transcripts exist (verified via caption probes). Substance lives in the 5
  companion support articles, all read. Contributed the safety-first
  curriculum spine, not mechanism.

## Index-only

- Deeper SDK sections (Choreography, Joint Control, Spot Arm internals,
  Data Acquisition internals) — indexed, not transferred. Nothing in
  Interlock depends on them.

## Explicitly unverified

- How a real Spot lease behaves under network partition (no robot was
  available; keepalive/expiry semantics here follow the documented API,
  not observed hardware).
- Binary BDDF interoperability — the ledger implements the *described
  structure* over JSONL, not the BDDF binary encoding.
- Whether leased authority measurably reduces real agent incidents — the
  substrate is testable; the safety claim is hedged in `CHARTER.md` and
  `SECURITY.md` by design.
