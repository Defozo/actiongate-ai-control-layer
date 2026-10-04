# ActionGate: AI Control Layer

**DEFOZO SOFTWARE HOUSE**

**Michał Kiełtyka**

## Project description

An agent opens a supplier document to review delivery terms. A hidden instruction
asks it to reveal confidential data. ActionGate checks the next action before it
reaches a model, API or MCP service.

The gateway combines access rules and data controls with a local semantic guard.
It checks the agent's identity, purpose, document permissions, destination and
available budget. Confidentiality follows the entire workflow, including memory
and delegated work. Protected publication requires approval of the exact content
and permitted recipient.

The demonstration follows a complete supplier review using synthetic business
data: read an approved document through MCP, summarize it with a local model,
save protected memory and create an internal report. The same dashboard shows
personal data being redacted, an injected instruction stopped before dispatch
and another tenant’s document rejected by access policy.

Operators can edit a signed control catalog, choose a stricter profile, publish
threat rules and compare policy decisions without repeating business effects.
Budgets cover local inference resources and commercial API commitments. Delegated
agents share the original allowance. Each operation links its decision to the
recorded effect and settlement, giving security teams a traceable audit
and management a clear view of resource use.

HTTP, a Chat Completions interface and MCP support existing agent clients. The
package includes Python and TypeScript examples, automated positive and negative
tests, reproducible Compose setup and an independent evaluation runbook. Judges
can change a rule, try their own input and inspect the resulting action.

## Presentation and demonstration

- [ActionGate.pdf](../artifacts/submission/ActionGate.pdf): English pitch, ten slides.
- [ActionGate.pptx](../artifacts/submission/ActionGate.pptx): editable deck with speaker notes.
- `artifacts/submission/ActionGate-pitch.mp4`: narrated product demonstration with
  quiet original music. Its edit preserves the actual workflow outcomes and labels
  the recorded processing interval omitted from the film.
- `artifacts/submission/ActionGate-cover.png`: presentation cover for the project page.
- `artifacts/submission/ActionGate-demo.en.vtt`: English captions timed from the
  actual narration alignment, accompanied by `ActionGate-transcript.txt`.
- `artifacts/submission/ActionGate-UI-workflows.mp4`: the dedicated browser walkthrough,
  accompanied by the persisted workflow records in `ui-workflow.json`.
- `artifacts/submission/audio-verification.json`: audio provenance, measurements
  and the documented perceptual review of the delivered soundtrack.
- [Pitch review evidence](../artifacts/submission/pitch-evidence/manifest.json):
  the exact audio assessments, audiovisual review, slide review, shot plan and
  render hashes referenced by the final verification report.
- `artifacts/submission/ActionGate-song-pl-2026-10-04-v2.mp4`: additional 129-second
  music video with Polish vocals and an optional English translation track.
  The English narrated film remains the main demonstration.
- [Media provenance](media-provenance.md): artwork and music sources, rights
  documentation and the scope of the audio and video reviews.
- [Jury runbook](jury-runbook.md): setup, editable scenarios and independent checks.
- [Acceptance documentation](acceptance.md): exact test results, performance
  evidence and links to the technical scope of each measurement.

The historical read-only `ActionGate-recorded-evidence.webm` remains in the
archive with its original manifest. The narrated pitch and current workflow
recording are the presentation materials for the final product.

## Evaluation package

The repository and downloadable release contain source, configuration, client
examples, test reports and synthetic audit exports. The project page links to
the running demonstration and these materials. Team attribution comes from
[TEAM.json](../TEAM.json). Submission requirements and source discrepancies are
recorded separately in [submission requirements](submission-requirements.md).
