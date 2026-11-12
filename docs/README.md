# Submission documentation

The eventual submission should include a design report explaining the two-tier
architecture, service responsibilities, REST API contracts, interservice calls,
separate SQLite ownership, concurrency/stock handling, and container deployment.
Document implementation decisions and known limitations as the lab progresses.

Capture actual execution output demonstrating startup and health checks for all
three services, both search topics, book info, CLI/gateway purchases, stock
changes, restart persistence, and relevant failure cases. Record the commands and observed
responses so results can be reproduced; never include secrets.

The root README contains the request flows and test instructions. Completed Step
4 host-side and Docker evidence is in [verification.md](verification.md), with
readable captured output in [sample-output.txt](sample-output.txt). This is
verification documentation, not the final design report.
