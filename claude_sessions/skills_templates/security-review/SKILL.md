---
name: security-review
description: Use when reviewing code or a diff for security issues before merging. Flags concrete, high-confidence vulnerabilities with fixes.
---

# Security review

Review changes for security problems that matter, and propose fixes.

## Check for

- **Injection**: SQL/shell/command/path built from untrusted input without parameterization or escaping.
- **AuthN/AuthZ**: missing checks, privilege escalation, IDOR (acting on objects the caller doesn't own).
- **Secrets**: hardcoded keys/tokens/passwords; secrets logged or committed.
- **Input validation**: unvalidated size/type/range; unsafe deserialization.
- **Crypto**: weak/rolled-your-own algorithms, static IVs, predictable randomness.
- **Web**: XSS (unescaped output), CSRF, open redirects, permissive CORS.
- **Dependencies & config**: known-vulnerable versions, debug/verbose errors leaking internals.

## Output

For each finding: **severity** (critical/high/medium/low), **location** (`file:line`), the **concrete exploit** (inputs → impact), and a **specific fix**.

## Rules

- Report only issues you can substantiate from the code — no generic checklists, no speculation dressed as fact.
- Rank by real-world exploitability, not theoretical purity.
- If you find nothing solid, say so plainly.

<!-- claudectl starter skill. Inspired by the security plugins in
     anthropics/claude-plugins-official and community security skills. -->
