# Security policy

Do not open public issues containing real iServ credentials, cookies, CSRF
tokens, parent-letter text, attachment contents, child names, or raw portal
responses.

Report vulnerabilities through GitHub's private vulnerability reporting for
this repository. Do not disclose security issues in a public issue. Revoke
affected credentials and terminate active iServ sessions if a secret may have
leaked.

This unofficial client relies on undocumented internal iServ web APIs that may
change without notice. Run the MCP server locally through stdio. Do not expose
it through an unauthenticated HTTP or network bridge.
