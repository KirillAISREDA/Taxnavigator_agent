"""Chat widget router — serves the embeddable chat widget."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def widget_page(request: Request):
    """Standalone chat widget page (for testing)."""
    return templates.TemplateResponse("widget.html", {"request": request})


@router.get("/embed.js")
async def widget_embed_script(request: Request):
    """JavaScript snippet for embedding the widget on external sites.

    Supports configuration via data-attributes on the script tag:
      data-position    = "right" | "left"         (default: right)
      data-offset-x    = pixels from edge          (default: 20)
      data-offset-y    = pixels from bottom         (default: 20)
      data-gdpr        = "auto" | "immediate"      (default: immediate)
      data-open        = "true" | "false"           (default: false)
      data-lang        = "nl" | "uk" | "ru" | "en" (default: auto)
      data-pages       = comma-separated path prefixes to show on (empty = all)
    """
    base_url = str(request.base_url).rstrip("/")
    script = f"""
(function() {{
  'use strict';

  // ── Helpers ────────────────────────────────────────────────────
  var WIDGET_ID  = 'taxnav-chat-widget';
  var BTN_ID     = 'taxnav-chat-btn';
  var ORIGIN     = '{base_url}';

  // Prevent double-init
  if (document.getElementById(WIDGET_ID)) return;

  // Read config from <script data-...> attributes
  var me = document.currentScript || (function() {{
    var scripts = document.getElementsByTagName('script');
    for (var i = scripts.length - 1; i >= 0; i--) {{
      if (scripts[i].src && scripts[i].src.indexOf('/widget/embed.js') !== -1) return scripts[i];
    }}
  }})();

  var cfg = {{
    position:  (me && me.getAttribute('data-position'))  || 'right',
    offsetX:   parseInt((me && me.getAttribute('data-offset-x')) || '20', 10),
    offsetY:   parseInt((me && me.getAttribute('data-offset-y')) || '20', 10),
    gdpr:      (me && me.getAttribute('data-gdpr'))      || 'immediate',
    autoOpen:  (me && me.getAttribute('data-open'))       === 'true',
    lang:      (me && me.getAttribute('data-lang'))       || '',
    pages:     (me && me.getAttribute('data-pages'))      || '',
  }};

  // ── Page filter ────────────────────────────────────────────────
  if (cfg.pages) {{
    var allowed = cfg.pages.split(',').map(function(p) {{ return p.trim(); }});
    var path = window.location.pathname;
    var match = allowed.some(function(prefix) {{
      return path === prefix || path.indexOf(prefix + '/') === 0 || prefix === '/';
    }});
    if (!match) return;
  }}

  // ── GDPR consent gate ─────────────────────────────────────────
  function initWidget() {{
    // Position helpers
    var side = cfg.position === 'left' ? 'left' : 'right';
    var oppositeSide = side === 'left' ? 'right' : 'left';

    // ── Chat iframe ──────────────────────────────────────────────
    var iframe = document.createElement('iframe');
    iframe.id  = WIDGET_ID;
    iframe.src = ORIGIN + '/widget/' + (cfg.lang ? '?lang=' + cfg.lang : '');
    iframe.allow = 'clipboard-write';
    iframe.style.cssText = [
      'position:fixed',
      'bottom:' + (cfg.offsetY + 70) + 'px',
      side + ':' + cfg.offsetX + 'px',
      oppositeSide + ':auto',
      'width:400px',
      'max-width:calc(100vw - 32px)',
      'height:min(600px, calc(100vh - 120px))',
      'border:none',
      'border-radius:16px',
      'box-shadow:0 8px 32px rgba(0,0,0,0.15)',
      'z-index:99999',
      'display:none',
      'transition:opacity 0.25s ease, transform 0.25s ease',
      'opacity:0',
      'transform:translateY(12px)',
    ].join(';');

    // ── Toggle button ────────────────────────────────────────────
    var btn = document.createElement('div');
    btn.id = BTN_ID;
    btn.setAttribute('role', 'button');
    btn.setAttribute('aria-label', 'Open chat');
    btn.setAttribute('tabindex', '0');
    btn.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2">'
      + '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
    btn.style.cssText = [
      'position:fixed',
      'bottom:' + cfg.offsetY + 'px',
      side + ':' + cfg.offsetX + 'px',
      oppositeSide + ':auto',
      'width:60px',
      'height:60px',
      'background:#1B4D3E',
      'border-radius:50%',
      'display:flex',
      'align-items:center',
      'justify-content:center',
      'cursor:pointer',
      'box-shadow:0 4px 16px rgba(0,0,0,0.2)',
      'z-index:100000',
      'transition:transform 0.2s ease, box-shadow 0.2s ease',
    ].join(';');

    btn.onmouseover = function() {{
      this.style.transform = 'scale(1.08)';
      this.style.boxShadow = '0 6px 24px rgba(0,0,0,0.25)';
    }};
    btn.onmouseout = function() {{
      this.style.transform = 'scale(1)';
      this.style.boxShadow = '0 4px 16px rgba(0,0,0,0.2)';
    }};

    // ── Open / Close logic ───────────────────────────────────────
    var isOpen = false;

    function openChat() {{
      isOpen = true;
      iframe.style.display = 'block';
      // Trigger reflow for transition
      void iframe.offsetHeight;
      iframe.style.opacity = '1';
      iframe.style.transform = 'translateY(0)';
      btn.style.display = 'none';
    }}

    function closeChat() {{
      isOpen = false;
      iframe.style.opacity = '0';
      iframe.style.transform = 'translateY(12px)';
      setTimeout(function() {{
        if (!isOpen) iframe.style.display = 'none';
      }}, 260);
      btn.style.display = 'flex';
    }}

    btn.onclick = openChat;
    btn.onkeydown = function(e) {{ if (e.key === 'Enter' || e.key === ' ') openChat(); }};

    // Listen for close message from widget iframe
    window.addEventListener('message', function(e) {{
      if (e.data === 'taxnav-close') closeChat();
    }});

    // ── Responsive: mobile adjustments ───────────────────────────
    function adjustMobile() {{
      var mobile = window.innerWidth < 500;
      if (mobile) {{
        iframe.style.width  = '100vw';
        iframe.style.height = '100vh';
        iframe.style.bottom = '0';
        iframe.style[side]  = '0';
        iframe.style.borderRadius = '0';
        iframe.style.maxWidth = '100vw';
      }} else {{
        iframe.style.width  = '400px';
        iframe.style.height = 'min(600px, calc(100vh - 120px))';
        iframe.style.bottom = (cfg.offsetY + 70) + 'px';
        iframe.style[side]  = cfg.offsetX + 'px';
        iframe.style.borderRadius = '16px';
        iframe.style.maxWidth = 'calc(100vw - 32px)';
      }}
    }}
    window.addEventListener('resize', adjustMobile);
    adjustMobile();

    // ── Mount ────────────────────────────────────────────────────
    document.body.appendChild(iframe);
    document.body.appendChild(btn);

    if (cfg.autoOpen) setTimeout(openChat, 500);
  }}

  // ── GDPR gate logic ────────────────────────────────────────────
  if (cfg.gdpr === 'immediate') {{
    // Load immediately — widget is considered functional, not tracking
    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', initWidget);
    }} else {{
      initWidget();
    }}
  }} else {{
    // Wait for consent signal
    // Option A: listen for common cookie-consent events
    // Option B: expose global function for manual trigger
    window.TaxNavWidget = {{ init: initWidget }};

    // Auto-detect popular consent managers
    // CookieYes
    document.addEventListener('cookieyes_consent_update', function(e) {{
      var detail = e.detail || {{}};
      if (detail.accepted && (detail.accepted.indexOf('functional') !== -1 || detail.accepted.indexOf('analytics') !== -1)) {{
        initWidget();
      }}
    }});

    // Complianz / CookieBot / generic
    window.addEventListener('CookiebotOnAccept', function() {{ initWidget(); }});

    // Fallback: if no consent manager fires within 10s and cookie_consent cookie exists
    setTimeout(function() {{
      if (!document.getElementById(WIDGET_ID)) {{
        // Check for common consent cookies
        if (document.cookie.indexOf('cookieyes-consent=') !== -1 ||
            document.cookie.indexOf('CookieConsent=') !== -1 ||
            document.cookie.indexOf('cmplz_functional=') !== -1) {{
          initWidget();
        }}
      }}
    }}, 3000);
  }}
}})();
"""
    return Response(
        content=script,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )
