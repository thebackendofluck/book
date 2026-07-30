// Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * game-info-bar.js
 *
 * Jurisdiction-aware compliance display system for multi-licensed iGaming operators.
 *
 * Reads the data-jurisdiction attribute from body or the script tag to determine
 * which compliance elements to render.
 * Default jurisdiction is "uk,malta" (maximum compliance / most restrictive rules).
 *
 * Usage:
 *   <body data-jurisdiction="uk">
 *   or
 *   <script src="game-info-bar.js" data-jurisdiction="sweden,malta"></script>
 *
 * Supported jurisdictions: uk, malta, sweden, denmark, brazil, curacao, generic
 *
 * Chapter 6 - Comprehensive Licensing Guide
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Jurisdiction configuration registry
  // ---------------------------------------------------------------------------

  var JURISDICTION_CONFIG = {
    uk: {
      licenseText: 'Licensed and regulated by the UK Gambling Commission',
      licenseNumber: '000-123456-R-123456-001',
      licenseUrl: 'https://www.gamblingcommission.gov.uk/',
      selfExclusionScheme: 'GamStop',
      selfExclusionUrl: 'https://www.gamstop.co.uk/',
      ageWarning: 'Underage gambling is an offence. 18+',
      rgOrgs: [
        { name: 'BeGambleAware', url: 'https://www.begambleaware.org/' },
        { name: 'GamCare', url: 'https://www.gamcare.org.uk/' },
      ],
      currency: 'GBP',
      language: 'en',
      cookieBannerRequired: true,
      depositLimitRequired: true,
      realityCheckInterval: 60,
      maxWageringMultiplier: 10,
      helpline: null,
    },
    malta: {
      licenseText: 'Licensed and regulated by the Malta Gaming Authority',
      licenseNumber: 'MGA/B2C/000/2024',
      licenseUrl: 'https://www.mga.org.mt/',
      selfExclusionScheme: 'MGA Self-Exclusion',
      selfExclusionUrl: 'https://www.mga.org.mt/player-support/self-exclusion/',
      ageWarning: 'Gambling for under 18s is an offence. 18+',
      rgOrgs: [
        { name: 'RGF Malta', url: 'https://rgfmalta.org/' },
      ],
      currency: 'EUR',
      language: 'en',
      cookieBannerRequired: true,
      depositLimitRequired: false,
      realityCheckInterval: 0,
      maxWageringMultiplier: null,
      helpline: null,
    },
    sweden: {
      licenseText: 'Licens utfardad av Spelinspektionen',
      licenseNumber: '18Li000000',
      licenseUrl: 'https://www.spelinspektionen.se/',
      selfExclusionScheme: 'Spelpaus',
      selfExclusionUrl: 'https://www.spelpaus.se/',
      ageWarning: 'Spel for personer under 18 ar ar forbjudet. 18+',
      rgOrgs: [
        { name: 'Stodlinjen', url: 'https://www.stodlinjen.se/' },
      ],
      currency: 'SEK',
      language: 'sv',
      cookieBannerRequired: true,
      depositLimitRequired: true,
      realityCheckInterval: 0,
      maxWageringMultiplier: null,
      helpline: '020-819 100',
      singleBonusRule: true,
    },
    denmark: {
      licenseText: 'Licens fra Spillemyndigheden',
      licenseNumber: 'DK-00000000',
      licenseUrl: 'https://www.spillemyndigheden.dk/',
      selfExclusionScheme: 'ROFUS',
      selfExclusionUrl: 'https://www.rofus.nu/',
      ageWarning: 'Spil for personer under 18 ar er forbudt. 18+',
      rgOrgs: [
        { name: 'Ludomani', url: 'https://www.ludomani.dk/' },
      ],
      currency: 'DKK',
      language: 'da',
      cookieBannerRequired: true,
      depositLimitRequired: false,
      realityCheckInterval: 0,
      maxWageringMultiplier: null,
      helpline: null,
    },
    brazil: {
      licenseText: 'Licenciado pelo Ministerio da Fazenda (SPA-MF)',
      licenseNumber: 'BR-00000000-2025',
      licenseUrl: 'https://www.gov.br/fazenda/',
      selfExclusionScheme: 'Cadastro Nacional de Apostadores',
      selfExclusionUrl: 'https://www.gov.br/fazenda/exclusao',
      ageWarning: 'Proibido para menores de 18 anos. 18+',
      rgOrgs: [
        { name: 'jogadorresponsavel.com.br', url: 'https://www.jogadorresponsavel.com.br/' },
      ],
      currency: 'BRL',
      language: 'pt-BR',
      cookieBannerRequired: true,
      depositLimitRequired: false,
      realityCheckInterval: 30,
      maxWageringMultiplier: null,
      helpline: 'Disque 100 / Ligue 180',
      pixOnly: true,
      cpfRequired: true,
      sigapIntegration: true,
      welfareBlock: true,
    },
    curacao: {
      licenseText: 'Licensed under Curacao Gaming License',
      licenseNumber: 'OGL/2025/0000/0000',
      licenseUrl: 'https://www.curacao-egaming.com/',
      selfExclusionScheme: null,
      selfExclusionUrl: null,
      ageWarning: 'Must be 18+ to play.',
      rgOrgs: [],
      currency: 'USD',
      language: 'en',
      cookieBannerRequired: false,
      depositLimitRequired: false,
      realityCheckInterval: 0,
      maxWageringMultiplier: null,
      helpline: null,
    },
    generic: {
      licenseText: 'Licensed and regulated gambling operator',
      licenseNumber: '',
      licenseUrl: '#',
      selfExclusionScheme: null,
      selfExclusionUrl: null,
      ageWarning: '18+ only. Gamble Responsibly.',
      rgOrgs: [],
      currency: 'USD',
      language: 'en',
      cookieBannerRequired: false,
      depositLimitRequired: false,
      realityCheckInterval: 0,
      maxWageringMultiplier: null,
      helpline: null,
    },
  };

  // ---------------------------------------------------------------------------
  // Jurisdiction detection
  // ---------------------------------------------------------------------------

  function detectJurisdictions() {
    var sources = [
      document.body && document.body.dataset && document.body.dataset.jurisdiction,
      document.currentScript && document.currentScript.dataset && document.currentScript.dataset.jurisdiction,
      new URLSearchParams(window.location.search).get('jurisdiction'),
    ];
    for (var i = 0; i < sources.length; i++) {
      if (sources[i]) {
        return sources[i].split(',').map(function (j) { return j.trim().toLowerCase(); });
      }
    }
    return ['uk', 'malta'];
  }

  function mergeConfigs(jurisdictions) {
    var configs = jurisdictions.map(function (j) {
      return JURISDICTION_CONFIG[j] || JURISDICTION_CONFIG.generic;
    });
    if (configs.length === 1) return configs[0];

    return configs.reduce(function (acc, cfg) {
      var merged = Object.assign({}, acc);
      merged.cookieBannerRequired = acc.cookieBannerRequired || cfg.cookieBannerRequired;
      merged.depositLimitRequired = acc.depositLimitRequired || cfg.depositLimitRequired;
      merged.realityCheckInterval = Math.max(acc.realityCheckInterval || 0, cfg.realityCheckInterval || 0);
      var existingOrgs = acc.rgOrgs || [];
      var newOrgs = cfg.rgOrgs || [];
      merged.rgOrgs = existingOrgs.concat(newOrgs);
      return merged;
    });
  }

  // ---------------------------------------------------------------------------
  // DOM Builders (safe; no innerHTML used for user content)
  // ---------------------------------------------------------------------------

  function _esc(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.textContent || div.innerText || '';
  }

  function _el(tag, attrs, styles) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    }
    if (styles) {
      Object.keys(styles).forEach(function (k) { el.style[k] = styles[k]; });
    }
    return el;
  }

  function _link(href, text, styles) {
    var a = _el('a', { href: href, target: '_blank', rel: 'noopener noreferrer' }, styles || { color: '#aaa' });
    a.textContent = text;
    return a;
  }

  function _span(text, styles) {
    var s = _el('span', {}, styles);
    s.textContent = text;
    return s;
  }

  function buildComplianceFooter(config, jurisdictions) {
    var existing = document.getElementById('game-info-bar-footer');
    if (existing) existing.remove();

    var bar = _el('div', {
      id: 'game-info-bar-footer',
      role: 'contentinfo',
      'aria-label': 'Regulatory compliance information',
    }, {
      background: '#1a1a2e',
      color: '#ccc',
      fontSize: '11px',
      padding: '10px 20px',
      borderTop: '1px solid #333',
      textAlign: 'center',
      position: 'relative',
      zIndex: '9999',
      lineHeight: '2',
    });

    // License text
    if (config.licenseText) {
      var licSpan = _el('span');
      if (config.licenseUrl) {
        licSpan.appendChild(_link(config.licenseUrl, config.licenseText));
      } else {
        licSpan.textContent = config.licenseText;
      }
      if (config.licenseNumber) {
        licSpan.appendChild(document.createTextNode(' — License No. ' + config.licenseNumber));
      }
      bar.appendChild(licSpan);
      bar.appendChild(document.createTextNode('  |  '));
    }

    // Age warning
    if (config.ageWarning) {
      bar.appendChild(_span(config.ageWarning, { color: '#e74c3c', fontWeight: 'bold' }));
      bar.appendChild(document.createTextNode('  |  '));
    }

    // Self-exclusion
    if (config.selfExclusionScheme) {
      var seSpan = _el('span');
      seSpan.appendChild(document.createTextNode('Self-Exclusion: '));
      if (config.selfExclusionUrl) {
        seSpan.appendChild(_link(config.selfExclusionUrl, config.selfExclusionScheme));
      } else {
        seSpan.appendChild(document.createTextNode(config.selfExclusionScheme));
      }
      bar.appendChild(seSpan);
      bar.appendChild(document.createTextNode('  |  '));
    }

    // RG organisations
    if (config.rgOrgs && config.rgOrgs.length > 0) {
      var rgSpan = _el('span');
      rgSpan.appendChild(document.createTextNode('Help: '));
      config.rgOrgs.forEach(function (org, idx) {
        if (idx > 0) rgSpan.appendChild(document.createTextNode(' | '));
        rgSpan.appendChild(_link(org.url, org.name));
      });
      bar.appendChild(rgSpan);
      bar.appendChild(document.createTextNode('  |  '));
    }

    // Helpline
    if (config.helpline) {
      bar.appendChild(_span('Helpline: ' + config.helpline));
      bar.appendChild(document.createTextNode('  |  '));
    }

    // Max wagering (UK)
    if (config.maxWageringMultiplier) {
      bar.appendChild(_span('Max bonus wagering: ' + config.maxWageringMultiplier + 'x'));
    }

    // Brazil specifics
    if (config.pixOnly) {
      bar.appendChild(document.createTextNode('  |  '));
      bar.appendChild(_span('Pagamentos apenas via PIX'));
    }
    if (config.singleBonusRule) {
      bar.appendChild(document.createTextNode('  |  '));
      bar.appendChild(_span('Endast ett valkomestrbjudande per kund'));
    }

    var existingFooter = document.querySelector('footer');
    if (existingFooter) {
      existingFooter.insertAdjacentElement('afterend', bar);
    } else {
      document.body.appendChild(bar);
    }
  }

  function buildAgeGate(config) {
    if (sessionStorage.getItem('age-verified') === '1') return;

    var overlay = _el('div', {
      id: 'game-info-bar-age-gate',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': 'Age verification',
    }, {
      position: 'fixed',
      top: '0',
      left: '0',
      width: '100%',
      height: '100%',
      background: 'rgba(0,0,0,0.92)',
      zIndex: '99999',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    });

    var box = _el('div', {}, {
      background: '#fff',
      padding: '40px',
      maxWidth: '400px',
      textAlign: 'center',
      borderRadius: '8px',
    });

    var h2 = document.createElement('h2');
    h2.textContent = 'Age Verification';
    h2.style.marginTop = '0';
    box.appendChild(h2);

    var warn = _span(config.ageWarning, { color: '#c00', fontWeight: 'bold' });
    var p1 = document.createElement('p');
    p1.appendChild(warn);
    box.appendChild(p1);

    var p2 = document.createElement('p');
    p2.textContent = 'You must be 18 years of age or older to access this site.';
    box.appendChild(p2);

    var btnYes = _el('button', { id: 'age-gate-yes' }, {
      background: '#2ecc71', color: '#fff', padding: '12px 24px',
      border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '16px', margin: '5px',
    });
    btnYes.textContent = 'I am 18+';

    var btnNo = _el('button', { id: 'age-gate-no' }, {
      background: '#e74c3c', color: '#fff', padding: '12px 24px',
      border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '16px', margin: '5px',
    });
    btnNo.textContent = 'I am under 18';

    box.appendChild(btnYes);
    box.appendChild(btnNo);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    document.body.style.overflow = 'hidden';

    btnYes.addEventListener('click', function () {
      sessionStorage.setItem('age-verified', '1');
      overlay.remove();
      document.body.style.overflow = '';
    });

    btnNo.addEventListener('click', function () {
      window.location.href = 'https://www.begambleaware.org/';
    });
  }

  function buildCookieBanner(config) {
    if (!config.cookieBannerRequired) return;
    if (localStorage.getItem('cookie-consent')) return;

    var banner = _el('div', {
      id: 'game-info-bar-cookie',
      role: 'alertdialog',
      'aria-label': 'Cookie consent',
    }, {
      position: 'fixed',
      bottom: '0',
      left: '0',
      width: '100%',
      background: '#2c2c3e',
      color: '#fff',
      padding: '16px 20px',
      zIndex: '99998',
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      flexWrap: 'wrap',
      gap: '10px',
      boxSizing: 'border-box',
    });

    var msgSpan = _el('span', {}, { flex: '1', minWidth: '200px', fontSize: '13px' });
    msgSpan.appendChild(document.createTextNode(
      'We use cookies to improve your experience and comply with regulatory requirements. '
    ));
    msgSpan.appendChild(_link('/privacy', 'Privacy Policy', { color: '#aef' }));
    banner.appendChild(msgSpan);

    var btnArea = _el('div');

    var btnAll = _el('button', { id: 'cookie-accept-all' }, {
      background: '#2ecc71', color: '#fff', padding: '8px 16px',
      border: 'none', borderRadius: '4px', cursor: 'pointer', margin: '3px',
    });
    btnAll.textContent = 'Accept All';

    var btnEssential = _el('button', { id: 'cookie-accept-essential' }, {
      background: '#555', color: '#fff', padding: '8px 16px',
      border: 'none', borderRadius: '4px', cursor: 'pointer', margin: '3px',
    });
    btnEssential.textContent = 'Essential Only';

    btnArea.appendChild(btnAll);
    btnArea.appendChild(btnEssential);
    banner.appendChild(btnArea);
    document.body.appendChild(banner);

    btnAll.addEventListener('click', function () {
      localStorage.setItem('cookie-consent', 'all');
      banner.remove();
    });
    btnEssential.addEventListener('click', function () {
      localStorage.setItem('cookie-consent', 'essential');
      banner.remove();
    });
  }

  function setupRealityCheck(config) {
    var intervalMinutes = config.realityCheckInterval;
    if (!intervalMinutes || intervalMinutes <= 0) return;

    var intervalMs = intervalMinutes * 60 * 1000;
    var sessionStart = Date.now();

    setInterval(function () {
      var elapsedMs = Date.now() - sessionStart;
      var elapsedMin = Math.floor(elapsedMs / 60000);
      var event = new CustomEvent('game-info-bar:reality-check', {
        detail: { sessionDurationMinutes: elapsedMin },
        bubbles: true,
      });
      document.dispatchEvent(event);
    }, intervalMs);
  }

  function triggerDepositLimitPrompt(config) {
    if (!config.depositLimitRequired) return;
    if (sessionStorage.getItem('deposit-limit-prompted') === '1') return;
    sessionStorage.setItem('deposit-limit-prompted', '1');
    document.dispatchEvent(new CustomEvent('game-info-bar:deposit-limit-required', {
      detail: { jurisdiction: detectJurisdictions() },
      bubbles: true,
    }));
  }

  // ---------------------------------------------------------------------------
  // Bootstrap
  // ---------------------------------------------------------------------------

  function init() {
    var jurisdictions = detectJurisdictions();
    var config = mergeConfigs(jurisdictions);

    buildAgeGate(config);
    buildCookieBanner(config);
    buildComplianceFooter(config, jurisdictions);
    setupRealityCheck(config);
    triggerDepositLimitPrompt(config);

    window.GameInfoBar = {
      jurisdictions: jurisdictions,
      config: config,
      JURISDICTION_CONFIG: JURISDICTION_CONFIG,
      refresh: function () { init(); },
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
