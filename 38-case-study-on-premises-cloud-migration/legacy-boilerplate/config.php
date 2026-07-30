<?php

//define version to help with caching, change this to force css/js to reload.
define('VER',250714);

//Mysql Settings
define('gb_server', 'cmsdb'); //this is set dependant on the server see below.
define('gb_user','acme_cms');
define('gb_password','${CMS_DB_PASSWORD}');
define('gb_database','acme_cms');
define('DBPREFIX','');

//Main Brand settings can be done here.
$brand = 'acebet';
$ubrand = ucfirst($brand);
$domain = 'acebet.acmetocasino.com';
$urls = array('www.'.$domain, 'dev.'.$domain, $brand.'.acmestage.com', 'dev-'.$brand.'.acmestage.com');
$brandid = 106;

//Site Settings.
define('LPS_AFFILIATEID',$brandid);	//This is now used as the site ID CMS only.
define('PLATFORM_AFFILIATEID',$brandid);	//New Platform Affiliate ID.
define('SESSIONID', strtoupper($brand).'_');
define('PAGE_TITLE','AceBet');
define('CASINO_NAME','AceBet');
define('BRAND_NAME', $brand);
define('DEFAULT_TITLE', 'AceBet');
define('DEFAULT_BTAG','');
define('RM_COOKIE', $ubrand);
define('GOOGLE_ANAL_CODE','');	//TBA
define('SMS_FROM', 'AceBet');
define('LANGUAGE','en');
define('CURRENCIES','EUR,GBP,SEK,NOK,CAD,NZD,ZAR'); //Supported site currencies
define('LC_GROUP_ID',38);//Default Live Chat Group ID.
define('LANGUAGES','en');

define('CURACAO_LICENSE_ID',''); //TBA

//Game Settings
define('GAME_NOTLOGGEDIN', 1); //allow fun games without being logged in.
define('GAME_FUN_PAGE','/index.php?page=login');
define('GAME_REAL_ONLY', 1);

//Deposit cashout limits
define('gb_min_deposit', 20);
define('gb_min_withdrawal', 20);

//CMS
$CMSName = "AceBet Content Management System";

// Registration settings
define('REGISTER_FROM_NAME','AceBet');
define('REGISTER_FROM_EMAIL','support@'.$domain);
define('REG_AFFILIATE_NAME','AceBet');

// Support email
define('SUPPORT_EMAIL','support@'.$domain);
define('MANAGER_EMAIL','manager@'.$domain);

//Default Meta Description
define('DEFAULT_META_KEY','AceBet casino, online casino uk, uk casino');
define('DEFAULT_META_DESC','Join the fun at AceBet and get the best in UK casino entertainment. Play Slots, Blackjack, Roulette, Live Casino and more!');

//Environment differences.
$key = array_search($_SERVER['HTTP_HOST'], $urls);
$cms_path = $key == 1 ? 'D:/Repos/Bin/Static/var/www/dev/cms/shared' : '/var/www/html/cms/shared';

if(!defined('CMS_PATH')) define("CMS_PATH", $cms_path);
if(!defined('LPS_TEST') && $key > 0) define('LPS_TEST',$key);
define('SITE_URL',$_SERVER['HTTP_HOST']);

define('gb_deposit_url', 'https://'.SITE_URL.'/index.php?page=deposit');
define('gb_activation_url',SITE_URL);
define('SITE_PATH', str_replace("shared", $brand, CMS_PATH));
define('LANGUAGE_PATH', CMS_PATH.'/includes/language');
define('CUSTOM_MOBILE_GAME_IMG_PATH','/images/games/');

//disable popup window.
define('GAME_POPUP_DISABLED', 1);

define('SEO_URLS',1);

//logged in redirect.
define('LOGIN_REDIRECT','/casino-games');
define('LOGIN_REDIRECT_MOBILE','/casino-games');

define('BOILERPLATE', 1);
