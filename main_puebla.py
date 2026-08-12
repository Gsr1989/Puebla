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
    html = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Secretaría de Movilidad y Transporte - Consulta de Permisos</title>
    <link href="https://cdn.jsdelivr.net/npm/uikit@3.23.5/dist/css/uikit.min.css" rel="stylesheet" />
    <style>
        :root { --template-special-color: #001B4C; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { height: 100%; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", sans-serif;
            background: #f5f5f5; 
            color: #495057;
            display: flex;
            flex-direction: column;
        }
        .header { 
            background: white; 
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); 
            padding: 20px 0;
            flex-shrink: 0;
        }
        .container { 
            max-width: 1200px; 
            margin: 0 auto; 
            padding: 0 20px; 
        }
        .header h1 { 
            font-size: 1.5rem; 
            color: #001B4C; 
            font-weight: 300; 
            margin: 0;
        }
        .header p { 
            color: #949494; 
            font-size: 0.9rem; 
            margin: 5px 0 0 0;
        }
        .main-content {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 40px 20px;
        }
        .consulta-section { 
            background: white; 
            border-radius: 20px; 
            padding: 40px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
            max-width: 900px;
            width: 100%;
        }
        .consulta-titulo { 
            font-size: 2rem; 
            color: #001B4C; 
            text-align: center; 
            margin-bottom: 30px; 
            font-weight: 300;
        }
        .formulario-consulta { 
            display: grid; 
            grid-template-columns: 1fr 1fr 1fr auto; 
            gap: 15px; 
            margin-bottom: 40px; 
            align-items: flex-end;
        }
        .form-group { 
            display: flex; 
            flex-direction: column;
        }
        .form-group label { 
            font-size: 0.95rem; 
            font-weight: 600; 
            color: #495057; 
            margin-bottom: 8px;
        }
        .form-group input { 
            padding: 12px 15px; 
            border: 1px solid #ddd; 
            border-radius: 8px; 
            font-size: 1rem;
            font-family: inherit;
        }
        .form-group input:focus { 
            outline: none; 
            border-color: #001B4C; 
            box-shadow: 0 0 0 3px rgba(0, 27, 76, 0.1);
        }
        .btn-consultar { 
            padding: 12px 30px; 
            background: #c79b66; 
            color: white; 
            border: none; 
            border-radius: 8px; 
            font-size: 1rem; 
            font-weight: 600; 
            cursor: pointer;
        }
        .btn-consultar:hover { 
            background: #b8894e;
        }
        .resultado-container { 
            margin-top: 40px; 
            display: none;
        }
        .resultado-container.visible { 
            display: block;
        }
        .estado-folio { 
            text-align: center; 
            margin-bottom: 20px; 
            padding: 20px; 
            border-radius: 12px; 
            font-size: 1.1rem; 
            font-weight: 600;
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
            background: #f6f6f6; 
            border-radius: 12px; 
            padding: 30px; 
            margin-top: 20px;
        }
        .resultado-grid { 
            display: grid; 
            grid-template-columns: 1fr 1fr; 
            gap: 20px 40px;
        }
        .resultado-item { 
            padding: 15px; 
            background: white; 
            border-radius: 8px; 
            border-left: 4px solid #c79b66;
        }
        .resultado-label { 
            font-size: 0.85rem; 
            font-weight: 700; 
            color: #949494; 
            text-transform: uppercase; 
            margin-bottom: 5px;
        }
        .resultado-valor { 
            font-size: 1.1rem; 
            color: #001B4C; 
            font-weight: 500;
        }
        .footer { 
            background: #5f1b2d; 
            color: #fffbef; 
            padding: 40px 0; 
            text-align: center;
            flex-shrink: 0;
        }
        .loading { 
            display: none; 
            text-align: center; 
            padding: 20px;
        }
        .loading.active { 
            display: block;
        }
        .spinner { 
            border: 3px solid #f3f3f3; 
            border-top: 3px solid #c79b66; 
            border-radius: 50%; 
            width: 40px; 
            height: 40px; 
            animation: spin 1s linear infinite; 
            margin: 0 auto;
        }
        @keyframes spin { 
            0% { transform: rotate(0deg); } 
            100% { transform: rotate(360deg); } 
        }
        @media (max-width: 768px) { 
            .formulario-consulta { grid-template-columns: 1fr; } 
            .btn-consultar { width: 100%; } 
            .resultado-grid { grid-template-columns: 1fr; }
            .header { padding: 15px 0; }
            .header h1 { font-size: 1.2rem; }
            .consulta-titulo { font-size: 1.5rem; }
        }
    </style>
</head>
<body>
    <header class="header">
        <div class="container">
            <h1>Secretaría de Movilidad y Transporte</h1>
            <p>Consulta de Permisos Vehiculares</p>
        </div>
    </header>
    
    <div class="main-content">
        <div class="consulta-section">
            <h2 class="consulta-titulo">Consulta de Permisos</h2>
            <form class="formulario-consulta" id="formularioConsulta" onsubmit="buscar(event)">
                <div class="form-group">
                    <label for="folio">Folio</label>
                    <input type="text" id="folio" placeholder="722000001" required>
                </div>
                <div class="form-group">
                    <label for="placa">Placa</label>
                    <input type="text" id="placa" placeholder="ABC-1234">
                </div>
                <div class="form-group">
                    <label for="serie">Serie</label>
                    <input type="text" id="serie" placeholder="VIN">
                </div>
                <div class="form-group">
                    <button type="submit" class="btn-consultar">Consultar</button>
                </div>
            </form>
            <div class="loading" id="loading">
                <div class="spinner"></div>
                <p style="margin-top: 10px; color: #949494;">Buscando...</p>
            </div>
            <div class="resultado-container" id="resultado">
                <div class="estado-folio" id="estado"></div>
                <div class="resultado-tabla">
                    <div class="resultado-grid">
                        <div class="resultado-item"><div class="resultado-label">Folio</div><div class="resultado-valor" id="resF">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Expedición</div><div class="resultado-valor" id="resExp">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Vencimiento</div><div class="resultado-valor" id="resVen">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Marca</div><div class="resultado-valor" id="resMar">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Línea</div><div class="resultado-valor" id="resLin">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Año</div><div class="resultado-valor" id="resAno">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Serie</div><div class="resultado-valor" id="resSer">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Motor</div><div class="resultado-valor" id="resMot">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Color</div><div class="resultado-valor" id="resCol">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Propietario</div><div class="resultado-valor" id="resPro">—</div></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <footer class="footer">
        <p>© 2024 Secretaría de Movilidad y Transporte | movilidadytransporte@puebla.gob.mx</p>
    </footer>
    
    <script>
        async function buscar(e) {
            e.preventDefault();
            const folio = document.getElementById('folio').value.toUpperCase().trim();
            document.getElementById('loading').classList.add('active');
            document.getElementById('resultado').classList.remove('visible');
            
            try {
                const res = await fetch(`/api/consultar_folio/${folio}`);
                const data = await res.json();
                document.getElementById('loading').classList.remove('active');
                
                if (!data.ok) {
                    mostrar_no_existe();
                    return;
                }
                
                const est = document.getElementById('estado');
                if (data.vigente) {
                    est.className = 'estado-folio vigente';
                    est.innerHTML = `✓ <strong>${data.folio}</strong> está vigente`;
                } else {
                    est.className = 'estado-folio vencido';
                    est.innerHTML = `⚠ <strong>${data.folio}</strong> está vencido`;
                }
                
                document.getElementById('resF').textContent = data.folio;
                document.getElementById('resExp').textContent = data.fecha_expedicion;
                document.getElementById('resVen').textContent = data.fecha_vencimiento;
                document.getElementById('resMar').textContent = data.marca;
                document.getElementById('resLin').textContent = data.linea;
                document.getElementById('resAno').textContent = data.anio;
                document.getElementById('resSer').textContent = data.numero_serie;
                document.getElementById('resMot').textContent = data.numero_motor;
                document.getElementById('resCol').textContent = data.color;
                document.getElementById('resPro').textContent = data.nombre;
                
                document.getElementById('resultado').classList.add('visible');
            } catch (e) {
                document.getElementById('loading').classList.remove('active');
                alert('Error: ' + e.message);
            }
        }
        
        function mostrar_no_existe() {
            const est = document.getElementById('estado');
            est.className = 'estado-folio no-existe';
            est.innerHTML = '✗ <strong>No existe</strong> permiso';
            ['resF', 'resExp', 'resVen', 'resMar', 'resLin', 'resAno', 'resSer', 'resMot', 'resCol', 'resPro'].forEach(id => {
                document.getElementById(id).textContent = '—';
            });
            document.getElementById('resultado').classList.add('visible');
        }
    </script>
</body>
</html>"""
    return HTMLResponse(html)

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
