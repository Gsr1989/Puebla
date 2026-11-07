# ============================================================
# consulta_publica.py
# Página pública de consulta de folio (QR) - Puebla SMT
# Diseño basado en smt.puebla.gob.mx
#
# USO en main.py (2 líneas):
#   from consulta_publica import crear_router_consulta
#   app.include_router(crear_router_consulta(supabase, ENTIDAD, TZ))
# ============================================================

import html as html_lib
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def crear_router_consulta(supabase, entidad: str, timezone: str) -> APIRouter:

    router = APIRouter()

    @router.get("/estado_folio/{folio}", response_class=HTMLResponse)
    async def estado_folio_qr(folio: str):

        folio = folio.strip().upper()

        SMT = "https://smt.puebla.gob.mx"

        def esc(valor):
            return html_lib.escape(
                str(valor if valor not in (None, "") else "—")
            )

        def parse_fecha(valor):
            if not valor:
                return None
            texto = str(valor).strip()
            try:
                return datetime.fromisoformat(
                    texto.replace("Z", "+00:00")
                ).date()
            except Exception:
                try:
                    return datetime.strptime(texto[:10], "%Y-%m-%d").date()
                except Exception:
                    return None

        # ------------------------------------------------------------------
        # CSS (string normal, SIN f-string, por eso las llaves van sencillas)
        # Autocontenido: solo se usan imágenes del sitio oficial.
        # ------------------------------------------------------------------
        css = """
    :root{
      --vino:#5f1b2d; --vino-dark:#48101e; --dorado:#c09761; --dorado2:#c79b66;
      --gris:#949494; --gris-txt:#6b6b6b; --fondo:#f0f4fb; --azul:#001B4C;
    }
    *{box-sizing:border-box}
    html,body{margin:0;padding:0;background:var(--fondo);
      font-family:"Montserrat","Segoe UI",Arial,Helvetica,sans-serif;color:#495057}
    a{color:inherit}
    img{max-width:100%;height:auto}

    /* HEADER */
    .hd{background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.08);position:relative;z-index:5}
    .hd-in{max-width:1380px;margin:auto;padding:14px 30px;display:flex;
      align-items:center;justify-content:space-between;gap:24px}
    .hd-logos{display:flex;align-items:center;gap:20px;min-width:0}
    .hd-logos img{height:78px;width:auto;object-fit:contain}
    .hd-frase{height:70px;width:auto}

    /* MENU */
    .nav{background:var(--vino)}
    .nav-in{max-width:1380px;margin:auto;padding:0 30px;display:flex;
      justify-content:flex-end;flex-wrap:wrap}
    .nav a{color:#fff;text-decoration:none;padding:16px 22px;font-size:14px;
      letter-spacing:.3px;transition:background .2s}
    .nav a:hover{background:rgba(255,255,255,.14)}

    /* TITULO */
    .titulo{max-width:1100px;margin:34px auto 0;padding:0 20px;text-align:center}
    .titulo small{display:block;color:var(--dorado2);font-size:13px;font-weight:700;
      letter-spacing:3px;text-transform:uppercase;margin-bottom:8px}
    .titulo h1{margin:0;color:var(--vino);font-size:32px;font-weight:300}
    .titulo p{margin:8px 0 0;color:var(--gris);font-size:15px}

    /* TARJETA RESULTADO */
    .wrap{max-width:1000px;margin:26px auto 0;padding:0 18px}
    .res{background:#fff;border-radius:26px;overflow:hidden;
      box-shadow:0 12px 40px rgba(0,0,0,.12)}

    .banner{display:flex;align-items:center;gap:22px;padding:28px 34px;color:#fff}
    .banner.ok{background:linear-gradient(120deg,#2e7d44,#3fa05a)}
    .banner.venc{background:linear-gradient(120deg,#b9770e,#d9a21b)}
    .banner.err{background:linear-gradient(120deg,#8e1f2e,#b72f3c)}
    .banner-ico{width:64px;height:64px;flex:0 0 64px;border-radius:50%;
      background:rgba(255,255,255,.2);border:3px solid rgba(255,255,255,.75);
      display:flex;align-items:center;justify-content:center;
      font-size:34px;font-weight:700;line-height:1}
    .banner h2{margin:0 0 4px;font-size:22px;font-weight:700;letter-spacing:.8px}
    .banner p{margin:0;font-size:14px;opacity:.95;line-height:1.45}

    .folio-box{text-align:center;padding:30px 20px 22px;
      background:linear-gradient(#fbf8f4,#fff)}
    .folio-box span{display:block;color:var(--gris);font-size:12px;font-weight:700;
      letter-spacing:4px;margin-bottom:8px}
    .folio-box strong{display:block;color:var(--vino);font-size:42px;
      font-weight:700;letter-spacing:2px;overflow-wrap:anywhere}
    .folio-box i{display:block;width:70px;height:4px;background:var(--dorado);
      border-radius:4px;margin:16px auto 0}

    .body{padding:10px 34px 34px}
    .bloque{margin-top:26px}
    .bloque h3{margin:0 0 14px;color:var(--vino);font-size:16px;font-weight:700;
      letter-spacing:1px;text-transform:uppercase;display:flex;align-items:center;gap:10px}
    .bloque h3::after{content:"";flex:1;height:1px;background:#e8e8e8}

    .titular{background:#faf7f3;border-left:5px solid var(--dorado);
      border-radius:14px;padding:18px 22px}
    .titular span,.dato span{display:block;color:#979797;font-size:11px;
      font-weight:700;letter-spacing:.9px;text-transform:uppercase;margin-bottom:6px}
    .titular strong{display:block;color:#3f3f3f;font-size:20px;font-weight:600;
      overflow-wrap:anywhere}

    .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
    .dato{background:#f7f7f7;border-radius:12px;padding:15px 17px;min-width:0}
    .dato strong{display:block;color:#444;font-size:16px;font-weight:600;
      overflow-wrap:anywhere;line-height:1.35}
    .dato.wide{grid-column:span 3}
    .mono{font-family:"Courier New",monospace;letter-spacing:1.2px;font-size:17px!important}

    /* VIGENCIA */
    .vig{background:#f7f7f7;border-radius:14px;padding:20px 22px}
    .vig-fechas{display:flex;justify-content:space-between;gap:14px;margin-bottom:14px}
    .vig-fechas div{min-width:0}
    .vig-fechas span{display:block;color:#979797;font-size:11px;font-weight:700;
      letter-spacing:.9px;text-transform:uppercase;margin-bottom:5px}
    .vig-fechas strong{font-size:18px;color:#3f3f3f}
    .vig-fechas div:last-child{text-align:right}
    .barra{height:12px;background:#e4e4e4;border-radius:20px;overflow:hidden}
    .barra b{display:block;height:100%;border-radius:20px}
    .barra b.ok{background:linear-gradient(90deg,#3fa05a,#2e7d44)}
    .barra b.venc{background:linear-gradient(90deg,#d9a21b,#b9770e)}
    .vig-txt{margin-top:10px;font-size:13px;color:var(--gris-txt);text-align:center}

    /* SELLO / NOTA */
    .sello{margin-top:26px;display:flex;gap:14px;align-items:flex-start;
      background:#faf7f3;border-left:5px solid var(--dorado);border-radius:12px;
      padding:16px 18px;color:#686868;font-size:13px;line-height:1.6}
    .sello b{color:var(--vino)}
    .sello .lock{font-size:22px;line-height:1}

    .acciones{margin-top:22px;text-align:center}
    .btn{display:inline-block;background:var(--vino);color:#fff;border:0;
      padding:13px 28px;border-radius:10px;font-size:14px;font-weight:600;
      cursor:pointer;font-family:inherit}
    .btn:hover{background:var(--vino-dark)}

    .noenc{padding:40px 34px 44px;text-align:center}
    .noenc p{color:#777;font-size:15px;line-height:1.6;max-width:520px;margin:0 auto}

    /* CONTACTO */
    .contacto{max-width:1180px;margin:34px auto 0;padding:0 18px}
    .contacto-in{background:#fff;border-radius:20px;padding:20px 26px;
      box-shadow:0 4px 16px rgba(0,0,0,.07);display:grid;
      grid-template-columns:auto 1.2fr 1.4fr 1.4fr;gap:22px;align-items:center}
    .contacto-in div{color:#8a8a8a;font-size:14px;line-height:1.55}
    .contacto-in .lbl{color:#b2b2b2;font-size:19px}
    .contacto-in b{display:block;color:var(--dorado);margin-bottom:3px}

    /* REDES */
    .redes{background:#e3e3e3;margin-top:40px;padding:34px 20px;text-align:center}
    .redes .t{color:#949494;font-size:20px;margin-bottom:18px;letter-spacing:1px}
    .redes .ic{display:flex;justify-content:center;gap:20px;flex-wrap:wrap}
    .redes img{width:46px;height:46px}

    /* TRANSPARENCIA */
    .transp{max-width:1100px;margin:0 auto;padding:30px 20px;display:grid;
      grid-template-columns:1fr 1fr 1fr;gap:20px;align-items:center}
    .transp h2{margin:0;color:#b2b2b1;font-weight:300;font-size:26px}
    .transp img{width:80%;filter:grayscale(100%);opacity:.5}

    /* FOOTER */
    .ft{background:var(--vino);color:#fff;padding:44px 25px}
    .ft-in{max-width:1150px;margin:auto;display:grid;grid-template-columns:1.1fr 1fr;
      gap:46px;align-items:center}
    .ft-logo{width:100%;max-width:520px}
    .ft-911{height:64px;width:auto;margin-top:18px}
    .ft ul{list-style:none;margin:0;padding:0}
    .ft li{margin:7px 0}
    .ft li a{color:#fffbef;text-decoration:none;font-size:14px;line-height:1.7}
    .ft li a:hover{text-decoration:underline}
    .cop{background:var(--vino-dark);color:rgba(255,255,255,.7);text-align:center;
      font-size:12px;padding:15px 20px}

    /* RESPONSIVE */
    @media(max-width:860px){
      .contacto-in{grid-template-columns:1fr;text-align:center}
      .transp{grid-template-columns:1fr;text-align:center}
      .transp img{width:60%}
      .ft-in{grid-template-columns:1fr;text-align:center}
      .ft-logo{margin:auto}
    }
    @media(max-width:640px){
      .hd-in{padding:12px 14px;justify-content:center}
      .hd-logos{gap:10px;width:100%;justify-content:center}
      .hd-logos img{height:auto;max-height:54px;max-width:47%}
      .hd-frase{display:none}
      .nav-in{padding:0 6px;justify-content:center}
      .nav a{padding:14px 12px;font-size:13px}
      .titulo h1{font-size:25px}
      .banner{padding:20px 18px;gap:14px}
      .banner-ico{width:50px;height:50px;flex-basis:50px;font-size:27px}
      .banner h2{font-size:17px}
      .banner p{font-size:12.5px}
      .folio-box strong{font-size:28px;letter-spacing:1px}
      .body{padding:6px 16px 26px}
      .grid{grid-template-columns:1fr 1fr}
      .dato.wide{grid-column:span 2}
      .vig-fechas strong{font-size:15px}
      .res{border-radius:20px}
    }
    @media(max-width:380px){
      .grid{grid-template-columns:1fr}
      .dato.wide{grid-column:span 1}
    }
    @media print{
      .nav,.contacto,.redes,.transp,.ft,.cop,.acciones{display:none!important}
      body{background:#fff}
      .res{box-shadow:none}
    }
    """

        # ------------------------------------------------------------------
        # Piezas fijas de la página (header / footer oficiales)
        # ------------------------------------------------------------------
        header_html = f"""
    <header class="hd">
      <div class="hd-in">
        <div class="hd-logos">
          <a href="https://puebla.gob.mx" target="_blank" rel="noopener noreferrer">
            <img src="{SMT}/templates/puebla/images/header/logo_puebla_gob.svg" alt="Gobierno del Estado de Puebla">
          </a>
          <a href="{SMT}/" target="_blank" rel="noopener noreferrer">
            <img src="{SMT}/images/headers/MOVILIDAD_02.png" alt="Secretaría de Movilidad y Transporte">
          </a>
        </div>
        <img class="hd-frase" src="{SMT}/templates/puebla/images/header/puebla_frases_gob.svg" alt="Amor a Puebla Gobierno">
      </div>
    </header>
    <nav class="nav">
      <div class="nav-in">
        <a href="https://rl.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Pagos en línea</a>
        <a href="https://ventanilladigital.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Trámites</a>
        <a href="{SMT}/images/documentos/DIT_E081_SMT.pdf" target="_blank" rel="noopener noreferrer">DIT</a>
      </div>
    </nav>
    """

        footer_html = f"""
    <section class="contacto">
      <div class="contacto-in">
        <div class="lbl">Contáctanos</div>
        <div><b>Teléfono</b>(222) 2 29 06 00<br>Ext. 1000 y 3503</div>
        <div><b>Dirección</b>Av. Rosendo Márquez 1501, col. La Paz<br>C.P. 72160 Puebla, Pue.</div>
        <div><b>Correo electrónico</b>movilidadytransporte@puebla.gob.mx</div>
      </div>
    </section>

    <section class="redes">
      <div class="t"><strong>MANTENTE</strong> AL DÍA</div>
      <div class="ic">
        <a href="https://www.facebook.com/share/16oEpS2k6P/?mibextid=wwXIfr" target="_blank" rel="noopener noreferrer" aria-label="Facebook"><img src="{SMT}/templates/puebla/images/icons/icon_redes/icon_f.svg" alt="Facebook"></a>
        <a href="https://x.com/mtgobpue" target="_blank" rel="noopener noreferrer" aria-label="X"><img src="{SMT}/templates/puebla/images/icons/icon_redes/icon_x.svg" alt="X"></a>
        <a href="https://www.instagram.com/mtgobpue" target="_blank" rel="noopener noreferrer" aria-label="Instagram"><img src="{SMT}/templates/puebla/images/icons/icon_redes/icon_in.svg" alt="Instagram"></a>
        <a href="https://www.tiktok.com/@mtgobpue" target="_blank" rel="noopener noreferrer" aria-label="TikTok"><img src="{SMT}/templates/puebla/images/icons/icon_redes/icon_tt.svg" alt="TikTok"></a>
      </div>
    </section>

    <section class="transp">
      <h2>TRANSPARENCIA</h2>
      <a href="https://www.plataformadetransparencia.org.mx/web/guest/inicio" target="_blank" rel="noopener noreferrer"><img src="{SMT}/templates/puebla/images/transparencia/transparencia.png" alt="Plataforma nacional de transparencia"></a>
      <a href="https://consultapublicamx.plataformadetransparencia.org.mx/vut-web/faces/view/consultaPublica.xhtml?idEntidad=MjE=&amp;idOrgano=MjE=&amp;idSujetoObligado=MjMyNTc=#inicio" target="_blank" rel="noopener noreferrer"><img src="{SMT}/templates/puebla/images/transparencia/consulta.png" alt="Obligaciones de transparencia"></a>
    </section>

    <footer class="ft">
      <div class="ft-in">
        <div>
          <img class="ft-logo" src="{SMT}/templates/puebla/images/footer/Escudo_pie.svg" alt="Gobierno del Estado de Puebla">
          <img class="ft-911" src="{SMT}/templates/puebla/images/footer/icon_911.svg" alt="911">
        </div>
        <ul>
          <li><a href="https://planeader.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">PLAN ESTATAL DE DESARROLLO</a></li>
          <li><a href="https://transparenciafiscal.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">TRANSPARENCIA FISCAL</a></li>
          <li><a href="https://www.gob.mx/empleo" target="_blank" rel="noopener noreferrer">PORTAL DEL EMPLEO</a></li>
          <li><a href="https://presupuestociudadano.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">PRESUPUESTO CIUDADANO</a></li>
          <li><a href="https://www.gob.mx/presidencia" target="_blank" rel="noopener noreferrer">PRESIDENCIA DE LA REPÚBLICA</a></li>
          <li><a href="https://lgcg.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">LEY GENERAL DE CONTABILIDAD GUBERNAMENTAL</a></li>
        </ul>
      </div>
    </footer>
    """

        def armar_pagina(contenido: str, anio: int) -> str:
            return f"""<!DOCTYPE html>
    <html lang="es">
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <meta name="description" content="Secretaría de Movilidad y Transporte - Consulta de permiso">
    <title>.: Secretaría de Movilidad y Transporte :.</title>
    <link rel="icon" href="{SMT}/templates/puebla/favicon.ico" type="image/vnd.microsoft.icon">
    <style>{css}</style>
    </head>
    <body>
    {header_html}

    <section class="titulo">
      <small>Verificación oficial</small>
      <h1>Consulta de Permiso</h1>
      <p>Secretaría de Movilidad y Transporte</p>
    </section>

    <main class="wrap">
    {contenido}
    </main>

    {footer_html}

    <div class="cop">© {anio} Gobierno del Estado de Puebla. Secretaría de Movilidad y Transporte.</div>
    </body>
    </html>"""

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

            # ==============================================================
            # FOLIO NO ENCONTRADO
            # ==============================================================
            if not res.data:

                contenido = f"""
    <article class="res">
      <div class="banner err">
        <div class="banner-ico">×</div>
        <div>
          <h2>FOLIO NO ENCONTRADO</h2>
          <p>No existe un permiso registrado con este folio.</p>
        </div>
      </div>
      <div class="folio-box">
        <span>FOLIO CONSULTADO</span>
        <strong>{esc(folio)}</strong>
        <i></i>
      </div>
      <div class="noenc">
        <p>Verifica que el folio esté capturado correctamente. Si el problema
        continúa, comunícate con la Secretaría de Movilidad y Transporte.</p>
      </div>
    </article>
    """
                return HTMLResponse(
                    content=armar_pagina(contenido, hoy.year),
                    status_code=200
                )

            # ==============================================================
            # FOLIO ENCONTRADO
            # ==============================================================
            r = res.data[0]

            fecha_exp = parse_fecha(r.get("fecha_expedicion"))
            fecha_ven = parse_fecha(r.get("fecha_vencimiento"))

            vigente = fecha_ven is not None and hoy <= fecha_ven

            if vigente:
                clase = "ok"
                icono = "✓"
                titulo_estado = "PERMISO VIGENTE"
                sub_estado = (
                    "El permiso se encuentra dentro de su periodo de vigencia."
                )
            else:
                clase = "venc"
                icono = "!"
                titulo_estado = "PERMISO VENCIDO"
                sub_estado = (
                    "El periodo de vigencia de este permiso ha concluido."
                )

            fe_txt = fecha_exp.strftime("%d/%m/%Y") if fecha_exp else "—"
            fv_txt = fecha_ven.strftime("%d/%m/%Y") if fecha_ven else "—"

            # ---------------- Barra de vigencia ----------------
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

            contenido = f"""
    <article class="res">

      <div class="banner {clase}">
        <div class="banner-ico">{icono}</div>
        <div>
          <h2>{titulo_estado}</h2>
          <p>{sub_estado}</p>
        </div>
      </div>

      <div class="folio-box">
        <span>FOLIO</span>
        <strong>{esc(folio)}</strong>
        <i></i>
      </div>

      <div class="body">

        <section class="bloque">
          <h3>Titular</h3>
          <div class="titular">
            <span>Contribuyente</span>
            <strong>{esc(r.get("contribuyente"))}</strong>
          </div>
        </section>

        <section class="bloque">
          <h3>Vehículo</h3>
          <div class="grid">
            <div class="dato"><span>Marca</span><strong>{esc(r.get("marca"))}</strong></div>
            <div class="dato"><span>Línea / Modelo</span><strong>{esc(r.get("linea"))}</strong></div>
            <div class="dato"><span>Año</span><strong>{esc(r.get("anio"))}</strong></div>
            <div class="dato"><span>Color</span><strong>{esc(r.get("color"))}</strong></div>
            <div class="dato wide"><span>Número de serie / NIV</span><strong class="mono">{esc(r.get("numero_serie"))}</strong></div>
            <div class="dato wide"><span>Número de motor</span><strong class="mono">{esc(r.get("numero_motor"))}</strong></div>
          </div>
        </section>

        <section class="bloque">
          <h3>Vigencia</h3>
          <div class="vig">
            <div class="vig-fechas">
              <div><span>Expedición</span><strong>{fe_txt}</strong></div>
              <div><span>Vencimiento</span><strong>{fv_txt}</strong></div>
            </div>
            <div class="barra"><b class="{clase}" style="width:{pct}%"></b></div>
            <div class="vig-txt">{esc(texto_vig) if texto_vig else ""}</div>
          </div>
        </section>

        <div class="sello">
          <div class="lock">🔒</div>
          <div>
            <b>Consulta verificada.</b>
            La información mostrada corresponde al registro asociado al folio
            consultado al día {hoy.strftime("%d/%m/%Y")}.
          </div>
        </div>

        <div class="acciones">
          <button class="btn" type="button" onclick="window.print()">Imprimir constancia</button>
        </div>

      </div>
    </article>
    """

            return HTMLResponse(
                content=armar_pagina(contenido, hoy.year),
                status_code=200
            )

        except Exception as e:

            print(
                "[ESTADO_FOLIO] "
                f"Error consultando {folio}: "
                f"{repr(e)}"
            )

            contenido_error = """
    <article class="res">
      <div class="banner err">
        <div class="banner-ico">!</div>
        <div>
          <h2>NO FUE POSIBLE REALIZAR LA CONSULTA</h2>
          <p>Inténtalo nuevamente en unos minutos.</p>
        </div>
      </div>
      <div class="noenc"><p>Si el problema continúa, comunícate con la Secretaría de Movilidad y Transporte.</p></div>
    </article>
    """
            return HTMLResponse(
                content=armar_pagina(
                    contenido_error,
                    datetime.now(ZoneInfo(timezone)).year
                ),
                status_code=500
            )

    return router
