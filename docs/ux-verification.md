# UI verification for delivery v0.1.1

Delivery v0.1.1 contains two rounds of UI corrections. The core package and SBOM version remain 0.1.0. The 95 fingerprinted core sources, model and policy are unchanged by these corrections; UI sources, browser contracts and build outputs have separate recorded hashes.

The corrections cover six behaviors:

- Closed mobile navigation is inert. Opening, Escape, closing and viewport changes preserve visible keyboard focus.
- Invalid action JSON focuses the field, associates its error and prevents dispatch. Valid revalidation clears the invalid state.
- An expected unauthenticated session response does not appear as a failed sign-in. Network, server and rejected sign-in errors remain visible.
- Budget headings use structured scope and resource fields. Full account identifiers remain visible and wrap; periods and ledger amounts are preserved.
- Metric detail text has increased contrast. Sixteen computed samples across Overview and Budgets at desktop and mobile widths met 4.5:1.
- Test Lab reports pending, completed and failed inspection requests through a stable polite status region. Pending requests do not present an earlier result as current; errors and execution permissions are preserved.

The production build and complete browser contract suite passed 22/22 tests with zero failures or skips. These use controlled API responses, including delayed success, failure and repeated requests. They also check six account scopes, four resource units, exact amounts and full identifiers at 390 px. Actual deployed workflows remain separate evidence.

The [UX verification record](../artifacts/reports/ux-verification.json) binds the reviewed source hashes, browser contract report and original independent observations:

- [Initial application review](../artifacts/reports/ux-review/application-initial.json)
- [Application review after round 1](../artifacts/reports/ux-review/application-after-round1.json)
- [Final application review](../artifacts/reports/ux-review/application-final.json)
- [Final materials-page review](../artifacts/reports/ux-review/materials-final.json)

The final independent application review has status `partial`. It inspected normal entry and all five dashboard views in 56 browser calls and retained two findings:

- **UX-01, medium:** Test Lab can show `Block`, a benign guard verdict and risk 0 without distinguishing the input assessment from an output-stage block. In the observed case, input text appeared under “Inspected output” although the full evidence recorded `stage: output`, `status: output_blocked` and `result: null`. The presentation does not clearly explain the provenance of the text or the reason codes. This is an unresolved presentation issue; the review does not establish whether the protective decision was correct.
- **A11Y-01, low:** The descriptions in the empty Verification history and Verification evidence panels measured approximately 4.49:1 contrast, below 4.5:1. The sampled text was 11 px, with foreground `rgb(120, 134, 125)` on `rgb(25, 28, 30)`. This remaining finding concerns shared empty-state descriptions, separate from the corrected metric detail text.

The final materials-page review also has status `partial`, with zero findings in its sampled coverage. Coverage does not confirm actual browser zoom at 200%, real screen-reader use or every operation state. This is not a full WCAG conformity assessment. Both permitted correction rounds were completed; the two remaining application findings are documented without a third edit round.
