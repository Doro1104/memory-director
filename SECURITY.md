# Security and privacy reports

Do not post private media, credentials, databases, raw logs, local paths or certificate keys in public issues. Provide a minimal reproduction with synthetic images and the sanitized doctor.py summary. If a future repository enables private vulnerability reporting, use that mechanism for security-sensitive details.

Deployment defaults to loopback. Native-app LAN access uses pinned TLS. Browser LAN access is a separate, explicit opt-in using unencrypted HTTP, intended only for a trusted home Wi-Fi network. Both require account authentication. Do not expose either listener directly to the Internet. The web worker caches only public connection-help assets, never account pages, APIs or media. The deployment skill is a workflow policy, not a process sandbox or a guarantee against other software with equal operating-system permissions.
