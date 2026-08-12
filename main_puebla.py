from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import os
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import random
import qrcode
from io import BytesIO
import fitz

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
FOLIO_NUM_PREFIJO = "722"
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
        
        if len(doc) < 2:
            raise ValueError(f"❌ {PLANTILLA} debe tener 2 páginas")
        
        pg_permiso = doc[0]
        pg_recibo = doc[1]
        
        # PÁGINA 1 - PERMISO (ROJO) - Fuente SEGURA: "helv"
        pg_permiso.insert_text((245, 165), datos['folio'],
            fontsize=72, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((200, 270), datos['marca'].upper(),
            fontsize=20, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((280, 270), datos['linea'].upper(),
            fontsize=18, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((480, 270), datos['anio'],
            fontsize=20, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((200, 310), datos['motor'].upper(),
            fontsize=18, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((340, 310), datos['serie'].upper(),
            fontsize=17, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((200, 350), datos['color'].upper(),
            fontsize=20, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((180, 410), datos['fecha_exp'],
            fontsize=16, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((400, 410), datos['fecha_ven'],
            fontsize=16, color=(1, 0, 0), fontname="helv")
        
        # QR
        qr = qrcode.QRCode()
        qr.add_data(f"{BASE_URL}/estado_folio/{datos['folio']}")
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buf = BytesIO()
        img_qr.save(buf, format="PNG")
        buf.seek(0)
        qr_pix = fitz.Pixmap(buf.read())
        pg_permiso.insert_image(fitz.Rect(490, 200, 590, 300), pixmap=qr_pix, overlay=True)
        
        # PÁGINA 2 - RECIBO (NEGRO) - Fuente SEGURA: "helv"
        pg_recibo.insert_text((200, 150), "CENTRO INTEGRAL DE SERVICIOS",
            fontsize=14, color=(0,0,0), fontname="helv")
        pg_recibo.insert_text((180, 200), datos['fecha_exp'],
            fontsize=14, color=(0,0,0), fontname="helv")
        pg_recibo.insert_text((200, 280), datos["nombre"].upper(),
            fontsize=12, color=(0,0,0), fontname="helv")
        pg_recibo.insert_text((420, 150), datos['folio'],
            fontsize=64, color=(0,0,0), fontname="helv")
        
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
    await message.answer("Paso 7/7: NOMBRE COMPLETO del titular:")
    await state.set_state(PermisoForm.nombre)

@dp.message(PermisoForm.nombre)
async def get_nombre(message: types.Message, state: FSMContext):
    datos = await state.get_data()
    datos["nombre"] = message.text.upper().strip()
    datos["folio"] = await generar_folio_async()
    
    tz = ZoneInfo(TZ)
    hoy = datetime.now(tz)
    ven = hoy + timedelta(days=30)
    datos["fecha_exp"] = hoy.strftime("%d DE %B %Y").upper()
    datos["fecha_ven"] = ven.strftime("%d DE %B %Y").upper()
    
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
                f"📄 PERMISO + RECIBO — PUEBLA\n"
                f"Folio: PUE / {datos['folio']} / 2024\n\n"
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

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    await dp.feed_webhook_update(bot, types.Update(**data))
    return {"ok": True}

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
            box-shadow: 0 2px 7px rgba(0,0,0,.10);
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
                rgba(95,27,45,.96),
                rgba(0,27,76,.92));
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
            box-shadow: 0 6px 25px rgba(0,0,0,.09);
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
            box-shadow: 0 0 0 3px rgba(199,155,102,.15);
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
            box-shadow: 0 1px 3px rgba(0,0,0,.04);
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
            box-shadow: 0 3px 14px rgba(0,0,0,.06);
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
    """Endpoint para escaneo de QR - Muestra página con datos del folio"""
    folio = folio.strip().upper()
    
    html_respuesta = """<style>
.folio-resultado { background: white; border-radius: 20px; padding: 40px; margin: 40px auto; max-width: 900px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.estado-banner { text-align: center; padding: 20px; border-radius: 12px; margin-bottom: 30px; font-size: 1.1rem; font-weight: 600; }
.estado-vigente { background: #d4edda; color: #155724; border: 2px solid #28a745; }
.estado-vencido { background: #fff3cd; color: #856404; border: 2px solid #ffc107; }
.datos-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 20px; }
.dato-item { padding: 15px; background: #f9f9f9; border-left: 4px solid #c79b66; border-radius: 5px; }
.dato-label { font-size: 0.85rem; font-weight: 700; color: #949494; text-transform: uppercase; margin-bottom: 5px; }
.dato-valor { font-size: 1.1rem; color: #001B4C; font-weight: 500; }
@media (max-width: 600px) { .datos-grid { grid-template-columns: 1fr; } }
</style>"""
    
    try:
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).eq("entidad", ENTIDAD).limit(1).execute()
        
        if not res.data:
            return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>No Encontrado</title></head><body style="background:#f5f5f5;font-family:Arial;padding:20px"><div style="max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center"><h1>❌ Folio No Encontrado</h1><p>El folio no existe</p></div></body></html>"""
        
        r = res.data[0]
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        fecha_ven = datetime.fromisoformat(r["fecha_vencimiento"]).date()
        vigente = hoy <= fecha_ven
        
        estado_class = "estado-vigente" if vigente else "estado-vencido"
        estado_texto = "✓ VIGENTE" if vigente else "⚠ VENCIDO"
        
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Folio {folio}</title>
    {html_respuesta}
</head>
<body style="background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:20px">
    <div style="max-width:900px;margin:0 auto">
        <div style="background:white;padding:20px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:30px">
            <h1 style="color:#001B4C;font-size:1.5rem;margin:0">Secretaría de Movilidad y Transporte</h1>
            <p style="color:#949494;margin:5px 0 0 0">Consulta de Permiso</p>
        </div>
        <div class="folio-resultado">
            <div class="estado-banner {estado_class}">{estado_texto} - {folio}</div>
            <div class="datos-grid">
                <div class="dato-item"><div class="dato-label">Folio</div><div class="dato-valor">{folio}</div></div>
                <div class="dato-item"><div class="dato-label">Expedición</div><div class="dato-valor">{datetime.fromisoformat(r['fecha_expedicion']).strftime('%d/%m/%Y')}</div></div>
                <div class="dato-item"><div class="dato-label">Vencimiento</div><div class="dato-valor">{fecha_ven.strftime('%d/%m/%Y')}</div></div>
                <div class="dato-item"><div class="dato-label">Marca</div><div class="dato-valor">{r.get('marca', '—')}</div></div>
                <div class="dato-item"><div class="dato-label">Línea</div><div class="dato-valor">{r.get('linea', '—')}</div></div>
                <div class="dato-item"><div class="dato-label">Año</div><div class="dato-valor">{r.get('anio', '—')}</div></div>
                <div class="dato-item"><div class="dato-label">Serie</div><div class="dato-valor">{r.get('numero_serie', '—')}</div></div>
                <div class="dato-item"><div class="dato-label">Motor</div><div class="dato-valor">{r.get('numero_motor', '—')}</div></div>
                <div class="dato-item"><div class="dato-label">Color</div><div class="dato-valor">{r.get('color', '—')}</div></div>
                <div class="dato-item"><div class="dato-label">Propietario</div><div class="dato-valor">{r.get('contribuyente', '—')}</div></div>
            </div>
        </div>
        <div style="background:#5f1b2d;color:#fffbef;text-align:center;padding:20px;margin-top:30px">
            <p>© 2024 Secretaría de Movilidad y Transporte</p>
        </div>
    </div>
</body>
</html>"""
        
    except Exception as e:
        return f"<html><body style='background:#f5f5f5;padding:20px'><div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'><h1>❌ Error</h1><p>{str(e)}</p></div></body></html>"

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
