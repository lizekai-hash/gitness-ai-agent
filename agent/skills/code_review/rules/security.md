# Security Review Rules

## Critical (Always Flag, Confidence 90+)

### Injection
- SQL queries built with string concatenation/formatting → use parameterized queries
- Shell command execution with user-controlled input → sanitize or avoid
- Path traversal: user input used in file paths without validation

### Secrets
- Hardcoded API keys, passwords, tokens, or connection strings
- Secrets logged to stdout/stderr or included in error messages
- Secrets committed to version control (even in comments)

### Authentication & Authorization
- Missing authentication on sensitive endpoints
- Authorization checks that can be bypassed (IDOR)
- Insecure token storage (localStorage for sensitive tokens)

## Important (Flag with Confidence 70-89)

### Data Exposure
- Sensitive data in URL query parameters (logged by proxies)
- Verbose error messages exposing internal details to users
- Missing rate limiting on authentication endpoints

### Cryptography
- Use of deprecated hash algorithms (MD5, SHA1 for security)
- Hardcoded cryptographic keys or IVs
- Insufficient key length (< 256 bits for symmetric, < 2048 for RSA)

### Configuration
- Debug mode enabled in production configuration
- CORS allowing all origins (`*`) on authenticated endpoints
- Missing security headers (CSP, X-Frame-Options, etc.)
