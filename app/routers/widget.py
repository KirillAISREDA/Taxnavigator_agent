"""Chat widget router — serves the embeddable chat widget."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def widget_page(request: Request):
    """Standalone chat widget page (for testing)."""
    return templates.TemplateResponse("widget.html", {"request": request})


@router.get("/embed.js")
async def widget_embed_script(request: Request):
    """JavaScript snippet for embedding the widget on external sites."""
    base_url = str(request.base_url).rstrip("/")
    script = f"""
(function() {{
  var iframe = document.createElement('iframe');
  iframe.src = '{base_url}/widget/';
  iframe.style.cssText = 'position:fixed;bottom:20px;right:20px;width:400px;height:600px;border:none;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.15);z-index:99999;display:none;';
  iframe.id = 'taxnav-chat-widget';

  var btn = document.createElement('div');
  btn.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
  btn.style.cssText = 'position:fixed;bottom:20px;right:20px;width:60px;height:60px;background:#1B4D3E;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,0.2);z-index:100000;transition:transform 0.2s;';
  btn.onmouseover = function() {{ this.style.transform='scale(1.1)'; }};
  btn.onmouseout = function() {{ this.style.transform='scale(1)'; }};

  var open = false;
  btn.onclick = function() {{
    open = !open;
    iframe.style.display = open ? 'block' : 'none';
    btn.style.display = open ? 'none' : 'flex';
  }};

  window.addEventListener('message', function(e) {{
    if (e.data === 'taxnav-close') {{
      open = false;
      iframe.style.display = 'none';
      btn.style.display = 'flex';
    }}
  }});

  document.body.appendChild(iframe);
  document.body.appendChild(btn);
}})();
"""
    return Response(content=script, media_type="application/javascript")


from fastapi.responses import Response
