# ============================================================
# consulta_publica.py
# Consulta pública de folio (QR) - Puebla SMT
#
# La página es el HTML oficial de smt.puebla.gob.mx COMPLETO
# (slider, tarjetas, semblanza, convocatorias, redes, footer...)
# y lo único que se agrega es el cajón de resultado.
#
# USO en main_puebla.py:
#   from consulta_publica import crear_router_consulta
#   app.include_router(crear_router_consulta(supabase, ENTIDAD, TZ))
# ============================================================

import re
import html as html_lib
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

SMT = "https://smt.puebla.gob.mx"

# ------------------------------------------------------------
# Estilos SOLO del cajón de resultado (prefijo rc-).
# Misma paleta del sitio. Una sola tipografía (hereda la del sitio).
# ------------------------------------------------------------
CSS_RESULTADO = """
.rc-wrap{ max-width:1000px; margin:30px auto 30px; padding:0 20px; font-family:inherit; }
.rc-wrap *{ font-family:inherit; box-sizing:border-box; }

.rc-head{ text-align:center; margin-bottom:24px; }
.rc-head small{ display:block; color:#c79b66; font-size:1.2rem; margin-bottom:4px; }
.rc-head h1{ margin:0; color:#5f1b2d; font-size:2rem; font-weight:300; line-height:1.2; }
.rc-head p{ margin:8px 0 0; color:#949494; font-size:1.1rem; }

.rc-card{ background:#f6f6f6; border-radius:25px; overflow:hidden; box-shadow:0 8px 28px rgba(0,0,0,.10); }

.rc-status{ display:flex; align-items:center; gap:18px; padding:24px 30px; }
.rc-status.ok{ background:#e6f4e8; color:#155724; }
.rc-status.venc{ background:#fff7dc; color:#856404; }
.rc-status.err{ background:#f8d7da; color:#721c24; }
.rc-ico{ width:54px; height:54px; flex:0 0 54px; border-radius:50%; display:flex;
  align-items:center; justify-content:center; font-size:1.8rem; font-weight:700; color:#fff; line-height:1; }
.rc-status.ok .rc-ico{ background:#38934d; }
.rc-status.venc .rc-ico{ background:#d59e16; }
.rc-status.err .rc-ico{ background:#b72f3c; }
.rc-status h2{ margin:0 0 3px; font-size:1.25rem; font-weight:700; letter-spacing:.5px; color:inherit; }
.rc-status p{ margin:0; font-size:1rem; line-height:1.4; color:inherit; }

.rc-body{ padding:28px 30px 32px; }

.rc-folio{ text-align:center; padding-bottom:24px; }
.rc-folio span{ display:block; color:#949494; font-size:.85rem; font-weight:600; letter-spacing:3px; margin-bottom:6px; }
.rc-folio strong{ display:block; color:#5f1b2d; font-size:2.2rem; font-weight:600; letter-spacing:1px; overflow-wrap:anywhere; }
.rc-folio i{ display:block; width:70px; height:4px; background:#c79b66; border-radius:4px; margin:14px auto 0; }

.rc-sub{ color:#c79b66; font-size:1.2rem; margin:0 0 14px; padding-bottom:8px; border-bottom:1px solid #e3e3e3; }

.rc-grid{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
.rc-item{ background:#fff; border-radius:14px; border-left:5px solid #c79b66; padding:14px 18px; min-width:0; }
.rc-item.wide{ grid-column:span 2; }
.rc-item span{ display:block; color:#949494; font-size:.75rem; font-weight:600; letter-spacing:1px; text-transform:uppercase; margin-bottom:5px; }
.rc-item strong{ display:block; color:#4b4b4b; font-size:1.05rem; font-weight:500; line-height:1.4; overflow-wrap:anywhere; }

.rc-vig{ margin-top:26px; background:#fff; border-radius:14px; border-left:5px solid #c79b66; padding:18px 20px; }
.rc-bar{ height:12px; background:#e3e3e3; border-radius:20px; overflow:hidden; }
.rc-bar b{ display:block; height:100%; border-radius:20px; }
.rc-bar b.ok{ background:linear-gradient(90deg,#c79b66,#38934d); }
.rc-bar b.venc{ background:linear-gradient(90deg,#c79b66,#d59e16); }
.rc-vig-txt{ margin-top:10px; text-align:center; color:#949494; font-size:.95rem; }

.rc-note{ margin-top:22px; background:#fff; border-radius:14px; border-left:5px solid #5f1b2d; padding:14px 18px; color:#949494; font-size:.9rem; line-height:1.55; }
.rc-note b{ color:#5f1b2d; font-weight:600; }

.rc-empty{ padding:34px 30px 40px; text-align:center; color:#949494; font-size:1.05rem; line-height:1.6; }

@media (max-width:640px){
  .rc-wrap{ padding:0 12px; margin-top:20px; }
  .rc-head h1{ font-size:1.6rem; }
  .rc-head p{ font-size:1rem; }
  .rc-card{ border-radius:20px; }
  .rc-status{ padding:18px 18px; gap:14px; }
  .rc-ico{ width:44px; height:44px; flex-basis:44px; font-size:1.4rem; }
  .rc-status h2{ font-size:1.05rem; }
  .rc-status p{ font-size:.9rem; }
  .rc-body{ padding:22px 16px 26px; }
  .rc-folio strong{ font-size:1.6rem; }
  .rc-grid{ grid-template-columns:1fr; }
  .rc-item.wide{ grid-column:span 1; }
}
"""


def _esc(valor):
    return html_lib.escape(str(valor if valor not in (None, "") else "—"))


def _parse_fecha(valor):
    if not valor:
        return None
    texto = str(valor).strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(texto[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _pagina(contenido: str, anio: int = 0) -> str:
    """HTML oficial completo + el cajón de resultado."""
    extra_head = (
        '<meta name="robots" content="noindex, nofollow">\n'
        "<style>" + CSS_RESULTADO + "</style>\n"
    )
    pagina = _BASE.replace("</head>", extra_head + "</head>", 1)
    return pagina.replace("__RESULTADO__", contenido, 1)


def _cabecera_resultado() -> str:
    return """
<div class="rc-head">
  <small>Verificación oficial</small>
  <h1>Consulta de permiso</h1>
  <p>Secretaría de Movilidad y Transporte</p>
</div>
"""


def crear_router_consulta(supabase, entidad: str, timezone: str) -> APIRouter:

    router = APIRouter()

    @router.get("/estado_folio/{folio}", response_class=HTMLResponse)
    async def estado_folio_qr(folio: str):

        folio = folio.strip().upper()

        try:

            res = (
                supabase
                .table("folios_registrados")
                .select("*")
                .eq("folio", folio)
                .eq("entidad", entidad)
                .limit(1)
                .execute()
            )

            hoy = datetime.now(ZoneInfo(timezone)).date()

            # ------------------------------------------------------
            # FOLIO NO ENCONTRADO
            # ------------------------------------------------------
            if not res.data:

                contenido = f"""
<div class="rc-wrap">
{_cabecera_resultado()}
  <div class="rc-card">
    <div class="rc-status err">
      <div class="rc-ico">×</div>
      <div>
        <h2>FOLIO NO ENCONTRADO</h2>
        <p>No existe un permiso registrado con este folio.</p>
      </div>
    </div>
    <div class="rc-body">
      <div class="rc-folio">
        <span>FOLIO CONSULTADO</span>
        <strong>{_esc(folio)}</strong>
        <i></i>
      </div>
      <div class="rc-empty" style="padding-top:0">
        Verifica que el folio esté capturado correctamente. Si el problema
        continúa, comunícate con la Secretaría de Movilidad y Transporte.
      </div>
    </div>
  </div>
</div>
"""
                return HTMLResponse(
                    content=_pagina(contenido, hoy.year),
                    status_code=200
                )

            # ------------------------------------------------------
            # FOLIO ENCONTRADO
            # ------------------------------------------------------
            r = res.data[0]

            fecha_exp = _parse_fecha(r.get("fecha_expedicion"))
            fecha_ven = _parse_fecha(r.get("fecha_vencimiento"))

            vigente = fecha_ven is not None and hoy <= fecha_ven

            if vigente:
                clase = "ok"
                icono = "✓"
                titulo_estado = "PERMISO VIGENTE"
                sub_estado = "El permiso se encuentra dentro de su periodo de vigencia."
            else:
                clase = "venc"
                icono = "!"
                titulo_estado = "PERMISO VENCIDO"
                sub_estado = "El periodo de vigencia de este permiso ha concluido."

            fe_txt = fecha_exp.strftime("%d/%m/%Y") if fecha_exp else "—"
            fv_txt = fecha_ven.strftime("%d/%m/%Y") if fecha_ven else "—"

            pct = 0
            texto_vig = ""

            if fecha_exp and fecha_ven:

                total_dias = max(1, (fecha_ven - fecha_exp).days + 1)

                if hoy < fecha_exp:
                    pct = 0
                    faltan = (fecha_exp - hoy).days
                    texto_vig = (
                        f"La vigencia inicia en {faltan} "
                        f"día{'s' if faltan != 1 else ''} "
                        f"(duración total: {total_dias} días)."
                    )
                elif hoy > fecha_ven:
                    pct = 100
                    pasados = (hoy - fecha_ven).days
                    texto_vig = (
                        f"Venció hace {pasados} "
                        f"día{'s' if pasados != 1 else ''} "
                        f"(duración total: {total_dias} días)."
                    )
                else:
                    transcurridos = (hoy - fecha_exp).days + 1
                    pct = round(transcurridos / total_dias * 100)
                    restantes = (fecha_ven - hoy).days + 1
                    texto_vig = (
                        f"Día {transcurridos} de {total_dias} · "
                        f"quedan {restantes} "
                        f"día{'s' if restantes != 1 else ''} de vigencia."
                    )

                pct = max(0, min(100, pct))

            vig_html = (
                f'<div class="rc-vig-txt">{_esc(texto_vig)}</div>'
                if texto_vig else ""
            )

            contenido = f"""
<div class="rc-wrap">
{_cabecera_resultado()}
  <div class="rc-card">

    <div class="rc-status {clase}">
      <div class="rc-ico">{icono}</div>
      <div>
        <h2>{titulo_estado}</h2>
        <p>{sub_estado}</p>
      </div>
    </div>

    <div class="rc-body">

      <div class="rc-folio">
        <span>FOLIO</span>
        <strong>{_esc(folio)}</strong>
        <i></i>
      </div>

      <h3 class="rc-sub">Información del permiso</h3>

      <div class="rc-grid">
        <div class="rc-item wide"><span>Contribuyente</span><strong>{_esc(r.get("contribuyente"))}</strong></div>
        <div class="rc-item"><span>Marca</span><strong>{_esc(r.get("marca"))}</strong></div>
        <div class="rc-item"><span>Línea / Modelo</span><strong>{_esc(r.get("linea"))}</strong></div>
        <div class="rc-item"><span>Año</span><strong>{_esc(r.get("anio"))}</strong></div>
        <div class="rc-item"><span>Color</span><strong>{_esc(r.get("color"))}</strong></div>
        <div class="rc-item"><span>Número de serie / NIV</span><strong>{_esc(r.get("numero_serie"))}</strong></div>
        <div class="rc-item"><span>Número de motor</span><strong>{_esc(r.get("numero_motor"))}</strong></div>
        <div class="rc-item"><span>Fecha de expedición</span><strong>{fe_txt}</strong></div>
        <div class="rc-item"><span>Fecha de vencimiento</span><strong>{fv_txt}</strong></div>
      </div>

      <div class="rc-vig">
        <div class="rc-bar"><b class="{clase}" style="width:{pct}%"></b></div>
        {vig_html}
      </div>

      <div class="rc-note">
        <b>Consulta verificada.</b>
        La información mostrada corresponde al registro asociado al folio
        consultado al día {hoy.strftime("%d/%m/%Y")}.
      </div>

    </div>
  </div>
</div>
"""

            return HTMLResponse(
                content=_pagina(contenido, hoy.year),
                status_code=200
            )

        except Exception as e:

            print(
                "[ESTADO_FOLIO] "
                f"Error consultando {folio}: "
                f"{repr(e)}"
            )

            contenido_error = f"""
<div class="rc-wrap">
{_cabecera_resultado()}
  <div class="rc-card">
    <div class="rc-status err">
      <div class="rc-ico">!</div>
      <div>
        <h2>NO FUE POSIBLE REALIZAR LA CONSULTA</h2>
        <p>Inténtalo nuevamente en unos minutos.</p>
      </div>
    </div>
    <div class="rc-empty">
      Si el problema continúa, comunícate con la Secretaría de Movilidad y Transporte.
    </div>
  </div>
</div>
"""
            return HTMLResponse(
                content=_pagina(
                    contenido_error,
                    datetime.now(ZoneInfo(timezone)).year
                ),
                status_code=500
            )

    return router


# ============================================================
# HTML OFICIAL DE smt.puebla.gob.mx  (tal cual)
# Único cambio: el marcador __RESULTADO__ donde va el cajón.
# Las rutas relativas (/templates/..., /images/...) se vuelven
# absolutas más abajo para que carguen desde el sitio oficial.
# ============================================================
_PLANTILLA = r"""<!DOCTYPE html>
<html>

<head>
    <meta charset="utf-8">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<meta name="description" content="Secretaria de Movilidad y Transporte">
	<meta name="generator" content="Joomla! - Open Source Content Management">
	<title>.: Secretaría de Movilidad y Transporte :.</title>
	<link href="/index.php?format=feed&amp;type=rss" rel="alternate" type="application/rss+xml" title=".: Secretaría de Movilidad y Transporte :.">
	<link href="/index.php?format=feed&amp;type=atom" rel="alternate" type="application/atom+xml" title=".: Secretaría de Movilidad y Transporte :.">
	<link href="/templates/puebla/favicon.ico" rel="icon" type="image/vnd.microsoft.icon">

    <link href="/templates/puebla/uikit-3.23.5/css/uikit.min.css?e08630" rel="stylesheet" />
	<link href="/templates/puebla/css/template.css?e08630" rel="stylesheet" />
	<link href="/templates/puebla/css/puebla.css?e08630" rel="stylesheet" />
	<link href="/templates/puebla/css/nanoscroller.css?e08630" rel="stylesheet" />
	<link href="/media/vendor/joomla-custom-elements/css/joomla-alert.min.css?0.2.0" rel="stylesheet" />
	<link href="https://smt.puebla.gob.mx/templates/puebla/html/mod_menu/css/style.css" rel="stylesheet" />
	<link href="https://smt.puebla.gob.mx/modules/mod_articles/tmpl/convocatoria/css/style.css" rel="stylesheet" />
	<link href="https://smt.puebla.gob.mx/modules/mod_articles/tmpl/slider/css/style.css" rel="stylesheet" />
	<style>:root {
		--hue: 214;
		--template-bg-light: #f0f4fb;
		--template-text-dark: #495057;
		--template-text-light: #ffffff;
		--template-link-color: var(--link-color);
		--template-special-color: #001B4C;
		$fontStyles
	}</style>

    <script src="/media/vendor/metismenujs/js/metismenujs.min.js?1.4.0" defer></script>
	<script src="/templates/puebla/uikit-3.23.5/js/uikit.min.js?e08630"></script>
	<script src="/templates/puebla/uikit-3.23.5/js/uikit-icons.min.js?e08630"></script>
	<script src="/templates/puebla/js/template.js?e08630"></script>
	<script src="/media/templates/site/cassiopeia/js/mod_menu/menu-metismenu.min.js?e08630" defer></script>
	<script type="application/json" class="joomla-script-options new">{"joomla.jtext":{"ERROR":"Error","MESSAGE":"Mensaje","NOTICE":"Notificación","WARNING":"Advertencia","JCLOSE":"Cerrar","JOK":"OK","JOPEN":"Abrir"},"system.paths":{"root":"","rootFull":"https://smt.puebla.gob.mx/","base":"","baseFull":"https://smt.puebla.gob.mx/"},"csrf.token":"bea05fd6450a69118324625301de28fe","accessibility-options":{"labels":{"menuTitle":"Opciones de accesibilidad","increaseText":"Aumentar el tamaño del texto","decreaseText":"Disminuir el tamaño del texto","increaseTextSpacing":"Aumentar el espaciado del texto","decreaseTextSpacing":"Disminuir el espaciado del texto","invertColors":"Invertir colores","grayHues":"Tonos grises","underlineLinks":"Subrayar enlaces","bigCursor":"Cursor grande","readingGuide":"Guía de lectura","textToSpeech":"Texto a voz","speechToText":"Voz a texto","resetTitle":"Restablecer","closeTitle":"Cerrar"},"icon":{"position":{"left":{"size":"0","units":"px"}},"useEmojis":true},"hotkeys":{"enabled":true,"helpTitles":true},"textToSpeechLang":["es-ES"],"speechToTextLang":["es-ES"]}}</script>
	<script src="/media/system/js/core.min.js?e20992"></script>
	<script src="/media/system/js/messages.min.js?7a5169" type="module"></script>
	<script src="/media/vendor/accessibility/js/accessibility.min.js?3.0.17" defer></script>
	<script type="module">window.addEventListener("load", function() {new Accessibility(Joomla.getOptions("accessibility-options") || {});});</script>

</head>

<header class="header" id="header">
  <div class="uk-grid header-inner" uk-grid>
    <div class="uk-width-3-5@s uk-padding-remove">
      <div class="uk-flex uk-flex-left uk-flex-middle uk-flex-nowrap">
        <!-- Escudo -->
        <div>
          <a href="https://puebla.gob.mx">
            <img src="/templates/puebla/images/header/logo_puebla_gob.svg"
              class="img-header" alt="Logo Puebla Gobierno">
          </a>
        </div>
        <!-- Logo Secretaría -->
                <div>
          <a href="/">
            <img class="img-header" alt="Logo de Secretaría" class="img-header" src="/images/headers/MOVILIDAD_02.png#joomlaImage://local-images/headers/MOVILIDAD_02.png?width=935&height=400 "  />          </a>
        </div>
                <!-- Logo Subsecretaría -->
              </div>
    </div>
    <div class="uk-width-2-5@s uk-padding-remove uk-visible@s">
      <div class="uk-flex uk-flex-right uk-flex-middle uk-flex-nowrap">
        <!-- Frase -->
        <div class="frase">
          <img src="/templates/puebla/images/header/puebla_frases_gob.svg"
            class="img-header" alt="Frase Amor a Puebla Gobierno" style="padding-left: 20px;">
        </div>
        <!-- Accesibilidad escritorio -->
        <div id="accesibilidad-position-desktop" class="uk-margin-small-left"></div>
      </div>
    </div>
  </div>

  <div class="uk-margin-remove uk-flex uk-flex-middle uk-visible@s" uk-grid>
    <!-- Menú -->
    <div class="uk-width-expand@m uk-padding-remove">
            <div class="menu-container">
        <ul class="mod-menu mod-menu_dropdown-metismenu metismenu mod-list ">
<li class="metismenu-item item-119 level-1"><a href="https://rl.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Pagos en línea</a></li><li class="metismenu-item item-120 level-1"><a href="https://ventanilladigital.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Trámites</a></li><li class="metismenu-item item-136 level-1"><a href="https://smt.puebla.gob.mx/images/documentos/DIT_E081_SMT.pdf" >DIT</a></li></ul>

      </div>
          </div>
    <!-- Search escritorio -->
    <div class="uk-width-1-5@m uk-margin-remove">
      <div id="search-position-desktop" class="search-container uk-margin-remove uk-padding-small uk-align-right"></div>
    </div>
  </div>

  <div class="menu-container uk-grid uk-child-width-1-3 uk-flex-middle uk-margin-remove uk-padding-remove uk-hidden@s" uk-grid>
    <!-- Search móvil -->
    <div class="uk-text-left">
      <div id="search-position-mobile" class="search-container"></div>
    </div>
    <!-- Menú offcanvas -->
    <div class="uk-text-center">
            <a href="#offcanvas" class="uk-hidden@s c-484747 bgch-484747" uk-toggle="target: #offcanvas-flip">
        <span uk-icon="icon: menu; ratio: 2"></span>
      </a>
          </div>
    <!-- Accesibilidad móvil -->
    <div class="uk-text-right">
      <div id="accesibilidad-position-mobile" class="accesibilidad-container uk-margin-remove  uk-align-right"></div>
    </div>
  </div>
</header>

<!-- Módulos Repetidos -->
<div style="display: none;">
  <div id="search-module">
      </div>
  <div id="accesibilidad-module">
    <img src="/templates/puebla/images/header/logo_accesibilidad.svg"
         class="img-access" alt="Accesibilidad" />
  </div>
</div>

<body>
    <div class="body">

__RESULTADO__

                <div class="slider">
            <div class="uk-margin-top">
                        
<div class="">
    <div class="  uk-position-relative uk-visible-toggle uk-light" tabindex="-1" uk-slideshow="animation: pull; autoplay: true; autoplay-interval: 5000;">

        <div class="uk-slideshow-items bsbr tamaño" >

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://www.facebook.com/share/p/1HqsSjkgpi/" target="_blank" >
                    <img class="img-sli" src="/images/banners/movilidad_n.jpeg#joomlaImage://local-images/banners/movilidad_n.jpeg?width=1400&height=700" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://drive.google.com/drive/folders/1zLpyVLy-oIGjH5UvUyrHVPP5wacsoyVe" target="_blank" >
                    <img class="img-sli" src="/images/banners/bannercosecionesjulio17.jpeg#joomlaImage://local-images/banners/bannercosecionesjulio17.jpeg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="/" target="_blank" >
                    <img class="img-sli" src="/images/banners/ViaRecreativaSemanal_2.jpg#joomlaImage://local-images/banners/ViaRecreativaSemanal_2.jpg?width=1400&height=788" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://drive.google.com/file/d/1Lokg5RKu73WfscTSzoGiqVD5jG3kl-fN/view" target="_blank" >
                    <img class="img-sli" src="/images/banners/Gruas%20Tarifas.jpeg#joomlaImage://local-images/banners/Gruas Tarifas.jpeg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://drive.google.com/file/d/1328f2lXP4kb-LKAnE3SzExAYPQjdnkwW/view" target="_blank" >
                    <img class="img-sli" src="/images/banners/Gruas%20Empresas.jpeg#joomlaImage://local-images/banners/Gruas Empresas.jpeg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://drive.google.com/file/d/1VdJSsgWw6ZP1DHBSDuTymoOI_pa2wOiU/view" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerGuia.jpeg#joomlaImage://local-images/banners/BannerGuia.jpeg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://drive.google.com/file/d/1RTRyr6_YiOUFHTE-d29yS3QCo0wBXt1r/view" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerProgramadeActualizacion.jpeg#joomlaImage://local-images/banners/BannerProgramadeActualizacion.jpeg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="/" target="_blank" >
                    <img class="img-sli" src="/images/banners/MonitorVial.jpg#joomlaImage://local-images/banners/MonitorVial.jpg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://drive.google.com/file/d/1KDByDO2S8hnufRHqBmivd-bjLOqgxaFu/view" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerspaginaTransporteTemporal.jpeg#joomlaImage://local-images/banners/BannerspaginaTransporteTemporal.jpeg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://www.facebook.com/reel/1821465978555176" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerOperativo.jpg#joomlaImage://local-images/banners/BannerOperativo.jpg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://www.facebook.com/MTGobPue/posts/pfbid02vnHuirtuNNUCWkTn6LfuDH8inTk8nLfoU7hAwEqMJGv2EN4tm7rWgsXrSfkvH7mNl" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerAgentes.jpg#joomlaImage://local-images/banners/BannerAgentes.jpg?width=1400&height=750" alt="">
                </a>
            </div>

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://x.com/MTGobPue" target="_blank" >
                    <img class="img-sli" src="/images/banners/banner-inspector.jpeg#joomlaImage://local-images/banners/banner-inspector.jpeg?width=1600&height=800" alt="">
                </a>
            </div>

                    </div>

        <a class="uk-position-center-left uk-position-small uk-hidden-hover" href uk-slidenav-previous
            uk-slideshow-item="previous"></a>
        <a class="uk-position-center-right uk-position-small uk-hidden-hover" href uk-slidenav-next
            uk-slideshow-item="next"></a>

    </div>

</div>
            </div>
        </div>
        
        
                <div class="above-top">
            <div class="container">
                
<div id="mod-custom117" class="mod-custom custom">
    <div class=" contour ">
  <div class="uk-grid-match uk-margin-remove" uk-grid>
   
    <div class="uk-width-auto@m uk-width-1-1@s">
      <div class="uk-text-center uk-flex uk-flex-center correo-container lin">
            <spam class="c-b2b2b2">Contáctanos</spam>
     </div>
    </div>
    
     <div class="uk-width-1-4@m uk-width-1-1@s">
       <div class="uk-text-center uk-flex uk-flex-center telefono-container dire">
         <img class="image-telefono c-794141" src="/templates/puebla/images/icons/contact/icon_phone.svg" alt="Telefono">
       <!--  <a href="tel:(222)" class="c-b2b2b2 uk-text-bold"> --->
          <p  class="c-b2b2b2 uk-text-bold">(222) 2 29 06 00 Ext. 1000 y 3503</p> 
             <!--</a>-->
       </div>
    </div>
    
     <div class="uk-width-1-4@m uk-width-1-1@s">
       <div class="dire">
         <spam class="c-b2b2b2 ">Av. Rosendo Márquez 1501 colonia La Paz<br>CP. 72160 Puebla, Pue.
         </spam>
       </div>
    </div>
    
      <div class="uk-width-auto@m uk-width-1-1@s">
       <div class="uk-text-center uk-flex uk-flex-center correo-container contac-email lin">
        <img class="image-correo" src="/templates/puebla/images/icons/contact/icon_mail.svg">  
        <!--<a href="mailto:sm@puebla.gob.mx"  target="_blank" tabindex="0" aria-label="correo electronico">-->
            <spam class="c-b2b2b2">movilidadytransporte@puebla.gob.mx</spam>
        <!--</a>-->
     </div>
    </div>
    
  </div>
</div>
  


<style>
  .c-b2b2b2{
    color: #b2b2b2;
    font-size: 1.25rem;
  }
  
.image-correo{
  height: 35px;
  filter: brightness(0) saturate(100%) invert(80%) sepia(3%) saturate(0%) hue-rotate(180deg);
}
  
.image-telefono{
  height: 40px;
    filter: brightness(0) saturate(100%) invert(80%) sepia(3%) saturate(0%) hue-rotate(180deg);
}

.contour{
  margin: 20px 0px 20px 0;
  background-color:white;
  color:#c2c2c2;
  border-radius: 20px;
  padding:12px;
  z-index: 1;
 }


 .correo-container,
 .telefono-container {
  display: flex;
  align-items: center;
  gap: 8px;  
} 
  
.lin a, 
.lin a:hover, 
.lin a:visited, 
.lin a:focus{
  color: #c2c2c2;
}

.contac-email{
  word-break: break-word;
  }
  
@media only screen and (max-width:959px){
 .dire{
  text-align:center;
 } 
  
 .correo-container,
 .telefono-container {
    flex-direction: column;
  }
}
</style></div>

            </div>
        </div>
        
                <div class="above-bottom">
            <div class="container">
                
<ul class=" uk-flex uk-flex-center uk-grid custom-grid uk-child-width-1-2@s uk-child-width-1-4@m uk-grid-match uk-grid-small">
<li class="metismenu-item uk-margin-bottom item-131 level-1"><div class="uk-card uk-card-default uk-card-body custom-card color-1"><a href="/index.php/informe-anual-de-cumplimiento-archivo" >Informe Anual de Cumplimiento Archivo</a></div></li><li class="metismenu-item uk-margin-bottom item-132 level-1"><div class="uk-card uk-card-default uk-card-body custom-card color-2"><a href="/index.php/programa-anual-de-desarrollo-archivistico-pada" >Programa Anual de Desarrollo Archivistico PADA</a></div></li></ul>

            </div>
        </div>
        
                <div class="main-top">
            <div class="container">
                
<div id="mod-custom120" class="mod-custom custom">
    <div class="pa-40">
  <div class="uk-grid-match" uk-grid>
    <div class="uk-width-1-3@m uk-width-1-1@s uk-padding-remove">
      <img class="radios" src="/images/site/semblanza/SilviaTanusOsorio.jpg" alt="Foto Titular de Silvia Tanús Osorio">
    </div>

      <div class="uk-width-2-3@m uk-width-1-1@s  uk-padding-remove">
      <div class="semblanza">
        <div class="TituloNombre">Silvia Tanús Osorio</div>
        <div class="SubtituloSecre">
          Secretaria de Movilidad y Transporte
        </div>
        <div class="Sutitulo">Semblanza</div>
          <div class="uk-text-justify"> 
<p>Nació en la ciudad de Puebla. Inició su formación académica como Profesora de Educación Primaria en el Benemérito Instituto Normal del Estado (BINE). Posteriormente, cursó la Licenciatura en Historia, así como una Maestría en Educación Superior y un Doctorado en Excelencia Docente por la Universidad de los Ángeles.</p>

<p>A lo largo de su trayectoria en el servicio público, ocupó diversos cargos de relevancia. Se desempeñó como Contralora Municipal de Puebla, Jefa de la Oficina de Presidencia Municipal y Coordinadora Regional de la Secretaría de Finanzas y Desarrollo Social del Municipio de Puebla.</p>

<p>Además, fue Subsecretaria de Enlace Institucional y Participación Ciudadana en la Secretaría de Gobernación, Regidora y Secretaria General del Ayuntamiento de Puebla. Su experiencia legislativa incluye haber sido electa tres veces como Diputada Local, consolidando así una amplia trayectoria en la política y la administración pública.</p>

<p>Su compromiso con el servicio público y la formación académica la han convertido en una figura destacada en la vida política de Puebla.</p>
          </div>
      </div>
    </div>
  </div>
</div>

<style>
.semblanza{
  background-color: #f6f6f6;
  color:#949494;
  border-radius: 25px;
  padding:30px;
  margin-left:13px;
}
  
.pa-40{
  padding-left: 40px
  }
  
.radios{
  border-radius: 25px;
  z-index: 1;
}

  
.TituloNombre{
  font-size: 1.5rem;
  font-weight: 300;
  padding-bottom:3px;
}
  
.SubtituloSecre{
  font-size: 1.1rem;
  padding-bottom:2px;
}
  
.Sutitulo{
  font-size: 1.2rem;
  color:#c79b66;
  padding-bottom:5px;
}
</style></div>

            </div>
        </div>
        
        <div class="grid-child container-component">
            
            <div id="system-message-container" aria-live="polite"></div>

            <main>
                <div class="blog-featured">
    
    
    
    
    
</div>

            </main>
        </div>

                <div class="main-bottom">
            <div class="container">
                        
<div class="boder bgc-41625b uk-padding uk-margin-remove">
	<div uk-slider="autoplay: true; autoplay-interval: 2000">
		<!-- Título y navegación del slider -->
		<div class="uk-grid uk-flex-middle" uk-grid>
			<div class="uk-width-expand">
				<div class="">	
					<h3 class="Titu-convoca">Convocatorias</h3>
				</div>
			</div>
			<div class="uk-width-auto">
				<ul class="uk-slider-nav uk-dotnav uk-flex-right"></ul>
			</div>
		</div>


		<!-- Slider principal -->
		<div class="uk-position-relative uk-margin-small">
			<div class="uk-slider-container">
				<ul class="uk-slider-items colors-items boder uk-grid-small uk-child-width-1-1@s uk-child-width-1-3@m uk-grid-match" uk-height-match="target: > li > a > .uk-card">
					  
						<li class="border">
						<!--	<a class="sin-subrayado" href="/<?//php echo $i->link ?>">-->
								<a class="sin-subrayado" href="/index.php/convocatorias/convocatoria-article/programa-integral-de-reordenamiento-y-apoyo-para-la-modernizacion-del-servicio-publico?mod=convocatoria">
								<div class="uk-card uk-card-default uk-padding-small uk-height-1-1 uk-flex uk-flex-column">
									<div class="uk-inline uk-cover-container">
																					<img class="boder" src="/images/site/convocatoria/convocatoria.jpg#joomlaImage://local-images/site/convocatoria/convocatoria.jpg?width=600&amp;height=750" alt="" uk-cover>
																				<canvas width="600" height="800"></canvas>
									</div>
									
									<div class="Titu-convoca uk-margin-small-top">
										Programa Integral de Reordenamiento y Apoyo para la Modernización del Servicio Público									</div>

									<div class="Des-convoca uk-margin-small-top">
																			</div>

									<div class="uk-margin-top uk-margin-auto-top">
										<p class="c-c79b66">Saber +</p>
									</div>
								</div>
							</a>
						</li>
					  
						<li class="border">
						<!--	<a class="sin-subrayado" href="/<?//php echo $i->link ?>">-->
								<a class="sin-subrayado" href="/index.php/convocatorias/convocatoria-article/tarifas-gruas?mod=convocatoria">
								<div class="uk-card uk-card-default uk-padding-small uk-height-1-1 uk-flex uk-flex-column">
									<div class="uk-inline uk-cover-container">
																					<img class="boder" src="/images/site/convocatoria/convocatoria.jpg#joomlaImage://local-images/site/convocatoria/convocatoria.jpg?width=600&amp;height=750" alt="" uk-cover>
																				<canvas width="600" height="800"></canvas>
									</div>
									
									<div class="Titu-convoca uk-margin-small-top">
										Tarifas Grúas 2025									</div>

									<div class="Des-convoca uk-margin-small-top">
																			</div>

									<div class="uk-margin-top uk-margin-auto-top">
										<p class="c-c79b66">Saber +</p>
									</div>
								</div>
							</a>
						</li>
									</ul>

			</div>
		</div> 
	</div>

		<div class="uk-flex uk-flex-center uk-margin-top">
		<a class="button-convocatoria" 
		   href="/./convocatorias" 
		   title="Todas las convocatorias">
			Todas las convocatorias		</a>
	</div>

</div>





            </div>
        </div>    
                
                <div class="below-top">
            <div class="container">
                
<div id="mod-custom121" class="mod-custom custom">
    <div class="uk-container uk-container-large bgc-e3e3e3">
  <div class="uk-grid-match" uk-grid>
    <div class="uk-padding uk-width-2-3@m uk-width-1-1@s">
      <div class="titu uk-text-center"><strong>MANTENTE</strong> AL DÍA</div>
      <div class="uk-flex uk-flex-center ">
        <div class="uk-padding-small">
          <a href="https://www.facebook.com/share/16oEpS2k6P/?mibextid=wwXIfr" class="uk-margin-remove" target="_blank" tabindex="0" aria-label="Facebook">
            <img class="image-facebook" src="/templates/puebla/images/icons/icon_redes/icon_f.svg">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://x.com/mtgobpue?s=21&amp;t=9rxyoSfKsdWZorPTBsx6bA" target="_blank" tabindex="0" aria-label="Twitter">
            <img class="image-twitter" src="/templates/puebla/images/icons/icon_redes/icon_x.svg">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://www.instagram.com/mtgobpue?igsh=eW8zbTl0YXQwNGJ4&amp;utm_source=qr" class="uk-margin-remove" target="_blank" tabindex="0" aria-label="Instagram">
            <img class="image-instagram" src="/templates/puebla/images/icons/icon_redes/icon_in.svg">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://www.tiktok.com/@mtgobpue?_t=ZS-8yiQkNHYYWV&amp;_r=1" class="uk-margin-remove" target="_blank" tabindex="0" aria-label="Tiktok">
            <img class="image-tiktok" src="/templates/puebla/images/icons/icon_redes/icon_tt.svg">
          </a>
        </div>
     <!--  <div class="uk-padding-small">
          <a href="https://www.youtube.com/" class="uk-margin-remove" target="_blank" tabindex="0" aria-label="Youtube">
            <img class="image-youtube" src="/templates/puebla/images/icons/icon_redes/icon_yt.svg">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://www.whatsapp.com/" class="uk-margin-remove" target="_blank" tabindex="0" aria-label="Whatsapp">
            <img class="image-whats" src="/templates/puebla/images/icons/icon_redes/icon_w.svg">
          </a>
        </div>-->
      </div>
    </div>
    
    <div class="uk-padding uk-width-1-3@m uk-width-1-1@s">
      <div class="boton uk-text-center uk-margin">
        <div class="titulo">CONTACTO</div>
          <div class="uk-padding contac-email"> 
              <img class="image-email" src="/templates/puebla/images/icons/icon_redes/icon_email.svg">  
              <spam class="titul">movilidadytransporte@puebla.gob.mx</spam>
          <!--  </a>-->
          </div>
      </div>
    </div>
  </div>
</div>


<style>
.image-facebook{width: 50px;}
.image-twitter{width: 50px;}
.image-instagram{width: 50px;}
.image-tiktok{width: 50px;}
.image-youtube{width: 50px;}
.image-whats{width: 50px;}
.image-email{height: 50px;}

.boton{
  background-color:#c09761;
  border-radius: 35px;
  padding:10px;
  box-shadow: 0px 0px 10px 3px rgba(0, 0, 0, 0.20);
  z-index: 1;
}
  
.bgc-e3e3e3{
  background-color:#e3e3e3;
}

.titulo{
  color:white;
  font-size: 1.2rem;
  padding-top:15px;
}
  
.titul{
  color:white;
  font-size: 1.1rem;
  font-weight: 300;
}
  
.titu{
  color:#949494;
  font-size: 1.2rem;
  font-weight: 400;
  align-content:center;
}
  
.contac-email{
  word-break: break-word;
  }
  
</style></div>

            </div>
        </div>
        
                <div class="below-bottom">
            <div class="">
                
<div id="mod-custom114" class="mod-custom custom">
     <div class="uk-container ">
  <div uk-grid>
    <div class="uk-width-1-3@l uk-width-1-3@m uk-width-1-1@s uk-width-1-1@xs">
         <h2 class="text-2024 uk-margin-remove" tabindex="0">TRANSPARENCIA</h2>
    </div>
    <div class="uk-width-1-3@l uk-width-1-3@m uk-width-1-2@s uk-width-1-2@xs grayScale-2024">
      <div class="uk-width-3-4 uk-width-1@l uk-margin-auto">
        <a href="https://www.plataformadetransparencia.org.mx/web/guest/inicio" title="Plataforma nacional de transparencia" target="_blank">
          <img src="/templates/puebla/images/transparencia/transparencia.png"  width="80%" alt="Plataforma nacional de transparencia">
        </a>
      </div>
    </div>
    <div class="uk-width-1-3@l uk-width-1-3@m uk-width-1-2@s uk-width-1-2@xs grayScale-2024">
      <div class="uk-width-3-4 uk-width-1@l uk-margin-auto">
       <a href="https://consultapublicamx.plataformadetransparencia.org.mx/vut-web/faces/view/consultaPublica.xhtml?idEntidad=MjE=&idOrgano=MjE=&idSujetoObligado=MjMyNTc=#inicio" title="Consulta nuestras obligaciones de transparencia" target="_blank">
          <img src="/templates/puebla/images/transparencia/consulta.png"  width="80%" alt="Consulta nuestras obligaciones de transparencia">
        </a>
      </div>
    </div>
  </div>
</div>


<style>
.text-2024{
   font-family: 'Corra Montserra';
   font-weight: 300; 
   font-size: 26pt;
   color: #b2b2b1;
}

.grayScale-2024{
   filter: grayscale(100%)!important;
   opacity: 0.5!important;
}
</style>
</div>

            </div>
        </div>
            </div>

    <footer class="footer  uk-position-relative">
        <div class="uk-container footer-container uk-container uk-flex uk-flex-center">
            <div
                class="uk-position-absolute uk-padding-remove uk-margin-remove uk-width-1-1 uk-flex uk-flex-center sombra-footer">
                <!-- Imagen vertical para pantallas grandes -->
                <img src="/templates/puebla/images/footer/sombra_vertical.png"
                    class="sombra-vertical uk-visible@m" alt="Sombra Vertical">

            </div>
            <div class="uk-flex uk-flex-middle uk-flex-center uk-padding-remove uk-margin-remove" uk-grid>
                <div class="uk-width-1-2@xl uk-width-1-2@l uk-width-1-2@m uk-width-1-2@s uk-width-1-1@xs">
                                        <div class="uk-flex uk-flex-middle uk-flex-center uk-padding">
                        
<div id="mod-custom111" class="mod-custom custom">
    <div class="Uk-container uk-padding escudo">
<img src="/templates/puebla/images/footer/Escudo_pie.svg" />
</div>



 <!-- <a href="#" class="">-->
    <div class="image-hover">
      <img src="/templates/puebla/images/footer/icon_911.svg" class="image-1">
      <img src="/templates/puebla/images/footer/icon_911_hover.svg" class="image-2">
      </div>
 <!-- </a>-->

<style>
.escudo img{
 /* width:360px;*/
   width:560px;
  height:auto;
}
  
.image-hover {
    display: block;
    position: relative;
    width: auto; 
    height: 80px;
  overflow:hidden;
}

.image-hover:hover {
  /*  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.4);*/
}

.image-hover img {
    display: block;
    width: 100%; 
    height: 100%; 
    position: absolute; 
    top: 0;
    left: 0;
    transition: opacity 0.3s ease; 
 /*  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
  overflow:hidden;*/
}

.image-hover .image-1 {
    opacity: 1; 
}

.image-hover .image-2 {
    opacity: 0;
}

.image-hover:hover .image-1 {
    opacity: 0; 
}

.image-hover:hover .image-2 {
    opacity: 1;
}

</style>
  </div>

                    </div>
                                    </div>
                <!-- Imagen horizontal para pantallas pequeñas -->
                <img src="/templates/puebla/images/footer/sombra_horizontal.png"
                    class="sombra-horizontal uk-hidden@m" alt="Sombra Horizontal">
                <div class="uk-width-1-2@xl uk-width-1-2@l uk-width-1-2@m uk-width-1-2@s uk-width-1-1@xs">
                                        <div class="uk-flex uk-flex-middle uk-flex-center uk-padding">
                        
<div id="mod-custom112" class="mod-custom custom">
    <ul class="uk-list">
<li class="Te_Recomendamos" tabindex="0"><a href="https://planeader.puebla.gob.mx/" target="_blank">PLAN ESTATAL DE DESARROLLO</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://transparenciafiscal.puebla.gob.mx/" target="_blank">TRANSPARENCIA FISCAL</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://www.gob.mx/empleo" target="_blank">PORTAL DEL EMPLEO</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://presupuestociudadano.puebla.gob.mx/" target="_blank">PRESUPUESTO CIUDADANO</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://www.gob.mx/presidencia" target="_blank">PRESIDENCIA DE LA REPÚBLICA</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://lgcg.puebla.gob.mx/" target="_blank">LEY GENERAL DE CONTABILIDAD GUBERNAMENTAL</a>
</li>
</ul>

<Style>

.Te_Recomendamos a, 
.Te_Recomendamos a:hover, 
.Te_Recomendamos a:visited, 
.Te_Recomendamos a:focus {
  color: #fffbef;
  font-size: 14pt;
  line-height: 1.8;
}
</Style></div>

                    </div>
                                    </div>
            </div>
        </div>

    </footer>
    
    <div class="copyright">
        <div class="uk-width-1-1 uk-flex uk-flex-center uk-padding-remove uk-margin-remove">
                        <div class="copyright-container  uk-padding-remove uk-margin-remove">
                
<div id="mod-custom113" class="mod-custom custom">
    <img src="/templates/puebla/images/footer/copyright.svg" class="cop" style="width:1400px; height:auto; background:#5f1b2d;"/>

<style>
.Copry{
  font-size: 18pt;
  padding-bottom:50px!important;
  background-color:white;
  letter-spacing: 6px; 
  color:#bbbbbb;
  margin-top:0px;
  }
.bgc-white{ background-color:white;}
</style>
</div>

            </div>
                    </div>
    </div>


    
        <div id="offcanvas-flip" uk-offcanvas="mode: push; overlay: true; flip: true">
        <div class="uk-offcanvas-bar uk-padding-remove">
            <button class="uk-offcanvas-close" type="button" uk-close></button>
             <h3 class="uk-padding uk-padding-remove-bottom uk-margin-remove">Secretaria de Movilidad y Transporte</h3>
            
<ul class="uk-nav uk-nav-default uk-nav-parent-icon uk-padding " uk-nav="multiple: true">
    <li class="" style="padding: 2px;"><a href="https://rl.puebla.gob.mx/">Pagos en línea</a></li>    <li class="" style="padding: 2px;"><a href="https://ventanilladigital.puebla.gob.mx/">Trámites</a></li>    <li class="" style="padding: 2px;"><a href="https://smt.puebla.gob.mx/images/documentos/DIT_E081_SMT.pdf">DIT</a></li></ul>

        </div>
    </div>
</body>

</html>"""

# Rutas relativas del sitio oficial (src="/...", href="/...") -> absolutas,
# para que todo cargue desde smt.puebla.gob.mx. Nada más se modifica.
_BASE = re.sub(r'(src|href)="/(?!/)', r'\1="' + SMT + '/', _PLANTILLA)
