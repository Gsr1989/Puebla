from fastapi import FastAPI, Request, Form
import html as html_lib
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import os
from contextlib import asynccontextmanager, suppress
from starlette.middleware.sessions import SessionMiddleware
import asyncio
import random
from io import BytesIO
import qrcode
import fitz
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import FSInputFile, ContentType, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import aiohttp
from urllib.parse import quote

# ==================== CONFIG ====================
BOT_TOKEN    = os.getenv("BOT_TOKEN_PUEBLA", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BASE_URL     = "https://smt-puebla-gob-mx.onrender.com"
OUTPUT_DIR   = "documentos"
PLANTILLA    = "PUEBLA_PLANTILLA_COMPLETA.pdf"
ENTIDAD      = "puebla"
PRECIO       = 180
TZ           = "America/Mexico_City"
ADMIN_USER = os.getenv("ADMIN_USER", "")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

if not ADMIN_USER or not ADMIN_PASS:
    print("[WARN] ADMIN_USER / ADMIN_PASS no configurados")

if not SECRET_KEY:
    print("[WARN] SECRET_KEY no configurada; usando temporal")
    SECRET_KEY = os.urandom(32).hex()

os.makedirs(OUTPUT_DIR, exist_ok=True)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ==================== TIMERS ====================
timers_activos = {}
user_folios = {}

async def eliminar_folio_automatico(folio: str):
    try:
        uid = timers_activos[folio]["user_id"] if folio in timers_activos else None
        supabase.table("folios_registrados").delete().eq("folio", folio).execute()
        supabase.table("borradores_registros").delete().eq("folio", folio).execute()
        if uid:
            await bot.send_message(uid,
                f"⏰ TIEMPO AGOTADO - PUEBLA\n\n"
                f"El folio {folio} fue eliminado.\n\n"
                f"Use /permiso para generar otro")
        limpiar_timer_folio(folio)
    except Exception as e:
        print(f"Error eliminando folio {folio}: {e}")

async def enviar_recordatorio(folio: str, minutos_restantes: int):
    try:
        if folio not in timers_activos: return
        uid = timers_activos[folio]["user_id"]
        await bot.send_message(uid,
            f"⚡ RECORDATORIO - PUEBLA\n\n"
            f"Folio: {folio}\nTiempo restante: {minutos_restantes} min\n"
            f"Monto: ${PRECIO}\n\n"
            f"📸 Envíe comprobante de pago.")
    except Exception:
        pass

async def iniciar_timer_36h(user_id: int, folio: str):
    async def timer_task():
        await asyncio.sleep(34.5 * 3600)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 90)
        await asyncio.sleep(30 * 60)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 60)
        await asyncio.sleep(30 * 60)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 30)
        await asyncio.sleep(20 * 60)
        if folio not in timers_activos: return
        await enviar_recordatorio(folio, 10)
        await asyncio.sleep(10 * 60)
        if folio in timers_activos:
            await eliminar_folio_automatico(folio)

    task = asyncio.create_task(timer_task())
    timers_activos[folio] = {"task": task, "user_id": user_id, "start_time": datetime.now()}
    user_folios.setdefault(user_id, []).append(folio)

def cancelar_timer_folio(folio: str) -> bool:
    if folio not in timers_activos: return False
    timers_activos[folio]["task"].cancel()
    uid = timers_activos[folio]["user_id"]
    del timers_activos[folio]
    if uid in user_folios and folio in user_folios[uid]:
        user_folios[uid].remove(folio)
        if not user_folios[uid]: del user_folios[uid]
    return True

def limpiar_timer_folio(folio: str):
    if folio not in timers_activos: return
    uid = timers_activos[folio]["user_id"]
    del timers_activos[folio]
    if uid in user_folios and folio in user_folios[uid]:
        user_folios[uid].remove(folio)
        if not user_folios[uid]: del user_folios[uid]

# ==================== FOLIOS ====================
FOLIO_NUM_PREFIJO = "P0"
_folio_counter = {"siguiente": 11223}
_folio_lock = asyncio.Lock()

def _leer_watermark() -> int | None:
    try:
        r = supabase.table("folio_watermark").select("ultimo_asignado").eq("prefijo", "PUE").execute()
        if r.data:
            return r.data[0]["ultimo_asignado"]
        return None
    except:
        return None

def _guardar_watermark(numero: int):
    try:
        supabase.table("folio_watermark").upsert({
            "prefijo": "PUE", "ultimo_asignado": numero
        }).execute()
    except:
        pass

def _inicializar_folio():
    watermark = _leer_watermark()
    if watermark is not None:
        _folio_counter["siguiente"] = watermark + 1
        return
    try:
        resp = supabase.table("folios_registrados").select("folio").eq("entidad", ENTIDAD).execute()
        numeros = []
        for row in resp.data or []:
            f = row.get("folio", "")
            if f.startswith(FOLIO_NUM_PREFIJO) and f[len(FOLIO_NUM_PREFIJO):].isdigit():
                numeros.append(int(f[len(FOLIO_NUM_PREFIJO):]))
        if numeros:
            maximo = max(numeros)
            _folio_counter["siguiente"] = maximo + 1
            _guardar_watermark(maximo)
        else:
            _folio_counter["siguiente"] = 1
    except:
        _folio_counter["siguiente"] = 1

def _folio_existe(folio: str) -> bool:
    try:
        r = supabase.table("folios_registrados").select("folio").eq("folio", folio).execute()
        return len(r.data) > 0
    except:
        return False

def _generar_folio_sync() -> str:
    candidato = _folio_counter["siguiente"]
    for _ in range(100_000):
        folio = f"{FOLIO_NUM_PREFIJO}{candidato:05d}"
        if not _folio_existe(folio):
            _folio_counter["siguiente"] = candidato + 1
            _guardar_watermark(candidato)
            return folio
        candidato += 1
    return f"{FOLIO_NUM_PREFIJO}{random.randint(10000, 99999)}"

async def generar_folio_async() -> str:
    async with _folio_lock:
        return await asyncio.to_thread(_generar_folio_sync)

# ==================== PDF ====================
def generar_pdf(datos: dict) -> str:
    out = os.path.join(OUTPUT_DIR, f"{datos['folio']}_puebla.pdf")
    try:
        if not os.path.exists(PLANTILLA):
            raise FileNotFoundError(f"❌ FALTA: {PLANTILLA}")
        
        doc = fitz.open(PLANTILLA)
        
        if len(doc) < 1:
            raise ValueError(f"❌ {PLANTILLA} debe tener al menos 1 página")
        
        pg_permiso = doc[0]
        
        # Generar cadena
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz)
        # Formato: FOLIO + OFICINA + TIMESTAMP + CONSTANTE
        cadena = f"{datos['folio']}ANGELOPOLIS{hoy.strftime('%Y%m%d%H%M%S')}2506445694706082025"
        
        # PÁGINA 1 - PERMISO (ÚNICA) - Fuente SEGURA: "helv"
        
        # Folio grande - 10 puntos a la izquierda
        pg_permiso.insert_text((210, 270), datos['folio'],
            fontsize=60, color=(0, 0, 0), fontname="helv")
        
        # Datos generales
        pg_permiso.insert_text((87, 312), datos['marca'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((300, 312), datos['linea'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((80, 340), datos['anio'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((585, 285), datos['motor'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((575, 255), datos['serie'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        
        # Combustible y cilindros (sin rúbulos, solo valores)
        pg_permiso.insert_text((390, 398), datos['cilindros'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        
        pg_permiso.insert_text((350, 428), datos['fecha_exp'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((585, 225), datos['fecha_ven'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        
        # QR - esquina superior izquierda, bajado 20 puntos
        qr = qrcode.QRCode()
        qr.add_data(f"{BASE_URL}/estado_folio/{datos['folio']}")
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buf = BytesIO()
        img_qr.save(buf, format="PNG")
        buf.seek(0)
        qr_pix = fitz.Pixmap(buf.read())
        pg_permiso.insert_image(
            fitz.Rect(50, 220, 140, 310),
            pixmap=qr_pix,
            overlay=True
        )
        
        # Cadena en la parte inferior (pequeña)
        pg_permiso.insert_text((50, 580), f"Cadena: {cadena}",
            fontsize=8, color=(0, 0, 0), fontname="helv")
        
        doc.save(out)
        doc.close()
        return out
        
    except Exception as e:
        print(f"❌ PDF ERROR: {e}")
        raise

# ==================== FSM ====================
class PermisoForm(StatesGroup):
    marca = State()
    linea = State()
    anio = State()
    serie = State()
    motor = State()
    color = State()
    nombre = State()
    combustible = State()
    cilindros = State()

# ==================== BOT ====================
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏛️ Sistema Digital de Permisos Puebla\n\n"
        f"💰 Costo: ${PRECIO} MXN\n"
        "⏰ Tiempo límite: 36 horas\n\n"
        "📋 Use /permiso para generar un permiso.")

@dp.message(Command("permiso"))
async def permiso_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"🚗 NUEVO PERMISO - PUEBLA\n\n"
        f"💰 Costo: ${PRECIO} MXN\n"
        f"⏰ Plazo: 36 horas\n\n"
        f"Paso 1/7: MARCA del vehículo:")
    await state.set_state(PermisoForm.marca)

@dp.message(PermisoForm.marca)
async def get_marca(message: types.Message, state: FSMContext):
    await state.update_data(marca=message.text.upper().strip())
    await message.answer("Paso 2/7: LÍNEA/MODELO:")
    await state.set_state(PermisoForm.linea)

@dp.message(PermisoForm.linea)
async def get_linea(message: types.Message, state: FSMContext):
    await state.update_data(linea=message.text.upper().strip())
    await message.answer("Paso 3/7: AÑO:")
    await state.set_state(PermisoForm.anio)

@dp.message(PermisoForm.anio)
async def get_anio(message: types.Message, state: FSMContext):
    await state.update_data(anio=message.text.strip())
    await message.answer("Paso 4/7: NÚMERO DE SERIE:")
    await state.set_state(PermisoForm.serie)

@dp.message(PermisoForm.serie)
async def get_serie(message: types.Message, state: FSMContext):
    await state.update_data(serie=message.text.upper().strip())
    await message.answer("Paso 5/7: NÚMERO DE MOTOR:")
    await state.set_state(PermisoForm.motor)

@dp.message(PermisoForm.motor)
async def get_motor(message: types.Message, state: FSMContext):
    await state.update_data(motor=message.text.upper().strip())
    await message.answer("Paso 6/7: COLOR:")
    await state.set_state(PermisoForm.color)

@dp.message(PermisoForm.color)
async def get_color(message: types.Message, state: FSMContext):
    await state.update_data(color=message.text.upper().strip())
    await message.answer("Paso 7/9: NOMBRE COMPLETO del titular:")
    await state.set_state(PermisoForm.nombre)

@dp.message(PermisoForm.nombre)
async def get_nombre(message: types.Message, state: FSMContext):
    await state.update_data(nombre=message.text.upper().strip())
    await message.answer("Paso 8/9: COMBUSTIBLE (ej: GASOLINA):")
    await state.set_state(PermisoForm.combustible)

@dp.message(PermisoForm.combustible)
async def get_combustible(message: types.Message, state: FSMContext):
    await state.update_data(combustible=message.text.upper().strip())
    await message.answer("Paso 9/9: CILINDROS CC O PBV:")
    await state.set_state(PermisoForm.cilindros)

@dp.message(PermisoForm.cilindros)
async def get_cilindros(message: types.Message, state: FSMContext):
    await state.update_data(cilindros=message.text.upper().strip())
    datos = await state.get_data()
    datos["folio"] = await generar_folio_async()
    
    tz = ZoneInfo(TZ)
    hoy = datetime.now(tz)
    ven = hoy + timedelta(days=30)
    
    # Meses en español
    meses_es = {
        1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
        5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
        9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE"
    }
    
    datos["fecha_exp"] = f"{hoy.day:02d} DE {meses_es[hoy.month]} {hoy.year}"
    datos["fecha_ven"] = f"{ven.day:02d} DE {meses_es[ven.month]} {ven.year}"
    
    await state.clear()
    await message.answer(f"🔄 Generando permiso {datos['folio']}...")
    
    try:
        pdf_path = await asyncio.to_thread(generar_pdf, datos)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔑 Validar Admin", callback_data=f"validar_{datos['folio']}"),
        ]])
        
        await bot.send_document(
            message.chat.id, FSInputFile(pdf_path),
            caption=(
                f"📄 PERMISO - PUEBLA\n"
                f"Folio: {datos['folio']}\n\n"
                f"⏰ TIMER ACTIVO (36 horas)"
            ),
            reply_markup=keyboard
        )
        
        hoy_iso = hoy.date().isoformat()
        ven_iso = ven.date().isoformat()
        
        supabase.table("folios_registrados").insert({
            "folio": datos['folio'],
            "marca": datos["marca"],
            "linea": datos["linea"],
            "anio": datos["anio"],
            "numero_serie": datos["serie"],
            "numero_motor": datos["motor"],
            "fecha_expedicion": hoy_iso,
            "fecha_vencimiento": ven_iso,
            "entidad": ENTIDAD,
            "estado": "PENDIENTE",
            "user_id": message.from_user.id,
            "username": message.from_user.username or "Sin username"
        }).execute()
        
        await iniciar_timer_36h(message.from_user.id, datos["folio"])
        
        await message.answer(
            f"💰 INSTRUCCIONES DE PAGO\n\n"
            f"📄 Folio: {datos['folio']}\n"
            f"💵 Monto: ${PRECIO} MXN\n"
            f"⏰ Tiempo límite: 36 horas\n\n"
            f"📸 Envíe su comprobante de pago (imagen).")
    
    except Exception as e:
        print(f"❌ ERROR: {e}")
        await message.answer(f"❌ Error: {e}\n\nUse /permiso para reintentar.")

@dp.callback_query(lambda c: c.data and c.data.startswith("validar_"))
async def callback_validar(callback: types.CallbackQuery):
    folio = callback.data.replace("validar_", "")
    if folio in timers_activos:
        uid = timers_activos[folio]["user_id"]
        cancelar_timer_folio(folio)
        supabase.table("folios_registrados").update({
            "estado": "VALIDADO_ADMIN",
            "fecha_comprobante": datetime.now().isoformat()
        }).eq("folio", folio).execute()
        await callback.answer("✅ Folio validado", show_alert=True)
        try:
            await bot.send_message(uid, f"✅ PAGO VALIDADO\n📄 Folio: {folio}\n\nPermiso activo.")
        except:
            pass

@dp.message()
async def fallback(message: types.Message):
    await message.answer("Use /permiso o /start")

# ==================== FASTAPI ====================
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_inicializar_folio)
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook", allowed_updates=["message", "callback_query"])
    print(f"✅ Bot Puebla iniciado")
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=True
)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    await dp.feed_webhook_update(bot, types.Update(**data))
    return {"ok": True}

# ==================== LOGIN ADMIN ====================

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if request.session.get("admin"):
        return RedirectResponse("/admin", status_code=302)

    error = request.query_params.get("error")

    error_html = """
    <div class="error">
        Usuario o contraseña incorrectos
    </div>
    """ if error else ""

    return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="es">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
    Acceso administrativo
</title>

<link
    rel="icon"
    href="https://smt.puebla.gob.mx/templates/puebla/favicon.ico"
    type="image/vnd.microsoft.icon"
>

<style>

:root {{
    --vino:#5f1b2d;
    --azul:#001B4C;
    --dorado:#c79b66;
    --gris:#949494;
}}

* {{
    box-sizing:border-box;
}}

html,
body {{
    margin:0;
    min-height:100%;
}}

body {{
    font-family:
        Arial,
        Helvetica,
        sans-serif;

    background:
        linear-gradient(
            135deg,
            #5f1b2d,
            #001B4C
        );
}}


/* ================================
   HEADER
================================ */

.header {{
    background:white;

    box-shadow:
        0 2px 8px
        rgba(0,0,0,0.1);
}}

.header-inner {{
    max-width:1380px;

    margin:auto;

    padding:
        18px 30px;

    display:flex;

    align-items:center;

    justify-content:
        space-between;

    gap:30px;
}}

.logos {{
    display:flex;

    align-items:center;

    gap:22px;
}}

.logo-gob {{
    width:245px;

    max-height:82px;

    object-fit:contain;
}}

.logo-secretaria {{
    width:225px;

    max-height:88px;

    object-fit:contain;
}}

.frase {{
    width:300px;

    max-height:90px;

    object-fit:contain;
}}


/* ================================
   BARRA
================================ */

.barra {{
    height:14px;

    background:
        var(--vino);
}}


/* ================================
   LOGIN
================================ */

.login-area {{
    min-height:
        calc(100vh - 130px);

    display:flex;

    align-items:center;

    justify-content:center;

    padding:
        55px 20px;
}}

.card {{
    width:100%;

    max-width:520px;

    background:white;

    padding:
        46px 44px;

    border-radius:24px;

    box-shadow:
        0 18px 60px
        rgba(0,0,0,0.26);
}}

.card h1 {{
    margin:
        0 0 8px;

    text-align:center;

    color:
        var(--azul);

    font-size:
        34px;

    font-weight:
        400;
}}

.subtitulo {{
    text-align:center;

    color:
        var(--gris);

    font-size:
        18px;

    margin-bottom:
        34px;
}}

label {{
    display:block;

    margin:
        18px 0
        8px;

    font-size:
        17px;

    font-weight:
        600;

    color:
        #555;
}}

input {{
    width:100%;

    padding:
        14px 16px;

    border:
        1px solid
        #d5d5d5;

    border-radius:
        10px;

    font-size:
        17px;

    outline:none;
}}

input:focus {{
    border-color:
        var(--dorado);

    box-shadow:
        0 0 0
        3px
        rgba(199,155,102,0.16);
}}

button {{
    width:100%;

    margin-top:
        26px;

    padding:
        14px;

    border:0;

    border-radius:
        10px;

    background:
        var(--dorado);

    color:white;

    font-size:
        18px;

    font-weight:
        700;

    cursor:pointer;
}}

button:hover {{
    background:
        #b8894e;
}}

.volver {{
    text-align:center;

    margin-top:
        24px;
}}

.volver a {{
    color:
        var(--azul);

    text-decoration:none;

    font-size:
        16px;
}}

.error {{
    background:
        #f8d7da;

    color:
        #721c24;

    border:
        1px solid
        #e7abb1;

    border-radius:
        9px;

    padding:
        12px;

    margin-bottom:
        20px;

    text-align:center;

    font-size:
        14px;
}}


/* ================================
   CELULAR
================================ */

@media
(max-width:700px) {{

    .header-inner {{
        padding:
            14px 15px;

        gap:
            10px;
    }}

    .logos {{
        gap:
            8px;

        flex:1;
    }}

    .logo-gob {{
        width:
            52%;
    }}

    .logo-secretaria {{
        width:
            46%;
    }}

    .frase {{
        display:none;
    }}

    .login-area {{
        padding:
            40px 18px;
    }}

    .card {{
        padding:
            38px 30px;

        border-radius:
            20px;
    }}

    .card h1 {{
        font-size:
            29px;
    }}

    .subtitulo {{
        font-size:
            16px;
    }}
}}

</style>

</head>


<body>


<header class="header">

    <div class="header-inner">

        <div class="logos">

            <a
                href="https://puebla.gob.mx/"
                target="_blank"
                rel="noopener"
            >

                <img
                    class="logo-gob"

                    src="
https://smt.puebla.gob.mx/templates/puebla/images/header/logo_puebla_gob.svg
                    "

                    alt="
Gobierno del Estado de Puebla
                    "
                >

            </a>


            <img
                class="logo-secretaria"

                src="
https://smt.puebla.gob.mx/images/headers/MOVILIDAD_02.png
                "

                alt="
Secretaría de Movilidad y Transporte
                "
            >

        </div>


        <img
            class="frase"

            src="
https://smt.puebla.gob.mx/templates/puebla/images/header/puebla_frases_gob.svg
            "

            alt="Puebla"
        >

    </div>

</header>


<div class="barra"></div>


<main class="login-area">

    <div class="card">

        <h1>
            Panel Administrativo
        </h1>

        <div class="subtitulo">
            Sistema Puebla
        </div>


        {error_html}


        <form
            method="post"
            action="/login"
        >

            <label
                for="username"
            >
                Usuario
            </label>

            <input
                id="username"
                type="text"
                name="username"
                autocomplete="username"
                required
            >


            <label
                for="password"
            >
                Contraseña
            </label>

            <input
                id="password"
                type="password"
                name="password"
                autocomplete="current-password"
                required
            >


            <button
                type="submit"
            >
                Ingresar
            </button>

        </form>


        <div class="volver">

            <a href="/">
                ← Volver
            </a>

        </div>

    </div>

</main>


</body>
</html>
""")

# ==================== PANEL ADMIN ====================

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):

    if not request.session.get("admin"):
        return RedirectResponse("/login", status_code=302)

    try:
        resp = (
            supabase
            .table("folios_registrados")
            .select("folio,fecha_vencimiento,estado")
            .eq("entidad", ENTIDAD)
            .execute()
        )

        registros = resp.data or []

    except Exception as e:
        print(f"[ADMIN] Error leyendo folios: {e}")
        registros = []

    total = len(registros)

    hoy = datetime.now(ZoneInfo(TZ)).date()

    vigentes = 0
    vencidos = 0

    for row in registros:
        try:
            fv = datetime.fromisoformat(
                str(row["fecha_vencimiento"]).replace("Z", "+00:00")
            ).date()

            if hoy <= fv:
                vigentes += 1
            else:
                vencidos += 1

        except Exception:
            pass

    timers = len(timers_activos)

    siguiente = f"{FOLIO_NUM_PREFIJO}{_folio_counter['siguiente']}"

    username = html_lib.escape(
        str(request.session.get("username", "Admin"))
    )

    return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="es">
<head>

<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<title>Panel Administrativo Puebla</title>

<style>

:root {{
    --vino:#5f1b2d;
    --vino2:#48101e;
    --azul:#001B4C;
    --dorado:#c79b66;
    --fondo:#f4f5f7;
}}

* {{
    box-sizing:border-box;
}}

body {{
    margin:0;
    background:var(--fondo);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
    color:#495057;
}}

.layout {{
    min-height:100vh;
    display:grid;
    grid-template-columns:250px 1fr;
}}

.sidebar {{
    background:var(--vino);
    color:white;
    padding:24px 17px;
}}

.brand {{
    padding:0 10px 23px;
    border-bottom:1px solid rgba(255,255,255,0.18);
    margin-bottom:20px;
}}

.brand h2 {{
    margin:0;
    font-size:1.25rem;
}}

.brand p {{
    margin:5px 0 0;
    opacity:.72;
    font-size:.8rem;
}}

.menu {{
    display:flex;
    flex-direction:column;
    gap:6px;
}}

.menu a {{
    color:white;
    text-decoration:none;
    padding:12px 13px;
    border-radius:8px;
    font-size:.92rem;
}}

.menu a:hover,
.menu a.active {{
    background:rgba(255,255,255,0.14);
}}

.logout {{
    margin-top:12px;
    background:rgba(0,0,0,0.15);
}}

.main {{
    min-width:0;
}}

.topbar {{
    background:white;
    min-height:72px;
    padding:0 28px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    box-shadow:0 2px 8px rgba(0,0,0,0.05);
}}

.topbar h1 {{
    margin:0;
    color:var(--azul);
    font-size:1.35rem;
}}

.user {{
    color:#777;
    font-size:.9rem;
}}

.content {{
    padding:30px;
}}

.stats {{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:20px;
    margin-bottom:28px;
}}

.stat {{
    background:white;
    border-radius:14px;
    padding:24px;
    box-shadow:0 3px 12px rgba(0,0,0,0.06);
}}

.stat-label {{
    color:#888;
    font-size:.77rem;
    font-weight:700;
    text-transform:uppercase;
    margin-bottom:10px;
}}

.stat-value {{
    color:var(--azul);
    font-size:2rem;
    font-weight:700;
}}

.stat-sub {{
    margin-top:5px;
    color:#999;
    font-size:.8rem;
}}

.columns {{
    display:grid;
    grid-template-columns:1.3fr 1fr;
    gap:20px;
}}

.card {{
    background:white;
    border-radius:14px;
    padding:25px;
    box-shadow:0 3px 12px rgba(0,0,0,0.06);
}}

.card h2 {{
    margin:0 0 20px;
    color:var(--azul);
    font-size:1.1rem;
}}

.actions {{
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:12px;
}}

.action {{
    text-decoration:none;
    color:#555;
    border:1px solid #ececec;
    border-radius:10px;
    padding:17px;
    transition:.2s;
}}

.action:hover {{
    border-color:var(--dorado);
    transform:translateY(-1px);
}}

.action strong {{
    display:block;
    color:var(--azul);
    margin-bottom:4px;
}}

.action span {{
    color:#888;
    font-size:.8rem;
}}

.status {{
    padding:11px 0;
    border-bottom:1px solid #eee;
    display:flex;
    justify-content:space-between;
    gap:20px;
}}

.status:last-child {{
    border-bottom:0;
}}

.ok {{
    color:#198754;
    font-weight:650;
}}

@media(max-width:900px) {{
    .layout {{
        grid-template-columns:1fr;
    }}

    .sidebar {{
        display:none;
    }}

    .stats {{
        grid-template-columns:repeat(2,1fr);
    }}

    .columns {{
        grid-template-columns:1fr;
    }}
}}

@media(max-width:550px) {{
    .content {{
        padding:18px;
    }}

    .stats {{
        grid-template-columns:1fr;
    }}

    .actions {{
        grid-template-columns:1fr;
    }}
}}

</style>

</head>

<body>

<div class="layout">

<aside class="sidebar">

    <div class="brand">
        <h2>Panel Puebla</h2>
        <p>Administración del sistema</p>
    </div>

    <nav class="menu">

        <a href="/admin" class="active">
            📊 Dashboard
        </a>

        <a href="/admin/folios">
            📄 Folios
        </a>

        <a href="/admin/crear_folio">
            ➕ Crear folio
        </a>

        <a href="/admin/usuarios">
            👥 Usuarios terceros
        </a>

        <a href="/admin/tablas">
            🗄️ Tablas
        </a>

        <a href="/admin/auditoria">
            🧾 Auditoría
        </a>

        <a href="/logout" class="logout">
            🚪 Salir
        </a>

    </nav>

</aside>


<section class="main">

<header class="topbar">

    <h1>Dashboard</h1>

    <div class="user">
        👤 {username}
    </div>

</header>


<main class="content">

<div class="stats">

    <div class="stat">
        <div class="stat-label">Folios registrados</div>
        <div class="stat-value">{total}</div>
        <div class="stat-sub">Puebla</div>
    </div>

    <div class="stat">
        <div class="stat-label">Vigentes</div>
        <div class="stat-value">{vigentes}</div>
        <div class="stat-sub">Dentro de vigencia</div>
    </div>

    <div class="stat">
        <div class="stat-label">Vencidos</div>
        <div class="stat-value">{vencidos}</div>
        <div class="stat-sub">Fuera de vigencia</div>
    </div>

    <div class="stat">
        <div class="stat-label">Timers activos</div>
        <div class="stat-value">{timers}</div>
        <div class="stat-sub">Pendientes de pago</div>
    </div>

</div>


<div class="columns">

<section class="card">

    <h2>Accesos rápidos</h2>

    <div class="actions">

        <a class="action" href="/admin/crear_folio">
            <strong>➕ Crear permiso</strong>
            <span>Generar un folio manualmente</span>
        </a>

        <a class="action" href="/admin/folios">
            <strong>📄 Administrar folios</strong>
            <span>Consultar, editar y eliminar</span>
        </a>

        <a class="action" href="/admin/usuarios">
            <strong>👥 Usuarios terceros</strong>
            <span>Cuentas y paquetes de folios</span>
        </a>

        <a class="action" href="/admin/tablas">
            <strong>🗄️ Tablas Supabase</strong>
            <span>Consultar datos del sistema</span>
        </a>

    </div>

</section>


<section class="card">

    <h2>Estado del sistema</h2>

    <div class="status">
        <span>Supabase</span>
        <span class="ok">● Conectado</span>
    </div>

    <div class="status">
        <span>Telegram bot</span>
        <span class="ok">● Configurado</span>
    </div>

    <div class="status">
        <span>Entidad</span>
        <span>{ENTIDAD.upper()}</span>
    </div>

    <div class="status">
        <span>Precio</span>
        <span>${PRECIO} MXN</span>
    </div>

    <div class="status">
        <span>Siguiente folio</span>
        <span>{siguiente}</span>
    </div>

</section>

</div>

</main>

</section>

</div>

</body>
</html>
""")

@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    if username == ADMIN_USER and password == ADMIN_PASS:

        request.session.clear()

        request.session["admin"] = True
        request.session["username"] = ADMIN_USER

        return RedirectResponse(
            "/admin",
            status_code=303
        )

    return RedirectResponse(
        "/login?error=1",
        status_code=303
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()

    return RedirectResponse(
        "/login",
        status_code=302
    )


@app.get("/", response_class=HTMLResponse)
async def root():
    html = r"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Consulta de Permisos Vehiculares</title>

    <style>
        :root {
            --vino: #5f1b2d;
            --azul: #001B4C;
            --dorado: #c79b66;
            --gris: #949494;
            --fondo: #f5f5f5;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        html, body {
            min-height: 100%;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                         "Helvetica Neue", Arial, sans-serif;
            background: var(--fondo);
            color: #495057;
            display: flex;
            flex-direction: column;
        }

        .header {
            background: #fff;
            box-shadow: 0 2px 7px rgba(0,0,0,0.1);
            flex-shrink: 0;
        }

        .header-top {
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px 25px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 25px;
        }

        .brand h1 {
            color: var(--azul);
            font-size: 1.55rem;
            font-weight: 400;
        }

        .brand p {
            color: var(--gris);
            margin-top: 5px;
            font-size: .92rem;
        }

        .nav {
            background: var(--vino);
            color: white;
        }

        .nav-inner {
            max-width: 1200px;
            margin: auto;
            padding: 12px 25px;
            display: flex;
            gap: 25px;
            font-size: .95rem;
        }

        .nav span {
            opacity: .95;
        }

        .hero {
            background:
                linear-gradient(135deg,
                rgba(95,27,45,0.96),
                rgba(0,27,76,0.92));
            color: white;
            padding: 55px 20px;
            text-align: center;
        }

        .hero h2 {
            font-size: 2.25rem;
            font-weight: 300;
            margin-bottom: 10px;
        }

        .hero p {
            opacity: .9;
            font-size: 1rem;
        }

        .main-content {
            flex: 1;
            width: 100%;
            padding: 45px 20px 60px;
        }

        .consulta-section {
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 6px 25px rgba(0,0,0,0.09);
            max-width: 950px;
            margin: -75px auto 0;
            position: relative;
        }

        .consulta-titulo {
            font-size: 1.85rem;
            color: var(--azul);
            text-align: center;
            margin-bottom: 10px;
            font-weight: 400;
        }

        .consulta-subtitulo {
            color: var(--gris);
            text-align: center;
            margin-bottom: 35px;
        }

        .formulario-consulta {
            display: grid;
            grid-template-columns: 1.2fr 1fr 1fr auto;
            gap: 15px;
            align-items: flex-end;
        }

        .form-group {
            display: flex;
            flex-direction: column;
        }

        .form-group label {
            font-size: .90rem;
            font-weight: 600;
            color: #555;
            margin-bottom: 8px;
        }

        .form-group input {
            width: 100%;
            padding: 13px 14px;
            border: 1px solid #d6d6d6;
            border-radius: 8px;
            font-size: 1rem;
            background: white;
        }

        .form-group input:focus {
            outline: none;
            border-color: var(--dorado);
            box-shadow: 0 0 0 3px rgba(199,155,102,0.15);
        }

        .btn-consultar {
            min-height: 47px;
            padding: 12px 28px;
            background: var(--dorado);
            color: white;
            border: 0;
            border-radius: 8px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: .2s ease;
        }

        .btn-consultar:hover {
            background: #ae814c;
            transform: translateY(-1px);
        }

        .loading {
            display: none;
            text-align: center;
            padding: 35px;
        }

        .loading.active {
            display: block;
        }

        .spinner {
            border: 4px solid #eee;
            border-top: 4px solid var(--dorado);
            border-radius: 50%;
            width: 44px;
            height: 44px;
            animation: spin .8s linear infinite;
            margin: 0 auto;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .resultado-container {
            margin-top: 35px;
            display: none;
        }

        .resultado-container.visible {
            display: block;
        }

        .estado-folio {
            text-align: center;
            padding: 18px;
            border-radius: 12px;
            font-size: 1.08rem;
            font-weight: 600;
            margin-bottom: 20px;
        }

        .estado-folio.vigente {
            background: #d4edda;
            color: #155724;
            border: 2px solid #28a745;
        }

        .estado-folio.vencido {
            background: #fff3cd;
            color: #856404;
            border: 2px solid #ffc107;
        }

        .estado-folio.no-existe {
            background: #f8d7da;
            color: #721c24;
            border: 2px solid #dc3545;
        }

        .resultado-tabla {
            background: #f7f7f7;
            border-radius: 14px;
            padding: 28px;
        }

        .resultado-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }

        .resultado-item {
            background: white;
            border-radius: 9px;
            padding: 15px 16px;
            border-left: 4px solid var(--dorado);
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }

        .resultado-label {
            font-size: .76rem;
            color: var(--gris);
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: .04em;
            margin-bottom: 5px;
        }

        .resultado-valor {
            font-size: 1.05rem;
            color: var(--azul);
            font-weight: 500;
            word-break: break-word;
        }

        .info {
            max-width: 950px;
            margin: 28px auto 0;
            background: white;
            border-radius: 14px;
            padding: 22px 28px;
            color: #666;
            box-shadow: 0 3px 14px rgba(0,0,0,0.06);
        }

        .info strong {
            color: var(--azul);
        }

        .footer {
            margin-top: auto;
            background: var(--vino);
            color: #fffbea;
            padding: 35px 20px;
            text-align: center;
        }

        .footer p {
            opacity: .92;
            line-height: 1.6;
        }

        @media (max-width: 800px) {
            .header-top {
                padding: 19px 18px;
            }

            .nav-inner {
                padding: 10px 18px;
            }

            .hero {
                padding: 45px 18px 80px;
            }

            .hero h2 {
                font-size: 1.7rem;
            }

            .consulta-section {
                padding: 26px 18px;
                margin-top: -65px;
            }

            .formulario-consulta {
                grid-template-columns: 1fr;
            }

            .btn-consultar {
                width: 100%;
            }

            .resultado-grid {
                grid-template-columns: 1fr;
            }

            .resultado-tabla {
                padding: 18px;
            }
        }
    </style>
</head>

<body>

<header class="header">
    <div class="header-top">
        <div class="brand">
            <h1>Sistema Digital de Consulta Vehicular</h1>
            <p>Consulta electrónica de permisos registrados</p>
        </div>
    </div>

    <div class="nav">
        <div class="nav-inner">
            <span>Consulta de folios</span>
        </div>
    </div>
</header>

<section class="hero">
    <h2>Consulta de Permisos Vehiculares</h2>
    <p>Ingresa los datos correspondientes para consultar el registro.</p>
</section>

<main class="main-content">

    <section class="consulta-section">

        <h2 class="consulta-titulo">Consulta de permiso</h2>

        <p class="consulta-subtitulo">
            Ingresa el folio para consultar los datos registrados.
        </p>

        <form
            class="formulario-consulta"
            id="formularioConsulta"
            onsubmit="buscar(event)"
        >

            <div class="form-group">
                <label for="folio">Folio</label>
                <input
                    type="text"
                    id="folio"
                    placeholder="722000001"
                    autocomplete="off"
                    required
                >
            </div>

            <div class="form-group">
                <label for="placa">Placa</label>
                <input
                    type="text"
                    id="placa"
                    placeholder="ABC-1234"
                    autocomplete="off"
                >
            </div>

            <div class="form-group">
                <label for="serie">Serie / VIN</label>
                <input
                    type="text"
                    id="serie"
                    placeholder="Número de serie"
                    autocomplete="off"
                >
            </div>

            <div class="form-group">
                <button
                    type="submit"
                    class="btn-consultar"
                >
                    Consultar
                </button>
            </div>

        </form>

        <div class="loading" id="loading">
            <div class="spinner"></div>
            <p style="margin-top:12px;color:#949494">
                Consultando información...
            </p>
        </div>

        <div class="resultado-container" id="resultado">

            <div
                class="estado-folio"
                id="estado"
            ></div>

            <div class="resultado-tabla">

                <div class="resultado-grid">

                    <div class="resultado-item">
                        <div class="resultado-label">Folio</div>
                        <div class="resultado-valor" id="resF">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Expedición</div>
                        <div class="resultado-valor" id="resExp">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Vencimiento</div>
                        <div class="resultado-valor" id="resVen">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Marca</div>
                        <div class="resultado-valor" id="resMar">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Línea</div>
                        <div class="resultado-valor" id="resLin">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Año</div>
                        <div class="resultado-valor" id="resAno">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Serie</div>
                        <div class="resultado-valor" id="resSer">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Motor</div>
                        <div class="resultado-valor" id="resMot">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Color</div>
                        <div class="resultado-valor" id="resCol">—</div>
                    </div>

                    <div class="resultado-item">
                        <div class="resultado-label">Titular</div>
                        <div class="resultado-valor" id="resPro">—</div>
                    </div>

                </div>
            </div>
        </div>

    </section>

    <div class="info">
        <strong>Consulta electrónica:</strong>
        la información mostrada corresponde a los registros almacenados
        en este sistema.
    </div>

</main>

<footer class="footer">
    <p>
        Sistema Digital de Consulta Vehicular<br>
        Consulta electrónica de registros
    </p>
</footer>

<script>
async function buscar(e) {
    e.preventDefault();

    const folioInput =
        document.getElementById("folio");

    const folio =
        folioInput.value
            .toUpperCase()
            .trim();

    const loading =
        document.getElementById("loading");

    const resultado =
        document.getElementById("resultado");

    loading.classList.add("active");
    resultado.classList.remove("visible");

    try {

        const res =
            await fetch(
                `/api/consultar_folio/${encodeURIComponent(folio)}`
            );

        const data =
            await res.json();

        loading.classList.remove("active");

        if (!data.ok) {
            mostrar_no_existe(folio);
            return;
        }

        const estado =
            document.getElementById("estado");

        if (data.vigente) {

            estado.className =
                "estado-folio vigente";

            estado.innerHTML =
                `✓ <strong>${esc(data.folio)}</strong> está vigente`;

        } else {

            estado.className =
                "estado-folio vencido";

            estado.innerHTML =
                `⚠ <strong>${esc(data.folio)}</strong> está vencido`;
        }

        poner("resF",   data.folio);
        poner("resExp", data.fecha_expedicion);
        poner("resVen", data.fecha_vencimiento);
        poner("resMar", data.marca);
        poner("resLin", data.linea);
        poner("resAno", data.anio);
        poner("resSer", data.numero_serie);
        poner("resMot", data.numero_motor);
        poner("resCol", data.color);
        poner("resPro", data.nombre);

        resultado.classList.add("visible");

    } catch (error) {

        loading.classList.remove("active");

        const estado =
            document.getElementById("estado");

        estado.className =
            "estado-folio no-existe";

        estado.textContent =
            "No fue posible realizar la consulta.";

        resultado.classList.add("visible");

        console.error(error);
    }
}


function poner(id, valor) {

    document.getElementById(id).textContent =
        valor || "—";
}


function mostrar_no_existe(folio) {

    const estado =
        document.getElementById("estado");

    estado.className =
        "estado-folio no-existe";

    estado.innerHTML =
        `✗ El folio <strong>${esc(folio)}</strong> no fue encontrado`;

    [
        "resF",
        "resExp",
        "resVen",
        "resMar",
        "resLin",
        "resAno",
        "resSer",
        "resMot",
        "resCol",
        "resPro"

    ].forEach(id => {

        document
            .getElementById(id)
            .textContent = "—";
    });

    document
        .getElementById("resultado")
        .classList
        .add("visible");
}


function esc(valor) {

    return String(valor || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}
</script>

</body>
</html>"""

    return HTMLResponse(content=html)

@app.get("/estado_folio/{folio}", response_class=HTMLResponse)
async def estado_folio_qr(folio: str):
    folio = folio.strip().upper()

    def esc(valor):
        return html_lib.escape(str(valor if valor is not None else "—"))

    try:
        res = (
            supabase.table("folios_registrados")
            .select("*")
            .eq("folio", folio)
            .eq("entidad", ENTIDAD)
            .limit(1)
            .execute()
        )

        # ==========================================================
        # FOLIO NO ENCONTRADO
        # ==========================================================
        if not res.data:
            estado_html = f"""
            <div class="resultado-box">
                <div class="estado no-encontrado">
                    <div class="estado-icono">✕</div>
                    <div>
                        <strong>FOLIO NO ENCONTRADO</strong>
                        <span>El folio {esc(folio)} no se encuentra registrado.</span>
                    </div>
                </div>
            </div>
            """

        else:
            r = res.data[0]

            tz = ZoneInfo(TZ)
            hoy = datetime.now(tz).date()

            fecha_exp = datetime.fromisoformat(
                str(r["fecha_expedicion"]).replace("Z", "+00:00")
            ).date()

            fecha_ven = datetime.fromisoformat(
                str(r["fecha_vencimiento"]).replace("Z", "+00:00")
            ).date()

            vigente = hoy <= fecha_ven

            if vigente:
                estado_clase = "vigente"
                estado_icono = "✓"
                estado_titulo = "PERMISO VIGENTE"
                estado_subtitulo = "El permiso se encuentra dentro de su periodo de vigencia."
            else:
                estado_clase = "vencido"
                estado_icono = "!"
                estado_titulo = "PERMISO VENCIDO"
                estado_subtitulo = "El periodo de vigencia de este permiso ha concluido."

            estado_html = f"""
            <div class="resultado-box">

                <div class="estado {estado_clase}">
                    <div class="estado-icono">{estado_icono}</div>
                    <div>
                        <strong>{estado_titulo}</strong>
                        <span>{estado_subtitulo}</span>
                    </div>
                </div>

                <div class="folio-principal">
                    <div class="folio-label">FOLIO</div>
                    <div class="folio-numero">{esc(folio)}</div>
                </div>

                <div class="separador"></div>

                <h2 class="titulo-seccion">
                    Información del permiso
                </h2>

                <div class="datos-grid">

                    <div class="dato">
                        <div class="dato-label">Folio</div>
                        <div class="dato-valor">{esc(folio)}</div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Contribuyente</div>
                        <div class="dato-valor">
                            {esc(r.get("contribuyente", "—"))}
                        </div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Marca</div>
                        <div class="dato-valor">
                            {esc(r.get("marca", "—"))}
                        </div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Línea / Modelo</div>
                        <div class="dato-valor">
                            {esc(r.get("linea", "—"))}
                        </div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Año</div>
                        <div class="dato-valor">
                            {esc(r.get("anio", "—"))}
                        </div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Color</div>
                        <div class="dato-valor">
                            {esc(r.get("color", "—"))}
                        </div>
                    </div>

                    <div class="dato dato-ancho">
                        <div class="dato-label">
                            Número de Identificación Vehicular / Serie
                        </div>
                        <div class="dato-valor mono">
                            {esc(r.get("numero_serie", "—"))}
                        </div>
                    </div>

                    <div class="dato dato-ancho">
                        <div class="dato-label">
                            Número de motor
                        </div>
                        <div class="dato-valor mono">
                            {esc(r.get("numero_motor", "—"))}
                        </div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Fecha de expedición</div>
                        <div class="dato-valor">
                            {fecha_exp.strftime("%d/%m/%Y")}
                        </div>
                    </div>

                    <div class="dato">
                        <div class="dato-label">Fecha de vencimiento</div>
                        <div class="dato-valor">
                            {fecha_ven.strftime("%d/%m/%Y")}
                        </div>
                    </div>

                </div>

                <div class="vigencia-nota">
                    <strong>Estado de vigencia:</strong>
                    La información presentada corresponde al registro
                    asociado al folio consultado.
                </div>

            </div>
            """

        # ==========================================================
        # HTML COMPLETO
        # ==========================================================
        year = datetime.now(ZoneInfo(TZ)).year

        pagina = f"""<!DOCTYPE html>
<html lang="es">

<head>

    <meta charset="utf-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <meta
        name="description"
        content="Consulta de permiso vehicular"
    >

    <title>
        Secretaría de Movilidad y Transporte - Consulta
    </title>

    <link
        rel="icon"
        href="https://smt.puebla.gob.mx/templates/puebla/favicon.ico"
        type="image/vnd.microsoft.icon"
    >

    <style>

        :root {{
            --vino: #5f1b2d;
            --vino-oscuro: #48101e;
            --dorado: #c09761;
            --dorado-claro: #c79b66;
            --gris: #949494;
            --gris2: #b2b2b2;
            --gris-claro: #f6f6f6;
            --azul: #001B4C;
            --blanco: #ffffff;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        html {{
            min-height: 100%;
            background: #f4f4f4;
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            background: #f4f4f4;
            color: #555;
            font-family:
                Arial,
                Helvetica,
                sans-serif;
        }}

        img {{
            max-width: 100%;
            height: auto;
        }}

        /* =====================================================
           HEADER
           ===================================================== */

        .header {{
            background: #fff;
            position: relative;
            z-index: 10;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}

        .header-inner {{
            max-width: 1380px;
            margin: 0 auto;
            padding: 18px 30px;

            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 30px;
        }}

        .logos {{
            display: flex;
            align-items: center;
            gap: 22px;
            min-width: 0;
        }}

        .logo-gob {{
            width: 245px;
            max-height: 82px;
            object-fit: contain;
        }}

        .logo-secretaria {{
            width: 225px;
            max-height: 88px;
            object-fit: contain;
        }}

        .frase-header {{
            width: 300px;
            max-height: 90px;
            object-fit: contain;
        }}

        /* =====================================================
           MENU
           ===================================================== */

        .menu {{
            background: var(--vino);
        }}

        .menu-inner {{
            max-width: 1380px;
            margin: auto;
            min-height: 52px;
            padding: 0 30px;

            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 12px;
        }}

        .menu a {{
            color: white;
            text-decoration: none;
            font-size: 15px;
            padding: 17px 19px;
            transition: background .2s ease;
        }}

        .menu a:hover {{
            background: rgba(255,255,255,0.1);
        }}

        /* =====================================================
           HERO
           ===================================================== */

        .hero {{
            position: relative;
            overflow: hidden;
            background:
                linear-gradient(
                    120deg,
                    #f8f8f8 0%,
                    #f4f4f4 65%,
                    #eee 100%
                );

            border-bottom: 1px solid #e4e4e4;

            padding:
                50px 20px
                90px;
        }}

        .hero::after {{
            content: "";
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 7px;
            background: var(--dorado);
        }}

        .hero-inner {{
            max-width: 1050px;
            margin: auto;
            text-align: center;
        }}

        .hero h1 {{
            color: var(--vino);
            font-size: 34px;
            font-weight: 400;
            margin-bottom: 9px;
        }}

        .hero p {{
            color: var(--gris);
            font-size: 17px;
        }}

        /* =====================================================
           RESULTADO
           ===================================================== */

        .contenido {{
            padding:
                0 20px
                60px;
        }}

        .resultado-box {{
            position: relative;
            z-index: 2;

            width: 100%;
            max-width: 1000px;

            margin:
                -55px auto
                40px;

            background: white;

            border-radius: 24px;

            padding:
                38px 40px
                42px;

            box-shadow:
                0 8px 32px
                rgba(0,0,0,0.11);
        }}

        .estado {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 15px;

            border-radius: 14px;

            padding: 18px 25px;

            margin-bottom: 30px;
        }}

        .estado-icono {{
            width: 46px;
            height: 46px;
            flex: 0 0 46px;

            border-radius: 50%;

            display: flex;
            align-items: center;
            justify-content: center;

            font-size: 25px;
            font-weight: bold;
        }}

        .estado strong {{
            display: block;
            font-size: 17px;
            margin-bottom: 3px;
        }}

        .estado span {{
            display: block;
            font-size: 14px;
            font-weight: normal;
        }}

        .vigente {{
            color: #155724;
            background: #e6f4e8;
            border: 1px solid #b9dfbf;
        }}

        .vigente .estado-icono {{
            color: white;
            background: #38934d;
        }}

        .vencido {{
            color: #856404;
            background: #fff7dc;
            border: 1px solid #f0d98a;
        }}

        .vencido .estado-icono {{
            color: white;
            background: #d59e16;
        }}

        .no-encontrado {{
            color: #721c24;
            background: #f8d7da;
            border: 1px solid #e7abb1;
        }}

        .no-encontrado .estado-icono {{
            color: white;
            background: #b72f3c;
        }}

        .folio-principal {{
            text-align: center;
            margin:
                10px 0
                27px;
        }}

        .folio-label {{
            color: var(--gris);
            font-size: 12px;
            font-weight: bold;
            letter-spacing: 3px;
            margin-bottom: 7px;
        }}

        .folio-numero {{
            color: var(--vino);
            font-size: 30px;
            font-weight: 600;
            letter-spacing: 1px;
        }}

        .separador {{
            width: 100%;
            height: 1px;
            background: #e9e9e9;
            margin: 0 0 28px;
        }}

        .titulo-seccion {{
            color: var(--vino);
            font-size: 22px;
            font-weight: 400;
            margin-bottom: 22px;
        }}

        .datos-grid {{
            display: grid;

            grid-template-columns:
                repeat(2, minmax(0, 1fr));

            gap: 15px;
        }}

        .dato {{
            background: var(--gris-claro);
            border-radius: 12px;
            padding: 16px 18px;

            border-left:
                4px solid
                var(--dorado-claro);
        }}

        .dato-ancho {{
            grid-column: auto;
        }}

        .dato-label {{
            color: var(--gris);
            font-size: 11px;
            font-weight: bold;

            text-transform: uppercase;

            letter-spacing: .8px;

            margin-bottom: 6px;
        }}

        .dato-valor {{
            color: #484848;
            font-size: 16px;
            font-weight: 500;
            line-height: 1.35;
            overflow-wrap: anywhere;
        }}

        .mono {{
            font-family:
                "Courier New",
                monospace;
            letter-spacing: .3px;
        }}

        .vigencia-nota {{
            background: #faf7f3;
            border-left: 4px solid var(--dorado);
            margin-top: 25px;

            padding:
                16px 18px;

            color: #686868;
            font-size: 13px;
            line-height: 1.55;
        }}

        .vigencia-nota strong {{
            color: var(--vino);
        }}

        /* =====================================================
           CONTACTO
           ===================================================== */

        .contacto {{
            max-width: 1180px;
            margin: 35px auto;

            background: white;

            border-radius: 22px;

            padding:
                24px 30px;

            box-shadow:
                0 4px 18px
                rgba(0,0,0,0.07);
        }}

        .contacto-grid {{
            display: grid;

            grid-template-columns:
                1fr 1fr 1.4fr;

            align-items: center;

            gap: 30px;
        }}

        .contacto-item {{
            color: var(--gris);
            font-size: 14px;
            line-height: 1.6;
        }}

        .contacto-item strong {{
            display: block;
            color: var(--vino);
            margin-bottom: 4px;
            font-size: 14px;
        }}

        /* =====================================================
           TRANSPARENCIA
           ===================================================== */

        .transparencia {{
            background: #e3e3e3;
            padding: 35px 20px;
        }}

        .transparencia-inner {{
            max-width: 1100px;
            margin: auto;
            text-align: center;
        }}

        .transparencia h2 {{
            color: #aaa;
            font-weight: 300;
            letter-spacing: 3px;
            margin-bottom: 18px;
            font-size: 24px;
        }}

        .transparencia-links {{
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 10px 18px;
        }}

        .transparencia a {{
            color: #8e8e8e;
            font-size: 12px;
            text-decoration: none;
        }}

        .transparencia a:hover {{
            color: var(--vino);
        }}

        /* =====================================================
           FOOTER
           ===================================================== */

        .footer {{
            background: var(--vino);
            color: #fff;
            padding: 45px 25px;
        }}

        .footer-inner {{
            max-width: 1150px;
            margin: auto;

            display: grid;

            grid-template-columns:
                1.1fr 1fr;

            gap: 55px;

            align-items: center;
        }}

        .footer-logo {{
            max-width: 480px;
        }}

        .footer-links {{
            list-style: none;
        }}

        .footer-links li {{
            margin: 7px 0;
        }}

        .footer-links a {{
            color: #fffbef;
            text-decoration: none;
            font-size: 14px;
            line-height: 1.5;
        }}

        .footer-links a:hover {{
            text-decoration: underline;
        }}

        .copyright {{
            padding: 15px 20px;
            background: var(--vino-oscuro);

            color:
                rgba(255,255,255,0.72);

            text-align: center;

            font-size: 12px;
        }}

        /* =====================================================
           RESPONSIVE
           ===================================================== */

        @media (max-width: 900px) {{

            .header-inner {{
                padding:
                    15px 20px;
            }}

            .logo-gob {{
                width:
                    190px;
            }}

            .logo-secretaria {{
                width:
                    175px;
            }}

            .frase-header {{
                display:
                    none;
            }}

            .contacto-grid {{
                grid-template-columns:
                    1fr;
                text-align: center;
            }}

            .footer-inner {{
                grid-template-columns:
                    1fr;
                text-align: center;
            }}

            .footer-logo {{
                margin: auto;
            }}
        }}

        @media (max-width: 650px) {{

            .header-inner {{
                display:
                    block;
            }}

            .logos {{
                justify-content:
                    center;

                gap:
                    10px;
            }}

            .logo-gob {{
                width:
                    48%;
            }}

            .logo-secretaria {{
                width:
                    44%;
            }}

            .menu-inner {{
                justify-content:
                    center;

                padding:
                    0 10px;
            }}

            .menu a {{
                font-size:
                    13px;

                padding:
                    15px 10px;
            }}

            .hero {{
                padding:
                    38px 15px
                    80px;
            }}

            .hero h1 {{
                font-size:
                    26px;
            }}

            .hero p {{
                font-size:
                    14px;
            }}

            .contenido {{
                padding:
                    0 12px
                    40px;
            }}

            .resultado-box {{
                margin:
                    -45px auto
                    30px;

                padding:
                    24px 16px
                    28px;

                border-radius:
                    17px;
            }}

            .estado {{
                justify-content:
                    flex-start;

                text-align:
                    left;

                padding:
                    15px;
            }}

            .estado-icono {{
                width:
                    40px;

                height:
                    40px;

                flex-basis:
                    40px;

                font-size:
                    21px;
            }}

            .estado strong {{
                font-size:
                    14px;
            }}

            .estado span {{
                font-size:
                    12px;
            }}

            .folio-numero {{
                font-size:
                    23px;
            }}

            .titulo-seccion {{
                font-size:
                    19px;
            }}

            .datos-grid {{
                grid-template-columns:
                    1fr;
            }}

            .contacto {{
                margin:
                    25px 12px;

                padding:
                    22px;
            }}

            .footer {{
                padding:
                    35px 20px;
            }}
        }}

    </style>

</head>


<body>

<!-- =====================================================
     HEADER INSTITUCIONAL
     ===================================================== -->

<header class="header">

    <div class="header-inner">

        <div class="logos">

            <a
                href="https://puebla.gob.mx/"
                target="_blank"
                rel="noopener"
            >
                <img
                    class="logo-gob"
                    src="https://smt.puebla.gob.mx/templates/puebla/images/header/logo_puebla_gob.svg"
                    alt="Gobierno del Estado de Puebla"
                >
            </a>

            <img
                class="logo-secretaria"
                src="https://smt.puebla.gob.mx/images/headers/MOVILIDAD_02.png"
                alt="Secretaría de Movilidad y Transporte"
            >

        </div>


        <img
            class="frase-header"
            src="https://smt.puebla.gob.mx/templates/puebla/images/header/puebla_frases_gob.svg"
            alt="Puebla"
        >

    </div>

</header>


<nav class="menu">

    <div class="menu-inner">

        <a
            href="https://rl.puebla.gob.mx/"
            target="_blank"
            rel="noopener"
        >
            Pagos en línea
        </a>

        <a
            href="https://ventanilladigital.puebla.gob.mx/"
            target="_blank"
            rel="noopener"
        >
            Trámites
        </a>

    </div>

</nav>


<!-- =====================================================
     ENCABEZADO DE CONSULTA
     ===================================================== -->

<section class="hero">

    <div class="hero-inner">

        <h1>
            Resultado de Consulta
        </h1>

        <p>
            Consulta de permiso vehicular
        </p>

    </div>

</section>


<!-- =====================================================
     RESULTADO DINÁMICO
     ===================================================== -->

<main class="contenido">

    {estado_html}

</main>


<!-- =====================================================
     CONTACTO
     ===================================================== -->

<section class="contacto">

    <div class="contacto-grid">

        <div class="contacto-item">

            <strong>
                Contáctanos
            </strong>

            (222) 2 29 06 00
            <br>
            Ext. 1000 y 3503

        </div>


        <div class="contacto-item">

            <strong>
                Dirección
            </strong>

            Av. Rosendo Márquez 1501
            <br>
            Col. La Paz, Puebla, Pue.

        </div>


        <div class="contacto-item">

            <strong>
                Correo electrónico
            </strong>

            movilidadytransporte@puebla.gob.mx

        </div>

    </div>

</section>


<!-- =====================================================
     TRANSPARENCIA
     ===================================================== -->

<section class="transparencia">

    <div class="transparencia-inner">

        <h2>
            TRANSPARENCIA
        </h2>

        <div class="transparencia-links">

            <a
                href="https://planeader.puebla.gob.mx/"
                target="_blank"
                rel="noopener"
            >
                PLAN ESTATAL DE DESARROLLO
            </a>

            <a
                href="https://transparenciafiscal.puebla.gob.mx/"
                target="_blank"
                rel="noopener"
            >
                TRANSPARENCIA FISCAL
            </a>

            <a
                href="https://www.gob.mx/empleo"
                target="_blank"
                rel="noopener"
            >
                PORTAL DEL EMPLEO
            </a>

            <a
                href="https://presupuestociudadano.puebla.gob.mx/"
                target="_blank"
                rel="noopener"
            >
                PRESUPUESTO CIUDADANO
            </a>

        </div>

    </div>

</section>


<!-- =====================================================
     FOOTER
     ===================================================== -->

<footer class="footer">

    <div class="footer-inner">

        <div>

            <img
                class="footer-logo"
                src="https://smt.puebla.gob.mx/templates/puebla/images/footer/Escudo_pie.svg"
                alt="Gobierno del Estado de Puebla"
            >

        </div>


        <ul class="footer-links">

            <li>
                <a
                    href="https://planeader.puebla.gob.mx/"
                    target="_blank"
                    rel="noopener"
                >
                    PLAN ESTATAL DE DESARROLLO
                </a>
            </li>

            <li>
                <a
                    href="https://transparenciafiscal.puebla.gob.mx/"
                    target="_blank"
                    rel="noopener"
                >
                    TRANSPARENCIA FISCAL
                </a>
            </li>

            <li>
                <a
                    href="https://www.gob.mx/empleo"
                    target="_blank"
                    rel="noopener"
                >
                    PORTAL DEL EMPLEO
                </a>
            </li>

            <li>
                <a
                    href="https://www.gob.mx/presidencia"
                    target="_blank"
                    rel="noopener"
                >
                    PRESIDENCIA DE LA REPÚBLICA
                </a>
            </li>

            <li>
                <a
                    href="https://lgcg.puebla.gob.mx/"
                    target="_blank"
                    rel="noopener"
                >
                    LEY GENERAL DE CONTABILIDAD GUBERNAMENTAL
                </a>
            </li>

        </ul>

    </div>

</footer>


<div class="copyright">

    © {year} Gobierno del Estado de Puebla

</div>


</body>
</html>
"""

        return HTMLResponse(
            content=pagina,
            status_code=200
        )

    except Exception as e:

        print(
            f"[ESTADO_FOLIO] Error consultando "
            f"{folio}: {e}"
        )

        return HTMLResponse(
            content="""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Error de consulta</title>
</head>
<body style="
    margin:0;
    background:#f4f4f4;
    font-family:Arial,sans-serif;
">
    <div style="
        max-width:600px;
        margin:80px auto;
        background:white;
        padding:35px;
        border-radius:15px;
        text-align:center;
        box-shadow:0 5px 20px rgba(0,0,0,0.1);
    ">
        <h2 style="color:#5f1b2d;">
            No fue posible realizar la consulta
        </h2>
        <p style="color:#777;">
            Inténtelo nuevamente más tarde.
        </p>
    </div>
</body>
</html>
""",
            status_code=500
        )
        
@app.get("/api/consultar_folio/{folio}")
async def api_consultar(folio: str):
    folio = folio.strip().upper()
    try:
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).eq("entidad", ENTIDAD).limit(1).execute()
        if not res.data:
            return {"ok": False}
        
        r = res.data[0]
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        fecha_ven = datetime.fromisoformat(r["fecha_vencimiento"]).date()
        
        return {
            "ok": True,
            "vigente": hoy <= fecha_ven,
            "folio": folio,
            "nombre": r.get("contribuyente", ""),
            "marca": r.get("marca", ""),
            "linea": r.get("linea", ""),
            "anio": r.get("anio", ""),
            "color": r.get("color", ""),
            "numero_serie": r.get("numero_serie", ""),
            "numero_motor": r.get("numero_motor", ""),
            "fecha_expedicion": datetime.fromisoformat(r["fecha_expedicion"]).strftime("%d/%m/%Y"),
            "fecha_vencimiento": fecha_ven.strftime("%d/%m/%Y")
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Puebla"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
