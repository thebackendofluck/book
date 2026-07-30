-- HAProxy Lua script for intelligent user-based request routing
-- Extracts user identity from JSON/XML payloads and routes requests
-- to consistent backend hub instances (session affinity without cookies)
--
-- Production pattern from AcmetoCasino platform proxy layer

require "pl"

local function query_result(parameter, value, req_query)
  if parameter ~= nil and value ~= nil then
    if req_query ~= nil then
      return req_query .. "&" .. parameter .. "=" .. value
    else
      return parameter .. "=" .. value
    end
  else
    return ""
  end
end

-- Uses txn variables set if request body is JSON.
-- Decodes session to get user ID.
function build_from_txn_vars(txn, username, sessionid, userid, req_query)
  local request_type = txn.f:path()
  local parameter = {}
  local value = {}

  if request_type == "/platform/usergateway/userlogin" or request_type == "/platform/usergateway/registeruser" then
    parameter = "username"
    value = username
  else
    parameter = "userid"
    if userid ~= nil then
      value = userid
    end
    if sessionid ~= nil then
      local a1, a2, a3, a4, a5, a6, a7, a8 = string.byte(sessionid, 9, 16)
      value = (a1 << 56) + (a2 << 48) + (a3 << 40) + (a4 << 32) + (a5 << 24) + (a6 << 16) + (a7 << 8) + a8
    end
  end

  return query_result(parameter, value, req_query)
end

-- Parses XML body, looking for user ID or username
function build_from_xml_body(txn, req_query)
  local req, err = xml.parse(txn.get_var(txn, "txn.xml_body"))
  local parameter = "userid"
  local value = ""

  -- Requests from BackOffice
  local userId = req:child_with_name("userId")
  if userId ~= nil then
    value = userId:get_text()
  end

  -- Requests from payments service
  userId = req:child_with_name("payment")
  if userId ~= nil and value == "" then
    value = userId:child_with_name("userID"):get_text()
  end

  -- Requests from clients, for login and registration
  local username = req:child_with_name("username")
  if username ~= nil then
    parameter = "username"
    value = username:get_text()
  end

  return query_result(parameter, value, req_query)
end

function add_query_param(txn)
  local method = txn.f:method()
  local content_type = txn.f:hdr("content-type")
  local req_query = txn.get_var(txn, "txn.request_query_string")

  if content_type == "text/xml" or content_type == "application/xml" then
    return build_from_xml_body(txn, req_query)
  end

  local userid = txn.get_var(txn, "txn.userid")
  local username = txn.get_var(txn, "txn.username")
  local sessionid = txn.get_var(txn, "txn.sessionid")

  if method ~= "POST" or (username == nil and sessionid == nil and userid == nil) then
    return req_query
  end

  if string.find(content_type, "application/json") ~= nil then
    return build_from_txn_vars(txn, username, sessionid, userid, req_query)
  end

end

-- Checks if previously ran functions have set the query string.
-- Used for forming an ACL in HAProxy config.
-- A 'fetch' method in HAProxy is required to return string.
function is_roundrobin(txn)
  local query_string = txn.get_var(txn, "txn.query_string")
  local method = txn.f:method()

  if query_string == "" or method == "OPTIONS" then
    return "true"
  else
    return "false"
  end
end

function resolve_hub(txn)
  local req_path = txn.f:path()
  local query_string = txn.get_var(txn, "txn.query_string")

  if req_path == "/platform/version" or req_path == "/platform/health" or req_path == "/platform/health/full" or req_path == "/platform/admingateway/updatestatic" then
    return query_string
  else
    return ""
  end
end

core.register_fetches("is_roundrobin", is_roundrobin)
core.register_fetches("add_query_param", add_query_param)
core.register_fetches("resolve_hub", resolve_hub)
