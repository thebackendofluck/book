-- NJ DGE View 09: PERSONALLY IDENTIFIABLE INFORMATION
-- Contains patron PII: name, DOB, address, contact info, and KYC status.
-- Accessed on an as-needed basis by the Division of Gaming Enforcement.
-- Includes full US state name mapping and KYC verification details.
--
-- Sources:
--   casino_core.users                              -- primary user record
--   casino_replica.user_info          -- demographics and registration data
--   casino_replica.user_information_field_audit  -- most recent PII change
--   casino_replica.user_kyc_status    -- most recent KYC verification
--
-- DISTINCT ON pattern: both audit subqueries use DISTINCT ON (user_id) ordered
--   by changed_on/updated_on DESC to select the most recent record per patron.
--   Supported by composite indexes: idx_ifa_user_id_changed_on, idx_uks_user_id_updated_on
--
-- Key design choices:
--   - Missing contact/address fields fall back to 'NO<FIELD>' sentinel values (DGE requirement)
--   - State stored as 2-char code; full state name required by DGE spec
--   - KYC_VERIFICATION_METHOD: admin_user_id > 0 means a human reviewed it (MANUAL)
--   - LAST_UPDATE_TIME reflects the most recent PII field change, not account creation
-- Supported by indexes: idx_ifa_changed_on, idx_ifa_user_id_changed_on,
--                        idx_uks_user_id_updated_on

CREATE OR REPLACE VIEW "dge"."vNJDGE09PII" AS
SELECT
    'ACMETOCASINO' AS SKIN_NAME,
    spoke_users.id::text AS PATRON_ACCOUNT_ID,
    spoke_users.name AS PATRON_USER_NAME,
    hub_user_info.uniform_patron_identifier AS DUPI,
    hub_user_info.firstname AS FIRST_NAME,
    hub_user_info.lastname AS LAST_NAME,
    NULL AS MIDDLE_INITIAL,
    TO_CHAR(hub_user_info.dob, 'YYYYMMDD') AS DOB,
    UPPER(hub_user_info.gender) AS GENDER,
    TO_CHAR(hub_user_info.created, 'YYYYMMDD HH24MISS.MS +00:00')
        AS SIGNUPTIME_SYSTEM,
    CONCAT(
        TO_CHAR(hub_user_info.created AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(hub_user_info.created AT TIME ZONE 'America/New_York' - hub_user_info.created AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS SIGNUPTIME_EASTERN,
    COALESCE(hub_user_info.email,      'NOEMAIL')    AS PATRON_CONTACT_EMAIL,
    COALESCE(hub_user_info.phone,      'NOPHONE')    AS PATRON_CONTACT_PHONE,
    COALESCE(hub_user_info.address1,   'NOADDRESS1') AS PATRON_PRIMARY_ADDRESS1,
    COALESCE(hub_user_info.address2,   'NOADDRESS2') AS PATRON_PRIMARY_ADDRESS2,
    COALESCE(hub_user_info.town,       'NOCITY')     AS PATRON_PRIMARY_CITY,
    COALESCE(hub_user_info.postalcode, 'NOZIPCODE')  AS PATRON_PRIMARY_ZIPCODE,
    CASE hub_user_info.state
        WHEN 'AL' THEN 'ALABAMA'     WHEN 'AK' THEN 'ALASKA'
        WHEN 'AZ' THEN 'ARIZONA'     WHEN 'AR' THEN 'ARKANSAS'
        WHEN 'CA' THEN 'CALIFORNIA'  WHEN 'CO' THEN 'COLORADO'
        WHEN 'CT' THEN 'CONNECTICUT' WHEN 'DE' THEN 'DELAWARE'
        WHEN 'DC' THEN 'DISTRICT OF COLUMBIA'
        WHEN 'FL' THEN 'FLORIDA'     WHEN 'GA' THEN 'GEORGIA'
        WHEN 'HI' THEN 'HAWAII'      WHEN 'ID' THEN 'IDAHO'
        WHEN 'IL' THEN 'ILLINOIS'    WHEN 'IN' THEN 'INDIANA'
        WHEN 'IA' THEN 'IOWA'        WHEN 'KS' THEN 'KANSAS'
        WHEN 'KY' THEN 'KENTUCKY'    WHEN 'LA' THEN 'LOUISIANA'
        WHEN 'ME' THEN 'MAINE'       WHEN 'MD' THEN 'MARYLAND'
        WHEN 'MA' THEN 'MASSACHUSETTS' WHEN 'MI' THEN 'MICHIGAN'
        WHEN 'MN' THEN 'MINNESOTA'   WHEN 'MS' THEN 'MISSISSIPPI'
        WHEN 'MO' THEN 'MISSOURI'    WHEN 'MT' THEN 'MONTANA'
        WHEN 'NE' THEN 'NEBRASKA'    WHEN 'NV' THEN 'NEVADA'
        WHEN 'NH' THEN 'NEW HAMPSHIRE' WHEN 'NJ' THEN 'NEW JERSEY'
        WHEN 'NM' THEN 'NEW MEXICO'  WHEN 'NY' THEN 'NEW YORK'
        WHEN 'NC' THEN 'NORTH CAROLINA' WHEN 'ND' THEN 'NORTH DAKOTA'
        WHEN 'OH' THEN 'OHIO'        WHEN 'OK' THEN 'OKLAHOMA'
        WHEN 'OR' THEN 'OREGON'      WHEN 'PA' THEN 'PENNSYLVANIA'
        WHEN 'RI' THEN 'RHODE ISLAND' WHEN 'SC' THEN 'SOUTH CAROLINA'
        WHEN 'SD' THEN 'SOUTH DAKOTA' WHEN 'TN' THEN 'TENNESSEE'
        WHEN 'TX' THEN 'TEXAS'       WHEN 'UT' THEN 'UTAH'
        WHEN 'VT' THEN 'VERMONT'     WHEN 'VA' THEN 'VIRGINIA'
        WHEN 'WA' THEN 'WASHINGTON'  WHEN 'WV' THEN 'WEST VIRGINIA'
        WHEN 'WI' THEN 'WISCONSIN'   WHEN 'WY' THEN 'WYOMING'
        ELSE 'NOSTATE'
    END AS PATRON_PRIMARY_STATE,
    CASE hub_user_info.country
        WHEN 'US' THEN 'UNITED STATES OF AMERICA'
        ELSE 'NOCOUNTRY'
    END AS PATRON_PRIMARY_COUNTRY,
    (
        CASE
            WHEN hub_user_kyc_status.admin_user_id > 0 THEN 'MANUAL'
            ELSE 'AUTOMATIC'
        END
    ) AS KYC_VERIFICATION_METHOD,
    TO_CHAR(hub_user_kyc_status.updated_on, 'YYYYMMDD HH24MISS.MS +00:00')
        AS KYC_VERIFICATION_TIME_SYSTEM,
    CONCAT(
        TO_CHAR(hub_user_kyc_status.updated_on AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(hub_user_kyc_status.updated_on AT TIME ZONE 'America/New_York' - hub_user_kyc_status.updated_on AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS KYC_VERIFICATION_TIME_EASTERN,
    CASE
        WHEN hub_user_kyc_status.status = true THEN 'PASSED'
        WHEN hub_user_kyc_status.status = false THEN 'FAILED'
        ELSE 'NOSTATUS'
    END AS KYC_VERIFICATION_STATUS,
    TO_CHAR(hub_user_information_field_audit.changed_on, 'YYYYMMDD HH24MISS.MS +00:00')
        AS LAST_UPDATE_TIME_SYSTEM,
    CONCAT(
        TO_CHAR(hub_user_information_field_audit.changed_on AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(hub_user_information_field_audit.changed_on AT TIME ZONE 'America/New_York' - hub_user_information_field_audit.changed_on AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS LAST_UPDATE_TIME_EASTERN,
    hub_user_information_field_audit.changed_on AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
        AS LAST_UPDATE_TIME_EASTERN_INDEX
FROM casino_core.users spoke_users
JOIN casino_replica.user_info hub_user_info
    ON hub_user_info.userid = spoke_users.id
JOIN (
    SELECT DISTINCT ON (user_id) *
    FROM casino_replica.user_information_field_audit
    ORDER BY user_id, changed_on DESC
) hub_user_information_field_audit
    ON hub_user_information_field_audit.user_id = hub_user_info.userid
JOIN (
    SELECT DISTINCT ON (user_id) *
    FROM casino_replica.user_kyc_status
    ORDER BY user_id, updated_on DESC
) hub_user_kyc_status
    ON hub_user_kyc_status.user_id = hub_user_info.userid;

GRANT SELECT ON "dge"."vNJDGE09PII" TO dge_readonly_external;
