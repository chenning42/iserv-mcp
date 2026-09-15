# Security policy

Do not open public issues containing real iServ credentials, cookies, CSRF
tokens, parent-letter text, attachment contents, child names, or raw portal
responses.

Report vulnerabilities through GitHub's private vulnerability reporting for
this repository. Do not disclose security issues in a public issue. Revoke
affected credentials and terminate active iServ sessions if a secret may have
leaked.

This unofficial client uses private web endpoints that can change without
notice. Keep deployments local and do not expose the stdio server through an
unauthenticated network bridge.
