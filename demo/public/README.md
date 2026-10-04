# Jury access and published materials

The hosted demonstration uses a separate synthetic-data GPU installation. The
CPU installation remains the reference for local acceptance. Neither the model
workers nor the database receive a public port.

An additional Nginx gateway authenticates the jury, validates the exact browser
Origin and forwards to the demo's operator edge. It removes the HTTP Basic
credential before forwarding. The application still issues its own role-scoped
session cookie and enforces all action, approval, budget and release controls.
The public cookie is Secure, HttpOnly and SameSite=Strict. The hosted installation
has no enabled commercial-provider connector.

`configure.py` reads `ACTIONGATE_JURY_PASSWORD` from the environment and writes
only below the ignored `.state` directory. The generated credential is stored
in psst. Supply it to judges through the existing private submission, never
through the public repository, static site, video or query string.

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

`start-tunnel.ps1` checks this proof before starting the single named ngrok
endpoint with a selectively injected `ACTIONGATE_NGROK_AUTHTOKEN`. Traffic
inspection and remote management are disabled. It consumes the private
`.state/public-demo/ngrok.yaml` configuration, which contains no token or other
application endpoint. The tunnel must remain running during jury evaluation.
After a tunnel restart, verify its assigned URL and repeat public/browser checks
before updating the project page. Do not reuse stale access evidence.

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
