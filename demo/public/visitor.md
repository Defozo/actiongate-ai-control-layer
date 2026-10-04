# Public visitor entry

The visitor page runs four fixed examples through the live ActionGate gateway:
supplier review, prompt injection, tenant isolation and personal-data redaction.
It uses local models and the seeded `synthetic_test_tenant` workspace. A response
contains the real run and operation records; model capacity can cause a wait.

The operator dashboard is at `/operator` and retains HTTP Basic authentication.
All original `/api` routes remain behind that authentication. Visitor requests
use separate HttpOnly cookies scoped to `/public-api/`, and the proxy forces the
analyst identity and the synthetic tenant. It accepts only four fixed scenario
bodies. Operator cookies and Authorization headers are not forwarded on visitor
routes. Policy changes, approvals and arbitrary actions are not visitor routes.

After configuring the private operator gateway as described in README.md, keep
its rendered configuration as a private baseline. Render the additional entry:

```powershell
python demo/public/render_visitor.py --base-config .state/public-demo/operator-baseline.conf --output .state/public-demo/visitor-candidate.conf
```

Validate with the same pinned nginx image and private password-file mount, then
copy the candidate over the mounted `nginx.conf` and run `nginx -t` followed by
`nginx -s reload` in the proxy container. Keep the baseline outside public source
control for rollback. Rendering does not read or rotate the operator password.

Session creation is limited to 20 requests per minute per client address. The
shared visitor workflow limit is six requests per minute and one active workflow.
Rate-limited requests return 429. Submitted workflows are never retried by the
page. Exact Host and Origin checks from the baseline remain in force.

Verify anonymous HTML, CSS, JavaScript and readiness, protected operator/API
routes, Origin enforcement, forced identity and all four real examples after
deployment. Preserve actual failures in the result. The original authenticated
browser script targets the older dashboard root; use `/operator` for that view.
