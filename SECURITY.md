# Security Policy

## ⚠️ Important Security Notice

**This project is NOT production-ready for handling real financial data.**

This is a hobby/learning project with known security limitations. It should only be used for:
- Personal experimentation
- Learning purposes
- Local development
- Testing with anonymized data

**DO NOT use this in production with real financial data without significant security enhancements.**

## Known Security Limitations

### Current Issues

1. **No Authentication/Authorization**
   - Single-user deployment only
   - No user isolation
   - No access controls
   - Anyone with network access can view all data

2. **Plaintext Password Storage**
   - PDF passwords stored in database without encryption
   - Visible to anyone with database access

3. **No Data Encryption**
   - Sensitive financial data stored in plaintext
   - SQLite database is not encrypted
   - Transaction details, account numbers, balances all unencrypted

4. **No Input Validation for SQL Injection**
   - Relies on SQLAlchemy parameterization only
   - No additional sanitization layers

5. **No Rate Limiting**
   - API endpoints can be abused
   - No protection against DoS attacks

6. **No HTTPS/TLS**
   - Development server runs on HTTP
   - Data transmitted in cleartext

7. **File Upload Vulnerabilities**
   - PDF files saved directly to filesystem
   - Limited validation on uploaded files
   - No virus/malware scanning

8. **Session Management**
   - No session tokens
   - No CSRF protection
   - No secure cookies

## Required Security Enhancements for Production

If you want to use this with real data, you MUST implement:

### Critical (Required)

- [ ] **User Authentication**: OAuth2, JWT, or similar
- [ ] **Encryption at Rest**: Encrypt database and sensitive fields
- [ ] **HTTPS/TLS**: Use reverse proxy (nginx) with SSL certificates
- [ ] **Password Hashing**: Hash PDF passwords with bcrypt/argon2
- [ ] **Input Validation**: Comprehensive sanitization for all inputs
- [ ] **Rate Limiting**: Implement per-IP and per-user limits
- [ ] **CORS Configuration**: Restrict origins properly
- [ ] **File Upload Security**: Validate file types, scan for malware
- [ ] **Session Management**: Secure session tokens with expiry

### Important (Recommended)

- [ ] **Database Encryption**: Use SQLCipher or PostgreSQL with encryption
- [ ] **Audit Logging**: Track all access and modifications
- [ ] **Multi-factor Authentication**: For sensitive operations
- [ ] **API Key Management**: Rotate keys regularly
- [ ] **Content Security Policy**: Implement CSP headers
- [ ] **Regular Security Audits**: Professional penetration testing
- [ ] **Dependency Scanning**: Regular updates and CVE monitoring
- [ ] **Backup Encryption**: Encrypt all backups

### Nice to Have

- [ ] Intrusion detection system
- [ ] Web application firewall (WAF)
- [ ] Security headers (HSTS, X-Frame-Options, etc.)
- [ ] Anomaly detection for suspicious activity
- [ ] Compliance with financial data regulations (PCI DSS, etc.)

## Reporting Security Vulnerabilities

If you discover a security vulnerability, please:

1. **DO NOT** open a public GitHub issue
2. **DO NOT** post details in public forums
3. **Email privately** to the repository maintainer
4. Provide detailed information:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We'll acknowledge receipt within 48 hours and work on a fix.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

Since this is a hobby project, "supported" means community-driven fixes only.

## Security Best Practices for Users

If you choose to use this project:

1. **Run locally only** - Don't expose to public internet
2. **Use anonymized data** for testing
3. **Regularly update dependencies**: `pip install -r requirements.txt --upgrade`
4. **Review uploaded PDFs** - Only upload trusted files
5. **Keep backups encrypted** - If you backup the database
6. **Use strong PDF passwords** - Even though stored in plaintext
7. **Monitor file access** - Check who has access to the upload directory
8. **Run in isolated environment** - Use Docker or VM if possible

## Compliance Considerations

This project does NOT currently comply with:
- PCI DSS (Payment Card Industry Data Security Standard)
- GDPR (General Data Protection Regulation)
- SOC 2
- ISO 27001
- Any financial industry regulations

**Do not use for any regulated financial service without extensive modifications.**

## Third-Party Dependencies

Security of third-party packages:

- **FastAPI**: Well-maintained, active security updates
- **SQLAlchemy**: Protects against SQL injection when used correctly
- **pdfplumber/pikepdf**: Limited security history, be cautious with untrusted PDFs
- **scikit-learn**: Generally secure, but pickle files can execute code (don't load untrusted models)

Regularly check for CVEs: `pip-audit` or `safety check`

## License and Liability

This software is provided "AS IS" under the MIT License, with NO WARRANTY.
The authors are NOT liable for any security breaches or data loss.

By using this software, you acknowledge and accept all security risks.

---

**Last Updated**: December 11, 2025

For questions about security, please contact the repository maintainer privately.
