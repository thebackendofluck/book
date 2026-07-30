# Security headers for regulated iGaming markets (UKGC, MGA, NJ DGE).
# IMPORTANT nginx rule: add_header directives are inherited from the server
# block ONLY if a location defines no add_header of its own. Any location
# that sets its own headers (Cache-Control, X-Cache-Status, CORS) silently
# cancels ALL inherited headers, so this file must be re-included in every
# such location. That is why it exists as an include instead of inline lines.

# Prevent clickjacking (slot games in iframes on rogue sites)
add_header X-Frame-Options "SAMEORIGIN" always;
# Prevent MIME type sniffing
add_header X-Content-Type-Options "nosniff" always;
# XSS filter for older browsers
add_header X-XSS-Protection "1; mode=block" always;
# HSTS: force HTTPS for 1 year (important for player trust)
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
# Content Security Policy: restrict where scripts/frames can load from
add_header Content-Security-Policy "default-src 'self' https:; script-src 'self' 'unsafe-inline' https://cdn.example.com; frame-ancestors 'self'" always;
# Referrer policy: don't leak player URLs to third parties
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
