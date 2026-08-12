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
    html = """<<!DOCTYPE html>
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
	<script type="application/json" class="joomla-script-options new">{"joomla.jtext":{"ERROR":"Error","MESSAGE":"Mensaje","NOTICE":"Notificación","WARNING":"Advertencia","JCLOSE":"Cerrar","JOK":"OK","JOPEN":"Abrir"},"system.paths":{"root":"","rootFull":"https://smt.puebla.gob.mx/","base":"","baseFull":"https://smt.puebla.gob.mx/"},"csrf.token":"2a25cd4380579ae308fccace2aa0969f","accessibility-options":{"labels":{"menuTitle":"Opciones de accesibilidad","increaseText":"Aumentar el tamaño del texto","decreaseText":"Disminuir el tamaño del texto","increaseTextSpacing":"Aumentar el espaciado del texto","decreaseTextSpacing":"Disminuir el espaciado del texto","invertColors":"Invertir colores","grayHues":"Tonos grises","underlineLinks":"Subrayar enlaces","bigCursor":"Cursor grande","readingGuide":"Guía de lectura","textToSpeech":"Texto a voz","speechToText":"Voz a texto","resetTitle":"Restablecer","closeTitle":"Cerrar"},"icon":{"position":{"left":{"size":"0","units":"px"}},"useEmojis":true},"hotkeys":{"enabled":true,"helpTitles":true},"textToSpeechLang":["es-ES"],"speechToTextLang":["es-ES"]}}</script>
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
<li class="metismenu-item item-119 level-1"><a href="https://rl.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Pagos en línea</a></li><li class="metismenu-item item-120 level-1"><a href="https://ventanilladigital.puebla.gob.mx/" target="_blank" rel="noopener noreferrer">Trámites</a></li></ul>

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
                <div class="slider">
            <div class="uk-margin-top">
                        
<div class="">
    <div class="  uk-position-relative uk-visible-toggle uk-light" tabindex="-1" uk-slideshow="animation: pull; autoplay: true; autoplay-interval: 5000;">

        <div class="uk-slideshow-items tamaño" >

                            
                                    
            

            <div class="img-slider">
                
                <a href="https://smtdistintivos.puebla.gob.mx/" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerPaginaWebCascos.jpeg#joomlaImage://local-images/banners/BannerPaginaWebCascos.jpeg?width=1400&height=750" alt="">
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
                
                <a href="https://docs.google.com/forms/d/e/1FAIpQLScL9AKdJ1aeIGX2TrnSNYDAasuUZri4HgabTBrCxqMbja3OuQ/viewform?pli=1" target="_blank" >
                    <img class="img-sli" src="/images/banners/BannerProgramademodernizacion.jpeg#joomlaImage://local-images/banners/BannerProgramademodernizacion.jpeg?width=1400&height=750" alt="">
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
    <li class="" style="padding: 2px;"><a href="https://rl.puebla.gob.mx/">Pagos en línea</a></li>    <li class="" style="padding: 2px;"><a href="https://ventanilladigital.puebla.gob.mx/">Trámites</a></li></ul>

        </div>
    </div>
        <!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-P1YZQ6YV61"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());

  gtag('config', 'G-P1YZQ6YV61');
</script><script type="text/javascript" src="/bnith__gp0uMdfx-_HY3c29e17BKxxLRYqAZgeJZMETqW1Tt3U82bLNsdSCXEEir-pM9ABQjI-hD16wanU="></script> <script language="JavaScript" type="text/javascript">const _0x35e8=['visitorId','18127kSXadA','356575NPKVMA','7306axxsAH','get','657833TzFjkt','717302TQdBjl','34lMHocq','x-bni-rncf=1786487452444;expires=Thu, 01 Jan 2037 00:00:00 UTC;path=/;','61XMWbpU','cookie',';expires=Thu, 01 Jan 2037 00:00:00 UTC;path=/;','then','651866OSUgMa','811155xdatvf','x-bni-fpc='];function _0x258e(_0x5954fe,_0x43567d){return _0x258e=function(_0x35e81f,_0x258e26){_0x35e81f=_0x35e81f-0x179;let _0x1280dc=_0x35e8[_0x35e81f];return _0x1280dc;},_0x258e(_0x5954fe,_0x43567d);}(function(_0x5674de,_0xdcf1af){const _0x512a29=_0x258e;while(!![]){try{const _0x55f636=parseInt(_0x512a29(0x17b))+-parseInt(_0x512a29(0x179))*parseInt(_0x512a29(0x17f))+-parseInt(_0x512a29(0x183))+-parseInt(_0x512a29(0x184))+parseInt(_0x512a29(0x187))*parseInt(_0x512a29(0x17d))+parseInt(_0x512a29(0x188))+parseInt(_0x512a29(0x17c));if(_0x55f636===_0xdcf1af)break;else _0x5674de['push'](_0x5674de['shift']());}catch(_0xd3a1ce){_0x5674de['push'](_0x5674de['shift']());}}}(_0x35e8,0x6b42d));function getClientIdentity(){const _0x47e86b=_0x258e,_0x448fbc=FingerprintJS['load']();_0x448fbc[_0x47e86b(0x182)](_0x4bb924=>_0x4bb924[_0x47e86b(0x17a)]())[_0x47e86b(0x182)](_0x2f8ca1=>{const _0x44872c=_0x47e86b,_0xa48f50=_0x2f8ca1[_0x44872c(0x186)];document[_0x44872c(0x180)]=_0x44872c(0x185)+_0xa48f50+_0x44872c(0x181),document[_0x44872c(0x180)]=_0x44872c(0x17e);});}getClientIdentity();</script></body>


</html>
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
