# Jury access and published materials

The hosted demonstration uses a separate synthetic-data GPU installation. The
CPU installation remains the reference for local acceptance. Neither the model
workers nor the database receive a public port.

The optional [visitor entry](visitor.md) exposes four fixed synthetic examples at
`/`, with a forced analyst identity and a separate cookie under `/public-api/`.
The operator dashboard remains protected at `/operator`.

The base Nginx gateway authenticates the operator, validates the exact browser
Origin and forwards to the demo's operator edge. It removes the HTTP Basic
credential before forwarding. The application still issues its own role-scoped
session cookie and enforces all action, approval, budget and release controls.
The public cookie is Secure, HttpOnly and SameSite=Strict. The hosted installation
has no enabled commercial-provider connector.

A retained offline clean installation has only internal Docker networks, which
do not publish host ports. After its offline and performance checks, use
`start-local-relay.ps1 -Project actiongate-gpu-<id>` for the local browser and
SDK checks on `http://127.0.0.1:18089`. This separate, unprivileged Nginx relay
joins the selected edge and the existing jury transit network. It binds only
loopback, preserves workload authorization and browser Origin, and forwards
to the original operator edge. The application retains its session and CSRF
checks. No model worker or database joins the transit network. Verify the relay
and actual client flows before using their reports; starting it alone is not
a successful browser test.

`configure.py` reads `ACTIONGATE_JURY_PASSWORD` from the environment and writes
only below the ignored `.state` directory. The generated credential is stored
in psst. Supply it only to reviewers who need the operator dashboard. The visitor
examples need no password. The Published HackTribe project, public
repository, static site, video and query strings contain no private access code.

The proxy runs as UID 101 with a read-only root, no Linux capabilities, bounded
memory and PIDs, and a loopback-only host port. It joins exactly two networks:
the isolated demonstration's edge network and its own transit network. Nginx
buffering is disabled so the application's SSE stream remains live. Browser
mutations without the expected Origin are rejected before any upstream request.

Run `verify_proxy.py` with the expected origin and upstream network before
publication. It exercises authentication and CSRF boundaries, checks the fresh
application session in `--mode application`, and binds the report to the actual
container, image, network, mounted configuration and producer hashes. Repeat
with `--public` to verify the HTTPS route. A synthetic echo probe is explicitly
labelled `echo`; it is not evidence of a working ActionGate application.

`start-tunnel.ps1` checks the operator-only baseline proof before starting the single named ngrok
endpoint with a selectively injected `ACTIONGATE_NGROK_AUTHTOKEN`. Traffic
inspection and remote management are disabled. It consumes the private
`.state/public-demo/ngrok.yaml` configuration, which contains no token or other
application endpoint. The tunnel must remain running during jury evaluation.
After a tunnel restart, verify its assigned URL and repeat public/browser checks
before updating the project page. Do not reuse stale access evidence. The optional
visitor configuration changes the rendered hash and route contract; validate it
with `verify_visitor.cjs` and preserve its own configuration binding before a
tunnel restart. Do not use the baseline proof as evidence for the changed routes.

`index.html` and `styles.css` form the public material page. The build helper
copies the final film, captions, cover, PDF and editable deck from the same
verified release ZIP into a separate static publishing directory. It checks
the archive's credential-scan binding and each asset's release-manifest hash
against the reviewed local file. Public material files do not require the
demonstration password.

After publication, `verify_materials.cjs` downloads every presentation asset
anonymously and compares its bytes with that build manifest. A fresh browser
then opens the page, decodes and plays both film variants, loads the English captions,
checks the demo and source links, and captures desktop and mobile views.
`verify_browser.cjs` separately verifies the actual authenticated application,
its model decisions, recorded effects, event stream and role-scoped exports.
The English narrated walkthrough is the main film. The separate Polish music
video has its own player and optional English translation. Their source and
review records are described in [media provenance](../../docs/media-provenance.md).
