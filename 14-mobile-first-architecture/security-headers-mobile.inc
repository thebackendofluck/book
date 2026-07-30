# Security headers. Re-included in every location that declares its own
# add_header, because nginx cancels ALL inherited add_header directives in
# any location that sets one of its own. CORS and Client-Hints headers are
# deliberately NOT in this file: several locations set their own
# Access-Control-Allow-Origin, and duplicating ACAO breaks CORS.
add_header X-Frame-Options "SAMEORIGIN";
add_header X-Content-Type-Options "nosniff";
add_header Referrer-Policy "strict-origin-when-cross-origin";
add_header Permissions-Policy "geolocation=(self), microphone=(), camera=()";
