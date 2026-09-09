from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import html as html_lib


def crear_router_admin_tablas(
    supabase,
    supabase_admin,
    ENTIDAD
):

    router = APIRouter()

    # ============================================================
    # PANEL DE TABLAS
    # ============================================================

    @router.get(
        "/admin/tablas",
        response_class=HTMLResponse
    )
    async def admin_tablas(
        request: Request
    ):

        if not request.session.get("admin"):

            return RedirectResponse(
                "/login",
                status_code=302
            )

        # --------------------------------------------------------
        # TABLAS QUE QUEREMOS MOSTRAR
        # --------------------------------------------------------

        tablas = [
            {
                "nombre": "folios_registrados",
                "titulo": "Folios registrados",
                "descripcion":
                    "Permisos generados y registrados en el sistema."
            },
            {
                "nombre": "clientes_permisos",
                "titulo": "Clientes",
                "descripcion":
                    "Cuentas de clientes y paquetes asignados."
            },
            {
                "nombre": "folio_watermark",
                "titulo": "Control de folios",
                "descripcion":
                    "Numeración utilizada para generar nuevos folios."
            },
            {
                "nombre": "borradores_registros",
                "titulo": "Borradores",
                "descripcion":
                    "Registros temporales utilizados por el sistema."
            }
        ]

        tarjetas = ""

        for tabla in tablas:

            nombre_tabla = tabla["nombre"]

            total = 0

            error = None

            try:

                # Las tablas administrativas se consultan
                # mediante service_role.
                resp = (
                    supabase_admin
                    .table(nombre_tabla)
                    .select("*", count="exact")
                    .limit(1)
                    .execute()
                )

                total = (
                    resp.count
                    if resp.count is not None
                    else 0
                )

            except Exception as e:

                error = str(e)

            titulo = html_lib.escape(
                tabla["titulo"]
            )

            descripcion = html_lib.escape(
                tabla["descripcion"]
            )

            nombre_seguro = html_lib.escape(
                nombre_tabla
            )

            if error:

                estado = """
                <span class="estado error">
                    Error de lectura
                </span>
                """

            else:

                estado = """
                <span class="estado ok">
                    Disponible
                </span>
                """

            tarjetas += f"""
            <article class="tabla-card">

                <div class="tabla-top">

                    <div>

                        <h3>
                            {titulo}
                        </h3>

                        <code>
                            {nombre_seguro}
                        </code>

                    </div>

                    {estado}

                </div>


                <p>
                    {descripcion}
                </p>


                <div class="tabla-total">

                    <span>
                        Registros
                    </span>

                    <strong>
                        {total}
                    </strong>

                </div>


                <a
                    class="boton"
                    href="/admin/tablas/{nombre_seguro}"
                >
                    Consultar tabla
                </a>

            </article>
            """

        return HTMLResponse(f"""
<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Tablas del sistema
</title>

<style>

:root {{

    --vino:#5f1b2d;
    --vino-oscuro:#48101e;
    --dorado:#c09761;
    --gris:#949494;
    --gris-claro:#f6f6f6;
    --verde:#198754;
    --rojo:#b02a37;
}}

* {{

    box-sizing:border-box;
    margin:0;
    padding:0;
}}

body {{

    background:#f4f4f4;

    color:#555;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}


/* =========================================================
   HEADER
========================================================= */

.header {{

    background:white;

    box-shadow:
        0 2px 8px
        rgba(0,0,0,.08);
}}

.header-inner {{

    max-width:1380px;

    margin:auto;

    padding:
        18px 30px;

    display:flex;

    justify-content:
        space-between;

    align-items:center;

    gap:30px;
}}

.logos {{

    display:flex;

    align-items:center;

    gap:22px;
}}

.logo-gob {{

    width:245px;
}}

.logo-secretaria {{

    width:225px;
}}

.frase {{

    width:300px;
}}


/* =========================================================
   MENU
========================================================= */

.menu {{

    background:
        var(--vino);
}}

.menu-inner {{

    max-width:1380px;

    margin:auto;

    padding:
        0 30px;

    display:flex;

    justify-content:
        space-between;

    align-items:center;
}}

.menu-links {{

    display:flex;

    overflow-x:auto;
}}

.menu a {{

    display:block;

    color:white;

    text-decoration:none;

    padding:
        17px 15px;

    font-size:14px;

    white-space:nowrap;
}}

.menu a:hover,
.menu a.active {{

    background:
        rgba(255,255,255,.12);
}}

.cerrar {{

    background:
        rgba(0,0,0,.16);

    border-radius:
        7px;
}}


/* =========================================================
   HERO
========================================================= */

.hero {{

    position:relative;

    text-align:center;

    padding:
        50px 20px
        90px;

    background:
        linear-gradient(
            120deg,
            #f8f8f8,
            #eeeeee
        );
}}

.hero::after {{

    content:"";

    position:absolute;

    left:0;

    bottom:0;

    width:100%;

    height:7px;

    background:
        var(--dorado);
}}

.hero h1 {{

    color:
        var(--vino);

    font-size:
        34px;

    font-weight:
        400;

    margin-bottom:
        8px;
}}

.hero p {{

    color:
        var(--gris);
}}


/* =========================================================
   CONTENIDO
========================================================= */

.contenido {{

    padding:
        0 20px
        60px;
}}

.panel {{

    position:relative;

    z-index:2;

    max-width:
        1100px;

    margin:
        -55px auto
        40px;

    background:white;

    padding:
        38px 40px;

    border-radius:
        24px;

    box-shadow:
        0 8px 32px
        rgba(0,0,0,.11);
}}

.panel h2 {{

    color:
        var(--vino);

    font-size:
        24px;

    font-weight:
        400;

    margin-bottom:
        6px;
}}

.panel-sub {{

    color:#888;

    margin-bottom:
        28px;

    font-size:
        14px;
}}


/* =========================================================
   TABLAS
========================================================= */

.grid {{

    display:grid;

    grid-template-columns:
        repeat(
            2,
            minmax(0,1fr)
        );

    gap:
        18px;
}}

.tabla-card {{

    border:
        1px solid
        #e5e5e5;

    border-left:
        4px solid
        var(--dorado);

    border-radius:
        13px;

    padding:
        21px;

    background:white;
}}

.tabla-top {{

    display:flex;

    justify-content:
        space-between;

    gap:
        15px;

    align-items:
        flex-start;
}}

.tabla-card h3 {{

    color:
        var(--vino);

    font-size:
        19px;

    margin-bottom:
        5px;
}}

.tabla-card code {{

    color:#888;

    font-size:
        12px;
}}

.tabla-card p {{

    margin:
        16px 0;

    color:#777;

    line-height:
        1.5;

    font-size:
        13px;
}}

.estado {{

    padding:
        6px 9px;

    border-radius:
        20px;

    font-size:
        10px;

    font-weight:
        bold;

    white-space:
        nowrap;
}}

.estado.ok {{

    background:
        #dff3e8;

    color:
        var(--verde);
}}

.estado.error {{

    background:
        #f6dfe2;

    color:
        var(--rojo);
}}

.tabla-total {{

    background:
        var(--gris-claro);

    padding:
        13px;

    border-radius:
        9px;

    margin-bottom:
        16px;
}}

.tabla-total span {{

    display:block;

    color:#888;

    text-transform:
        uppercase;

    font-size:
        10px;
}}

.tabla-total strong {{

    display:block;

    color:
        var(--vino);

    font-size:
        26px;

    margin-top:
        4px;
}}

.boton {{

    display:inline-block;

    background:
        var(--vino);

    color:white;

    text-decoration:none;

    padding:
        10px 15px;

    border-radius:
        8px;

    font-size:
        13px;
}}


/* =========================================================
   RESPONSIVE
========================================================= */

@media(max-width:700px) {{

    .header-inner {{

        display:block;

        padding:
            15px;
    }}

    .logos {{

        justify-content:center;

        gap:
            8px;
    }}

    .logo-gob {{

        width:50%;
    }}

    .logo-secretaria {{

        width:43%;
    }}

    .frase {{

        display:none;
    }}

    .menu-inner {{

        display:block;

        padding:
            0 10px;
    }}

    .menu-links {{

        overflow-x:auto;
    }}

    .menu a {{

        font-size:
            12px;

        padding:
            15px 10px;
    }}

    .cerrar {{

        text-align:center;

        margin:
            6px 0 10px;
    }}

    .panel {{

        margin:
            -45px auto
            30px;

        padding:
            24px 16px;

        border-radius:
            17px;
    }}

    .grid {{

        grid-template-columns:
            1fr;
    }}

}}

</style>

</head>


<body>


<header class="header">

<div class="header-inner">

<div class="logos">

<img
    class="logo-gob"
    src="https://smt.puebla.gob.mx/templates/puebla/images/header/logo_puebla_gob.svg"
>

<img
    class="logo-secretaria"
    src="https://smt.puebla.gob.mx/images/headers/MOVILIDAD_02.png"
>

</div>


<img
    class="frase"
    src="https://smt.puebla.gob.mx/templates/puebla/images/header/puebla_frases_gob.svg"
>

</div>

</header>


<nav class="menu">

<div class="menu-inner">

<div class="menu-links">

<a href="/admin">
    Inicio
</a>

<a href="/admin/crear">
    Crear permiso
</a>

<a href="/admin/folios">
    Gestionar folios
</a>

<a href="/admin/usuarios">
    Usuarios
</a>

<a
    href="/admin/tablas"
    class="active"
>
    Tablas
</a>

<a href="/admin/auditoria">
    Auditoría
</a>

</div>


<a
    href="/logout"
    class="cerrar"
>
    Cerrar sesión
</a>

</div>

</nav>


<section class="hero">

<h1>
    Tablas del Sistema
</h1>

<p>
    Consulta administrativa de la información almacenada
</p>

</section>


<main class="contenido">

<section class="panel">

<h2>
    Base de datos
</h2>

<p class="panel-sub">
    Seleccione una tabla para consultar sus registros.
</p>


<div class="grid">

{tarjetas}

</div>

</section>

</main>


</body>

</html>
""")


    # ============================================================
    # CONSULTAR UNA TABLA
    # ============================================================

    @router.get(
        "/admin/tablas/{tabla}",
        response_class=HTMLResponse
    )
    async def admin_ver_tabla(
        tabla: str,
        request: Request
    ):

        if not request.session.get("admin"):

            return RedirectResponse(
                "/login",
                status_code=302
            )

        # Lista blanca.
        # No permitimos que el nombre venga libre desde la URL.
        tablas_permitidas = {
            "folios_registrados",
            "clientes_permisos",
            "folio_watermark",
            "borradores_registros"
        }

        if tabla not in tablas_permitidas:

            return HTMLResponse(
                """
                <h2>
                    Tabla no permitida
                </h2>
                """,
                status_code=404
            )

        try:

            resp = (
                supabase_admin
                .table(tabla)
                .select("*")
                .limit(500)
                .execute()
            )

            registros = resp.data or []

        except Exception as e:

            return HTMLResponse(
                f"""
                <h2>
                    Error consultando tabla
                </h2>

                <pre>
                    {html_lib.escape(str(e))}
                </pre>
                """,
                status_code=500
            )

        # --------------------------------------------------------
        # SIN REGISTROS
        # --------------------------------------------------------

        if not registros:

            contenido_tabla = """

            <div class="vacio">
                Esta tabla no contiene registros.
            </div>

            """

        else:

            columnas = list(
                registros[0].keys()
            )

            headers = ""

            for columna in columnas:

                headers += (
                    "<th>"
                    + html_lib.escape(
                        str(columna)
                    )
                    + "</th>"
                )

            filas = ""

            for registro in registros:

                filas += "<tr>"

                for columna in columnas:

                    valor = registro.get(
                        columna
                    )

                    if valor is None:
                        valor = "—"

                    valor = html_lib.escape(
                        str(valor)
                    )

                    filas += (
                        "<td>"
                        + valor
                        + "</td>"
                    )

                filas += "</tr>"

            contenido_tabla = f"""

            <div class="tabla-scroll">

            <table>

                <thead>

                    <tr>
                        {headers}
                    </tr>

                </thead>

                <tbody>
                    {filas}
                </tbody>

            </table>

            </div>

            """

        tabla_segura = html_lib.escape(
            tabla
        )

        return HTMLResponse(f"""
<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    {tabla_segura}
</title>

<style>

:root {{

    --vino:#5f1b2d;

    --dorado:#c09761;
}}

* {{
    box-sizing:border-box;
}}

body {{

    margin:0;

    background:#f4f4f4;

    font-family:
        Arial,
        sans-serif;

    color:#555;
}}

.barra {{

    background:
        var(--vino);

    padding:
        17px 25px;

    color:white;
}}

.barra a {{

    color:white;

    text-decoration:none;
}}

.contenido {{

    max-width:
        1400px;

    margin:
        30px auto;

    padding:
        0 20px;
}}

.panel {{

    background:white;

    border-radius:
        16px;

    padding:
        25px;

    box-shadow:
        0 5px 20px
        rgba(0,0,0,.08);
}}

h1 {{

    color:
        var(--vino);

    font-weight:
        400;

    margin-top:0;
}}

.tabla-scroll {{

    width:100%;

    overflow-x:auto;
}}

table {{

    width:100%;

    border-collapse:
        collapse;

    font-size:
        13px;
}}

th {{

    background:
        var(--vino);

    color:white;

    padding:
        12px;

    text-align:left;

    white-space:
        nowrap;
}}

td {{

    border-bottom:
        1px solid
        #eeeeee;

    padding:
        11px 12px;

    vertical-align:
        top;

    max-width:
        350px;

    overflow-wrap:
        anywhere;
}}

tr:hover {{

    background:
        #fafafa;
}}

.vacio {{

    background:
        #f7f7f7;

    padding:
        30
