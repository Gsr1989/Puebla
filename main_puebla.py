from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import os
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import FSInputFile, ContentType, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from contextlib import asynccontextmanager, suppress
import asyncio
import aiohttp
import random
from PIL import Image
import qrcode
from io import BytesIO
import fitz
from starlette.middleware.sessions import SessionMiddleware

# ===================== CONFIGURACIÓN =====================
BOT_TOKEN        = os.getenv("BOT_TOKEN_PUEBLA", "")
SUPABASE_URL     = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY     = os.getenv("SUPABASE_KEY", "")
BASE_URL         = "https://smt-puebla-gob-mx.onrender.com"
OUTPUT_DIR       = "documentos"
PLANTILLA_PDF    = "DIGITAL_PUEBLA.pdf"
PLANTILLA_RECIBO = "Recibo-puebla.pdf"
ENTIDAD          = "puebla"
PRECIO_PERMISO   = 180
TZ               = "America/Mexico_City"

# FIX: credenciales desde variables de entorno
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24).hex())

TEMPLATES_DIR = "templates"
STATIC_DIR    = "static"
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR,    exist_ok=True)
os.makedirs(STATIC_DIR,    exist_ok=True)

templates  = Jinja2Templates(directory=TEMPLATES_DIR)
_jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

_bot_session = AiohttpSession(timeout=aiohttp.ClientTimeout(total=300))
bot     = Bot(token=BOT_TOKEN, session=_bot_session)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ===================== CONSECUTIVOS =====================
CONSECUTIVOS_INICIALES = {
    "recibo_ingreso": 403202608800627,
    "pase_caja":      9000002373220,
    "numero_1":       93161700,
    "numero_2":       47101510
}

def obtener_siguiente_consecutivo(tipo: str) -> int:
    for intento in range(1000):
        try:
            resp = supabase.table("consecutivos_puebla") \
                .select("valor").eq("tipo", tipo) \
                .order("valor", desc=True).limit(1).execute()
            siguiente = (int(resp.data[0]["valor"]) + 1) if resp.data else CONSECUTIVOS_INICIALES[tipo]
            supabase.table("consecutivos_puebla").insert({
                "tipo": tipo, "valor": siguiente,
                "created_at": datetime.now(ZoneInfo(TZ)).isoformat()
            }).execute()
            print(f"[CONSECUTIVO] {tipo}: {siguiente} (intento {intento+1})")
            return siguiente
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                continue
            raise e
    return CONSECUTIVOS_INICIALES[tipo] + random.randint(1000, 9999)

# ===================== TIMERS 36H =====================
timers_activos       = {}
user_folios          = {}
pending_comprobantes = {}
TOTAL_MINUTOS_TIMER  = 36 * 60

async def eliminar_folio_automatico(folio: str):
    try:
        uid = timers_activos[folio]["user_id"] if folio in timers_activos else None
        await asyncio.to_thread(lambda: (
            supabase.table("folios_registrados").delete().eq("folio", folio).execute(),
            supabase.table("borradores_registros").delete().eq("folio", folio).execute(),
        ))
        if uid:
            await bot.send_message(uid,
                f"⏰ TIEMPO AGOTADO - PUEBLA\n\n"
                f"El folio {folio} fue eliminado por no completar el pago en 36 horas.\n\n"
                f"📋 Para generar otro permiso use /permiso")
        limpiar_timer_folio(folio)
    except Exception as e:
        print(f"Error eliminando folio {folio}: {e}")

async def enviar_recordatorio(folio: str, minutos_restantes: int):
    try:
        if folio not in timers_activos: return
        uid = timers_activos[folio]["user_id"]
        await bot.send_message(uid,
            f"⚡ RECORDATORIO - PUEBLA\n\n"
            f"Folio: {folio}\nTiempo restante: {minutos_restantes} minutos\n"
            f"Monto: ${PRECIO_PERMISO}\n\n"
            f"📸 Envíe su comprobante de pago (imagen).\n\n"
            f"📋 Para generar otro permiso use /permiso")
    except Exception as e:
        print(f"Error recordatorio {folio}: {e}")

async def iniciar_timer_36h(user_id: int, folio: str):
    async def timer_task():
        print(f"[TIMER] Iniciado folio {folio}, usuario {user_id} (36h)")
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
            print(f"[TIMER] Expirado folio {folio} - eliminando")
            await eliminar_folio_automatico(folio)

    task = asyncio.create_task(timer_task())
    timers_activos[folio] = {"task": task, "user_id": user_id, "start_time": datetime.now()}
    user_folios.setdefault(user_id, []).append(folio)
    print(f"[SISTEMA] Timer 36h iniciado folio {folio}, total: {len(timers_activos)}")

def cancelar_timer_folio(folio: str) -> bool:
    if folio not in timers_activos: return False
    timers_activos[folio]["task"].cancel()
    uid = timers_activos[folio]["user_id"]
    del timers_activos[folio]
    if uid in user_folios and folio in user_folios[uid]:
        user_folios[uid].remove(folio)
        if not user_folios[uid]: del user_folios[uid]
    print(f"[SISTEMA] Timer cancelado folio {folio}")
    return True

def limpiar_timer_folio(folio: str):
    if folio not in timers_activos: return
    uid = timers_activos[folio]["user_id"]
    del timers_activos[folio]
    if uid in user_folios and folio in user_folios[uid]:
        user_folios[uid].remove(folio)
        if not user_folios[uid]: del user_folios[uid]

def obtener_folios_usuario(user_id: int) -> list:
    return user_folios.get(user_id, [])

# ===================== FOLIOS PUEBLA =====================
FOLIO_PREFIJO_PUEBLA  = "PUE"
FOLIO_NUM_PREFIJO     = "722"
_folio_counter_puebla = {"siguiente": 1}
_folio_lock_puebla    = asyncio.Lock()

def _sb_leer_watermark_puebla() -> int | None:
    try:
        r = supabase.table("folio_watermark") \
            .select("ultimo_asignado").eq("prefijo", FOLIO_PREFIJO_PUEBLA).execute()
        if r.data:
            return r.data[0]["ultimo_asignado"]
        return None
    except Exception as e:
        print(f"[ERROR] leer_watermark PUEBLA: {e}")
        return None

def _sb_guardar_watermark_puebla(numero: int):
    try:
        supabase.table("folio_watermark").upsert({
            "prefijo":         FOLIO_PREFIJO_PUEBLA,
            "ultimo_asignado": numero
        }).execute()
        print(f"[WATERMARK PUEBLA] Guardado: {FOLIO_NUM_PREFIJO}{numero}")
    except Exception as e:
        print(f"[ERROR] guardar_watermark PUEBLA: {e}")

def _sb_inicializar_folio_puebla():
    watermark = _sb_leer_watermark_puebla()
    if watermark is not None:
        _folio_counter_puebla["siguiente"] = watermark + 1
        print(f"[FOLIO PUEBLA] Desde watermark: {FOLIO_NUM_PREFIJO}{watermark} "
              f"-> siguiente: {_folio_counter_puebla['siguiente']}")
        return
    try:
        resp = supabase.table("folios_registrados") \
            .select("folio").eq("entidad", ENTIDAD) \
            .like("folio", f"{FOLIO_NUM_PREFIJO}%").execute()
        numeros = []
        for row in resp.data or []:
            f = row.get("folio", "")
            if isinstance(f, str) and f.startswith(FOLIO_NUM_PREFIJO):
                sufijo = f[len(FOLIO_NUM_PREFIJO):]
                if sufijo.isdigit():
                    numeros.append(int(sufijo))
        if numeros:
            maximo = max(numeros)
            _folio_counter_puebla["siguiente"] = maximo + 1
            _sb_guardar_watermark_puebla(maximo)
            print(f"[FOLIO PUEBLA] Desde DB (primera vez): {FOLIO_NUM_PREFIJO}{maximo} "
                  f"-> siguiente: {_folio_counter_puebla['siguiente']}")
        else:
            _folio_counter_puebla["siguiente"] = 1
            print(f"[FOLIO PUEBLA] Sin folios previos, empezando desde {FOLIO_NUM_PREFIJO}1")
    except Exception as e:
        print(f"[ERROR] inicializar_folio PUEBLA: {e}")
        _folio_counter_puebla["siguiente"] = 1

def _sb_folio_existe_puebla(folio: str) -> bool:
    try:
        r = supabase.table("folios_registrados").select("folio").eq("folio", folio).execute()
        return len(r.data) > 0
    except Exception as e:
        print(f"[ERROR] verificar folio {folio}: {e}")
        return False

def _generar_folio_puebla_sync() -> str:
    candidato = _folio_counter_puebla["siguiente"]
    for _ in range(100_000):
        folio = f"{FOLIO_NUM_PREFIJO}{candidato}"
        if not _sb_folio_existe_puebla(folio):
            _folio_counter_puebla["siguiente"] = candidato + 1
            _sb_guardar_watermark_puebla(candidato)
            print(f"[FOLIO PUEBLA] Asignado: {folio} (siguiente: {_folio_counter_puebla['siguiente']})")
            return folio
        print(f"[FOLIO PUEBLA] {folio} ocupado -> probando siguiente")
        candidato += 1
    return f"{FOLIO_NUM_PREFIJO}{random.randint(50000, 99999)}"

async def _generar_folio_puebla_async() -> str:
    async with _folio_lock_puebla:
        return await asyncio.to_thread(_generar_folio_puebla_sync)

def generar_folio_puebla() -> str:
    return _generar_folio_puebla_sync()

# ===================== AUXILIARES =====================
def limpiar_entrada(texto: str) -> str:
    if not texto: return ""
    return ''.join(c for c in texto if c.isalnum() or c.isspace() or c in '-_./').strip().upper()

def formatear_folio_completo(folio: str) -> str:
    return f"PUE  / {folio} / {datetime.now().year}"

def generar_qr_simple_puebla(folio):
    try:
        url = f"{BASE_URL}/estado_folio/{folio}"
        qr  = qrcode.QRCode(version=None,
                            error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=4, border=1)
        qr.add_data(url); qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")
    except Exception as e:
        print(f"[QR] Error: {e}"); return None

def generar_pdf_unificado_puebla(datos: dict) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR, f"{datos['folio']}_puebla.pdf")
    try:
        recibo_ingreso = obtener_siguiente_consecutivo("recibo_ingreso")
        pase_caja      = obtener_siguiente_consecutivo("pase_caja")
        numero_1       = obtener_siguiente_consecutivo("numero_1")
        numero_2       = obtener_siguiente_consecutivo("numero_2")

        serie_completa      = datos["serie"]
        ultimos_4_serie     = serie_completa[-4:] if len(serie_completa) >= 4 else serie_completa
        fecha_hora_dt       = datos['fecha_exp_dt']
        hora_formateada     = fecha_hora_dt.strftime("%I:%M %p").lower() \
                              .replace("am", "a. m.").replace("pm", "p. m.")
        fecha_hora_completa = f"{fecha_hora_dt.strftime('%d/%m/%Y')} {hora_formateada}"
        rfc_generico        = "XAXX010101000"
        
        # Formato de fecha para el permiso
        fecha_expedicion = datos['fecha_exp_dt'].strftime("%d DE %B %Y").upper()
        fecha_vencimiento = datos['fecha_ven_dt'].strftime("%d DE %B %Y").upper()

        if os.path.exists(PLANTILLA_PDF):
            doc_permiso = fitz.open(PLANTILLA_PDF)
            pg_permiso  = doc_permiso[0]
            
            # ==================== PRIMERA PÁGINA - PERMISO ====================
            # Folio grande (donde dice "24  05914025" en el documento)
            # Insertar el folio largo en la posición correcta (rojo)
            pg_permiso.insert_text((245, 165), datos['folio'],
                fontsize=72, color=(1, 0, 0), fontname="helv-Bold")
            
            # MARCA (en rojo)
            pg_permiso.insert_text((200, 270), datos['marca'].upper(),
                fontsize=20, color=(1, 0, 0), fontname="helv-Bold")
            
            # LÍNEA (en rojo)
            pg_permiso.insert_text((280, 270), datos['linea'].upper(),
                fontsize=18, color=(1, 0, 0), fontname="helv")
            
            # MODELO / AÑO (en rojo)
            pg_permiso.insert_text((480, 270), datos['anio'],
                fontsize=20, color=(1, 0, 0), fontname="helv-Bold")
            
            # NÚMERO DE MOTOR (en rojo)
            pg_permiso.insert_text((200, 310), datos['motor'].upper(),
                fontsize=18, color=(1, 0, 0), fontname="helv")
            
            # NÚMERO DE SERIE (en rojo)
            pg_permiso.insert_text((340, 310), datos['serie'].upper(),
                fontsize=17, color=(1, 0, 0), fontname="helv")
            
            # COLOR (en rojo)
            pg_permiso.insert_text((200, 350), datos['color'].upper(),
                fontsize=20, color=(1, 0, 0), fontname="helv-Bold")
            
            # FECHA DE EXPEDICIÓN (en rojo)
            pg_permiso.insert_text((180, 410), fecha_expedicion,
                fontsize=16, color=(1, 0, 0), fontname="helv")
            
            # VIGENCIA (en rojo)
            pg_permiso.insert_text((400, 410), fecha_vencimiento,
                fontsize=16, color=(1, 0, 0), fontname="helv")
            
            # QR - donde está el código de barras (en la posición del barcode)
            img_qr = generar_qr_simple_puebla(datos["folio"])
            if img_qr:
                buf = BytesIO(); img_qr.save(buf, format="PNG"); buf.seek(0)
                qr_pix = fitz.Pixmap(buf.read())
                # Insertar QR donde está el código de barras (lado derecho)
                pg_permiso.insert_image(fitz.Rect(490, 200, 590, 300),
                                        pixmap=qr_pix, overlay=True)
        else:
            doc_permiso = fitz.open()
            doc_permiso.new_page(width=595, height=842).insert_text(
                (50, 50), "PERMISO PUEBLA (Plantilla no encontrada)", fontsize=20)

        if os.path.exists(PLANTILLA_RECIBO):
            doc_recibo = fitz.open(PLANTILLA_RECIBO)
            pg_recibo  = doc_recibo[0]
            
            # ==================== SEGUNDA PÁGINA - RECIBO ====================
            # AGENCIA O DELEGACION (negro)
            pg_recibo.insert_text((200, 150), "CENTRO INTEGRAL DE SERVICIOS",
                fontsize=14, color=(0,0,0), fontname="helv-Bold")
            
            # FECHA DE EXPEDICIÓN (negro)
            pg_recibo.insert_text((180, 200), fecha_expedicion,
                fontsize=14, color=(0,0,0), fontname="helv")
            
            # PROPIETARIO (negro)
            pg_recibo.insert_text((200, 280), datos["nombre"].upper(),
                fontsize=12, color=(0,0,0), fontname="helv-Bold")
            
            # DOMICILIO - CALLE (negro)
            pg_recibo.insert_text((200, 330), "CALLE: CAMINO REAL XICOHTENCATL",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # DOMICILIO - COLONIA (negro)
            pg_recibo.insert_text((350, 330), "COLONIA: SAN DIEGO XOCOYUCAN",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # CÓDIGO POSTAL (negro)
            pg_recibo.insert_text((200, 350), "CODIGO POSTAL: 90122",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # MUNICIPIO (negro)
            pg_recibo.insert_text((350, 350), "MUNICIPIO: IXTACUIXTLA",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # ENTIDAD FEDERATIVA (negro)
            pg_recibo.insert_text((200, 370), "ENTIDAD FEDERATIVA: TLAXCALA",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # NÚMERO DE TELÉFONO (negro)
            pg_recibo.insert_text((350, 370), "NUMERO DE TELEFONO: 2212023076",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # CORREO (negro)
            pg_recibo.insert_text((200, 390), "CORREO: alonzoerivan@gmail.com",
                fontsize=10, color=(0,0,0), fontname="helv")
            
            # FOLIO GRANDE (negro) - lado derecho
            pg_recibo.insert_text((420, 150), datos['folio'],
                fontsize=64, color=(0,0,0), fontname="helv-Bold")
        else:
            doc_recibo = fitz.open()
            doc_recibo.new_page(width=595, height=842).insert_text(
                (50, 50), "RECIBO (Plantilla no encontrada)", fontsize=20)

        doc_final = fitz.open()
        doc_final.insert_pdf(doc_permiso)
        doc_final.insert_pdf(doc_recibo)
        doc_final.save(out)
        doc_final.close(); doc_permiso.close()
        if os.path.exists(PLANTILLA_RECIBO): doc_recibo.close()
        print(f"[PDF] ✅ Generado: {out}")
        return out
    except Exception as e:
        print(f"[PDF] Error crítico: {e}"); raise e

# ===================== BACKGROUND TASK =====================
async def _generar_y_enviar_background(chat_id: int, datos: dict, user_id: int):
    folio     = datos["folio"]
    folio_fmt = formatear_folio_completo(folio)
    try:
        pdf_path = await asyncio.to_thread(generar_pdf_unificado_puebla, datos)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔑 Validar Admin", callback_data=f"validar_{folio}"),
            InlineKeyboardButton(text="⏹️ Detener Timer", callback_data=f"detener_{folio}")
        ]])
        await bot.send_document(
            chat_id, FSInputFile(pdf_path),
            caption=(
                f"📄 PERMISO + RECIBO — PUEBLA\n"
                f"Folio: {folio_fmt}\n"
                f"Expedición: {datos['fecha_exp']}\n"
                f"Vencimiento: {datos['fecha_ven']}\n\n"
                f"⏰ TIMER ACTIVO (36 horas)"
            ),
            reply_markup=keyboard
        )
        hoy = datos["fecha_exp_dt"]
        ven = datos["fecha_ven_dt"]
        await asyncio.to_thread(lambda: supabase.table("folios_registrados").insert({
            "folio":             folio,
            "marca":             datos["marca"],
            "linea":             datos["linea"],
            "anio":              datos["anio"],
            "numero_serie":      datos["serie"],
            "numero_motor":      datos["motor"],
            "color":             datos["color"],
            "contribuyente":     datos["nombre"],
            "fecha_expedicion":  hoy.date().isoformat(),
            "fecha_vencimiento": ven.date().isoformat(),
            "entidad":           ENTIDAD,
            "estado":            "PENDIENTE",
            "user_id":           user_id,
            "username":          datos.get("username", "Sin username")
        }).execute())
        await iniciar_timer_36h(user_id, folio)
        await bot.send_message(user_id,
            f"💰 INSTRUCCIONES DE PAGO\n\n"
            f"📄 Folio: {folio_fmt}\n"
            f"💵 Monto: ${PRECIO_PERMISO} MXN\n"
            f"⏰ Tiempo límite: 36 horas\n\n"
            f"📸 Envíe la foto de su comprobante aquí mismo.\n"
            f"⚠️ Sin pago en 36h el folio se elimina automáticamente.\n\n"
            f"📋 Para generar otro permiso use /permiso")
    except Exception as e:
        print(f"[ERROR background] folio {folio}: {e}")
        try:
            await bot.send_message(user_id,
                f"❌ Error al generar el documento: {e}\n\nUse /permiso para reintentar.")
        except Exception:
            pass

# ===================== FSM =====================
class PermisoForm(StatesGroup):
    marca  = State()
    linea  = State()
    anio   = State()
    serie  = State()
    motor  = State()
    color  = State()
    nombre = State()

# ===================== HANDLERS BOT =====================

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏛️ Sistema Digital de Permisos Puebla\n\n"
        f"💰 Costo: ${PRECIO_PERMISO} MXN\n"
        "⏰ Tiempo límite: 36 horas\n\n"
        "⚠️ Su folio será eliminado automáticamente si no realiza el pago a tiempo.\n\n"
        "📋 Use /permiso para generar un permiso."
    )

@dp.message(Command("permiso"))
async def permiso_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    folios_activos = obtener_folios_usuario(message.from_user.id)
    if folios_activos:
        texto   = "📋 FOLIOS PUEBLA ACTIVOS\n" + "─" * 28 + "\n\n"
        botones = []
        for f in folios_activos:
            if f in timers_activos:
                seg  = max(0, int(TOTAL_MINUTOS_TIMER * 60 -
                                  (datetime.now() - timers_activos[f]["start_time"]).total_seconds()))
                h, m = divmod(seg // 60, 60)
                texto += f"Folio: {formatear_folio_completo(f)}\n{h}h {m}min restantes\n\n"
            else:
                texto += f"Folio: {formatear_folio_completo(f)}\n(sin timer)\n\n"
            botones.append([InlineKeyboardButton(
                text=f"⏹️ Detener timer {f}", callback_data=f"detener_{f}")])
        await message.answer(texto.strip(),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=botones))
        await message.answer(
            f"Para NUEVO permiso escribe la MARCA del vehículo:\n\nCosto: ${PRECIO_PERMISO} | Plazo: 36h")
    else:
        await message.answer(
            f"🚗 NUEVO PERMISO - PUEBLA\n\n"
            f"💰 Costo: ${PRECIO_PERMISO} MXN\n"
            f"⏰ Plazo de pago: 36 horas\n\n"
            f"Paso 1/7: MARCA del vehículo:")
    await state.set_state(PermisoForm.marca)

@dp.message(PermisoForm.marca)
async def get_marca(message: types.Message, state: FSMContext):
    await state.update_data(marca=limpiar_entrada(message.text))
    await message.answer("Paso 2/7: LÍNEA/MODELO:")
    await state.set_state(PermisoForm.linea)

@dp.message(PermisoForm.linea)
async def get_linea(message: types.Message, state: FSMContext):
    await state.update_data(linea=limpiar_entrada(message.text))
    await message.answer("Paso 3/7: AÑO (4 dígitos):")
    await state.set_state(PermisoForm.anio)

@dp.message(PermisoForm.anio)
async def get_anio(message: types.Message, state: FSMContext):
    anio = message.text.strip()
    if not anio.isdigit() or len(anio) != 4:
        await message.answer("⚠️ Año inválido. Usa 4 dígitos (ej. 2021):"); return
    await state.update_data(anio=anio)
    await message.answer("Paso 4/7: NÚMERO DE SERIE:")
    await state.set_state(PermisoForm.serie)

@dp.message(PermisoForm.serie)
async def get_serie(message: types.Message, state: FSMContext):
    await state.update_data(serie=limpiar_entrada(message.text))
    await message.answer("Paso 5/7: NÚMERO DE MOTOR:")
    await state.set_state(PermisoForm.motor)

@dp.message(PermisoForm.motor)
async def get_motor(message: types.Message, state: FSMContext):
    await state.update_data(motor=limpiar_entrada(message.text))
    await message.answer("Paso 6/7: COLOR:")
    await state.set_state(PermisoForm.color)

@dp.message(PermisoForm.color)
async def get_color(message: types.Message, state: FSMContext):
    await state.update_data(color=limpiar_entrada(message.text))
    await message.answer("Paso 7/7: NOMBRE COMPLETO del titular:")
    await state.set_state(PermisoForm.nombre)

@dp.message(PermisoForm.nombre)
async def get_nombre(message: types.Message, state: FSMContext):
    datos             = await state.get_data()
    datos["nombre"]   = limpiar_entrada(message.text)
    datos["username"] = message.from_user.username or "Sin username"
    datos["folio"]    = await _generar_folio_puebla_async()
    tz  = ZoneInfo(TZ)
    hoy = datetime.now(tz)
    ven = hoy + timedelta(days=30)
    datos["fecha_exp"]    = hoy.strftime("%d/%m/%Y")
    datos["fecha_ven"]    = ven.strftime("%d/%m/%Y")
    datos["fecha_exp_dt"] = hoy
    datos["fecha_ven_dt"] = ven
    await state.clear()
    await message.answer(
        f"🔄 Generando permiso...\n"
        f"📄 Folio: {formatear_folio_completo(datos['folio'])}\n"
        f"👤 Titular: {datos['nombre']}")
    asyncio.create_task(
        _generar_y_enviar_background(message.chat.id, datos, message.from_user.id))

# ===================== CALLBACKS =====================

@dp.callback_query(lambda c: c.data and c.data.startswith("validar_"))
async def callback_validar_admin(callback: CallbackQuery):
    folio = callback.data.replace("validar_", "")
    if not folio.startswith("722"):
        await callback.answer("❌ Folio inválido", show_alert=True); return
    if folio in timers_activos:
        uid = timers_activos[folio]["user_id"]
        cancelar_timer_folio(folio)
        try:
            await asyncio.to_thread(lambda: supabase.table("folios_registrados").update({
                "estado": "VALIDADO_ADMIN", "fecha_comprobante": datetime.now().isoformat()
            }).eq("folio", folio).execute())
        except Exception as e:
            print(f"Error BD validar {folio}: {e}")
        await callback.answer("✅ Folio validado por administración", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        try:
            await bot.send_message(uid,
                f"✅ PAGO VALIDADO — PUEBLA\n"
                f"📄 Folio: {formatear_folio_completo(folio)}\n"
                f"Tu permiso está activo.\n\n📋 Para generar otro permiso use /permiso")
        except Exception as e:
            print(f"Error notificando usuario: {e}")
    else:
        await callback.answer("❌ Folio no encontrado en timers activos", show_alert=True)

@dp.callback_query(lambda c: c.data and c.data.startswith("detener_"))
async def callback_detener_timer(callback: CallbackQuery):
    folio = callback.data.replace("detener_", "")
    if folio in timers_activos:
        cancelar_timer_folio(folio)
        try:
            await asyncio.to_thread(lambda: supabase.table("folios_registrados").update({
                "estado": "TIMER_DETENIDO", "fecha_detencion": datetime.now().isoformat()
            }).eq("folio", folio).execute())
        except Exception as e:
            print(f"Error BD detener {folio}: {e}")
        await callback.answer("⏹️ Timer detenido", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"⏹️ TIMER DETENIDO\n📄 Folio: {formatear_folio_completo(folio)}\n\n"
            f"El folio ya NO se eliminará automáticamente.\n\n"
            f"📋 Para generar otro permiso use /permiso")
    else:
        await callback.answer("❌ Timer ya no está activo", show_alert=True)

@dp.message(lambda m: m.text and m.text.strip().upper().startswith("SERO"))
async def codigo_admin(message: types.Message):
    texto = message.text.strip().upper()
    folio = texto.replace("SERO", "", 1).strip()
    if not folio or not folio.startswith("722"):
        await message.answer(
            "⚠️ Formato: SERO722X (folio debe iniciar con 722).\n\n"
            "📋 Para generar otro permiso use /permiso"); return
    cancelado = cancelar_timer_folio(folio)
    with suppress(Exception):
        await asyncio.to_thread(lambda: supabase.table("folios_registrados").update({
            "estado": "VALIDADO_ADMIN", "fecha_comprobante": datetime.now().isoformat()
        }).eq("folio", folio).execute())
    folio_fmt = formatear_folio_completo(folio)
    msg = (f"✅ Validación admin exitosa\n📄 Folio: {folio_fmt}\n⏹️ Timer detenido"
           if cancelado else
           f"✅ Validación admin\n📄 Folio: {folio_fmt}\n⚠️ Timer ya estaba inactivo")
    await message.answer(msg + "\n\n📋 Para generar otro permiso use /permiso")

@dp.message(lambda m: m.content_type == ContentType.PHOTO)
async def recibir_comprobante(message: types.Message):
    uid    = message.from_user.id
    folios = obtener_folios_usuario(uid)
    if not folios:
        await message.answer(
            "ℹ️ No tienes folios pendientes.\n\n📋 Para generar otro permiso use /permiso"); return
    if len(folios) > 1:
        lista = "\n".join(f"• {formatear_folio_completo(f)}" for f in folios)
        pending_comprobantes[uid] = "waiting_folio"
        await message.answer(
            f"📄 Varios folios activos:\n\n{lista}\n\n"
            f"Responde con el NÚMERO DE FOLIO para este comprobante.\n\n"
            f"📋 Para generar otro permiso use /permiso"); return
    folio = folios[0]; cancelar_timer_folio(folio)
    with suppress(Exception):
        await asyncio.to_thread(lambda: supabase.table("folios_registrados").update({
            "estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": datetime.now().isoformat()
        }).eq("folio", folio).execute())
    await message.answer(
        f"✅ Comprobante recibido\n📄 Folio: {formatear_folio_completo(folio)}\n"
        f"⏹️ Timer detenido.\n\n📋 Para generar otro permiso use /permiso")

@dp.message(lambda m: m.from_user.id in pending_comprobantes
            and pending_comprobantes[m.from_user.id] == "waiting_folio")
async def especificar_folio_comprobante(message: types.Message):
    uid = message.from_user.id
    fe  = message.text.strip().upper()
    fl  = obtener_folios_usuario(uid)
    if fe not in fl:
        await message.answer(
            "❌ Folio no en tu lista.\n\n📋 Para generar otro permiso use /permiso"); return
    cancelar_timer_folio(fe); del pending_comprobantes[uid]
    with suppress(Exception):
        await asyncio.to_thread(lambda: supabase.table("folios_registrados").update({
            "estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": datetime.now().isoformat()
        }).eq("folio", fe).execute())
    await message.answer(
        f"✅ Comprobante asociado.\n📄 Folio: {formatear_folio_completo(fe)}\n\n"
        f"📋 Para generar otro permiso use /permiso")

@dp.message(Command("folios"))
async def ver_folios_activos(message: types.Message):
    uid    = message.from_user.id
    folios = obtener_folios_usuario(uid)
    if not folios:
        await message.answer(
            "ℹ️ No hay folios activos.\n\n📋 Para generar otro permiso use /permiso"); return
    lista   = []
    botones = []
    for f in folios:
        if f in timers_activos:
            seg  = max(0, int(TOTAL_MINUTOS_TIMER * 60 -
                               (datetime.now() - timers_activos[f]["start_time"]).total_seconds()))
            h, m = divmod(seg // 60, 60)
            lista.append(f"• {formatear_folio_completo(f)} ({h}h {m}min)")
        else:
            lista.append(f"• {formatear_folio_completo(f)} (sin timer)")
        botones.append([InlineKeyboardButton(
            text=f"⏹️ Detener {f}", callback_data=f"detener_{f}")])
    await message.answer(
        f"📋 FOLIOS PUEBLA ACTIVOS ({len(folios)})\n\n" + "\n".join(lista) +
        "\n\n⏰ Timer 36h por folio.\n📸 Envía imagen para comprobante.\n\n"
        "📋 Para generar otro permiso use /permiso",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=botones))

@dp.message()
async def fallback(message: types.Message):
    await message.answer("🏛️ Sistema Digital Puebla.")

# ===================== FASTAPI =====================
_keep_task = None

async def keep_alive():
    while True:
        await asyncio.sleep(600)
        print("[HEARTBEAT] Sistema Puebla activo")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _keep_task
    await asyncio.to_thread(_sb_inicializar_folio_puebla)
    await bot.delete_webhook(drop_pending_updates=True)
    webhook_url = f"{BASE_URL}/webhook"
    await bot.set_webhook(webhook_url, allowed_updates=["message", "callback_query"])
    _keep_task = asyncio.create_task(keep_alive())
    print(f"[WEBHOOK] {webhook_url}")
    print(f"[SISTEMA] PUEBLA listo — "
          f"siguiente folio: {FOLIO_NUM_PREFIJO}{_folio_counter_puebla['siguiente']}")
    yield
    if _keep_task:
        _keep_task.cancel()
        with suppress(asyncio.CancelledError): await _keep_task
    await bot.session.close()

app = FastAPI(lifespan=lifespan, title="Bot Permisos Puebla", version="1.0")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ===================== WEBHOOK =====================

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        await dp.feed_webhook_update(bot, types.Update(**data))
        return {"ok": True}
    except Exception as e:
        print(f"[WEBHOOK] Error: {e}"); return {"ok": False, "error": str(e)}

# ===================== RUTAS WEB - PÁGINA PÚBLICA =====================

@app.get("/", response_class=HTMLResponse)
async def root_puebla():
    """Página pública de consulta de folios"""
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Secretaria de Movilidad y Transporte - Consulta de Permisos">
    <title>Secretaría de Movilidad y Transporte - Consulta</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; color: #495057; }
        .header { background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); padding: 15px 0; margin-bottom: 30px; }
        .header-inner { max-width: 1200px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; }
        .img-header { height: 60px; width: auto; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .consulta-section { background: white; border-radius: 20px; padding: 40px; margin: 40px auto; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .consulta-titulo { font-size: 2rem; color: #001B4C; text-align: center; margin-bottom: 30px; font-weight: 300; }
        .consulta-subtitulo { font-size: 1.1rem; color: #949494; text-align: center; margin-bottom: 40px; }
        .formulario-consulta { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 15px; margin-bottom: 40px; align-items: flex-end; }
        .form-group { display: flex; flex-direction: column; }
        .form-group label { font-size: 0.95rem; font-weight: 600; color: #495057; margin-bottom: 8px; }
        .form-group input { padding: 12px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; font-family: inherit; }
        .form-group input:focus { outline: none; border-color: #001B4C; box-shadow: 0 0 0 3px rgba(0, 27, 76, 0.1); }
        .btn-consultar { padding: 12px 30px; background: #c79b66; color: white; border: none; border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; }
        .btn-consultar:hover { background: #b8894e; }
        .resultado-container { margin-top: 40px; display: none; }
        .resultado-container.visible { display: block; }
        .estado-folio { text-align: center; margin-bottom: 20px; padding: 20px; border-radius: 12px; font-size: 1.1rem; font-weight: 600; }
        .estado-folio.vigente { background: #d4edda; color: #155724; border: 2px solid #28a745; }
        .estado-folio.vencido { background: #fff3cd; color: #856404; border: 2px solid #ffc107; }
        .estado-folio.no-existe { background: #f8d7da; color: #721c24; border: 2px solid #dc3545; }
        .resultado-tabla { background: #f6f6f6; border-radius: 12px; padding: 30px; margin-top: 20px; }
        .resultado-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px 40px; }
        .resultado-item { padding: 15px; background: white; border-radius: 8px; border-left: 4px solid #c79b66; }
        .resultado-label { font-size: 0.85rem; font-weight: 700; color: #949494; text-transform: uppercase; margin-bottom: 5px; }
        .resultado-valor { font-size: 1.1rem; color: #001B4C; font-weight: 500; }
        .footer { background: #5f1b2d; color: #fffbef; padding: 40px 0; margin-top: 60px; text-align: center; font-size: 0.9rem; }
        .loading { display: none; text-align: center; padding: 20px; }
        .loading.active { display: block; }
        .spinner { border: 3px solid #f3f3f3; border-top: 3px solid #c79b66; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .error-message { background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin-bottom: 20px; display: none; }
        .error-message.visible { display: block; }
        @media (max-width: 768px) { .formulario-consulta { grid-template-columns: 1fr; } .btn-consultar { width: 100%; } .resultado-grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <header class="header">
        <div class="header-inner">
            <div>
                <h1 style="font-size: 1.5rem; color: #001B4C; font-weight: 300;">Secretaría de Movilidad y Transporte</h1>
                <p style="color: #949494; font-size: 0.9rem;">Consulta de Permisos Vehiculares</p>
            </div>
        </div>
    </header>
    <div class="container">
        <div class="consulta-section">
            <h2 class="consulta-titulo">Consulta de Permisos</h2>
            <p class="consulta-subtitulo">Ingresa el folio para verificar tu permiso</p>
            <form class="formulario-consulta" id="formularioConsulta" onsubmit="buscarPermiso(event)">
                <div class="form-group">
                    <label for="numeroFolio">Folio</label>
                    <input type="text" id="numeroFolio" placeholder="Ej: 722000001" required autocomplete="off">
                </div>
                <div class="form-group">
                    <label for="placa">Placa</label>
                    <input type="text" id="placa" placeholder="ABC-1234" autocomplete="off">
                </div>
                <div class="form-group">
                    <label for="numeroSerie">Serie</label>
                    <input type="text" id="numeroSerie" placeholder="VIN" autocomplete="off">
                </div>
                <div class="form-group">
                    <button type="submit" class="btn-consultar">Consultar</button>
                </div>
            </form>
            <div class="loading" id="loadingIndicator">
                <div class="spinner"></div>
                <p style="margin-top: 10px; color: #949494;">Buscando...</p>
            </div>
            <div class="error-message" id="errorMessage"></div>
            <div class="resultado-container" id="resultadoContainer">
                <div class="estado-folio" id="estadoFolio"></div>
                <div class="resultado-tabla">
                    <div class="resultado-grid">
                        <div class="resultado-item"><div class="resultado-label">Folio</div><div class="resultado-valor" id="resultFolio">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Fecha Expedición</div><div class="resultado-valor" id="resultFechaExp">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Fecha Vencimiento</div><div class="resultado-valor" id="resultFechaVenc">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Marca</div><div class="resultado-valor" id="resultMarca">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Línea</div><div class="resultado-valor" id="resultLinea">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Año</div><div class="resultado-valor" id="resultAno">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Número Serie</div><div class="resultado-valor" id="resultSerie">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Número Motor</div><div class="resultado-valor" id="resultMotor">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Color</div><div class="resultado-valor" id="resultColor">—</div></div>
                        <div class="resultado-item"><div class="resultado-label">Contribuyente</div><div class="resultado-valor" id="resultContribuyente">—</div></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <footer class="footer">
        <p>© 2024 Secretaría de Movilidad y Transporte | Puebla</p>
        <p>movilidadytransporte@puebla.gob.mx | (222) 2 29 06 00</p>
    </footer>
    <script>
        async function buscarPermiso(event) {
            event.preventDefault();
            const folio = document.getElementById('numeroFolio').value.toUpperCase().trim();
            const loadingDiv = document.getElementById('loadingIndicator');
            const errorDiv = document.getElementById('errorMessage');
            const resultadoDiv = document.getElementById('resultadoContainer');
            
            errorDiv.classList.remove('visible');
            resultadoDiv.classList.remove('visible');
            loadingDiv.classList.add('active');
            
            try {
                const response = await fetch(`/api/consultar_folio/${folio}`);
                const data = await response.json();
                loadingDiv.classList.remove('active');
                
                if (!data.ok) {
                    mostrarError('Folio no encontrado');
                    mostrarResultadoNoExiste();
                    return;
                }
                
                mostrarResultado(data);
            } catch (error) {
                loadingDiv.classList.remove('active');
                mostrarError('Error: ' + error.message);
            }
        }
        
        function mostrarResultado(data) {
            const estadoDiv = document.getElementById('estadoFolio');
            const container = document.getElementById('resultadoContainer');
            
            if (data.vigente) {
                estadoDiv.className = 'estado-folio vigente';
                estadoDiv.innerHTML = `✓ Folio <strong>${data.folio}</strong> se está vigente`;
            } else {
                estadoDiv.className = 'estado-folio vencido';
                estadoDiv.innerHTML = `⚠ Folio <strong>${data.folio}</strong> está vencido`;
            }
            
            document.getElementById('resultFolio').textContent = data.folio;
            document.getElementById('resultFechaExp').textContent = data.fecha_expedicion;
            document.getElementById('resultFechaVenc').textContent = data.fecha_vencimiento;
            document.getElementById('resultMarca').textContent = data.marca;
            document.getElementById('resultLinea').textContent = data.linea;
            document.getElementById('resultAno').textContent = data.anio;
            document.getElementById('resultSerie').textContent = data.numero_serie;
            document.getElementById('resultMotor').textContent = data.numero_motor;
            document.getElementById('resultColor').textContent = data.color;
            document.getElementById('resultContribuyente').textContent = data.nombre;
            
            container.classList.add('visible');
        }
        
        function mostrarResultadoNoExiste() {
            const estadoDiv = document.getElementById('estadoFolio');
            const container = document.getElementById('resultadoContainer');
            estadoDiv.className = 'estado-folio no-existe';
            estadoDiv.innerHTML = '✗ <strong>No existe</strong> permiso con esos datos';
            ['resultFolio', 'resultFechaExp', 'resultFechaVenc', 'resultMarca', 'resultLinea', 'resultAno', 'resultSerie', 'resultMotor', 'resultColor', 'resultContribuyente'].forEach(id => {
                document.getElementById(id).textContent = '—';
            });
            container.classList.add('visible');
        }
        
        function mostrarError(msg) {
            document.getElementById('errorMessage').textContent = msg;
            document.getElementById('errorMessage').classList.add('visible');
        }
    </script>
</body>
</html>"""
    return HTMLResponse(html)

@app.get("/api/consultar_folio/{folio}")
async def api_consultar_folio(folio: str):
    """API para consultar folio vía AJAX"""
    folio = folio.strip().upper()
    try:
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).eq("entidad", ENTIDAD).limit(1).execute()
        
        if not res.data:
            return {"ok": False, "estado": "no_encontrado", "folio": folio}
        
        registro = res.data[0]
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        fecha_ven = datetime.fromisoformat(registro["fecha_vencimiento"]).date()
        fecha_exp = datetime.fromisoformat(registro["fecha_expedicion"]).date()
        vigente = hoy <= fecha_ven
        
        return {
            "ok": True,
            "estado": "encontrado",
            "vigente": vigente,
            "folio": folio,
            "nombre": registro.get("contribuyente", ""),
            "marca": registro.get("marca", ""),
            "linea": registro.get("linea", ""),
            "anio": registro.get("anio", ""),
            "color": registro.get("color", ""),
            "numero_serie": registro.get("numero_serie", ""),
            "numero_motor": registro.get("numero_motor", ""),
            "fecha_expedicion": fecha_exp.strftime("%d/%m/%Y"),
            "fecha_vencimiento": fecha_ven.strftime("%d/%m/%Y")
        }
    except Exception as e:
        print(f"[ERROR] api_consultar_folio {folio}: {e}")
        return {"ok": False, "estado": "error", "mensaje": str(e)}

@app.get("/health")
async def health_check():
    try:
        supabase.table("folios_registrados").select("count", count="exact").limit(1).execute()
        bot_info = await bot.get_me()
        return {
            "status": "healthy",
            "timestamp": datetime.now(ZoneInfo(TZ)).isoformat(),
            "services": {
                "database": "conectado",
                "telegram_bot": f"@{bot_info.username}",
                "timers_activos": len(timers_activos),
                "siguiente_folio": f"{FOLIO_NUM_PREFIJO}{_folio_counter_puebla['siguiente']}"
            }
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    print(f"[SISTEMA] PUEBLA iniciando...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
