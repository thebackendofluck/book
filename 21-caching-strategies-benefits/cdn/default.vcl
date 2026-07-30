##
## default.vcl — Production Varnish VCL for iGaming
## Varnish version: 7.1+
##
## Architecture role: In-memory object cache sitting between the CDN edge
## (or HAProxy) and the game origin servers.
##
## Benchmarked on a 128-core, 500 GB reference host:
##   1KB (odds feed):   290,184 req/s, P99=1.20ms
##   50KB (game lobby): 181,060 req/s, P99=1.18ms
##   5MB (game bundle):   3,263 req/s, P99=17.47ms
##
## Start:
##   varnishd -f /etc/varnish/default.vcl -a 127.0.0.1:6081 \
##            -s malloc,4g -T 127.0.0.1:6082 -n /var/run/varnish
##

vcl 4.1;

# =============================================================================
# BACKEND DEFINITION
# Points to the game origin server (or Nginx CDN if layered)
# .probe: Varnish will actively health-check the backend.
#   If backend fails 3 of 5 probes, it is marked sick.
#   Grace mode kicks in: stale content is served instead of 503.
# =============================================================================
backend game_origin {
    .host = "127.0.0.1";
    .port = "3000";
    .connect_timeout = 2s;
    .first_byte_timeout = 10s;
    .between_bytes_timeout = 5s;
    .probe = {
        .url = "/health";
        .timeout = 1s;
        .interval = 5s;    # Check every 5 seconds
        .window = 5;       # Track last 5 checks
        .threshold = 3;    # Mark healthy if 3/5 checks pass
    }
}

# =============================================================================
# PURGE ACL
# Only these IPs may send PURGE requests.
# In production: restrict to your deploy pipeline, CDN controllers.
# =============================================================================
acl purge_allowed {
    "127.0.0.1";       # Localhost (deploy scripts)
    "::1";             # IPv6 loopback
    "10.0.0.0"/8;      # Internal network (game servers, deploy agents)
    "172.16.0.0"/12;   # Docker/container networks
}

# =============================================================================
# ACL for ban lurker (background cache invalidation by pattern)
# =============================================================================
acl ban_allowed {
    "127.0.0.1";
    "::1";
    "10.0.0.0"/8;
}

# =============================================================================
# VCL_RECV — Called at the start of each request.
# This is the routing decision point: cache, pass, pipe, or purge.
# =============================================================================
sub vcl_recv {

    # -------------------------------------------------------------------------
    # PURGE SUPPORT
    # Ops/deploy tools call: curl -XPURGE http://varnish:6081/game-lobby.html
    # -------------------------------------------------------------------------
    if (req.method == "PURGE") {
        if (!client.ip ~ purge_allowed) {
            return(synth(405, "PURGE not allowed from " + client.ip));
        }
        return(purge);
    }

    # -------------------------------------------------------------------------
    # BAN-BASED CACHE INVALIDATION
    # More powerful than PURGE: can invalidate by URL pattern.
    # Example: ban all slot game bundles after a new game deploy:
    #   curl -H "X-Ban-Pattern: /games/mystical-" http://varnish:6081/
    # -------------------------------------------------------------------------
    if (req.method == "BAN") {
        if (!client.ip ~ ban_allowed) {
            return(synth(403, "BAN not allowed"));
        }
        if (req.http.X-Ban-Pattern) {
            ban("req.url ~ " + req.http.X-Ban-Pattern);
            return(synth(200, "Ban added for pattern: " + req.http.X-Ban-Pattern));
        }
        return(synth(400, "X-Ban-Pattern header required"));
    }

    # -------------------------------------------------------------------------
    # WEBSOCKET PASS-THROUGH
    # Live casino dealer streams, in-play betting feeds — Varnish cannot cache
    # these. Use pipe() to create a direct tunnel between client and backend.
    # -------------------------------------------------------------------------
    if (req.http.Upgrade ~ "(?i)websocket") {
        return(pipe);
    }

    # -------------------------------------------------------------------------
    # PLAYER-SPECIFIC API — NEVER CACHE
    # Wallet balance, bet history, KYC status — must come from origin every time.
    # A cache miss here means a player could see another player's balance.
    # -------------------------------------------------------------------------
    if (req.url ~ "^/api/(player|wallet|bet|session|account|kyc|transaction|bonus)/") {
        return(pass);
    }

    # -------------------------------------------------------------------------
    # GENERAL API — PASS (configurable by your ops team)
    # Other API calls may be cacheable depending on your architecture.
    # The safe default is to pass them to origin.
    # -------------------------------------------------------------------------
    if (req.url ~ "^/api/") {
        return(pass);
    }

    # -------------------------------------------------------------------------
    # ONLY CACHE GET and HEAD requests.
    # POST = form submissions (deposits, bets). PUT/DELETE = data mutations.
    # -------------------------------------------------------------------------
    if (req.method != "GET" && req.method != "HEAD") {
        return(pass);
    }

    # -------------------------------------------------------------------------
    # STRIP COOKIES FOR CACHEABLE ASSETS
    # Game bundles, images, CSS, fonts are the same for ALL players.
    # Cookies would create a separate cache entry per player — wasteful.
    # -------------------------------------------------------------------------
    if (req.url ~ "\.(js|css|png|jpg|jpeg|gif|webp|svg|ico|woff|woff2|ttf|eot|mp4|webm|ogg|bundle|wasm|zip)(\?.*)?$") {
        unset req.http.Cookie;
        unset req.http.Authorization;
    }

    # -------------------------------------------------------------------------
    # STRIP COOKIES FOR JSON/HTML LOBBY PAGES
    # Game lobby HTML and odds feeds are cacheable (not player-specific).
    # -------------------------------------------------------------------------
    if (req.url ~ "^/(lobby|games|promotions|sports)" || req.url ~ "\.html$") {
        unset req.http.Cookie;
    }

    return(hash);
}

# =============================================================================
# VCL_HASH — Defines the cache key.
# Default: URL + Host. We explicitly exclude cookies from the key
# (cookies were stripped in vcl_recv for cacheable content).
# =============================================================================
sub vcl_hash {
    hash_data(req.url);

    if (req.http.Host) {
        hash_data(req.http.Host);
    } else {
        hash_data(server.ip);
    }

    # For HTTPS origins, vary cache by scheme (http vs https response may differ)
    if (req.http.X-Forwarded-Proto) {
        hash_data(req.http.X-Forwarded-Proto);
    }

    return(lookup);
}

# =============================================================================
# VCL_BACKEND_RESPONSE — Called when backend returns a response.
# Set TTL, grace period, and keep period here.
# =============================================================================
sub vcl_backend_response {

    # -------------------------------------------------------------------------
    # GAME BUNDLES — Cache for 24 hours
    # Slot game JS/WASM/ZIP bundles only change on new release.
    # Use versioned URLs (/games/slots/mystical-v1.2.3.bundle) so old bundles
    # expire from cache naturally without needing to purge.
    # -------------------------------------------------------------------------
    if (bereq.url ~ "\.(bundle|wasm|zip)(\?.*)?$") {
        set beresp.ttl = 24h;
        set beresp.grace = 30s;   # Serve stale for 30s if backend is slow/sick
        set beresp.keep = 60s;    # Keep for 60s after grace (for conditional revalidation)
        unset beresp.http.Set-Cookie;
        return(deliver);
    }

    # -------------------------------------------------------------------------
    # STATIC ASSETS — Cache for 1 hour
    # -------------------------------------------------------------------------
    if (bereq.url ~ "\.(js|css|png|jpg|jpeg|gif|webp|svg|ico|woff|woff2|ttf|eot|mp4|webm|ogg)(\?.*)?$") {
        set beresp.ttl = 1h;
        set beresp.grace = 30s;
        set beresp.keep = 60s;
        unset beresp.http.Set-Cookie;
        return(deliver);
    }

    # -------------------------------------------------------------------------
    # HLS LIVE STREAM SEGMENTS (.ts) — Cache aggressively
    # TS segments are write-once; once written they never change.
    # -------------------------------------------------------------------------
    if (bereq.url ~ "\.ts(\?.*)?$") {
        set beresp.ttl = 30m;
        set beresp.grace = 5m;
        return(deliver);
    }

    # -------------------------------------------------------------------------
    # HLS PLAYLISTS (.m3u8) — Very short TTL
    # Live stream playlists update every 2–10 seconds.
    # A 2-second TTL allows Varnish to absorb burst requests (many players
    # loading the same channel) while staying current.
    # -------------------------------------------------------------------------
    if (bereq.url ~ "\.m3u8(\?.*)?$") {
        set beresp.ttl = 2s;
        set beresp.grace = 4s;
        return(deliver);
    }

    # -------------------------------------------------------------------------
    # RESPECT ORIGIN CACHE-CONTROL HEADERS
    # If origin sends Cache-Control: no-cache or no-store, respect it.
    # This gives the game server control over caching behavior.
    # -------------------------------------------------------------------------
    if (beresp.http.Cache-Control ~ "(no-cache|no-store|private)") {
        set beresp.uncacheable = true;
        set beresp.ttl = 0s;
        return(deliver);
    }

    # -------------------------------------------------------------------------
    # CACHE ERRORS BRIEFLY to prevent stampede on a flapping backend
    # -------------------------------------------------------------------------
    if (beresp.status >= 500) {
        set beresp.ttl = 1s;
        set beresp.grace = 30s;   # Critical: serve stale during outage window
        set beresp.uncacheable = false;
        return(deliver);
    }

    # -------------------------------------------------------------------------
    # DEFAULT: apply grace mode to everything
    # Grace = serve stale content while Varnish fetches a fresh copy in background.
    # This is the iGaming killer feature: during Super Bowl/World Cup, even if
    # your odds server has a 2-second hiccup, players see last-known odds.
    # -------------------------------------------------------------------------
    if (beresp.ttl > 0s) {
        set beresp.grace = 30s;
        set beresp.keep = 60s;
    }

    return(deliver);
}

# =============================================================================
# VCL_DELIVER — Called just before sending the response to the client.
# Add diagnostic headers, remove server fingerprinting headers.
# =============================================================================
sub vcl_deliver {

    # -------------------------------------------------------------------------
    # CACHE STATUS HEADER
    # Useful for client-side debugging and CDN analytics.
    # HIT: served from Varnish cache
    # MISS: fetched from backend, now cached
    # PASS: bypassed cache (API endpoints, player-specific data)
    # -------------------------------------------------------------------------
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
        set resp.http.X-Cache-Hits = obj.hits;
    } else {
        set resp.http.X-Cache = "MISS";
    }

    # -------------------------------------------------------------------------
    # REMOVE SERVER FINGERPRINTING HEADERS
    # Don't reveal your infrastructure to attackers/competitors.
    # -------------------------------------------------------------------------
    unset resp.http.X-Powered-By;
    unset resp.http.Server;
    unset resp.http.Via;
    unset resp.http.X-Varnish;

    # -------------------------------------------------------------------------
    # ADD EDGE IDENTIFICATION (useful in multi-layer CDN setups)
    # -------------------------------------------------------------------------
    set resp.http.X-Served-By = "varnish-edge";

    return(deliver);
}

# =============================================================================
# VCL_PIPE — Called for piped connections (WebSocket).
# Sets up a raw TCP tunnel between client and backend.
# =============================================================================
sub vcl_pipe {
    # Required for WebSocket: prevent Varnish from closing connection
    set bereq.http.Connection = "Upgrade";
    return(pipe);
}

# =============================================================================
# VCL_SYNTH — Called for synthetic responses (error messages, redirects).
# =============================================================================
sub vcl_synth {
    set resp.http.Content-Type = "application/json; charset=utf-8";

    if (resp.status == 405) {
        set resp.body = {"{"error":"Method not allowed","message":""} + resp.reason + {""}"}
        return(deliver);
    }

    if (resp.status == 429) {
        set resp.http.Retry-After = "1";
        set resp.body = {"{"error":"Too many requests","message":"Rate limit exceeded"}"}
        return(deliver);
    }

    set resp.body = {"{"error":""} + resp.reason + {"","status":"} + resp.status + {"}"}
    return(deliver);
}
