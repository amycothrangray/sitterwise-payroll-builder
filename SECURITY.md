# Security and private payroll data

Sitterwise Payroll is a local Mac application. Keep it bound to the loopback
interface; do not expose this Python server through hosting, port forwarding,
or a reverse proxy. A hosted service would need a separate security design.

Open payroll with its launcher. The launcher creates an owner-only local key
and opens an authenticated browser tab. The key stays on that Mac, is removed
from the URL immediately, and is excluded from history transfers. Reopen the
launcher if a new browser tab asks to reconnect. No password entry is needed.

The API checks the local address, request origin, and access key. File uploads
have size and path limits. History restores reject path aliases, escaping
symlinks, and unexpected database triggers or views. Keep macOS and the
browser updated; this does not protect against software already controlling
your logged-in Mac account.

Do not commit payroll databases, employee lists, real booking exports,
expected payroll totals, transfer archives, signing keys, or local access
keys. Use invented people and bookings in public tests, screenshots, issues,
and comments. Share payroll history privately with the authorized operator.
Deleting an example in a new commit does not remove it from older Git history.

Check dependencies with `python -m pip_audit -r packaging/requirements-macos.txt`.
Run `python3 -m unittest discover -s tests -v` and
`node tests/test_web_security.js` after changing request handling or rendering.
Openpyxl's XML protection requires defusedxml, included in the app dependencies.
See the [openpyxl security guidance](https://pypi.org/project/openpyxl/) and
[Python's server guidance](https://docs.python.org/3/library/http.server.html#security-considerations).

Report a suspected issue privately to the repository owner. Do not include
employee details or working credentials in a public GitHub issue.
