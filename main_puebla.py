from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    JSONResponse,
    FileResponse
)
import hashlib
import secrets
import hmac
import base64
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
FOLIO_NUM_PREFIJO = "P0722"
_folio_counter = {"siguiente": 1}
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
        folio = f"{FOLIO_NUM_PREFIJO}{candidato}"
        if not _folio_existe(folio):
            _folio_counter["siguiente"] = candidato + 1
            _guardar_watermark(candidato)
            return folio
        candidato += 1
    return f"{FOLIO_NUM_PREFIJO}{random.randint(50000, 99999)}"

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
        pg_permiso.insert_text((310, 312), datos['linea'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((80, 340), datos['anio'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((585, 285), datos['motor'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((575, 255), datos['serie'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((340, 340), datos['tipo_auto'],
            fontsize=12, color=(0, 0, 0), fontname="helv")
        pg_permiso.insert_text((620, 343), datos['presidencia'],
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
            fitz.Rect(50, 195, 140, 285),
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

def generar_pdf_2x1(datos_1: dict, datos_2: dict) -> str:
    """
    Genera los dos permisos individuales y después los une
    en un solo PDF de 2 páginas.
    """

    pdf_1 = generar_pdf(datos_1)
    pdf_2 = generar_pdf(datos_2)

    nombre_final = (
        f"{datos_1['folio']}_{datos_2['folio']}_2x1_puebla.pdf"
    )

    out_final = os.path.join(
        OUTPUT_DIR,
        nombre_final
    )

    doc_final = fitz.open()

    try:
        doc_1 = fitz.open(pdf_1)
        doc_2 = fitz.open(pdf_2)

        doc_final.insert_pdf(doc_1)
        doc_final.insert_pdf(doc_2)

        doc_final.save(out_final)

        doc_1.close()
        doc_2.close()

    finally:
        doc_final.close()

    return out_final
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
    vigencia = State()
    tipo_auto = State()
    presidencia = State()
    combustible = State()
    cilindros = State()
    vigencia = State()
    tipo_auto = State()
    presidencia = State()


# ============================================================
# PASSWORDS DE CLIENTES
# ============================================================

def crear_password_hash(password: str) -> str:

    password = str(password).strip()

    if len(password) < 6:
        raise ValueError(
            "La contraseña debe tener mínimo 6 caracteres"
        )

    iteraciones = 310_000

    salt = secrets.token_bytes(16)

    resultado = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iteraciones
    )

    salt_b64 = base64.b64encode(
        salt
    ).decode("utf-8")

    hash_b64 = base64.b64encode(
        resultado
    ).decode("utf-8")

    return (
        f"pbkdf2_sha256$"
        f"{iteraciones}$"
        f"{salt_b64}$"
        f"{hash_b64}"
    )


def verificar_password_cliente(
    password: str,
    password_hash: str
) -> bool:

    try:

        metodo, iteraciones, salt_b64, hash_b64 = (
            password_hash.split("$", 3)
        )

        if metodo != "pbkdf2_sha256":
            return False

        iteraciones = int(iteraciones)

        salt = base64.b64decode(
            salt_b64
        )

        hash_guardado = base64.b64decode(
            hash_b64
        )

        hash_nuevo = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iteraciones
        )

        return hmac.compare_digest(
            hash_guardado,
            hash_nuevo
        )

    except Exception:

        return False
        
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
        f"Paso 1/12: MARCA del vehículo:")
    await state.set_state(PermisoForm.marca)

@dp.message(PermisoForm.marca)
async def get_marca(message: types.Message, state: FSMContext):
    await state.update_data(marca=message.text.upper().strip())
    await message.answer("Paso 2/12: LÍNEA/MODELO:")
    await state.set_state(PermisoForm.linea)

@dp.message(PermisoForm.linea)
async def get_linea(message: types.Message, state: FSMContext):
    await state.update_data(linea=message.text.upper().strip())
    await message.answer("Paso 3/12: AÑO:")
    await state.set_state(PermisoForm.anio)

@dp.message(PermisoForm.anio)
async def get_anio(message: types.Message, state: FSMContext):
    await state.update_data(anio=message.text.strip())
    await message.answer("Paso 4/12: NÚMERO DE SERIE:")
    await state.set_state(PermisoForm.serie)

@dp.message(PermisoForm.serie)
async def get_serie(message: types.Message, state: FSMContext):
    await state.update_data(serie=message.text.upper().strip())
    await message.answer("Paso 5/12: NÚMERO DE MOTOR:")
    await state.set_state(PermisoForm.motor)

@dp.message(PermisoForm.motor)
async def get_motor(message: types.Message, state: FSMContext):
    await state.update_data(motor=message.text.upper().strip())
    await message.answer("Paso 6/12: COLOR:")
    await state.set_state(PermisoForm.color)

@dp.message(PermisoForm.color)
async def get_color(message: types.Message, state: FSMContext):
    await state.update_data(color=message.text.upper().strip())
    await message.answer("Paso 7/12: NOMBRE COMPLETO del titular:")
    await state.set_state(PermisoForm.nombre)

@dp.message(PermisoForm.nombre)
async def get_nombre(message: types.Message, state: FSMContext):
    await state.update_data(nombre=message.text.upper().strip())
    await message.answer("Paso 8/12: COMBUSTIBLE (ej: GASOLINA):")
    await state.set_state(PermisoForm.combustible)

@dp.message(PermisoForm.combustible)
async def get_combustible(message: types.Message, state: FSMContext):
    await state.update_data(combustible=message.text.upper().strip())
    await message.answer("Paso 9/12: CILINDROS CC O PBV:")
    await state.set_state(PermisoForm.cilindros)

@dp.message(PermisoForm.cilindros)
async def get_cilindros(message: types.Message, state: FSMContext):
    await state.update_data(cilindros=message.text.upper().strip())
    await message.answer("Paso 10/12: VIGENCIA\n1 para 15 días\n2 para 30 días:")
    await state.set_state(PermisoForm.vigencia)

@dp.message(PermisoForm.vigencia)
async def get_vigencia(message: types.Message, state: FSMContext):
    vigencia = message.text.strip()
    if vigencia not in ["1", "2"]:
        await message.answer("❌ Responde solo 1 o 2")
        return
    await state.update_data(vigencia=vigencia)
    await message.answer("Paso 11/12: TIPO DE AUTO\n(Automóvil, Motocicleta, Trailer, Carroza, Carreta):")
    await state.set_state(PermisoForm.tipo_auto)

@dp.message(PermisoForm.tipo_auto)
async def get_tipo_auto(message: types.Message, state: FSMContext):
    await state.update_data(tipo_auto=message.text.upper().strip())
    await message.answer("Paso 12/12: PRESIDENCIA:")
    await state.set_state(PermisoForm.presidencia)

@dp.message(PermisoForm.presidencia)
async def get_presidencia(message: types.Message, state: FSMContext):
    datos = await state.get_data()
    datos["presidencia"] = message.text.upper().strip()
    datos["folio"] = await generar_folio_async()
    
    tz = ZoneInfo(TZ)
    hoy = datetime.now(tz)
    
    vigencia_dias = 15 if datos['vigencia'] == "1" else 30
    ven = hoy + timedelta(days=vigencia_dias)
    
    datos["fecha_exp"] = f"{hoy.day:02d}-{hoy.month:02d}-{hoy.year}"
    datos["fecha_ven"] = f"{ven.day:02d}-{ven.month:02d}-{ven.year}"
    
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
            "color": datos["color"],
            "contribuyente": datos["nombre"],
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
    https_only=True,
    max_age=1800
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

    # ==========================================================
    # LEER INFORMACIÓN DEL SISTEMA
    # ==========================================================

    try:

        resp = (
            supabase
            .table("folios_registrados")
            .select("folio,fecha_vencimiento,estado")
            .eq("entidad", ENTIDAD)
            .execute()
        )

        registros = resp.data or []

        supabase_estado = "Conectado"
        supabase_clase = "ok"

    except Exception as e:

        print(f"[ADMIN] Error leyendo folios: {e}")

        registros = []

        supabase_estado = "Sin conexión"
        supabase_clase = "error-status"

    total = len(registros)

    hoy = datetime.now(
        ZoneInfo(TZ)
    ).date()

    vigentes = 0
    vencidos = 0

    for row in registros:

        try:

            fv = datetime.fromisoformat(
                str(
                    row["fecha_vencimiento"]
                ).replace(
                    "Z",
                    "+00:00"
                )
            ).date()

            if hoy <= fv:
                vigentes += 1
            else:
                vencidos += 1

        except Exception:
            pass

    timers = len(timers_activos)

    siguiente = (
        f"{FOLIO_NUM_PREFIJO}"
        f"{_folio_counter['siguiente']}"
    )

    username = html_lib.escape(
        str(
            request.session.get(
                "username",
                "Admin"
            )
        )
    )

    year = datetime.now(
        ZoneInfo(TZ)
    ).year

    # ==========================================================
    # HTML
    # ==========================================================

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
    Secretaría de Movilidad y Transporte - Administración
</title>

<link
    rel="icon"
    href="https://smt.puebla.gob.mx/templates/puebla/favicon.ico"
    type="image/vnd.microsoft.icon"
>

<style>

:root {{

    --vino:#5f1b2d;

    --vino-oscuro:#48101e;

    --dorado:#c09761;

    --dorado-claro:#c79b66;

    --gris:#949494;

    --gris-claro:#f6f6f6;

    --azul:#001B4C;

    --blanco:#ffffff;
}}

* {{
    margin:0;
    padding:0;
    box-sizing:border-box;
}}

html {{
    min-height:100%;
    background:#f4f4f4;
}}

body {{

    min-height:100vh;

    background:#f4f4f4;

    color:#555;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

img {{
    max-width:100%;
    height:auto;
}}


/* =====================================================
   HEADER
===================================================== */

.header {{

    background:#fff;

    position:relative;

    z-index:10;

    box-shadow:
        0 2px 8px
        rgba(0,0,0,0.08);
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

.frase-header {{

    width:300px;

    max-height:90px;

    object-fit:contain;
}}


/* =====================================================
   MENU
===================================================== */

.menu {{

    background:
        var(--vino);
}}

.menu-inner {{

    max-width:
        1380px;

    margin:auto;

    min-height:
        52px;

    padding:
        0 30px;

    display:flex;

    align-items:center;

    justify-content:
        space-between;

    gap:15px;
}}

.menu-links {{

    display:flex;

    align-items:center;

    gap:4px;
}}

.menu a {{

    color:white;

    text-decoration:none;

    font-size:14px;

    padding:
        17px 15px;

    transition:
        background .2s ease;
}}

.menu a:hover {{

    background:
        rgba(
            255,
            255,
            255,
            .1
        );
}}

.menu .cerrar {{

    background:
        rgba(
            0,
            0,
            0,
            .16
        );

    border-radius:
        7px;

    padding:
        10px 15px;
}}


/* =====================================================
   HERO
===================================================== */

.hero {{

    position:relative;

    overflow:hidden;

    background:

        linear-gradient(
            120deg,
            #f8f8f8 0%,
            #f4f4f4 65%,
            #eee 100%
        );

    border-bottom:
        1px solid
        #e4e4e4;

    padding:
        50px 20px
        90px;

    text-align:center;
}}

.hero::after {{

    content:"";

    position:absolute;

    bottom:0;

    left:0;

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
        9px;
}}

.hero p {{

    color:
        var(--gris);

    font-size:
        17px;
}}


/* =====================================================
   CONTENIDO
===================================================== */

.contenido {{

    padding:
        0 20px
        60px;
}}

.panel-box {{

    position:relative;

    z-index:2;

    width:100%;

    max-width:1100px;

    margin:
        -55px auto
        40px;

    background:white;

    border-radius:
        24px;

    padding:
        38px 40px
        42px;

    box-shadow:
        0 8px 32px
        rgba(
            0,
            0,
            0,
            .11
        );
}}


/* =====================================================
   BIENVENIDA
===================================================== */

.bienvenida {{

    display:flex;

    justify-content:
        space-between;

    align-items:center;

    gap:20px;

    margin-bottom:
        30px;
}}

.bienvenida h2 {{

    color:
        var(--vino);

    font-size:
        25px;

    font-weight:
        400;

    margin-bottom:
        5px;
}}

.bienvenida p {{

    color:
        var(--gris);

    font-size:
        14px;
}}

.usuario {{

    background:
        #faf7f3;

    border-left:
        4px solid
        var(--dorado);

    padding:
        12px 16px;

    border-radius:
        8px;

    font-size:
        14px;
}}


/* =====================================================
   ESTADÍSTICAS
===================================================== */

.stats {{

    display:grid;

    grid-template-columns:
        repeat(
            4,
            1fr
        );

    gap:
        15px;

    margin-bottom:
        32px;
}}

.stat {{

    background:
        var(--gris-claro);

    border-radius:
        12px;

    padding:
        20px;

    border-left:
        4px solid
        var(--dorado-claro);
}}

.stat-label {{

    color:
        var(--gris);

    font-size:
        11px;

    font-weight:
        bold;

    text-transform:
        uppercase;

    letter-spacing:
        .7px;

    margin-bottom:
        8px;
}}

.stat-value {{

    color:
        var(--vino);

    font-size:
        30px;

    font-weight:
        600;
}}

.stat-sub {{

    color:
        #999;

    font-size:
        12px;

    margin-top:
        4px;
}}


/* =====================================================
   SECCIONES
===================================================== */

.separador {{

    width:
        100%;

    height:
        1px;

    background:
        #e9e9e9;

    margin:
        28px 0;
}}

.titulo-seccion {{

    color:
        var(--vino);

    font-size:
        22px;

    font-weight:
        400;

    margin-bottom:
        20px;
}}


/* =====================================================
   ACCIONES
===================================================== */

.acciones {{

    display:grid;

    grid-template-columns:
        repeat(
            2,
            minmax(
                0,
                1fr
            )
        );

    gap:
        15px;
}}

.accion {{

    display:block;

    background:
        var(--gris-claro);

    border-radius:
        12px;

    padding:
        20px;

    border-left:
        4px solid
        var(--dorado-claro);

    text-decoration:
        none;

    transition:
        .2s ease;
}}

.accion:hover {{

    transform:
        translateY(-2px);

    box-shadow:
        0 5px 15px
        rgba(
            0,
            0,
            0,
            .07
        );
}}

.accion strong {{

    display:block;

    color:
        var(--vino);

    font-size:
        16px;

    margin-bottom:
        6px;
}}

.accion span {{

    color:
        #777;

    font-size:
        13px;
}}


/* =====================================================
   SISTEMA
===================================================== */

.sistema-grid {{

    display:grid;

    grid-template-columns:
        repeat(
            2,
            minmax(
                0,
                1fr
            )
        );

    gap:
        12px;
}}

.sistema-item {{

    background:
        #fafafa;

    border-radius:
        10px;

    padding:
        14px 16px;

    display:flex;

    justify-content:
        space-between;

    gap:
        15px;

    border:
        1px solid
        #eeeeee;

    font-size:
        14px;
}}

.ok {{

    color:
        #198754;

    font-weight:
        600;
}}

.error-status {{

    color:
        #b72f3c;

    font-weight:
        600;
}}


/* =====================================================
   NOTA
===================================================== */

.nota {{

    background:
        #faf7f3;

    border-left:
        4px solid
        var(--dorado);

    margin-top:
        25px;

    padding:
        16px 18px;

    color:
        #686868;

    font-size:
        13px;

    line-height:
        1.55;
}}

.nota strong {{

    color:
        var(--vino);
}}


/* =====================================================
   FOOTER
===================================================== */

.footer {{

    background:
        var(--vino);

    color:white;

    padding:
        45px 25px;
}}

.footer-inner {{

    max-width:
        1150px;

    margin:auto;

    text-align:center;
}}

.footer-logo {{

    max-width:
        480px;
}}

.copyright {{

    padding:
        15px 20px;

    background:
        var(--vino-oscuro);

    color:
        rgba(
            255,
            255,
            255,
            .72
        );

    text-align:center;

    font-size:
        12px;
}}


/* =====================================================
   CELULAR
===================================================== */

@media(max-width:900px) {{

    .stats {{

        grid-template-columns:
            repeat(
                2,
                1fr
            );
    }}

}}

@media(max-width:650px) {{

    .header-inner {{

        display:block;

        padding:
            15px;
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

    .frase-header {{

        display:none;
    }}

    .menu-inner {{

        padding:
            0 10px;

        display:block;
    }}

    .menu-links {{

        overflow-x:auto;
    }}

    .menu a {{

        white-space:
            nowrap;

        font-size:
            12px;

        padding:
            15px 10px;
    }}

    .menu .cerrar {{

        display:block;

        text-align:center;

        margin:
            6px 0 10px;
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

    .contenido {{

        padding:
            0 12px
            40px;
    }}

    .panel-box {{

        margin:
            -45px auto
            30px;

        padding:
            24px 16px
            28px;

        border-radius:
            17px;
    }}

    .bienvenida {{

        display:block;
    }}

    .usuario {{

        margin-top:
            15px;
    }}

    .stats {{

        grid-template-columns:
            1fr 1fr;
    }}

    .acciones {{

        grid-template-columns:
            1fr;
    }}

    .sistema-grid {{

        grid-template-columns:
            1fr;
    }}

}}

</style>

</head>


<body>


<!-- =====================================================
     HEADER
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


<!-- =====================================================
     MENÚ ADMIN
===================================================== -->

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

        <a href="/admin/tablas">
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


<!-- =====================================================
     HERO
===================================================== -->

<section class="hero">

    <h1>
        Panel Administrativo
    </h1>

    <p>
        Sistema de administración de permisos vehiculares
    </p>

</section>


<!-- =====================================================
     DASHBOARD
===================================================== -->

<main class="contenido">


<section class="panel-box">


    <div class="bienvenida">

        <div>

            <h2>
                Administración del sistema
            </h2>

            <p>
                Consulta y administra los registros disponibles.
            </p>

        </div>


        <div class="usuario">

            <strong>
                Usuario:
            </strong>

            {username}

        </div>

    </div>


    <div class="stats">


        <div class="stat">

            <div class="stat-label">
                Folios registrados
            </div>

            <div class="stat-value">
                {total}
            </div>

            <div class="stat-sub">
                Puebla
            </div>

        </div>


        <div class="stat">

            <div class="stat-label">
                Vigentes
            </div>

            <div class="stat-value">
                {vigentes}
            </div>

            <div class="stat-sub">
                Dentro de vigencia
            </div>

        </div>


        <div class="stat">

            <div class="stat-label">
                Vencidos
            </div>

            <div class="stat-value">
                {vencidos}
            </div>

            <div class="stat-sub">
                Fuera de vigencia
            </div>

        </div>


        <div class="stat">

            <div class="stat-label">
                Timers activos
            </div>

            <div class="stat-value">
                {timers}
            </div>

            <div class="stat-sub">
                Pendientes
            </div>

        </div>


    </div>


    <div class="separador"></div>


    <h2 class="titulo-seccion">
        Accesos rápidos
    </h2>


    <div class="acciones">


        <a
            class="accion"
            href="/admin/crear"
        >

            <strong>
                ＋ Crear permiso
            </strong>

            <span>
                Generar un nuevo permiso manualmente
            </span>

        </a>


        <a
            class="accion"
            href="/admin/folios"
        >

            <strong>
                Administrar folios
            </strong>

            <span>
                Consultar y eliminar registros
            </span>

        </a>


        <a
            class="accion"
            href="/admin/usuarios"
        >

            <strong>
                Usuarios terceros
            </strong>

            <span>
                Administrar cuentas y paquetes
            </span>

        </a>


        <a
            class="accion"
            href="/admin/tablas"
        >

            <strong>
                Tablas del sistema
            </strong>

            <span>
                Consultar información almacenada
            </span>

        </a>


    </div>


    <div class="separador"></div>


    <h2 class="titulo-seccion">
        Estado del sistema
    </h2>


    <div class="sistema-grid">


        <div class="sistema-item">

            <span>
                Supabase
            </span>

            <span class="{supabase_clase}">
                ● {supabase_estado}
            </span>

        </div>


        <div class="sistema-item">

            <span>
                Telegram Bot
            </span>

            <span class="ok">
                ● Configurado
            </span>

        </div>


        <div class="sistema-item">

            <span>
                Entidad
            </span>

            <strong>
                {ENTIDAD.upper()}
            </strong>

        </div>


        <div class="sistema-item">

            <span>
                Precio
            </span>

            <strong>
                ${PRECIO} MXN
            </strong>

        </div>


        <div class="sistema-item">

            <span>
                Siguiente folio
            </span>

            <strong>
                {siguiente}
            </strong>

        </div>


        <div class="sistema-item">

            <span>
                Sesión
            </span>

            <strong>
                30 minutos
            </strong>

        </div>


    </div>


    <div class="nota">

        <strong>
            Sesión administrativa:
        </strong>

        por seguridad el acceso caduca después de
        aproximadamente 30 minutos.

    </div>


</section>


</main>


<!-- =====================================================
     FOOTER
===================================================== -->

<footer class="footer">

<div class="footer-inner">

    <img
        class="footer-logo"
        src="https://smt.puebla.gob.mx/templates/puebla/images/footer/Escudo_pie.svg"
        alt="Gobierno del Estado de Puebla"
    >

</div>

</footer>


<div class="copyright">

    © {year} Gobierno del Estado de Puebla

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
async def root(request: Request):
    if request.session.get("admin"):
        return RedirectResponse("/admin", status_code=302)

    return RedirectResponse("/login", status_code=302)
 
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

# ==================== EXTENSIONES NUEVAS - SOLO COPIAR AL FINAL DEL ARCHIVO ====================

# ==================== CREAR PERMISO ====================
@app.get("/admin/crear", response_class=HTMLResponse)
async def crear_permiso_get(request: Request):

    if not request.session.get("admin"):
        return RedirectResponse("/login", status_code=302)

    return HTMLResponse("""
<!DOCTYPE html>
<html lang="es">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Secretaría de Movilidad y Transporte - Crear Permiso
</title>

<link
    rel="icon"
    href="https://smt.puebla.gob.mx/templates/puebla/favicon.ico"
    type="image/vnd.microsoft.icon"
>

<style>

:root {
    --vino:#5f1b2d;
    --vino-oscuro:#48101e;
    --dorado:#c09761;
    --dorado-claro:#c79b66;
    --gris:#949494;
    --gris-claro:#f6f6f6;
    --azul:#001B4C;
    --blanco:#ffffff;
}

* {
    margin:0;
    padding:0;
    box-sizing:border-box;
}

html {
    min-height:100%;
    background:#f4f4f4;
}

body {
    margin:0;
    min-height:100vh;
    background:#f4f4f4;
    color:#555;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

img {
    max-width:100%;
    height:auto;
}


/* =====================================================
   HEADER
===================================================== */

.header {
    background:#fff;
    position:relative;
    z-index:10;

    box-shadow:
        0 2px 8px
        rgba(0,0,0,0.08);
}

.header-inner {
    max-width:1380px;
    margin:auto;

    padding:
        18px 30px;

    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:30px;
}

.logos {
    display:flex;
    align-items:center;
    gap:22px;
}

.logo-gob {
    width:245px;
    max-height:82px;
    object-fit:contain;
}

.logo-secretaria {
    width:225px;
    max-height:88px;
    object-fit:contain;
}

.frase-header {
    width:300px;
    max-height:90px;
    object-fit:contain;
}


/* =====================================================
   MENU
===================================================== */

.menu {
    background:var(--vino);
}

.menu-inner {
    max-width:1380px;
    margin:auto;
    min-height:52px;

    padding:
        0 30px;

    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
}

.menu-links {
    display:flex;
    align-items:center;
    gap:4px;
}

.menu a {
    color:white;
    text-decoration:none;
    font-size:14px;

    padding:
        17px 15px;

    transition:
        background .2s ease;
}

.menu a:hover,
.menu a.active {
    background:
        rgba(255,255,255,.12);
}

.menu .cerrar {
    background:
        rgba(0,0,0,.16);

    border-radius:7px;

    padding:
        10px 15px;
}


/* =====================================================
   HERO
===================================================== */

.hero {
    position:relative;
    overflow:hidden;

    background:
        linear-gradient(
            120deg,
            #f8f8f8 0%,
            #f4f4f4 65%,
            #eee 100%
        );

    border-bottom:
        1px solid #e4e4e4;

    padding:
        50px 20px
        90px;

    text-align:center;
}

.hero::after {
    content:"";

    position:absolute;
    bottom:0;
    left:0;

    width:100%;
    height:7px;

    background:
        var(--dorado);
}

.hero h1 {
    color:var(--vino);

    font-size:34px;
    font-weight:400;

    margin-bottom:9px;
}

.hero p {
    color:var(--gris);
    font-size:17px;
}


/* =====================================================
   CONTENIDO
===================================================== */

.contenido {
    padding:
        0 20px
        60px;
}

.form-box {
    position:relative;
    z-index:2;

    width:100%;
    max-width:1000px;

    margin:
        -55px auto
        40px;

    background:white;

    border-radius:24px;

    padding:
        38px 40px
        42px;

    box-shadow:
        0 8px 32px
        rgba(0,0,0,.11);
}


/* =====================================================
   CABECERA FORMULARIO
===================================================== */

.form-header {
    margin-bottom:30px;
}

.form-header h2 {
    color:var(--vino);

    font-size:25px;
    font-weight:400;

    margin-bottom:6px;
}

.form-header p {
    color:var(--gris);
    font-size:14px;
}


/* =====================================================
   MENSAJES
===================================================== */

.error {
    background:#f8d7da;
    color:#721c24;

    border:
        1px solid #e7abb1;

    border-radius:10px;

    padding:15px 17px;

    margin-bottom:20px;

    display:none;
}

.success {
    background:#e6f4e8;
    color:#155724;

    border:
        1px solid #b9dfbf;

    border-radius:10px;

    padding:17px;

    margin-bottom:22px;

    display:none;

    line-height:1.6;
}


/* =====================================================
   SECCIONES
===================================================== */

.seccion {
    margin-bottom:30px;
}

.seccion-titulo {
    color:var(--vino);

    font-size:18px;
    font-weight:500;

    padding-bottom:10px;

    margin-bottom:18px;

    border-bottom:
        1px solid #e8e8e8;
}

.grid-2 {
    display:grid;

    grid-template-columns:
        repeat(
            2,
            minmax(0,1fr)
        );

    gap:
        18px;
}

.form-group {
    margin-bottom:18px;
}

label {
    display:block;

    color:#666;

    font-size:13px;
    font-weight:bold;

    text-transform:uppercase;

    letter-spacing:.4px;

    margin-bottom:8px;
}

input,
select {
    width:100%;

    padding:
        13px 14px;

    border:
        1px solid #d7d7d7;

    border-radius:9px;

    background:white;

    color:#444;

    font-size:16px;

    outline:none;
}

input:focus,
select:focus {
    border-color:
        var(--dorado);

    box-shadow:
        0 0 0
        3px rgba(192,151,97,.15);
}


/* =====================================================
   CAMPOS DESTACADOS
===================================================== */

.destacado {
    background:#faf7f3;

    border-radius:14px;

    padding:
        20px;

    border-left:
        4px solid
        var(--dorado);

    margin-top:5px;
}


/* =====================================================
   FECHA
===================================================== */

.fecha-controles {
    display:flex;
    gap:8px;
    align-items:center;
}

.fecha-controles input {
    flex:1;
}

.btn-fecha {
    width:auto;

    white-space:nowrap;

    background:#eee;

    color:#555;

    padding:
        12px 13px;

    border:0;

    border-radius:8px;

    cursor:pointer;
}

.btn-fecha:hover {
    background:#ddd;
}


/* =====================================================
   BOTONES
===================================================== */

.botones {
    display:flex;
    gap:12px;
    margin-top:28px;
}

.btn-principal {
    border:0;

    background:
        var(--dorado);

    color:white;

    padding:
        14px 24px;

    border-radius:9px;

    font-size:16px;
    font-weight:600;

    cursor:pointer;
}

.btn-principal:hover {
    background:#ad804c;
}

.btn-volver {
    border:0;

    background:#eeeeee;

    color:#555;

    padding:
        14px 24px;

    border-radius:9px;

    font-size:16px;

    cursor:pointer;
}

.btn-volver:hover {
    background:#dddddd;
}


/* =====================================================
   NOTA
===================================================== */

.nota {
    background:#faf7f3;

    border-left:
        4px solid
        var(--dorado);

    margin-top:25px;

    padding:
        16px 18px;

    color:#686868;

    font-size:13px;
    line-height:1.55;
}

.nota strong {
    color:var(--vino);
}


/* =====================================================
   FOOTER
===================================================== */

.footer {
    background:var(--vino);
    color:white;

    padding:
        45px 25px;
}

.footer-inner {
    max-width:1150px;
    margin:auto;
    text-align:center;
}

.footer-logo {
    max-width:480px;
}

.copyright {
    padding:
        15px 20px;

    background:
        var(--vino-oscuro);

    color:
        rgba(255,255,255,.72);

    text-align:center;

    font-size:12px;
}


/* =====================================================
   CELULAR
===================================================== */

@media(max-width:700px) {

    .header-inner {
        display:block;
        padding:15px;
    }

    .logos {
        justify-content:center;
        gap:10px;
    }

    .logo-gob {
        width:48%;
    }

    .logo-secretaria {
        width:44%;
    }

    .frase-header {
        display:none;
    }

    .menu-inner {
        padding:
            0 10px;

        display:block;
    }

    .menu-links {
        overflow-x:auto;
    }

    .menu a {
        white-space:nowrap;
        font-size:12px;

        padding:
            15px 10px;
    }

    .menu .cerrar {
        display:block;

        text-align:center;

        margin:
            6px 0 10px;
    }

    .hero {
        padding:
            38px 15px
            80px;
    }

    .hero h1 {
        font-size:26px;
    }

    .contenido {
        padding:
            0 12px
            40px;
    }

    .form-box {
        margin:
            -45px auto
            30px;

        padding:
            24px 16px
            28px;

        border-radius:17px;
    }

    .grid-2 {
        grid-template-columns:1fr;
        gap:0;
    }

    .fecha-controles {
        display:grid;
        grid-template-columns:1fr 1fr;
    }

    .fecha-controles input {
        grid-column:
            1 / -1;
    }

    .botones {
        flex-direction:column;
    }

    .btn-principal,
    .btn-volver {
        width:100%;
    }
}

</style>

</head>


<body>


<!-- =====================================================
     HEADER
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


<!-- =====================================================
     MENU ADMIN
===================================================== -->

<nav class="menu">

<div class="menu-inner">

    <div class="menu-links">

        <a href="/admin">
            Inicio
        </a>

        <a
            href="/admin/crear"
            class="active"
        >
            Crear permiso
        </a>

        <a href="/admin/folios">
            Gestionar folios
        </a>

        <a href="/admin/usuarios">
            Usuarios
        </a>

        <a href="/admin/tablas">
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


<!-- =====================================================
     HERO
===================================================== -->

<section class="hero">

    <h1>
        Crear Permiso
    </h1>

    <p>
        Registro administrativo de permiso vehicular
    </p>

</section>


<!-- =====================================================
     FORMULARIO
===================================================== -->

<main class="contenido">


<section class="form-box">


<div class="form-header">

    <h2>
        Datos del permiso
    </h2>

    <p>
        Capture la información correspondiente al vehículo y al titular.
    </p>

</div>


<div
    id="error"
    class="error"
></div>


<div
    id="success"
    class="success"
></div>


<form id="permisoForm">


<!-- =====================================================
     VEHÍCULO
===================================================== -->

<section class="seccion">

<h3 class="seccion-titulo">
    Información del vehículo
</h3>


<div class="grid-2">

    <div class="form-group">

        <label for="marca">
            Marca
        </label>

        <input
            type="text"
            id="marca"
            name="marca"
            required
        >

    </div>


    <div class="form-group">

        <label for="linea">
            Línea / Modelo
        </label>

        <input
            type="text"
            id="linea"
            name="linea"
            required
        >

    </div>

</div>


<div class="grid-2">

    <div class="form-group">

        <label for="anio">
            Año
        </label>

        <input
            type="text"
            id="anio"
            name="anio"
            required
        >

    </div>


    <div class="form-group">

        <label for="color">
            Color
        </label>

        <input
            type="text"
            id="color"
            name="color"
            required
        >

    </div>

</div>


<div class="grid-2">

    <div class="form-group">

        <label for="serie">
            Número de serie / VIN
        </label>

        <input
            type="text"
            id="serie"
            name="serie"
            required
        >

    </div>


    <div class="form-group">

        <label for="motor">
            Número de motor
        </label>

        <input
            type="text"
            id="motor"
            name="motor"
            required
        >

    </div>

</div>


<div class="grid-2">

    <div class="form-group">

        <label for="combustible">
            Combustible
        </label>

        <input
            type="text"
            id="combustible"
            name="combustible"
            placeholder="Ej: GASOLINA"
            required
        >

    </div>


    <div class="form-group">

        <label for="cilindros">
            Cilindros / CC
        </label>

        <input
            type="text"
            id="cilindros"
            name="cilindros"
            required
        >

    </div>

</div>


<div class="grid-2">

    <div class="form-group">

        <label for="tipo_auto">
            Tipo de vehículo
        </label>

        <select
            id="tipo_auto"
            name="tipo_auto"
            required
        >

            <option value="">
                Seleccionar...
            </option>

            <option>
                Automóvil
            </option>

            <option>
                Motocicleta
            </option>

            <option>
                Suv
            </option>

            <option>
                Van
            </option>

            <option>
                Vagoneta
            </option>

        </select>

    </div>


    <div class="form-group">

        <label for="presidencia">
            Presidencia
        </label>

        <input
            type="text"
            id="presidencia"
            name="presidencia"
            required
        >

    </div>

</div>

</section>


<!-- =====================================================
     TITULAR
===================================================== -->

<section class="seccion">

<h3 class="seccion-titulo">
    Datos del titular
</h3>


<div class="form-group">

    <label for="nombre">
        Nombre completo
    </label>

    <input
        type="text"
        id="nombre"
        name="nombre"
        required
    >

</div>

</section>


<!-- =====================================================
     VIGENCIA
===================================================== -->

<section class="seccion">

<h3 class="seccion-titulo">
    Vigencia y expedición
</h3>


<div class="destacado">


<div class="grid-2">

    <div class="form-group">

        <label for="vigencia">
            Vigencia
        </label>

        <select
            id="vigencia"
            name="vigencia"
            required
        >

            <option value="">
                Seleccionar...
            </option>

            <option value="1">
                15 días
            </option>

            <option value="2">
                30 días
            </option>

            <option value="3">
                2 × 15 días — 2x1
            </option>

        </select>

    </div>


    <div class="form-group">

        <label for="folio">
            Folio manual
        </label>

        <input
            type="text"
            id="folio"
            name="folio"
            placeholder="Dejar vacío para generar automáticamente"
        >

    </div>

</div>


<div class="form-group">

    <label for="fecha_exp">
        Fecha de expedición
    </label>


    <div class="fecha-controles">

        <input
            type="date"
            id="fecha_exp"
            name="fecha_exp"
            required
        >


        <button
            type="button"
            class="btn-fecha"
            onclick="cambiarFecha(-1)"
        >
            ← 1 día
        </button>


        <button
            type="button"
            class="btn-fecha"
            onclick="cambiarFecha(0)"
        >
            Hoy
        </button>


        <button
            type="button"
            class="btn-fecha"
            onclick="cambiarFecha(1)"
        >
            1 día →
        </button>

    </div>

</div>


</div>

</section>


<div class="nota">

    <strong>
        Generación:
    </strong>

    en modalidad 2×15 se generan dos folios
    consecutivos y un único archivo PDF de dos páginas.

</div>


<div class="botones">

    <button
        type="submit"
        class="btn-principal"
    >
        ✓ Crear permiso
    </button>


    <button
        type="button"
        class="btn-volver"
        onclick="window.location='/admin';"
    >
        ← Regresar al panel
    </button>

</div>


</form>


</section>


</main>


<!-- =====================================================
     FOOTER
===================================================== -->

<footer class="footer">

<div class="footer-inner">

    <img
        class="footer-logo"
        src="https://smt.puebla.gob.mx/templates/puebla/images/footer/Escudo_pie.svg"
        alt="Gobierno del Estado de Puebla"
    >

</div>

</footer>


<div class="copyright">
    Gobierno del Estado de Puebla
</div>


<!-- =====================================================
     JAVASCRIPT
===================================================== -->

<script>


function cambiarFecha(dias) {

    const input =
        document.getElementById(
            'fecha_exp'
        );

    const fecha =
        new Date();

    fecha.setDate(
        fecha.getDate()
        + dias
    );

    input.valueAsDate =
        fecha;
}


document
    .getElementById(
        'fecha_exp'
    )
    .valueAsDate =
        new Date();


document
    .getElementById(
        'permisoForm'
    )
    .addEventListener(
        'submit',
        async (e) => {

            e.preventDefault();


            const errorBox =
                document.getElementById(
                    'error'
                );

            const successBox =
                document.getElementById(
                    'success'
                );


            errorBox.style.display =
                'none';

            successBox.style.display =
                'none';


            const form =
                new FormData(
                    document.getElementById(
                        'permisoForm'
                    )
                );


            const datos =
                Object.fromEntries(
                    form
                );


            try {


                const res =
                    await fetch(
                        '/admin/crear',
                        {
                            method:
                                'POST',

                            headers: {
                                'Content-Type':
                                    'application/json'
                            },

                            body:
                                JSON.stringify(
                                    datos
                                )
                        }
                    );


                const result =
                    await res.json();


                if (result.ok) {


                    successBox.style.display =
                        'block';


                    if (
                        result.tipo
                        ===
                        '2x1'
                    ) {


                        successBox.innerHTML = `

                            ✓ <strong>
                            Paquete 2×15 creado correctamente
                            </strong>

                            <br><br>

                            <strong>
                            Folio 1:
                            </strong>

                            ${result.folio_1}

                            <br>

                            ${result.fecha_1_exp}
                            →
                            ${result.fecha_1_ven}

                            <br><br>

                            <strong>
                            Folio 2:
                            </strong>

                            ${result.folio_2}

                            <br>

                            ${result.fecha_2_exp}
                            →
                            ${result.fecha_2_ven}

                            <br><br>


                            <a
                                href="${result.pdf_url}"
                                target="_blank"

                                style="
                                    display:inline-block;
                                    background:#5f1b2d;
                                    color:white;
                                    padding:11px 18px;
                                    border-radius:8px;
                                    text-decoration:none;
                                    font-weight:600;
                                "
                            >
                                Descargar PDF 2×15
                            </a>
                        `;


                    } else {


                        successBox.innerHTML = `

                            ✓ Permiso creado correctamente

                            <br><br>

                            <strong>
                            Folio:
                            </strong>

                            ${result.folio}

                            <br>

                            ${result.fecha_exp}
                            →
                            ${result.fecha_ven}

                            <br><br>


                            <a
                                href="${result.pdf_url}"
                                target="_blank"

                                style="
                                    display:inline-block;
                                    background:#5f1b2d;
                                    color:white;
                                    padding:11px 18px;
                                    border-radius:8px;
                                    text-decoration:none;
                                    font-weight:600;
                                "
                            >
                                Descargar PDF
                            </a>
                        `;
                    }


                    document
                        .getElementById(
                            'permisoForm'
                        )
                        .reset();


                    document
                        .getElementById(
                            'fecha_exp'
                        )
                        .valueAsDate =
                            new Date();


                    window.scrollTo({
                        top:0,
                        behavior:'smooth'
                    });


                } else {


                    errorBox.style.display =
                        'block';

                    errorBox.textContent =
                        '✗ Error: '
                        + (
                            result.error
                            ||
                            'No fue posible crear el permiso'
                        );


                    window.scrollTo({
                        top:0,
                        behavior:'smooth'
                    });

                }


            } catch (err) {


                errorBox.style.display =
                    'block';

                errorBox.textContent =
                    '✗ Error: '
                    + err.message;


                window.scrollTo({
                    top:0,
                    behavior:'smooth'
                });

            }

        }
    );

</script>


</body>

</html>
""")

@app.post("/admin/crear")
async def crear_permiso_post(request: Request):

    if not request.session.get("admin"):
        raise HTTPException(
            status_code=401,
            detail="No autorizado"
        )

    folios_creados = []

    try:

        datos = await request.json()

        # =====================================================
        # VALIDACIONES BÁSICAS
        # =====================================================

        vigencia = str(
            datos.get("vigencia", "")
        ).strip()

        if vigencia not in ("1", "2", "3"):
            return JSONResponse({
                "ok": False,
                "error": "Vigencia inválida"
            })

        fecha_exp = datetime.strptime(
            datos["fecha_exp"],
            "%Y-%m-%d"
        ).date()

        # =====================================================
        # DATOS COMUNES
        # =====================================================

        datos_comunes = {
            "marca": datos["marca"].upper().strip(),
            "linea": datos["linea"].upper().strip(),
            "anio": datos["anio"].strip(),
            "serie": datos["serie"].upper().strip(),
            "motor": datos["motor"].upper().strip(),
            "color": datos["color"].upper().strip(),
            "nombre": datos["nombre"].upper().strip(),
            "combustible": datos["combustible"].upper().strip(),
            "cilindros": datos["cilindros"].upper().strip(),
            "tipo_auto": datos["tipo_auto"].upper().strip(),
            "presidencia": datos["presidencia"].upper().strip(),
        }

        # =====================================================
        # PRIMER FOLIO
        # =====================================================

        folio_manual = (
            datos.get("folio") or ""
        ).upper().strip()

        if folio_manual:

            if _folio_existe(folio_manual):
                return JSONResponse({
                    "ok": False,
                    "error": "El folio indicado ya existe"
                })

            folio_1 = folio_manual

        else:

            folio_1 = await generar_folio_async()

        # =====================================================
        # MODO 1 = 15 DÍAS
        # =====================================================

        if vigencia == "1":

            # Día de expedición cuenta como día 1.
            fecha_ven = (
                fecha_exp
                + timedelta(days=14)
            )

            pdf_datos = {
                **datos_comunes,

                "folio": folio_1,

                "fecha_exp":
                    fecha_exp.strftime(
                        "%d-%m-%Y"
                    ),

                "fecha_ven":
                    fecha_ven.strftime(
                        "%d-%m-%Y"
                    )
            }

            pdf_path = await asyncio.to_thread(
                generar_pdf,
                pdf_datos
            )

            supabase.table(
                "folios_registrados"
            ).insert({

                "folio": folio_1,

                "marca":
                    datos_comunes["marca"],

                "linea":
                    datos_comunes["linea"],

                "anio":
                    datos_comunes["anio"],

                "numero_serie":
                    datos_comunes["serie"],

                "numero_motor":
                    datos_comunes["motor"],

                "color":
                    datos_comunes["color"],

                "contribuyente":
                    datos_comunes["nombre"],

                "fecha_expedicion":
                    fecha_exp.isoformat(),

                "fecha_vencimiento":
                    fecha_ven.isoformat(),

                "entidad":
                    ENTIDAD,

                "estado":
                    "PENDIENTE",

                "user_id":
                    0,

                "username":
                    "admin"

            }).execute()

            folios_creados.append(
                folio_1
            )

            nombre_pdf = os.path.basename(
                pdf_path
            )

            return JSONResponse({

                "ok": True,

                "tipo": "15",

                "folio": folio_1,

                "fecha_exp":
                    fecha_exp.strftime(
                        "%d/%m/%Y"
                    ),

                "fecha_ven":
                    fecha_ven.strftime(
                        "%d/%m/%Y"
                    ),

                "pdf_url":
                    f"/admin/descargar/{nombre_pdf}"
            })

        # =====================================================
        # MODO 2 = 30 DÍAS NORMALES
        # =====================================================

        if vigencia == "2":

            # 30 días inclusivos:
            # día expedición = día 1.
            fecha_ven = (
                fecha_exp
                + timedelta(days=29)
            )

            pdf_datos = {
                **datos_comunes,

                "folio": folio_1,

                "fecha_exp":
                    fecha_exp.strftime(
                        "%d-%m-%Y"
                    ),

                "fecha_ven":
                    fecha_ven.strftime(
                        "%d-%m-%Y"
                    )
            }

            pdf_path = await asyncio.to_thread(
                generar_pdf,
                pdf_datos
            )

            supabase.table(
                "folios_registrados"
            ).insert({

                "folio": folio_1,

                "marca":
                    datos_comunes["marca"],

                "linea":
                    datos_comunes["linea"],

                "anio":
                    datos_comunes["anio"],

                "numero_serie":
                    datos_comunes["serie"],

                "numero_motor":
                    datos_comunes["motor"],

                "color":
                    datos_comunes["color"],

                "contribuyente":
                    datos_comunes["nombre"],

                "fecha_expedicion":
                    fecha_exp.isoformat(),

                "fecha_vencimiento":
                    fecha_ven.isoformat(),

                "entidad":
                    ENTIDAD,

                "estado":
                    "PENDIENTE",

                "user_id":
                    0,

                "username":
                    "admin"

            }).execute()

            folios_creados.append(
                folio_1
            )

            nombre_pdf = os.path.basename(
                pdf_path
            )

            return JSONResponse({

                "ok": True,

                "tipo": "30",

                "folio": folio_1,

                "fecha_exp":
                    fecha_exp.strftime(
                        "%d/%m/%Y"
                    ),

                "fecha_ven":
                    fecha_ven.strftime(
                        "%d/%m/%Y"
                    ),

                "pdf_url":
                    f"/admin/descargar/{nombre_pdf}"
            })

        # =====================================================
        # MODO 3 = 2 × 15 DÍAS
        # =====================================================

        if vigencia == "3":

            # -------------------------------------------------
            # PERMISO 1
            # -------------------------------------------------

            fecha_1_exp = fecha_exp

            fecha_1_ven = (
                fecha_1_exp
                + timedelta(days=14)
            )

            # -------------------------------------------------
            # PERMISO 2
            # Empieza exactamente al día siguiente
            # -------------------------------------------------

            fecha_2_exp = (
                fecha_1_ven
                + timedelta(days=1)
            )

            fecha_2_ven = (
                fecha_2_exp
                + timedelta(days=14)
            )

            # -------------------------------------------------
            # SEGUNDO FOLIO
            # -------------------------------------------------

            folio_2 = await generar_folio_async()

            # Seguridad extra por improbable colisión
            while (
                folio_2 == folio_1
                or _folio_existe(folio_2)
            ):
                folio_2 = await generar_folio_async()

            # -------------------------------------------------
            # DATOS PDF 1
            # -------------------------------------------------

            pdf_datos_1 = {

                **datos_comunes,

                "folio":
                    folio_1,

                "fecha_exp":
                    fecha_1_exp.strftime(
                        "%d-%m-%Y"
                    ),

                "fecha_ven":
                    fecha_1_ven.strftime(
                        "%d-%m-%Y"
                    )
            }

            # -------------------------------------------------
            # DATOS PDF 2
            # -------------------------------------------------

            pdf_datos_2 = {

                **datos_comunes,

                "folio":
                    folio_2,

                "fecha_exp":
                    fecha_2_exp.strftime(
                        "%d-%m-%Y"
                    ),

                "fecha_ven":
                    fecha_2_ven.strftime(
                        "%d-%m-%Y"
                    )
            }

            # -------------------------------------------------
            # GENERAR PDF ÚNICO DE 2 PÁGINAS
            # -------------------------------------------------

            pdf_final = await asyncio.to_thread(
                generar_pdf_2x1,
                pdf_datos_1,
                pdf_datos_2
            )

            # -------------------------------------------------
            # INSERTAR FOLIO 1
            # -------------------------------------------------

            supabase.table(
                "folios_registrados"
            ).insert({

                "folio":
                    folio_1,

                "marca":
                    datos_comunes["marca"],

                "linea":
                    datos_comunes["linea"],

                "anio":
                    datos_comunes["anio"],

                "numero_serie":
                    datos_comunes["serie"],

                "numero_motor":
                    datos_comunes["motor"],

                "color":
                    datos_comunes["color"],

                "contribuyente":
                    datos_comunes["nombre"],

                "fecha_expedicion":
                    fecha_1_exp.isoformat(),

                "fecha_vencimiento":
                    fecha_1_ven.isoformat(),

                "entidad":
                    ENTIDAD,

                "estado":
                    "PENDIENTE",

                "user_id":
                    0,

                "username":
                    "admin"

            }).execute()

            folios_creados.append(
                folio_1
            )

            # -------------------------------------------------
            # INSERTAR FOLIO 2
            # -------------------------------------------------

            supabase.table(
                "folios_registrados"
            ).insert({

                "folio":
                    folio_2,

                "marca":
                    datos_comunes["marca"],

                "linea":
                    datos_comunes["linea"],

                "anio":
                    datos_comunes["anio"],

                "numero_serie":
                    datos_comunes["serie"],

                "numero_motor":
                    datos_comunes["motor"],

                "color":
                    datos_comunes["color"],

                "contribuyente":
                    datos_comunes["nombre"],

                "fecha_expedicion":
                    fecha_2_exp.isoformat(),

                "fecha_vencimiento":
                    fecha_2_ven.isoformat(),

                "entidad":
                    ENTIDAD,

                "estado":
                    "PENDIENTE",

                "user_id":
                    0,

                "username":
                    "admin"

            }).execute()

            folios_creados.append(
                folio_2
            )

            nombre_pdf = os.path.basename(
                pdf_final
            )

            return JSONResponse({

                "ok":
                    True,

                "tipo":
                    "2x1",

                "folio_1":
                    folio_1,

                "folio_2":
                    folio_2,

                "fecha_1_exp":
                    fecha_1_exp.strftime(
                        "%d/%m/%Y"
                    ),

                "fecha_1_ven":
                    fecha_1_ven.strftime(
                        "%d/%m/%Y"
                    ),

                "fecha_2_exp":
                    fecha_2_exp.strftime(
                        "%d/%m/%Y"
                    ),

                "fecha_2_ven":
                    fecha_2_ven.strftime(
                        "%d/%m/%Y"
                    ),

                "pdf_url":
                    f"/admin/descargar/{nombre_pdf}"
            })

    except Exception as e:

        print(
            f"❌ Error creando permiso: {e}"
        )

        # =====================================================
        # ROLLBACK COMPENSATORIO
        #
        # Si el 2x1 alcanzó a insertar el primer folio
        # pero falla después, borramos lo insertado.
        # =====================================================

        for folio in folios_creados:

            try:

                supabase.table(
                    "folios_registrados"
                ).delete().eq(
                    "folio",
                    folio
                ).execute()

            except Exception as rollback_error:

                print(
                    "⚠ Error rollback "
                    f"{folio}: "
                    f"{rollback_error}"
                )

        return JSONResponse({

            "ok":
                False,

            "error":
                str(e)

        })

@app.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_usuarios(request: Request):

    if not request.session.get("admin"):
        return RedirectResponse(
            "/login",
            status_code=302
        )

    try:

        resp = (
            supabase
            .table("clientes_permisos")
            .select("*")
            .order(
                "creado_en",
                desc=True
            )
            .execute()
        )

        clientes = resp.data or []

    except Exception as e:

        print(
            f"[ADMIN USUARIOS] Error: {e}"
        )

        clientes = []

    filas_html = ""

    for cliente in clientes:

        cliente_id = int(
            cliente.get(
                "id",
                0
            )
        )

        usuario = html_lib.escape(
            str(
                cliente.get(
                    "usuario",
                    ""
                )
            )
        )

        nombre = html_lib.escape(
            str(
                cliente.get(
                    "nombre",
                    ""
                )
                or ""
            )
        )

        asignados = int(
            cliente.get(
                "permisos_asignados",
                0
            )
            or 0
        )

        usados = int(
            cliente.get(
                "permisos_usados",
                0
            )
            or 0
        )

        restantes = max(
            0,
            asignados - usados
        )

        if asignados > 0:

            porcentaje = round(
                (
                    usados
                    /
                    asignados
                )
                *
                100,
                1
            )

        else:

            porcentaje = 0

        porcentaje_visual = min(
            porcentaje,
            100
        )

        activo = bool(
            cliente.get(
                "activo",
                True
            )
        )

        estado_texto = (
            "ACTIVO"
            if activo
            else
            "BLOQUEADO"
        )

        estado_clase = (
            "activo"
            if activo
            else
            "bloqueado"
        )

        boton_estado = (
            "Bloquear"
            if activo
            else
            "Desbloquear"
        )

        filas_html += f"""

        <article class="cliente-card">

            <div class="cliente-top">

                <div>

                    <h3>
                        {usuario}
                    </h3>

                    <p>
                        {nombre if nombre else "Sin nombre registrado"}
                    </p>

                </div>


                <span
                    class="estado {estado_clase}"
                >
                    {estado_texto}
                </span>

            </div>


            <div class="numeros">

                <div class="numero">

                    <span>
                        Asignados
                    </span>

                    <strong>
                        {asignados}
                    </strong>

                </div>


                <div class="numero">

                    <span>
                        Usados
                    </span>

                    <strong>
                        {usados}
                    </strong>

                </div>


                <div class="numero">

                    <span>
                        Restantes
                    </span>

                    <strong>
                        {restantes}
                    </strong>

                </div>


                <div class="numero">

                    <span>
                        Consumo
                    </span>

                    <strong>
                        {porcentaje}%
                    </strong>

                </div>

            </div>


            <div class="barra-fondo">

                <div
                    class="barra-progreso"
                    style="
                        width:{porcentaje_visual}%;
                    "
                ></div>

            </div>


            <div class="barra-texto">

                {usados} de {asignados}
                permisos utilizados

            </div>


            <div class="acciones-cliente">


                <button
                    onclick="
                        recargar(
                            {cliente_id},
                            10
                        )
                    "
                >
                    +10
                </button>


                <button
                    onclick="
                        recargar(
                            {cliente_id},
                            20
                        )
                    "
                >
                    +20
                </button>


                <button
                    onclick="
                        recargar(
                            {cliente_id},
                            50
                        )
                    "
                >
                    +50
                </button>


                <button
                    onclick="
                        recargar(
                            {cliente_id},
                            100
                        )
                    "
                >
                    +100
                </button>


                <button
                    onclick="
                        recargar(
                            {cliente_id},
                            1000
                        )
                    "
                >
                    +1000
                </button>


                <button
                    class="personalizado"
                    onclick="
                        recargaPersonalizada(
                            {cliente_id}
                        )
                    "
                >
                    Otra cantidad
                </button>


                <button
                    class="estado-btn"
                    onclick="
                        cambiarEstado(
                            {cliente_id},
                            {str(not activo).lower()}
                        )
                    "
                >
                    {boton_estado}
                </button>

            </div>

        </article>

        """

    if not filas_html:

        filas_html = """

        <div class="vacio">

            Todavía no hay cuentas de clientes.

        </div>

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
    Administración de usuarios
</title>


<style>

:root {{

    --vino:#5f1b2d;
    --vino-oscuro:#48101e;
    --dorado:#c09761;
    --gris:#858585;
    --gris-claro:#f5f5f5;
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


/* =====================================================
   HEADER
===================================================== */

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

.frase-header {{

    width:300px;
}}


/* =====================================================
   MENU
===================================================== */

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

    padding:
        17px 15px;

    color:white;

    text-decoration:none;

    font-size:14px;

    white-space:nowrap;
}}

.menu a:hover,
.menu a.active {{

    background:
        rgba(
            255,
            255,
            255,
            .12
        );
}}

.menu .cerrar {{

    background:
        rgba(
            0,
            0,
            0,
            .16
        );

    border-radius:7px;

    padding:
        10px 15px;
}}


/* =====================================================
   HERO
===================================================== */

.hero {{

    position:relative;

    background:

        linear-gradient(
            120deg,
            #f8f8f8,
            #eeeeee
        );

    padding:
        50px 20px
        90px;

    text-align:center;
}}

.hero::after {{

    content:"";

    position:absolute;

    bottom:0;

    left:0;

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

    font-size:
        16px;
}}


/* =====================================================
   CONTENIDO
===================================================== */

.contenido {{

    padding:
        0 20px
        60px;
}}

.panel {{

    position:relative;

    z-index:2;

    max-width:1100px;

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
        rgba(
            0,
            0,
            0,
            .11
        );
}}


/* =====================================================
   CREAR CUENTA
===================================================== */

.titulo {{

    color:
        var(--vino);

    font-size:
        24px;

    font-weight:
        400;

    margin-bottom:
        6px;
}}

.subtitulo {{

    color:#888;

    font-size:
        14px;

    margin-bottom:
        24px;
}}

.crear-box {{

    background:
        #faf7f3;

    border-left:
        4px solid
        var(--dorado);

    border-radius:
        12px;

    padding:
        22px;

    margin-bottom:
        35px;
}}

.grid {{

    display:grid;

    grid-template-columns:
        repeat(
            2,
            minmax(
                0,
                1fr
            )
        );

    gap:
        16px;
}}

label {{

    display:block;

    color:#666;

    font-size:
        12px;

    font-weight:
        bold;

    text-transform:
        uppercase;

    margin-bottom:
        7px;
}}

input {{

    width:100%;

    border:
        1px solid
        #d4d4d4;

    background:white;

    padding:
        13px 14px;

    border-radius:
        8px;

    font-size:
        15px;

    outline:none;
}}

input:focus {{

    border-color:
        var(--dorado);

    box-shadow:
        0 0 0
        3px rgba(
            192,
            151,
            97,
            .15
        );
}}

.campo {{

    margin-bottom:
        16px;
}}

.crear-btn {{

    border:0;

    background:
        var(--dorado);

    color:white;

    padding:
        13px 23px;

    border-radius:
        8px;

    cursor:pointer;

    font-weight:
        bold;

    font-size:
        15px;
}}

.crear-btn:hover {{

    background:
        #ad804c;
}}


/* =====================================================
   CLIENTES
===================================================== */

.seccion-titulo {{

    color:
        var(--vino);

    font-size:
        22px;

    font-weight:
        400;

    padding-bottom:
        12px;

    margin-bottom:
        20px;

    border-bottom:
        1px solid
        #e8e8e8;
}}

.cliente-card {{

    border:
        1px solid
        #e5e5e5;

    border-radius:
        14px;

    padding:
        22px;

    margin-bottom:
        18px;

    background:
        white;
}}

.cliente-top {{

    display:flex;

    justify-content:
        space-between;

    align-items:
        flex-start;

    gap:
        20px;

    margin-bottom:
        20px;
}}

.cliente-top h3 {{

    color:
        var(--vino);

    font-size:
        21px;

    margin-bottom:
        4px;
}}

.cliente-top p {{

    color:#888;

    font-size:
        13px;
}}

.estado {{

    font-size:
        11px;

    font-weight:
        bold;

    padding:
        7px 10px;

    border-radius:
        20px;
}}

.estado.activo {{

    background:
        #dff3e8;

    color:
        var(--verde);
}}

.estado.bloqueado {{

    background:
        #f6dfe2;

    color:
        var(--rojo);
}}


/* =====================================================
   NUMEROS
===================================================== */

.numeros {{

    display:grid;

    grid-template-columns:
        repeat(
            4,
            1fr
        );

    gap:
        10px;

    margin-bottom:
        17px;
}}

.numero {{

    background:
        var(--gris-claro);

    border-radius:
        10px;

    padding:
        14px;
}}

.numero span {{

    display:block;

    color:#888;

    font-size:
        11px;

    text-transform:
        uppercase;

    margin-bottom:
        6px;
}}

.numero strong {{

    color:
        var(--vino);

    font-size:
        24px;
}}


/* =====================================================
   BARRA
===================================================== */

.barra-fondo {{

    width:100%;

    height:14px;

    background:
        #eeeeee;

    border-radius:
        30px;

    overflow:hidden;
}}

.barra-progreso {{

    height:100%;

    background:

        linear-gradient(
            90deg,
            var(--dorado),
            var(--vino)
        );

    border-radius:
        30px;
}}

.barra-texto {{

    color:#888;

    font-size:
        12px;

    margin-top:
        7px;

    margin-bottom:
        17px;
}}


/* =====================================================
   ACCIONES
===================================================== */

.acciones-cliente {{

    display:flex;

    flex-wrap:wrap;

    gap:
        8px;
}}

.acciones-cliente button {{

    border:0;

    background:
        #eeeeee;

    color:#555;

    padding:
        9px 13px;

    border-radius:
        7px;

    cursor:pointer;
}}

.acciones-cliente button:hover {{

    background:
        #dddddd;
}}

.acciones-cliente
.personalizado {{

    background:
        #faf7f3;

    color:
        var(--vino);
}}

.acciones-cliente
.estado-btn {{

    margin-left:auto;

    background:
        var(--vino);

    color:white;
}}

.vacio {{

    text-align:center;

    padding:
        35px;

    color:#999;

    background:
        #f7f7f7;

    border-radius:
        12px;
}}


/* =====================================================
   MENSAJES
===================================================== */

.mensaje {{

    display:none;

    margin-bottom:
        20px;

    border-radius:
        9px;

    padding:
        14px 16px;
}}

.mensaje.ok {{

    background:
        #e3f3e8;

    color:
        #155724;
}}

.mensaje.error {{

    background:
        #f8d7da;

    color:
        #721c24;
}}


/* =====================================================
   RESPONSIVE
===================================================== */

@media(max-width:700px) {{

    .header-inner {{

        display:block;

        padding:
            15px;
    }}

    .logos {{

        justify-content:
            center;

        gap:
            8px;
    }}

    .logo-gob {{

        width:50%;
    }}

    .logo-secretaria {{

        width:43%;
    }}

    .frase-header {{

        display:none;
    }}

    .menu-inner {{

        padding:
            0 10px;

        display:block;
    }}

    .menu-links {{

        overflow-x:auto;
    }}

    .menu .cerrar {{

        text-align:center;

        margin:
            6px 0 10px;
    }}

    .hero h1 {{

        font-size:
            27px;
    }}

    .panel {{

        padding:
            24px 16px;

        border-radius:
            17px;
    }}

    .grid {{

        grid-template-columns:
            1fr;
    }}

    .numeros {{

        grid-template-columns:
            1fr 1fr;
    }}

    .acciones-cliente
    .estado-btn {{

        margin-left:0;
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
    class="frase-header"
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

<a
    href="/admin/usuarios"
    class="active"
>
    Usuarios
</a>

<a href="/admin/tablas">
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
    Usuarios
</h1>

<p>
    Administración de cuentas y paquetes de permisos
</p>

</section>


<main class="contenido">


<section class="panel">


<div
    id="mensaje"
    class="mensaje"
></div>


<h2 class="titulo">
    Crear nueva cuenta
</h2>

<p class="subtitulo">

    Asigne un usuario,
    contraseña y paquete inicial.

</p>


<div class="crear-box">


<form id="crearUsuario">


<div class="grid">


<div class="campo">

<label>
    Nombre / Cliente
</label>

<input
    name="nombre"
    type="text"
    placeholder="Ej: Agencia López"
>

</div>


<div class="campo">

<label>
    Usuario
</label>

<input
    name="usuario"
    type="text"
    required
    autocomplete="off"
>

</div>


<div class="campo">

<label>
    Contraseña
</label>

<input
    name="password"
    type="password"
    minlength="6"
    required
    autocomplete="new-password"
>

</div>


<div class="campo">

<label>
    Permisos iniciales
</label>

<input
    name="permisos"
    type="number"
    min="0"
    max="1000000"
    value="20"
    required
>

</div>


</div>


<button
    class="crear-btn"
    type="submit"
>
    + Crear cuenta
</button>


</form>


</div>


<h2 class="seccion-titulo">

    Cuentas registradas

</h2>


<div id="clientes">

    {filas_html}

</div>


</section>


</main>


<script>


function mostrarMensaje(
    texto,
    tipo
) {{

    const caja =
        document.getElementById(
            "mensaje"
        );

    caja.className =
        "mensaje " + tipo;

    caja.textContent =
        texto;

    caja.style.display =
        "block";

    window.scrollTo({{
        top:0,
        behavior:"smooth"
    }});
}}


document
.getElementById(
    "crearUsuario"
)
.addEventListener(
    "submit",
    async function(e) {{

        e.preventDefault();

        const form =
            new FormData(this);

        const datos =
            Object.fromEntries(
                form
            );

        const res =
            await fetch(
                "/admin/usuarios/crear",
                {{
                    method:"POST",

                    headers:{{
                        "Content-Type":
                            "application/json"
                    }},

                    body:
                        JSON.stringify(
                            datos
                        )
                }}
            );

        const result =
            await res.json();

        if(result.ok) {{

            mostrarMensaje(
                "Cuenta creada correctamente",
                "ok"
            );

            setTimeout(
                () =>
                    location.reload(),
                600
            );

        }} else {{

            mostrarMensaje(
                result.error
                ||
                "No fue posible crear la cuenta",
                "error"
            );
        }}

    }}
);


async function recargar(
    clienteId,
    cantidad
) {{

    const confirmar =
        confirm(
            "¿Agregar "
            + cantidad
            + " permisos a esta cuenta?"
        );

    if(!confirmar)
        return;


    const res =
        await fetch(
            "/admin/usuarios/"
            + clienteId
            + "/recargar",
            {{
                method:"POST",

                headers:{{
                    "Content-Type":
                        "application/json"
                }},

                body:
                    JSON.stringify({{
                        cantidad:cantidad
                    }})
            }}
        );

    const result =
        await res.json();


    if(result.ok) {{

        location.reload();

    }} else {{

        alert(
            result.error
            ||
            "Error al recargar"
        );
    }}

}}


function recargaPersonalizada(
    clienteId
) {{

    const valor =
        prompt(
            "¿Cuántos permisos quieres agregar?"
        );

    if(valor === null)
        return;

    const cantidad =
        parseInt(
            valor
        );

    if(
        !cantidad
        ||
        cantidad <= 0
    ) {{

        alert(
            "Cantidad inválida"
        );

        return;
    }}

    recargar(
        clienteId,
        cantidad
    );
}}


async function cambiarEstado(
    clienteId,
    nuevoEstado
) {{

    const texto =
        nuevoEstado
        ?
        "desbloquear"
        :
        "bloquear";

    if(
        !confirm(
            "¿Seguro que quieres "
            + texto
            + " esta cuenta?"
        )
    )
        return;


    const res =
        await fetch(
            "/admin/usuarios/"
            + clienteId
            + "/estado",
            {{
                method:"POST",

                headers:{{
                    "Content-Type":
                        "application/json"
                }},

                body:
                    JSON.stringify({{
                        activo:
                            nuevoEstado
                    }})
            }}
        );


    const result =
        await res.json();


    if(result.ok) {{

        location.reload();

    }} else {{

        alert(
            result.error
            ||
            "No fue posible modificar la cuenta"
        );
    }}

}}


</script>


</body>

</html>
""")

@app.get("/admin/descargar/{archivo}")
async def descargar_pdf(
    archivo: str,
    request: Request
):
    if not request.session.get("admin"):
        raise HTTPException(
            status_code=401,
            detail="No autorizado"
        )

    # Evita rutas tipo ../../etc/passwd
    archivo_seguro = os.path.basename(archivo)

    ruta = os.path.join(
        OUTPUT_DIR,
        archivo_seguro
    )

    if not os.path.exists(ruta):
        raise HTTPException(
            status_code=404,
            detail="Archivo no encontrado"
        )

    return FileResponse(
        ruta,
        media_type="application/pdf",
        filename=archivo_seguro
    )

# ==================== GESTIONAR FOLIOS ====================
@app.get("/admin/folios", response_class=HTMLResponse)
async def gestionar_folios(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/login", status_code=302)

    return HTMLResponse("""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Gestionar Folios - Puebla</title>
    <style>
        :root { --vino: #5f1b2d; --azul: #001B4C; --dorado: #c79b66; --fondo: #f4f5f7; }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--fondo); font-family: Arial, sans-serif; color: #495057; }
        .layout { min-height: 100vh; display: grid; grid-template-columns: 250px 1fr; }
        .sidebar { background: var(--vino); color: white; padding: 24px 17px; }
        .brand { padding: 0 10px 23px; border-bottom: 1px solid rgba(255,255,255,0.18); margin-bottom: 20px; }
        .brand h2 { margin: 0; font-size: 1.25rem; }
        .brand p { margin: 5px 0 0; opacity: 0.72; font-size: 0.8rem; }
        .menu { display: flex; flex-direction: column; gap: 6px; }
        .menu a { color: white; text-decoration: none; padding: 12px 13px; border-radius: 8px; font-size: 0.92rem; }
        .menu a:hover, .menu a.active { background: rgba(255,255,255,0.14); }
        .logout { margin-top: 12px; background: rgba(0,0,0,0.15); }
        .topbar { background: white; min-height: 72px; padding: 0 28px; display: flex; align-items: center; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
        .topbar h1 { margin: 0; color: var(--azul); font-size: 1.35rem; }
        .content { padding: 30px; }
        .panel { background: white; border-radius: 14px; padding: 30px; box-shadow: 0 3px 12px rgba(0,0,0,0.06); }
        .search-bar { display: grid; grid-template-columns: 1fr 1fr 1fr auto auto; gap: 12px; margin-bottom: 25px; }
        input { padding: 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; }
        input:focus { outline: none; border-color: var(--dorado); box-shadow: 0 0 0 3px rgba(199,155,102,0.15); }
        button { background: var(--dorado); color: white; border: none; padding: 12px 20px; border-radius: 8px; font-size: 1rem; cursor: pointer; font-weight: 600; }
        button:hover { background: #b8894e; }
        .btn-delete { background: #dc3545; }
        .btn-delete:hover { background: #c82333; }
        table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
        th { background: var(--azul); color: white; padding: 15px; text-align: left; font-weight: 600; }
        td { padding: 12px 15px; border-bottom: 1px solid #eee; }
        tr:hover { background: #f9f9f9; }
        .checkbox { width: 20px; height: 20px; cursor: pointer; }
        .estado-vigente { background: #d4edda; color: #155724; padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; }
        .estado-vencido { background: #fff3cd; color: #856404; padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; }
        .loading { display: none; text-align: center; padding: 40px; }
        .spinner { display: inline-block; width: 30px; height: 30px; border: 3px solid #f3f3f3; border-top: 3px solid var(--dorado); border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .error { background: #f8d7da; color: #721c24; border: 1px solid #e7abb1; border-radius: 8px; padding: 15px; margin-bottom: 20px; display: none; }
        .success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; border-radius: 8px; padding: 15px; margin-bottom: 20px; display: none; }
        .info-bar { background: #e7f3ff; color: #004085; border: 1px solid #b8daff; border-radius: 8px; padding: 15px; margin-bottom: 20px; }
        @media(max-width:900px) { .layout { grid-template-columns: 1fr; } .sidebar { display: none; } .search-bar { grid-template-columns: 1fr; } table { font-size: 0.85rem; } }
    </style>
</head>
<body>
    <div class="layout">
        <aside class="sidebar">
            <div class="brand"><h2>Panel Puebla</h2><p>Administración</p></div>
            <nav class="menu">
                <a href="/admin">📊 Dashboard</a>
                <a href="/admin/crear">➕ Crear permiso</a>
                <a href="/admin/folios" class="active">📄 Gestionar folios</a>
                <a href="/logout" class="logout">🚪 Salir</a>
            </nav>
        </aside>
        <section class="main" style="min-width:0;">
            <header class="topbar"><h1>Gestionar Folios</h1></header>
            <main class="content">
                <div class="panel">
                    <div id="error" class="error"></div>
                    <div id="success" class="success"></div>
                    <div class="info-bar">
                        📊 Total folios en BD: <strong id="total_count">0</strong> | Vigentes: <strong id="vigentes_count">0</strong> | Vencidos: <strong id="vencidos_count">0</strong>
                    </div>

                    <div class="search-bar">
                        <input type="text" id="buscar_folio" placeholder="Buscar por folio..." onkeyup="buscar()">
                        <input type="text" id="buscar_serie" placeholder="Buscar por serie..." onkeyup="buscar()">
                        <input type="text" id="buscar_entidad" placeholder="Entidad (ej: puebla)..." onkeyup="buscar()">
                        <button onclick="buscar()">🔍 Buscar</button>
                        <button class="btn-delete" onclick="eliminarSeleccionados()">🗑 Eliminar</button>
                    </div>

                    <div id="loading" class="loading"><div class="spinner"></div></div>
                    
                    <div id="resultado" style="display:none;">
                        <p style="margin-bottom:15px;">Resultados encontrados: <strong id="results_count">0</strong></p>
                        <table id="tabla_folios">
                            <thead>
                                <tr>
                                    <th style="width:30px;"><input type="checkbox" id="selectAll" onchange="seleccionarTodos()"></th>
                                    <th>Folio</th>
                                    <th>Marca</th>
                                    <th>Serie</th>
                                    <th>Contribuyente</th>
                                    <th>Expedición</th>
                                    <th>Vencimiento</th>
                                    <th>Estado</th>
                                    <th>Entidad</th>
                                </tr>
                            </thead>
                            <tbody id="tabla_body">
                            </tbody>
                        </table>
                    </div>
                </div>
            </main>
        </section>
    </div>

    <script>
        let folios_data = [];

        async function cargarFolios() {
            document.getElementById('loading').style.display = 'block';
            document.getElementById('resultado').style.display = 'none';

            try {
                const res = await fetch('/admin/api/folios');
                const data = await res.json();
                
                folios_data = data.folios || [];
                document.getElementById('total_count').textContent = data.total;
                document.getElementById('vigentes_count').textContent = data.vigentes;
                document.getElementById('vencidos_count').textContent = data.vencidos;
                
                document.getElementById('loading').style.display = 'none';
                mostrarResultados(folios_data);
            } catch (err) {
                document.getElementById('error').style.display = 'block';
                document.getElementById('error').textContent = '✗ Error cargando folios: ' + err.message;
                document.getElementById('loading').style.display = 'none';
            }
        }

        async function buscar() {
            const folio = document.getElementById('buscar_folio').value.trim();
            const serie = document.getElementById('buscar_serie').value.trim();
            const entidad = document.getElementById('buscar_entidad').value.trim();

            if (!folio && !serie && !entidad) {
                mostrarResultados(folios_data);
                return;
            }

            let resultado = folios_data;
            if (folio) resultado = resultado.filter(f => f.folio.includes(folio.toUpperCase()));
            if (serie) resultado = resultado.filter(f => f.numero_serie.includes(serie.toUpperCase()));
            if (entidad) resultado = resultado.filter(f => f.entidad.toLowerCase().includes(entidad.toLowerCase()));

            mostrarResultados(resultado);
        }

        function mostrarResultados(folios) {
            const tbody = document.getElementById('tabla_body');
            const results = document.getElementById('results_count');
            tbody.innerHTML = '';
            results.textContent = folios.length;

            if (folios.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#999;">No hay resultados</td></tr>';
                document.getElementById('resultado').style.display = 'block';
                return;
            }

            const hoy = new Date().toISOString().split('T')[0];

            folios.forEach((f, idx) => {
                const vigente = f.fecha_vencimiento >= hoy ? 'vigente' : 'vencido';
                const row = `<tr>
                    <td><input type="checkbox" class="checkbox checkbox_folio" data-folio="${f.folio}"></td>
                    <td><strong>${f.folio}</strong></td>
                    <td>${f.marca || '—'}</td>
                    <td>${f.numero_serie || '—'}</td>
                    <td>${f.contribuyente || '—'}</td>
                    <td>${f.fecha_expedicion || '—'}</td>
                    <td>${f.fecha_vencimiento || '—'}</td>
                    <td><span class="estado-${vigente}">${vigente.toUpperCase()}</span></td>
                    <td>${f.entidad || '—'}</td>
                </tr>`;
                tbody.innerHTML += row;
            });

            document.getElementById('resultado').style.display = 'block';
        }

        function seleccionarTodos() {
            const checked = document.getElementById('selectAll').checked;
            document.querySelectorAll('.checkbox_folio').forEach(cb => cb.checked = checked);
        }

        async function eliminarSeleccionados() {
            const seleccionados = Array.from(document.querySelectorAll('.checkbox_folio:checked')).map(cb => cb.dataset.folio);
            
            if (seleccionados.length === 0) {
                document.getElementById('error').style.display = 'block';
                document.getElementById('error').textContent = '✗ Selecciona al menos un folio';
                return;
            }

            if (!confirm(`¿Eliminar ${seleccionados.length} folios? Esta acción no se puede deshacer.`)) return;

            try {
                const res = await fetch('/admin/api/eliminar_folios', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folios: seleccionados })
                });

                const result = await res.json();
                
                document.getElementById('success').style.display = 'block';
                document.getElementById('success').textContent = `✓ Eliminados: ${result.eliminados} | Errores: ${result.errores}`;
                
                cargarFolios();
            } catch (err) {
                document.getElementById('error').style.display = 'block';
                document.getElementById('error').textContent = '✗ Error: ' + err.message;
            }
        }

        cargarFolios();
    </script>
</body>
</html>
""")

@app.post("/admin/usuarios/crear")
async def admin_crear_usuario(
    request: Request
):

    if not request.session.get("admin"):

        return JSONResponse(
            {
                "ok": False,
                "error": "Sesión no válida"
            },
            status_code=401
        )

    try:

        datos = await request.json()

        usuario = (
            str(
                datos.get(
                    "usuario",
                    ""
                )
            )
            .strip()
            .upper()
        )

        nombre = (
            str(
                datos.get(
                    "nombre",
                    ""
                )
            )
            .strip()
        )

        password = str(
            datos.get(
                "password",
                ""
            )
        )

        permisos = int(
            datos.get(
                "permisos",
                0
            )
        )


        if not usuario:

            raise ValueError(
                "Debes escribir un usuario"
            )


        if len(usuario) < 3:

            raise ValueError(
                "El usuario debe tener mínimo 3 caracteres"
            )


        if len(password) < 6:

            raise ValueError(
                "La contraseña debe tener mínimo 6 caracteres"
            )


        if permisos < 0:

            raise ValueError(
                "Los permisos no pueden ser negativos"
            )


        if permisos > 1_000_000:

            raise ValueError(
                "Cantidad de permisos demasiado grande"
            )


        existe = (
            supabase
            .table(
                "clientes_permisos"
            )
            .select("id")
            .eq(
                "usuario",
                usuario
            )
            .limit(1)
            .execute()
        )


        if existe.data:

            return JSONResponse(
                {
                    "ok": False,
                    "error":
                        "Ese usuario ya existe"
                },
                status_code=409
            )


        password_hash = (
            crear_password_hash(
                password
            )
        )


        resp = (
            supabase
            .table(
                "clientes_permisos"
            )
            .insert(
                {
                    "usuario":
                        usuario,

                    "nombre":
                        nombre,

                    "password_hash":
                        password_hash,

                    "activo":
                        True,

                    "permisos_asignados":
                        permisos,

                    "permisos_usados":
                        0
                }
            )
            .execute()
        )


        return {
            "ok": True,
            "cliente":
                resp.data
        }


    except Exception as e:

        print(
            "[CREAR CLIENTE]",
            e
        )

        return JSONResponse(
            {
                "ok": False,
                "error": str(e)
            },
            status_code=400
    )
    
@app.get("/admin/api/folios")
async def api_get_folios(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(status_code=401)

    try:
        resp = supabase.table("folios_registrados").select("*").eq("entidad", ENTIDAD).execute()
        folios = resp.data or []
    except:
        folios = []
    
    tz = ZoneInfo(TZ)
    hoy = datetime.now(tz).date()
    
    vigentes = vencidos = 0
    
    for f in folios:
        try:
            fv = datetime.fromisoformat(str(f["fecha_vencimiento"]).replace("Z", "+00:00")).date()
            if hoy <= fv:
                vigentes += 1
            else:
                vencidos += 1
        except:
            pass
    
    return {
        "folios": folios,
        "total": len(folios),
        "vigentes": vigentes,
        "vencidos": vencidos
    }

@app.post("/admin/api/eliminar_folios")
async def api_eliminar_folios(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(status_code=401)

    datos = await request.json()
    folios = datos.get("folios", [])
    
    eliminados = 0
    errores = 0
    
    for folio in folios:
        try:
            supabase.table("folios_registrados").delete().eq("folio", folio).execute()
            supabase.table("borradores_registros").delete().eq("folio", folio).execute()
            eliminados += 1
        except:
            errores += 1
    
    return {
        "total": len(folios),
        "eliminados": eliminados,
        "errores": errores
    }

# ==================== FIN DE EXTENSIONES ====================


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
