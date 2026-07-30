---
name: 🛡️ Security-Related Pull Request
about: Changes that affect security, authentication, or data protection
title: '[SECURITY] '
labels: ['security', 'needs-review']
assignees: ''

---

## 🛡️ Security-Related Pull Request

### 📋 Summary
<!-- Provide a brief summary of the security changes -->

**Type of Security Change:**
- [ ] 🔒 Authentication/Authorization
- [ ] 🛡️ Input Validation/Sanitization
- [ ] 🔐 Cryptography/Encryption
- [ ] 🕵️ Secret Management
- [ ] 🧱 Infrastructure Security
- [ ] 📊 Data Protection/Privacy
- [ ] 🚨 Vulnerability Fix
- [ ] 🔍 Security Enhancement
- [ ] Other: ________________

### 🔍 Security Analysis

**What security issue does this PR address?**
<!-- Describe the security vulnerability or concern -->

**How does this change improve security?**
<!-- Explain the security benefits of this change -->

**Potential Security Risks:**
<!-- Identify any new security risks introduced by this change -->
- [ ] No new security risks introduced
- [ ] New risks identified and mitigated (see below)
- [ ] Requires security review

**Mitigation Strategies:**
<!-- If new risks were identified, how were they mitigated? -->

### 🧪 Security Testing

**Security Tests Performed:**
- [ ] SAST scanning (CodeQL/Bandit)
- [ ] Dependency vulnerability scan
- [ ] Container security scan
- [ ] DAST testing (if applicable)
- [ ] Manual security review
- [ ] Penetration testing
- [ ] Security code review

**Test Results:**
<!-- Include links to security scan results or summarize findings -->

**Security Scan Reports:**
- SAST Report: [Link to report]
- Container Scan: [Link to report]
- Dependency Scan: [Link to report]

### 📊 Risk Assessment

**Risk Level:**
- [ ] 🟢 Low Risk (Minor security improvements)
- [ ] 🟡 Medium Risk (Significant security changes)
- [ ] 🔴 High Risk (Critical security fixes)

**Business Impact:**
- [ ] No business impact
- [ ] Minor business impact
- [ ] Significant business impact
- [ ] Critical business impact

**Compliance Impact:**
- [ ] No compliance impact
- [ ] PCI DSS
- [ ] GDPR
- [ ] SOX
- [ ] HIPAA
- [ ] Other: ________________

### 🔧 Technical Details

**Files Changed:**
<!-- List the main files that were modified -->

**Security Controls Added/Modified:**
<!-- Describe specific security controls -->

**Dependencies:**
<!-- List any new dependencies and their security status -->

**Configuration Changes:**
<!-- Describe any configuration changes -->

### ✅ Security Checklist

**Code Security:**
- [ ] Input validation implemented
- [ ] Output encoding applied
- [ ] Authentication checks added
- [ ] Authorization verified
- [ ] Error handling secure
- [ ] Logging appropriate (no sensitive data)
- [ ] Cryptography implemented correctly
- [ ] Secrets not hardcoded

**Infrastructure Security:**
- [ ] Container images scanned
- [ ] Infrastructure as code secure
- [ ] Network policies configured
- [ ] Encryption enabled
- [ ] Access controls implemented

**Testing:**
- [ ] Security tests pass
- [ ] No new vulnerabilities introduced
- [ ] Existing security controls maintained
- [ ] Performance impact acceptable

### 👥 Review Requirements

**Required Reviews:**
- [ ] Security team review
- [ ] Senior developer review
- [ ] Architecture review (if applicable)
- [ ] Compliance review (if applicable)

**Security Team Sign-off:**
- [ ] @security-team-member-1
- [ ] @security-team-member-2

### 📋 Additional Information

**Screenshots/Diagrams:**
<!-- Include any relevant screenshots or architecture diagrams -->

**References:**
<!-- Link to related security issues, documentation, or standards -->

**Deployment Considerations:**
<!-- Any special deployment requirements -->

---

## 🔒 Security Team Review Checklist

*To be completed by security team members*

**Code Review:**
- [ ] Security controls properly implemented
- [ ] No hardcoded secrets or credentials
- [ ] Input validation adequate
- [ ] Error handling secure
- [ ] Cryptography implementation correct

**Architecture Review:**
- [ ] Security architecture sound
- [ ] No architectural security flaws
- [ ] Defense in depth applied
- [ ] Principle of least privilege followed

**Testing Review:**
- [ ] Security tests comprehensive
- [ ] Test results reviewed and acceptable
- [ ] No high/critical vulnerabilities
- [ ] Risk assessment accurate

**Final Approval:**
- [ ] Security team approval
- [ ] Risk acceptance documented
- [ ] Deployment authorization granted

---

**Security Review Comments:**
<!-- Add security team comments and feedback -->

**Risk Acceptance:**
- [ ] Risk accepted and documented
- [ ] Mitigation strategies approved
- [ ] Monitoring requirements defined

**Final Status:**
- [ ] ✅ Approved for deployment
- [ ] ⚠️ Approved with conditions
- [ ] ❌ Requires changes
- [ ] 🔄 Needs re-review

---

*This PR template ensures comprehensive security review of all changes that could impact application security.*