# ============================================================
# consulta_publica.py
# Consulta pública de folio (QR) - Puebla SMT
# Usa el diseño oficial de smt.puebla.gob.mx (mismos CSS/estructura)
#
# USO en main_puebla.py:
#   from consulta_publica import crear_router_consulta
#   app.include_router(crear_router_consulta(supabase, ENTIDAD, TZ))
# ============================================================

import html as html_lib
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

SMT = "https://smt.puebla.gob.mx"

# ------------------------------------------------------------
# CSS: bloques inline del sitio oficial + estilos del resultado.
# (String normal: las llaves van sencillas)
# ------------------------------------------------------------
CSS_EXTRA = """
:root {
    --hue: 214;
    --template-bg-light: #f0f4fb;
    --template-text-dark: #495057;
    --template-text-light: #ffffff;
    --template-link-color: var(--link-color);
    --template-special-color: #001B4C;
}

/* ---------- Módulo contacto (oficial) ---------- */
.c-b2b2b2{ color:#b2b2b2; font-size:1.25rem; }
.image-correo{ height:35px; filter:brightness(0) saturate(100%) invert(80%) sepia(3%) saturate(0%) hue-rotate(180deg); }
.image-telefono{ height:40px; filter:brightness(0) saturate(100%) invert(80%) sepia(3%) saturate(0%) hue-rotate(180deg); }
.contour{ margin:20px 0 20px 0; background-color:white; color:#c2c2c2; border-radius:20px; padding:12px; z-index:1; }
.correo-container, .telefono-container{ display:flex; align-items:center; gap:8px; }
.lin a, .lin a:hover, .lin a:visited, .lin a:focus{ color:#c2c2c2; }
.contac-email{ word-break:break-word; }
@media only screen and (max-width:959px){
  .dire{ text-align:center; }
  .correo-container, .telefono-container{ flex-direction:column; }
}

/* ---------- Módulo "Mantente al día" (oficial) ---------- */
.image-facebook{width:50px;}
.image-twitter{width:50px;}
.image-instagram{width:50px;}
.image-tiktok{width:50px;}
.image-email{height:50px;}
.boton{ background-color:#c09761; border-radius:35px; padding:10px; box-shadow:0 0 10px 3px rgba(0,0,0,.20); z-index:1; }
.bgc-e3e3e3{ background-color:#e3e3e3; }
.titulo{ color:white; font-size:1.2rem; padding-top:15px; }
.titul{ color:white; font-size:1.1rem; font-weight:300; }
.titu{ color:#949494; font-size:1.2rem; font-weight:400; align-content:center; }

/* ---------- Transparencia (oficial) ---------- */
.text-2024{ font-family:'Corra Montserra'; font-weight:300; font-size:26pt; color:#b2b2b1; }
.grayScale-2024{ filter:grayscale(100%)!important; opacity:.5!important; }

/* ---------- Footer (oficial) ---------- */
.escudo img{ width:560px; height:auto; }
.image-hover{ display:block; position:relative; width:auto; height:80px; overflow:hidden; }
.image-hover img{ display:block; width:100%; height:100%; position:absolute; top:0; left:0; transition:opacity .3s ease; }
.image-hover .image-1{ opacity:1; }
.image-hover .image-2{ opacity:0; }
.image-hover:hover .image-1{ opacity:0; }
.image-hover:hover .image-2{ opacity:1; }
.Te_Recomendamos a, .Te_Recomendamos a:hover, .Te_Recomendamos a:visited, .Te_Recomendamos a:focus{
  color:#fffbef; font-size:14pt; line-height:1.8;
}

/* =====================================================
   CAJÓN DE RESULTADO  (prefijo rc-)
   Misma paleta y bordes redondeados del sitio.
   Una sola tipografía: hereda la del sitio.
===================================================== */
.rc-wrap{ max-width:1000px; margin:30px auto 20px; padding:0 20px; font-family:inherit; }
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
/* TODOS los valores iguales: mismo tamaño, peso y fuente */
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


def _pagina(contenido: str, anio: int) -> str:
    """Página completa con la estructura oficial de smt.puebla.gob.mx."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="description" content="Secretaria de Movilidad y Transporte">
<title>.: Secretaría de Movilidad y Transporte :.</title>
<link href="{SMT}/templates/puebla/favicon.ico" rel="icon" type="image/vnd.microsoft.icon">

<link href="{SMT}/templates/puebla/uikit-3.23.5/css/uikit.min.css" rel="stylesheet">
<link href="{SMT}/templates/puebla/css/template.css" rel="stylesheet">
<link href="{SMT}/templates/puebla/css/puebla.css" rel="stylesheet">
<link href="{SMT}/templates/puebla/css/nanoscroller.css" rel="stylesheet">
<link href="{SMT}/templates/puebla/html/mod_menu/css/style.css" rel="stylesheet">
<style>{CSS_EXTRA}</style>

<script src="{SMT}/templates/puebla/uikit-3.23.5/js/uikit.min.js"></script>
<script src="{SMT}/templates/puebla/uikit-3.23.5/js/uikit-icons.min.js"></script>
</head>

<body>

<header class="header" id="header">
  <div class="uk-grid header-inner" uk-grid>
    <div class="uk-width-3-5@s uk-padding-remove">
      <div class="uk-flex uk-flex-left uk-flex-middle uk-flex-nowrap">
        <div>
          <a href="https://puebla.gob.mx">
            <img src="{SMT}/templates/puebla/images/header/logo_puebla_gob.svg" class="img-header" alt="Logo Puebla Gobierno">
          </a>
        </div>
        <div>
          <a href="{SMT}/">
            <img class="img-header" alt="Logo de Secretaría" src="{SMT}/images/headers/MOVILIDAD_02.png">
          </a>
        </div>
      </div>
    </div>
    <div class="uk-width-2-5@s uk-padding-remove uk-visible@s">
      <div class="uk-flex uk-flex-right uk-flex-middle uk-flex-nowrap">
        <div class="frase">
          <img src="{SMT}/templates/puebla/images/header/puebla_frases_gob.svg" class="img-header" alt="Frase Amor a Puebla Gobierno" style="padding-left: 20px;">
        </div>
      </div>
    </div>
  </div>

  <div class="uk-margin-remove uk-flex uk-flex-middle uk-visible@s" uk-grid>
    <div class="uk-width-expand@m uk-padding-remove">
      <div class="menu-container">
        <ul class="mod-menu mod-menu_dropdown-metismenu metismenu mod-list ">
<li class="metismenu-item item-119 level-1"><a href="https://rl.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Pagos en línea</a></li><li class="metismenu-item item-120 level-1"><a href="https://ventanilladigital.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Trámites</a></li><li class="metismenu-item item-136 level-1"><a href="{SMT}/images/documentos/DIT_E081_SMT.pdf">DIT</a></li></ul>
      </div>
    </div>
    <div class="uk-width-1-5@m uk-margin-remove">
      <div class="search-container uk-margin-remove uk-padding-small uk-align-right"></div>
    </div>
  </div>

  <div class="menu-container uk-grid uk-child-width-1-3 uk-flex-middle uk-margin-remove uk-padding-remove uk-hidden@s" uk-grid>
    <div class="uk-text-left"></div>
    <div class="uk-text-center">
      <a href="#offcanvas" class="uk-hidden@s c-484747 bgch-484747" uk-toggle="target: #offcanvas-flip">
        <span uk-icon="icon: menu; ratio: 2"></span>
      </a>
    </div>
    <div class="uk-text-right"></div>
  </div>
</header>

<div class="body">

  <div class="uk-margin-top"></div>

  <div class="above-top">
    <div class="container">
<div class="mod-custom custom">
    <div class=" contour ">
  <div class="uk-grid-match uk-margin-remove" uk-grid>

    <div class="uk-width-auto@m uk-width-1-1@s">
      <div class="uk-text-center uk-flex uk-flex-center correo-container lin">
        <span class="c-b2b2b2">Contáctanos</span>
      </div>
    </div>

    <div class="uk-width-1-4@m uk-width-1-1@s">
      <div class="uk-text-center uk-flex uk-flex-center telefono-container dire">
        <img class="image-telefono c-794141" src="{SMT}/templates/puebla/images/icons/contact/icon_phone.svg" alt="Telefono">
        <p class="c-b2b2b2 uk-text-bold">(222) 2 29 06 00 Ext. 1000 y 3503</p>
      </div>
    </div>

    <div class="uk-width-1-4@m uk-width-1-1@s">
      <div class="dire">
        <span class="c-b2b2b2">Av. Rosendo Márquez 1501 colonia La Paz<br>CP. 72160 Puebla, Pue.</span>
      </div>
    </div>

    <div class="uk-width-auto@m uk-width-1-1@s">
      <div class="uk-text-center uk-flex uk-flex-center correo-container contac-email lin">
        <img class="image-correo" src="{SMT}/templates/puebla/images/icons/contact/icon_mail.svg" alt="Correo">
        <span class="c-b2b2b2">movilidadytransporte@puebla.gob.mx</span>
      </div>
    </div>

  </div>
</div>
</div>
    </div>
  </div>

  <div class="main-top">
    <div class="container">
{contenido}
    </div>
  </div>

  <div class="below-top">
    <div class="container">
<div class="mod-custom custom">
    <div class="uk-container uk-container-large bgc-e3e3e3">
  <div class="uk-grid-match" uk-grid>
    <div class="uk-padding uk-width-2-3@m uk-width-1-1@s">
      <div class="titu uk-text-center"><strong>MANTENTE</strong> AL DÍA</div>
      <div class="uk-flex uk-flex-center ">
        <div class="uk-padding-small">
          <a href="https://www.facebook.com/share/16oEpS2k6P/?mibextid=wwXIfr" class="uk-margin-remove" target="_blank" aria-label="Facebook">
            <img class="image-facebook" src="{SMT}/templates/puebla/images/icons/icon_redes/icon_f.svg" alt="Facebook">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://x.com/mtgobpue?s=21&amp;t=9rxyoSfKsdWZorPTBsx6bA" target="_blank" aria-label="Twitter">
            <img class="image-twitter" src="{SMT}/templates/puebla/images/icons/icon_redes/icon_x.svg" alt="X">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://www.instagram.com/mtgobpue?igsh=eW8zbTl0YXQwNGJ4&amp;utm_source=qr" class="uk-margin-remove" target="_blank" aria-label="Instagram">
            <img class="image-instagram" src="{SMT}/templates/puebla/images/icons/icon_redes/icon_in.svg" alt="Instagram">
          </a>
        </div>
        <div class="uk-padding-small">
          <a href="https://www.tiktok.com/@mtgobpue?_t=ZS-8yiQkNHYYWV&amp;_r=1" class="uk-margin-remove" target="_blank" aria-label="Tiktok">
            <img class="image-tiktok" src="{SMT}/templates/puebla/images/icons/icon_redes/icon_tt.svg" alt="TikTok">
          </a>
        </div>
      </div>
    </div>

    <div class="uk-padding uk-width-1-3@m uk-width-1-1@s">
      <div class="boton uk-text-center uk-margin">
        <div class="titulo">CONTACTO</div>
        <div class="uk-padding contac-email">
          <img class="image-email" src="{SMT}/templates/puebla/images/icons/icon_redes/icon_email.svg" alt="Correo">
          <span class="titul">movilidadytransporte@puebla.gob.mx</span>
        </div>
      </div>
    </div>
  </div>
</div>
</div>
    </div>
  </div>

  <div class="below-bottom">
    <div class="">
<div class="mod-custom custom">
     <div class="uk-container ">
  <div uk-grid>
    <div class="uk-width-1-3@l uk-width-1-3@m uk-width-1-1@s uk-width-1-1@xs">
      <h2 class="text-2024 uk-margin-remove" tabindex="0">TRANSPARENCIA</h2>
    </div>
    <div class="uk-width-1-3@l uk-width-1-3@m uk-width-1-2@s uk-width-1-2@xs grayScale-2024">
      <div class="uk-width-3-4 uk-width-1@l uk-margin-auto">
        <a href="https://www.plataformadetransparencia.org.mx/web/guest/inicio" title="Plataforma nacional de transparencia" target="_blank">
          <img src="{SMT}/templates/puebla/images/transparencia/transparencia.png" width="80%" alt="Plataforma nacional de transparencia">
        </a>
      </div>
    </div>
    <div class="uk-width-1-3@l uk-width-1-3@m uk-width-1-2@s uk-width-1-2@xs grayScale-2024">
      <div class="uk-width-3-4 uk-width-1@l uk-margin-auto">
        <a href="https://consultapublicamx.plataformadetransparencia.org.mx/vut-web/faces/view/consultaPublica.xhtml?idEntidad=MjE=&amp;idOrgano=MjE=&amp;idSujetoObligado=MjMyNTc=#inicio" title="Consulta nuestras obligaciones de transparencia" target="_blank">
          <img src="{SMT}/templates/puebla/images/transparencia/consulta.png" width="80%" alt="Consulta nuestras obligaciones de transparencia">
        </a>
      </div>
    </div>
  </div>
</div>
</div>
    </div>
  </div>

</div>

<footer class="footer  uk-position-relative">
  <div class="uk-container footer-container uk-container uk-flex uk-flex-center">
    <div class="uk-position-absolute uk-padding-remove uk-margin-remove uk-width-1-1 uk-flex uk-flex-center sombra-footer">
      <img src="{SMT}/templates/puebla/images/footer/sombra_vertical.png" class="sombra-vertical uk-visible@m" alt="Sombra Vertical">
    </div>
    <div class="uk-flex uk-flex-middle uk-flex-center uk-padding-remove uk-margin-remove" uk-grid>
      <div class="uk-width-1-2@xl uk-width-1-2@l uk-width-1-2@m uk-width-1-2@s uk-width-1-1@xs">
        <div class="uk-flex uk-flex-middle uk-flex-center uk-padding">
<div class="mod-custom custom">
    <div class="Uk-container uk-padding escudo">
      <img src="{SMT}/templates/puebla/images/footer/Escudo_pie.svg" alt="Gobierno del Estado de Puebla">
    </div>
    <div class="image-hover">
      <img src="{SMT}/templates/puebla/images/footer/icon_911.svg" class="image-1" alt="911">
      <img src="{SMT}/templates/puebla/images/footer/icon_911_hover.svg" class="image-2" alt="911">
    </div>
</div>
        </div>
      </div>
      <img src="{SMT}/templates/puebla/images/footer/sombra_horizontal.png" class="sombra-horizontal uk-hidden@m" alt="Sombra Horizontal">
      <div class="uk-width-1-2@xl uk-width-1-2@l uk-width-1-2@m uk-width-1-2@s uk-width-1-1@xs">
        <div class="uk-flex uk-flex-middle uk-flex-center uk-padding">
<div class="mod-custom custom">
    <ul class="uk-list">
<li class="Te_Recomendamos" tabindex="0"><a href="https://planeader.puebla.gob.mx/" target="_blank">PLAN ESTATAL DE DESARROLLO</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://transparenciafiscal.puebla.gob.mx/" target="_blank">TRANSPARENCIA FISCAL</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://www.gob.mx/empleo" target="_blank">PORTAL DEL EMPLEO</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://presupuestociudadano.puebla.gob.mx/" target="_blank">PRESUPUESTO CIUDADANO</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://www.gob.mx/presidencia" target="_blank">PRESIDENCIA DE LA REPÚBLICA</a></li>
<li class="Te_Recomendamos" tabindex="0"><a href="https://lgcg.puebla.gob.mx/" target="_blank">LEY GENERAL DE CONTABILIDAD GUBERNAMENTAL</a></li>
</ul>
</div>
        </div>
      </div>
    </div>
  </div>
</footer>

<div class="copyright">
  <div class="uk-width-1-1 uk-flex uk-flex-center uk-padding-remove uk-margin-remove">
    <div class="copyright-container  uk-padding-remove uk-margin-remove">
<div class="mod-custom custom">
    <img src="{SMT}/templates/puebla/images/footer/copyright.svg" class="cop" style="width:1400px; height:auto; background:#5f1b2d;" alt="© {anio} Gobierno del Estado de Puebla">
</div>
    </div>
  </div>
</div>

<div id="offcanvas-flip" uk-offcanvas="mode: push; overlay: true; flip: true">
  <div class="uk-offcanvas-bar uk-padding-remove">
    <button class="uk-offcanvas-close" type="button" uk-close></button>
    <h3 class="uk-padding uk-padding-remove-bottom uk-margin-remove">Secretaria de Movilidad y Transporte</h3>
<ul class="uk-nav uk-nav-default uk-nav-parent-icon uk-padding " uk-nav="multiple: true">
    <li style="padding: 2px;"><a href="https://rl.puebla.gob.mx/">Pagos en línea</a></li>
    <li style="padding: 2px;"><a href="https://ventanilladigital.puebla.gob.mx/">Trámites</a></li>
    <li style="padding: 2px;"><a href="{SMT}/images/documentos/DIT_E081_SMT.pdf">DIT</a></li>
</ul>
  </div>
</div>

</body>
</html>"""


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

            # Barra de vigencia
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
