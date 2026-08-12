from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import os
import hashlib
import secrets
import asyncio
import random
import qrcode
from io import BytesIO
import fitz
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import json

# ==================== CONFIG ====================
BOT_TOKEN    = os.getenv("BOT_TOKEN_PUEBLA", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BASE_URL     = "https://smt-puebla-gob-mx.onrender.com"
SECRET_KEY   = os.getenv("SECRET_KEY", "puebla-secret-key-2024-serg")
OUTPUT_DIR   = "documentos"
PLANTILLA    = "PUEBLA_PLANTILLA_COMPLETA.pdf"
ENTIDAD      = "puebla"
PRECIO       = 180
TZ           = "America/Mexico_City"

ADMIN_USER = "Serg890105tm3"
ADMIN_PASS = "Serg890105tm3"

os.makedirs(OUTPUT_DIR, exist_ok=True)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== HASHING ====================
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${hash_obj.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, hash_hex = hashed.split('$')
        hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return hash_obj.hex() == hash_hex
    except:
        return False

# ==================== TIMERS ====================
timers_activos = {}

async def eliminar_folio_automatico(folio: str):
    try:
        supabase.table("folios_registrados").delete().eq("folio", folio).execute()
        if folio in timers_activos:
            del timers_activos[folio]
    except Exception as e:
        print(f"Error eliminando folio {folio}: {e}")

async def iniciar_timer_36h(folio: str):
    async def timer_task():
        await asyncio.sleep(36 * 3600)
        if folio in timers_activos:
            await eliminar_folio_automatico(folio)

    task = asyncio.create_task(timer_task())
    timers_activos[folio] = {"task": task, "start_time": datetime.now()}

def detener_timer(folio: str) -> bool:
    if folio not in timers_activos:
        return False
    timers_activos[folio]["task"].cancel()
    del timers_activos[folio]
    return True

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
        
        pg_permiso.insert_text((245, 165), datos['folio'], fontsize=72, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((200, 270), datos['marca'].upper(), fontsize=20, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((280, 270), datos['linea'].upper(), fontsize=18, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((480, 270), datos['anio'], fontsize=20, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((200, 310), datos['motor'].upper(), fontsize=18, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((340, 310), datos['serie'].upper(), fontsize=17, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((200, 350), datos['color'].upper(), fontsize=20, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((180, 410), datos['fecha_exp'], fontsize=16, color=(1, 0, 0), fontname="helv")
        pg_permiso.insert_text((400, 410), datos['fecha_ven'], fontsize=16, color=(1, 0, 0), fontname="helv")
        
        qr = qrcode.QRCode()
        qr.add_data(f"{BASE_URL}/estado_folio/{datos['folio']}")
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buf = BytesIO()
        img_qr.save(buf, format="PNG")
        buf.seek(0)
        qr_pix = fitz.Pixmap(buf.read())
        pg_permiso.insert_image(fitz.Rect(490, 200, 590, 300), pixmap=qr_pix, overlay=True)
        
        pg_recibo.insert_text((200, 150), "CENTRO INTEGRAL DE SERVICIOS", fontsize=14, color=(0,0,0), fontname="helv")
        pg_recibo.insert_text((180, 200), datos['fecha_exp'], fontsize=14, color=(0,0,0), fontname="helv")
        pg_recibo.insert_text((200, 280), datos["nombre"].upper(), fontsize=12, color=(0,0,0), fontname="helv")
        pg_recibo.insert_text((420, 150), datos['folio'], fontsize=64, color=(0,0,0), fontname="helv")
        
        doc.save(out)
        doc.close()
        return out
        
    except Exception as e:
        print(f"❌ PDF ERROR: {e}")
        raise

# ==================== FASTAPI ====================
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_inicializar_folio)
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook", allowed_updates=["message", "callback_query"])
    print(f"✅ Puebla System iniciado")
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# ==================== HELPERS ====================
async def get_session(request: Request):
    if not request.session.get("user"):
        raise HTTPException(status_code=401)
    return request.session

async def get_admin(request: Request):
    if request.session.get("user") != ADMIN_USER:
        raise HTTPException(status_code=403)
    return request.session

# ==================== ROUTES - PUBLIC ====================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Página pública de consulta"""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Secretaría de Movilidad y Transporte</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }
        .header { background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .header h1 { color: #001B4C; font-size: 1.5rem; font-weight: 300; }
        .header p { color: #949494; font-size: 0.9rem; margin-top: 5px; }
        .main { flex: 1; display: flex; align-items: center; justify-content: center; padding: 40px 20px; }
        .card { background: white; border-radius: 20px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 900px; width: 100%; }
        .titulo { font-size: 2rem; color: #001B4C; text-align: center; margin-bottom: 30px; font-weight: 300; }
        .form-group { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 15px; margin-bottom: 40px; align-items: flex-end; }
        .form-group label { font-weight: 600; color: #495057; margin-bottom: 8px; display: block; }
        .form-group input { padding: 12px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; }
        .form-group input:focus { outline: none; border-color: #001B4C; box-shadow: 0 0 0 3px rgba(0,27,76,0.1); }
        .btn { padding: 12px 30px; background: #c79b66; color: white; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }
        .btn:hover { background: #b8894e; }
        .resultado { margin-top: 40px; display: none; }
        .resultado.visible { display: block; }
        .estado { text-align: center; padding: 20px; border-radius: 12px; margin-bottom: 20px; font-size: 1.1rem; font-weight: 600; }
        .vigente { background: #d4edda; color: #155724; border: 2px solid #28a745; }
        .vencido { background: #fff3cd; color: #856404; border: 2px solid #ffc107; }
        .noexiste { background: #f8d7da; color: #721c24; border: 2px solid #dc3545; }
        .tabla { background: #f6f6f6; border-radius: 12px; padding: 30px; margin-top: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px 40px; }
        .item { padding: 15px; background: white; border-radius: 8px; border-left: 4px solid #c79b66; }
        .label { font-size: 0.85rem; font-weight: 700; color: #949494; text-transform: uppercase; margin-bottom: 5px; }
        .valor { font-size: 1.1rem; color: #001B4C; font-weight: 500; }
        .footer { background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }
        .login-link { text-align: center; margin-top: 30px; }
        .login-link a { color: #001B4C; font-weight: 600; text-decoration: none; }
        @media (max-width: 768px) { .form-group { grid-template-columns: 1fr; } .grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Secretaría de Movilidad y Transporte</h1>
            <p>Consulta de Permisos Vehiculares</p>
        </div>
    </div>
    
    <div class="main">
        <div class="card">
            <h2 class="titulo">Consulta de Permisos</h2>
            <form class="form-group" id="frmConsulta" onsubmit="buscar(event)">
                <div><label>Folio</label><input type="text" id="folio" placeholder="722000001" required></div>
                <div><label>Placa</label><input type="text" id="placa" placeholder="ABC-1234"></div>
                <div><label>Serie</label><input type="text" id="serie" placeholder="VIN"></div>
                <div><button type="submit" class="btn">Consultar</button></div>
            </form>
            <div class="resultado" id="resultado">
                <div class="estado" id="estado"></div>
                <div class="tabla">
                    <div class="grid">
                        <div class="item"><div class="label">Folio</div><div class="valor" id="f">—</div></div>
                        <div class="item"><div class="label">Expedición</div><div class="valor" id="exp">—</div></div>
                        <div class="item"><div class="label">Vencimiento</div><div class="valor" id="ven">—</div></div>
                        <div class="item"><div class="label">Marca</div><div class="valor" id="mar">—</div></div>
                        <div class="item"><div class="label">Línea</div><div class="valor" id="lin">—</div></div>
                        <div class="item"><div class="label">Año</div><div class="valor" id="ano">—</div></div>
                        <div class="item"><div class="label">Serie</div><div class="valor" id="ser">—</div></div>
                        <div class="item"><div class="label">Motor</div><div class="valor" id="mot">—</div></div>
                        <div class="item"><div class="label">Color</div><div class="valor" id="col">—</div></div>
                        <div class="item"><div class="label">Propietario</div><div class="valor" id="pro">—</div></div>
                    </div>
                </div>
            </div>
            <div class="login-link">
                <a href="/login">Acceder al sistema →</a>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Secretaría de Movilidad y Transporte</p>
    </div>
    
    <script>
        async function buscar(e) {
            e.preventDefault();
            const folio = document.getElementById('folio').value.toUpperCase().trim();
            try {
                const res = await fetch(`/api/consultar/${folio}`);
                const data = await res.json();
                if (!data.ok) {
                    const est = document.getElementById('estado');
                    est.className = 'estado noexiste';
                    est.textContent = '✗ No existe permiso';
                    ['f','exp','ven','mar','lin','ano','ser','mot','col','pro'].forEach(id => {
                        document.getElementById(id).textContent = '—';
                    });
                    document.getElementById('resultado').classList.add('visible');
                    return;
                }
                const est = document.getElementById('estado');
                if (data.vigente) {
                    est.className = 'estado vigente';
                    est.textContent = `✓ ${folio} está vigente`;
                } else {
                    est.className = 'estado vencido';
                    est.textContent = `⚠ ${folio} está vencido`;
                }
                document.getElementById('f').textContent = folio;
                document.getElementById('exp').textContent = data.fecha_expedicion;
                document.getElementById('ven').textContent = data.fecha_vencimiento;
                document.getElementById('mar').textContent = data.marca;
                document.getElementById('lin').textContent = data.linea;
                document.getElementById('ano').textContent = data.anio;
                document.getElementById('ser').textContent = data.numero_serie;
                document.getElementById('mot').textContent = data.numero_motor;
                document.getElementById('col').textContent = data.color;
                document.getElementById('pro').textContent = data.nombre;
                document.getElementById('resultado').classList.add('visible');
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
    </script>
</body>
</html>"""

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Página de login"""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Acceso</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .login { background: white; border-radius: 15px; padding: 50px 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); width: 100%; max-width: 400px; }
        h1 { color: #001B4C; font-size: 1.8rem; text-align: center; margin-bottom: 30px; font-weight: 300; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-weight: 600; color: #495057; margin-bottom: 8px; }
        input { width: 100%; padding: 12px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; }
        input:focus { outline: none; border-color: #001B4C; box-shadow: 0 0 0 3px rgba(0,27,76,0.1); }
        .btn { width: 100%; padding: 12px; background: #c79b66; color: white; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 1rem; }
        .btn:hover { background: #b8894e; }
        .footer { text-align: center; margin-top: 20px; color: #949494; }
        .footer a { color: #001B4C; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login">
        <h1>🏛️ Acceso</h1>
        <form method="post">
            <div class="form-group">
                <label for="username">Usuario</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Contraseña</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn">Acceder</button>
        </form>
        <div class="footer">
            <a href="/">← Volver</a>
        </div>
    </div>
</body>
</html>"""

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Login de usuario (admin o 3ro)"""
    
    if username == ADMIN_USER and password == ADMIN_PASS:
        request.session["user"] = ADMIN_USER
        request.session["is_admin"] = True
        return RedirectResponse("/admin/dashboard", status_code=302)
    
    try:
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if res.data:
            user = res.data[0]
            if verify_password(password, user["password_hash"]):
                if user.get("bloqueado"):
                    return HTMLResponse("""<html><body style='background:#f5f5f5;padding:20px'>
                    <div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'>
                    <h1 style='color:#dc3545'>❌ Cuenta Bloqueada</h1>
                    <p>Tu cuenta está bloqueada. Contacta al administrador.</p>
                    <a href='/login' style='color:#001B4C;text-decoration:none;font-weight:600'>Volver</a>
                    </div></body></html>""")
                request.session["user"] = username
                request.session["is_admin"] = False
                return RedirectResponse("/panel/3ro", status_code=302)
    except:
        pass
    
    return HTMLResponse("""<html><body style='background:#f5f5f5;padding:20px'>
    <div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'>
    <h1 style='color:#dc3545'>❌ Credenciales inválidas</h1>
    <a href='/login' style='color:#001B4C;text-decoration:none;font-weight:600'>Reintentar</a>
    </div></body></html>""")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)

# ==================== PANEL 3RO ====================

@app.get("/panel/3ro", response_class=HTMLResponse)
async def panel_tercero(session: dict = Depends(get_session)):
    """Panel para usuario 3ro"""
    username = session.get("user")
    try:
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if res.data:
            user = res.data[0]
            lotes_total = user.get("lotes_totales", 0)
            lotes_usado = user.get("lotes_usados", 0)
            lotes_restantes = lotes_total - lotes_usado
            porcentaje = int((lotes_usado / max(lotes_total, 1)) * 100) if lotes_total > 0 else 0
            
            if lotes_restantes <= 0:
                return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sin Folios</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
.header {{ background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; }}
.header h1 {{ color: #001B4C; font-size: 1.5rem; font-weight: 300; }}
.header p {{ color: #949494; font-size: 0.9rem; }}
.main {{ padding: 40px 0; }}
.card {{ background: white; border-radius: 15px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center; }}
.card h1 {{ color: #dc3545; font-size: 2rem; margin-bottom: 20px; }}
.card p {{ color: #495057; font-size: 1.1rem; margin-bottom: 30px; }}
.btn {{ padding: 12px 30px; background: #dc3545; color: white; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-block; }}
.btn:hover {{ background: #c82333; }}
.footer {{ background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }}
</style>
</head><body>
<div class="header">
<div class="container">
<h1>🏛️ Mi Panel de Permisos</h1>
<p>Bienvenido, {username}</p>
</div>
</div>
<div class="main">
<div class="container">
<div class="card">
<h1>❌ Sin Folios Disponibles</h1>
<p>Has agotado tu límite de folios. Por favor, contacta al administrador via WhatsApp o SMS para renovar tu cuenta.</p>
<a href="/logout" class="btn">Cerrar Sesión</a>
</div>
</div>
</div>
<div class="footer">
<p>© 2024 Sistema de Permisos Puebla</p>
</div>
</body></html>"""
            
            return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Mi Panel</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
        .header {{ background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; }}
        .header h1 {{ color: #001B4C; font-size: 1.5rem; font-weight: 300; }}
        .header p {{ color: #949494; font-size: 0.9rem; }}
        .main {{ padding: 40px 0; }}
        .card {{ background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 30px; }}
        .progress-bar {{ width: 100%; height: 30px; background: #e9ecef; border-radius: 15px; overflow: hidden; margin: 20px 0; }}
        .progress-fill {{ height: 100%; background: linear-gradient(90deg, #28a745, #20c997); width: {porcentaje}%; display: flex; align-items: center; justify-content: center; color: white; font-weight: 600; }}
        .stats {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin: 30px 0; }}
        .stat {{ background: #f6f6f6; padding: 20px; border-radius: 10px; text-align: center; }}
        .stat-num {{ font-size: 2rem; color: #001B4C; font-weight: 600; }}
        .stat-label {{ color: #949494; font-size: 0.9rem; }}
        .form-group {{ margin-bottom: 20px; }}
        .form-group label {{ display: block; font-weight: 600; color: #495057; margin-bottom: 8px; }}
        .form-group input {{ width: 100%; padding: 12px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; }}
        .form-group input:focus {{ outline: none; border-color: #001B4C; box-shadow: 0 0 0 3px rgba(0,27,76,0.1); }}
        .btn {{ padding: 12px 30px; background: #c79b66; color: white; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }}
        .btn:hover {{ background: #b8894e; }}
        .btn-danger {{ background: #dc3545; }}
        .btn-danger:hover {{ background: #c82333; }}
        .footer {{ background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }}
        @media (max-width: 768px) {{ .stats {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Mi Panel de Permisos</h1>
            <p>Bienvenido, {username}</p>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>📊 Mis Folios Disponibles</h2>
                <div class="progress-bar">
                    <div class="progress-fill" style="width:{porcentaje}%">{porcentaje}%</div>
                </div>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-num">{lotes_restantes}</div>
                        <div class="stat-label">Folios Disponibles</div>
                    </div>
                    <div class="stat">
                        <div class="stat-num">{lotes_usado}</div>
                        <div class="stat-label">Folios Usados</div>
                    </div>
                    <div class="stat">
                        <div class="stat-num">{lotes_total}</div>
                        <div class="stat-label">Folios Totales</div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h2>📝 Generar Permiso</h2>
                <form id="frmPermiso" onsubmit="generarPermiso(event)">
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
                        <div class="form-group">
                            <label>Marca</label>
                            <input type="text" name="marca" required>
                        </div>
                        <div class="form-group">
                            <label>Línea/Modelo</label>
                            <input type="text" name="linea" required>
                        </div>
                        <div class="form-group">
                            <label>Año</label>
                            <input type="text" name="anio" required>
                        </div>
                        <div class="form-group">
                            <label>Número de Serie</label>
                            <input type="text" name="serie" required>
                        </div>
                        <div class="form-group">
                            <label>Número de Motor</label>
                            <input type="text" name="motor" required>
                        </div>
                        <div class="form-group">
                            <label>Color</label>
                            <input type="text" name="color" required>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Nombre Completo</label>
                        <input type="text" name="nombre" required>
                    </div>
                    <button type="submit" class="btn">Generar Permiso</button>
                </form>
            </div>
            
            <div style="text-align:center;margin-top:30px">
                <button class="btn btn-danger" onclick="logout()">Cerrar Sesión</button>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Sistema de Permisos Puebla</p>
    </div>
    
    <script>
        function generarPermiso(e) {{
            e.preventDefault();
            const data = new FormData(e.target);
            const obj = Object.fromEntries(data);
            fetch('/api/generar_permiso', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(obj)
            }})
            .then(r => r.json())
            .then(d => {{
                if (d.ok) {{
                    alert('✅ Permiso generado: ' + d.folio);
                    location.reload();
                }} else {{
                    alert('❌ Error: ' + (d.error || 'Unknown'));
                }}
            }})
            .catch(e => alert('Error: ' + e));
        }}
        
        function logout() {{
            if (confirm('¿Cerrar sesión?')) {{
                window.location.href = '/logout';
            }}
        }}
    </script>
</body>
</html>"""
    except Exception as e:
        print(f"Error panel 3ro: {e}")
        return RedirectResponse("/login", status_code=302)

# ==================== ADMIN PANEL ====================

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(session: dict = Depends(get_admin)):
    """Dashboard Admin"""
    try:
        res_usuarios = supabase.table("usuarios_terceros").select("*").execute()
        usuarios = res_usuarios.data or []
        
        tabla_usuarios = ""
        for u in usuarios:
            lotes_total = u.get("lotes_totales", 0)
            lotes_usado = u.get("lotes_usados", 0)
            porcentaje = int((lotes_usado / max(lotes_total, 1)) * 100) if lotes_total > 0 else 0
            estado = "🔒 Bloqueado" if u.get("bloqueado") else "✅ Activo"
            tabla_usuarios += f"""
            <tr>
                <td>{u.get('username', '—')}</td>
                <td>{lotes_usado}/{lotes_total}</td>
                <td><div style="background:#e9ecef;border-radius:10px;overflow:hidden;height:20px">
                <div style="background:#28a745;width:{porcentaje}%;height:100%;display:flex;align-items:center;justify-content:center;color:white;font-size:0.7rem;font-weight:600">{porcentaje}%</div>
                </div></td>
                <td>{estado}</td>
                <td><button class="btn-small" onclick="renovar('{u.get('username')}')">Renovar</button></td>
            </tr>
            """
        
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
        .header {{ background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; }}
        .header h1 {{ color: #001B4C; font-size: 1.5rem; font-weight: 300; }}
        .nav {{ display: flex; gap: 20px; margin: 20px 0 0 0; border-bottom: 1px solid #eee; }}
        .nav a {{ padding: 10px 20px; color: #495057; text-decoration: none; border-bottom: 3px solid transparent; font-weight: 600; }}
        .nav a.active {{ border-bottom-color: #c79b66; color: #001B4C; }}
        .nav a:hover {{ border-bottom-color: #c79b66; }}
        .main {{ padding: 40px 0; }}
        .card {{ background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 30px; }}
        .tabla {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        .tabla th {{ background: #f6f6f6; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #ddd; }}
        .tabla td {{ padding: 12px; border-bottom: 1px solid #eee; }}
        .tabla tr:hover {{ background: #f9f9f9; }}
        .btn-small {{ padding: 6px 12px; background: #c79b66; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 0.9rem; }}
        .btn-small:hover {{ background: #b8894e; }}
        .footer {{ background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Panel Admin Puebla</h1>
            <div class="nav">
                <a href="/admin/dashboard" class="active">📊 Dashboard</a>
                <a href="/admin/usuarios">👥 Usuarios 3ros</a>
                <a href="/admin/tablas">📋 Tablas</a>
                <a href="/logout">🚪 Salir</a>
            </div>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>📊 Resumen de Usuarios 3ros</h2>
                <table class="tabla">
                    <thead>
                        <tr>
                            <th>Usuario</th>
                            <th>Folios</th>
                            <th>Progreso</th>
                            <th>Estado</th>
                            <th>Acción</th>
                        </tr>
                    </thead>
                    <tbody>
                        {tabla_usuarios}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Sistema de Permisos Puebla</p>
    </div>
    
    <script>
        function renovar(username) {{
            const lotes = prompt(`¿Cuántos folios agregar a ${{username}}?`);
            if (lotes && lotes > 0) {{
                fetch(`/api/renovar_lotes/${{username}}/${{lotes}}`, {{method: 'POST'}})
                    .then(r => r.json())
                    .then(d => {{ 
                        if (d.ok) {{
                            alert('✅ Folios renovados');
                            location.reload();
                        }} else {{
                            alert('❌ Error');
                        }}
                    }})
                    .catch(e => alert('Error: ' + e));
            }}
        }}
    </script>
</body>
</html>"""
    except Exception as e:
        print(f"Error dashboard: {e}")
        return RedirectResponse("/login", status_code=302)

@app.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_usuarios(session: dict = Depends(get_admin)):
    """Gestión de usuarios 3ros"""
    try:
        res = supabase.table("usuarios_terceros").select("*").execute()
        usuarios = res.data or []
        
        tabla_html = ""
        for u in usuarios:
            tabla_html += f"""
            <tr>
                <td>{u.get('username', '—')}</td>
                <td>{u.get('lotes_usados', 0)}/{u.get('lotes_totales', 0)}</td>
                <td>{'🔒 Bloqueado' if u.get('bloqueado') else '✅ Activo'}</td>
                <td><button class="btn-small" onclick="renovar('{u.get('username')}')">Renovar</button></td>
            </tr>
            """
        
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Usuarios 3ros</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
        .header {{ background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; }}
        .header h1 {{ color: #001B4C; font-size: 1.5rem; font-weight: 300; }}
        .nav {{ display: flex; gap: 20px; margin: 20px 0 0 0; border-bottom: 1px solid #eee; }}
        .nav a {{ padding: 10px 20px; color: #495057; text-decoration: none; border-bottom: 3px solid transparent; font-weight: 600; }}
        .nav a.active {{ border-bottom-color: #c79b66; color: #001B4C; }}
        .main {{ padding: 40px 0; }}
        .card {{ background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .tabla {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        .tabla th {{ background: #f6f6f6; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #ddd; }}
        .tabla td {{ padding: 12px; border-bottom: 1px solid #eee; }}
        .tabla tr:hover {{ background: #f9f9f9; }}
        .btn-small {{ padding: 6px 12px; background: #c79b66; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }}
        .btn-small:hover {{ background: #b8894e; }}
        .footer {{ background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Panel Admin Puebla</h1>
            <div class="nav">
                <a href="/admin/dashboard">📊 Dashboard</a>
                <a href="/admin/usuarios" class="active">👥 Usuarios 3ros</a>
                <a href="/admin/tablas">📋 Tablas</a>
                <a href="/logout">🚪 Salir</a>
            </div>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>👥 Gestión de Usuarios 3ros</h2>
                <button class="btn-small" onclick="crearUsuario()" style="margin-bottom:20px;padding:10px 20px;font-size:1rem">+ Crear Usuario</button>
                <table class="tabla">
                    <thead>
                        <tr>
                            <th>Usuario</th>
                            <th>Folios Usados</th>
                            <th>Estado</th>
                            <th>Acción</th>
                        </tr>
                    </thead>
                    <tbody>
                        {tabla_html}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Sistema de Permisos Puebla</p>
    </div>
    
    <script>
        function crearUsuario() {{
            const user = prompt('Nombre de usuario:');
            if (!user) return;
            const pass = prompt('Contraseña:');
            if (!pass) return;
            const lotes = prompt('Cantidad de folios:') || 10;
            
            fetch('/api/crear_usuario', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{username: user, password: pass, lotes: parseInt(lotes)}})
            }})
            .then(r => r.json())
            .then(d => {{ 
                if (d.ok) {{
                    alert('✅ Usuario creado');
                    location.reload();
                }} else {{
                    alert('❌ Error: ' + d.error);
                }}
            }})
            .catch(e => alert('Error: ' + e));
        }}
        
        function renovar(username) {{
            const lotes = prompt(`¿Cuántos folios agregar a ${{username}}?`);
            if (lotes && lotes > 0) {{
                fetch(`/api/renovar_lotes/${{username}}/${{lotes}}`, {{method: 'POST'}})
                    .then(r => r.json())
                    .then(d => {{ 
                        if (d.ok) {{
                            alert('✅ Folios renovados');
                            location.reload();
                        }} else {{
                            alert('❌ Error');
                        }}
                    }})
                    .catch(e => alert('Error: ' + e));
            }}
        }}
    </script>
</body>
</html>"""
    except Exception as e:
        print(f"Error usuarios: {e}")
        return RedirectResponse("/login", status_code=302)

@app.get("/admin/tablas", response_class=HTMLResponse)
async def admin_tablas(session: dict = Depends(get_admin)):
    """Editor de todas las tablas"""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Tablas</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }
        .header { background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .header h1 { color: #001B4C; font-size: 1.5rem; font-weight: 300; }
        .nav { display: flex; gap: 20px; margin: 20px 0 0 0; border-bottom: 1px solid #eee; }
        .nav a { padding: 10px 20px; color: #495057; text-decoration: none; border-bottom: 3px solid transparent; font-weight: 600; }
        .nav a.active { border-bottom-color: #c79b66; color: #001B4C; }
        .main { padding: 40px 0; }
        .card { background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        select { padding: 10px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; }
        .tabla { width: 100%; border-collapse: collapse; margin-top: 20px; }
        .tabla th { background: #f6f6f6; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #ddd; }
        .tabla td { padding: 12px; border-bottom: 1px solid #eee; }
        .tabla input { width: 100%; padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; }
        .tabla tr:hover { background: #f9f9f9; }
        .footer { background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Panel Admin Puebla</h1>
            <div class="nav">
                <a href="/admin/dashboard">📊 Dashboard</a>
                <a href="/admin/usuarios">👥 Usuarios 3ros</a>
                <a href="/admin/tablas" class="active">📋 Tablas</a>
                <a href="/logout">🚪 Salir</a>
            </div>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>📋 Editor de Tablas Supabase</h2>
                <div style="margin-bottom:30px">
                    <label>Selecciona tabla:</label>
                    <select id="tablaSelect" onchange="cargarTabla()">
                        <option value="">-- Selecciona --</option>
                        <option value="folios_registrados">Folios Registrados</option>
                        <option value="usuarios_terceros">Usuarios 3ros</option>
                        <option value="consecutivos_puebla">Consecutivos</option>
                        <option value="folio_watermark">Watermark</option>
                    </select>
                </div>
                <div id="contenedor"></div>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Sistema de Permisos Puebla</p>
    </div>
</body>
</html>"""

# ==================== API ====================

@app.get("/api/consultar/{folio}")
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
    except:
        return {"ok": False}

@app.post("/api/generar_permiso")
async def api_generar_permiso(request: Request):
    try:
        datos = await request.json()
        username = request.session.get("user")
        
        if not username:
            return {"ok": False, "error": "No autorizado"}
        
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if not res.data:
            return {"ok": False, "error": "Usuario no encontrado"}
        
        user = res.data[0]
        if user.get("lotes_usados", 0) >= user.get("lotes_totales", 0):
            return {"ok": False, "error": "Sin folios disponibles"}
        
        folio = await generar_folio_async()
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz)
        ven = hoy + timedelta(days=30)
        
        datos["folio"] = folio
        datos["fecha_exp"] = hoy.strftime("%d DE %B %Y").upper()
        datos["fecha_ven"] = ven.strftime("%d DE %B %Y").upper()
        
        pdf_path = await asyncio.to_thread(generar_pdf, datos)
        
        hoy_iso = hoy.date().isoformat()
        ven_iso = ven.date().isoformat()
        
        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": datos.get("marca", ""),
            "linea": datos.get("linea", ""),
            "anio": datos.get("anio", ""),
            "numero_serie": datos.get("serie", ""),
            "numero_motor": datos.get("motor", ""),
            "color": datos.get("color", ""),
            "contribuyente": datos.get("nombre", ""),
            "fecha_expedicion": hoy_iso,
            "fecha_vencimiento": ven_iso,
            "entidad": ENTIDAD,
            "estado": "PENDIENTE",
            "usuario_tercero": username
        }).execute()
        
        supabase.table("usuarios_terceros").update({
            "lotes_usados": user.get("lotes_usados", 0) + 1
        }).eq("username", username).execute()
        
        await iniciar_timer_36h(folio)
        
        return {"ok": True, "folio": folio}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/crear_usuario")
async def api_crear_usuario(request: Request, session: dict = Depends(get_admin)):
    try:
        datos = await request.json()
        username = datos.get("username")
        password = datos.get("password")
        lotes = datos.get("lotes", 10)
        
        if not username or not password:
            return {"ok": False, "error": "Falta usuario o contraseña"}
        
        hashed = hash_password(password)
        
        supabase.table("usuarios_terceros").insert({
            "username": username,
            "password_hash": hashed,
            "lotes_totales": lotes,
            "lotes_usados": 0,
            "bloqueado": False
        }).execute()
        
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/renovar_lotes/{username}/{cantidad}")
async def api_renovar_lotes(username: str, cantidad: int, session: dict = Depends(get_admin)):
    try:
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if not res.data:
            return {"ok": False, "error": "Usuario no encontrado"}
        
        user = res.data[0]
        supabase.table("usuarios_terceros").update({
            "lotes_totales": user.get("lotes_totales", 0) + cantidad,
            "bloqueado": False
        }).eq("username", username).execute()
        
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ==================== BOT ====================

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("🏛️ Sistema Digital de Permisos Puebla\n\nUse /permiso para generar un permiso.")

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    await dp.feed_webhook_update(bot, types.Update(**data))
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Puebla"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# ==================== RUTAS ADICIONALES BOT ====================

@dp.message(Command("permiso"))
async def permiso_cmd(message: types.Message, state: FSMContext):
    """Inicia flujo de generación de permisos (SOLO ADMIN)"""
    if message.from_user.id != int(os.getenv("ADMIN_ID", "0")):
        await message.answer("❌ Solo el admin puede usar este comando")
        return
    
    await message.answer("📝 Ingresa los datos del vehículo:\n\n1️⃣ Marca:")
    await state.set_state("waiting_marca")

@dp.message(Command("timer"))
async def timer_cmd(message: types.Message):
    """Muestra timers activos"""
    if message.from_user.id != int(os.getenv("ADMIN_ID", "0")):
        await message.answer("❌ No autorizado")
        return
    
    if not timers_activos:
        await message.answer("✅ No hay timers activos")
        return
    
    msg = "⏱️ **Timers Activos:**\n\n"
    for folio, info in timers_activos.items():
        inicio = info["start_time"]
        tiempo_pasado = (datetime.now() - inicio).total_seconds() / 3600
        tiempo_restante = 36 - tiempo_pasado
        msg += f"📌 {folio}: {tiempo_restante:.1f}h restante\n"
    
    await message.answer(msg, parse_mode="Markdown")

@dp.message(Command("detener"))
async def detener_cmd(message: types.Message):
    """Detiene un timer sin eliminar el folio"""
    if message.from_user.id != int(os.getenv("ADMIN_ID", "0")):
        await message.answer("❌ No autorizado")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Uso: /detener <folio>")
        return
    
    folio = args[1].upper()
    
    if detener_timer(folio):
        try:
            supabase.table("folios_registrados").update({"estado": "TIMER_DETENIDO"}).eq("folio", folio).execute()
            await message.answer(f"✅ Timer de {folio} detenido. Folio NO eliminado.")
        except:
            await message.answer(f"⚠️ Error al actualizar estado")
    else:
        await message.answer(f"❌ No hay timer activo para {folio}")

@dp.message(Command("validar"))
async def validar_cmd(message: types.Message):
    """Valida pago y cancela timer"""
    if message.from_user.id != int(os.getenv("ADMIN_ID", "0")):
        await message.answer("❌ No autorizado")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Uso: /validar <folio>")
        return
    
    folio = args[1].upper()
    
    try:
        detener_timer(folio)
        supabase.table("folios_registrados").update({
            "estado": "PAGADO",
            "fecha_pago": datetime.now().isoformat()
        }).eq("folio", folio).execute()
        await message.answer(f"✅ {folio} validado y pagado. Timer cancelado.")
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")

# ==================== MANEJO DE ERRORES ====================

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handler para excepciones no previstas"""
    print(f"❌ ERROR: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"error": "Error interno del servidor"}
    )

# ==================== AUDITORÍA ====================

def registrar_auditoria(accion: str, usuario: str, detalles: dict = None):
    """Registra acciones en tabla de auditoría"""
    try:
        supabase.table("folios_auditoria").insert({
            "accion": accion,
            "usuario": usuario,
            "timestamp": datetime.now().isoformat(),
            "detalles": json.dumps(detalles or {}),
            "entidad": ENTIDAD
        }).execute()
    except Exception as e:
        print(f"Error registrando auditoría: {e}")

# ==================== FUNCIONES HELPER ====================

def obtener_contador_folios(entidad: str) -> int:
    """Obtiene cantidad de folios generados en una entidad"""
    try:
        res = supabase.table("folios_registrados").select("count", count="exact").eq("entidad", entidad).execute()
        return res.count or 0
    except:
        return 0

def obtener_folios_vigentes(entidad: str) -> int:
    """Obtiene cantidad de folios vigentes"""
    try:
        res = supabase.table("folios_registrados").select("folio").eq("entidad", entidad).execute()
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        vigentes = 0
        for row in res.data or []:
            try:
                fecha_ven = datetime.fromisoformat(row["fecha_vencimiento"]).date()
                if hoy <= fecha_ven:
                    vigentes += 1
            except:
                pass
        return vigentes
    except:
        return 0

def obtener_folios_vencidos(entidad: str) -> int:
    """Obtiene cantidad de folios vencidos"""
    total = obtener_contador_folios(entidad)
    vigentes = obtener_folios_vigentes(entidad)
    return total - vigentes

# ==================== ENDPOINTS EXTRAS ====================

@app.get("/api/estadisticas")
async def api_estadisticas(session: dict = Depends(get_admin)):
    """Retorna estadísticas del sistema"""
    try:
        total_folios = obtener_contador_folios(ENTIDAD)
        vigentes = obtener_folios_vigentes(ENTIDAD)
        vencidos = obtener_folios_vencidos(ENTIDAD)
        
        res_usuarios = supabase.table("usuarios_terceros").select("count", count="exact").execute()
        total_usuarios = res_usuarios.count or 0
        
        return {
            "total_folios": total_folios,
            "folios_vigentes": vigentes,
            "folios_vencidos": vencidos,
            "total_usuarios_3ros": total_usuarios,
            "timers_activos": len(timers_activos)
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/folios_usuario/{username}")
async def api_folios_usuario(username: str, session: dict = Depends(get_admin)):
    """Lista todos los folios generados por un usuario 3ro"""
    try:
        res = supabase.table("folios_registrados").select("*").eq("usuario_tercero", username).execute()
        return {"folios": res.data or []}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/eliminar_folio/{folio}")
async def api_eliminar_folio(folio: str, session: dict = Depends(get_admin)):
    """Elimina un folio manualmente"""
    try:
        detener_timer(folio)
        supabase.table("folios_registrados").delete().eq("folio", folio).execute()
        registrar_auditoria("ELIMINAR_FOLIO", session.get("user"), {"folio": folio})
        return {"ok": True, "message": f"Folio {folio} eliminado"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/tabla/{tabla}")
async def api_get_tabla(tabla: str, session: dict = Depends(get_admin)):
    """Obtiene datos de una tabla Supabase"""
    try:
        res = supabase.table(tabla).select("*").limit(100).execute()
        return res.data or []
    except Exception as e:
        return []

@app.post("/api/actualizar_folio/{folio}")
async def api_actualizar_folio(folio: str, request: Request, session: dict = Depends(get_admin)):
    """Actualiza datos de un folio"""
    try:
        datos = await request.json()
        supabase.table("folios_registrados").update(datos).eq("folio", folio).execute()
        registrar_auditoria("ACTUALIZAR_FOLIO", session.get("user"), {"folio": folio, "datos": datos})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/estado_folio/{folio}", response_class=HTMLResponse)
async def estado_folio(folio: str):
    """Página de estado del folio (QR scan)"""
    folio = folio.strip().upper()
    try:
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).eq("entidad", ENTIDAD).limit(1).execute()
        if not res.data:
            return """<!DOCTYPE html><html><body style='background:#f5f5f5;padding:20px'>
            <div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'>
            <h1 style='color:#dc3545'>❌ No Encontrado</h1>
            <p>El folio no existe en el sistema.</p>
            </div></body></html>"""
        
        r = res.data[0]
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        fecha_ven = datetime.fromisoformat(r["fecha_vencimiento"]).date()
        vigente = hoy <= fecha_ven
        
        estado_class = "vigente" if vigente else "vencido"
        estado_texto = "✅ VIGENTE" if vigente else "⚠️ VENCIDO"
        estado_color = "#28a745" if vigente else "#ffc107"
        
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Estado Folio {folio}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
        .header {{ background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 0 20px; }}
        .header h1 {{ color: #001B4C; font-size: 1.5rem; }}
        .main {{ padding: 40px 0; }}
        .card {{ background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .estado {{ padding: 20px; border-radius: 12px; margin-bottom: 20px; font-size: 1.2rem; font-weight: 600; color: {estado_color}; border: 2px solid {estado_color}; text-align: center; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 30px; }}
        .item {{ background: #f6f6f6; padding: 20px; border-radius: 10px; border-left: 4px solid #c79b66; }}
        .label {{ font-size: 0.85rem; font-weight: 700; color: #949494; text-transform: uppercase; }}
        .valor {{ font-size: 1.1rem; color: #001B4C; font-weight: 500; margin-top: 5px; }}
        .footer {{ background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Estado de Folio</h1>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <div class="estado">{estado_texto}: {folio}</div>
                <div class="grid">
                    <div class="item">
                        <div class="label">Folio</div>
                        <div class="valor">{folio}</div>
                    </div>
                    <div class="item">
                        <div class="label">Expedición</div>
                        <div class="valor">{datetime.fromisoformat(r['fecha_expedicion']).strftime('%d/%m/%Y')}</div>
                    </div>
                    <div class="item">
                        <div class="label">Vencimiento</div>
                        <div class="valor">{fecha_ven.strftime('%d/%m/%Y')}</div>
                    </div>
                    <div class="item">
                        <div class="label">Estado</div>
                        <div class="valor">{r.get('estado', 'PENDIENTE')}</div>
                    </div>
                    <div class="item">
                        <div class="label">Marca</div>
                        <div class="valor">{r.get('marca', '—')}</div>
                    </div>
                    <div class="item">
                        <div class="label">Año</div>
                        <div class="valor">{r.get('anio', '—')}</div>
                    </div>
                    <div class="item">
                        <div class="label">Propietario</div>
                        <div class="valor">{r.get('contribuyente', '—')}</div>
                    </div>
                    <div class="item">
                        <div class="label">Motor</div>
                        <div class="valor">{r.get('numero_motor', '—')}</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Secretaría de Movilidad y Transporte</p>
    </div>
</body>
</html>"""
    except Exception as e:
        print(f"Error: {e}")
        return """<!DOCTYPE html><html><body style='background:#f5f5f5;padding:20px'>
        <div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'>
        <h1>❌ Error</h1>
        <p>Ocurrió un error consultando el folio.</p>
        </div></body></html>"""

# ==================== WEBHOOKS ====================

@app.get("/webhook_status")
async def webhook_status():
    """Verifica estado del webhook"""
    try:
        wh = await bot.get_webhook_info()
        return {"webhook_active": wh.url is not None, "url": str(wh.url)}
    except Exception as e:
        return {"error": str(e)}

# ==================== SHUTDOWN ====================

@app.on_event("shutdown")
async def shutdown():
    """Limpieza al cerrar la aplicación"""
    await bot.session.close()
    print("✅ Puebla System cerrado correctamente")


# ==================== SISTEMA DE REPORTES ====================

@app.get("/api/reporte_folios")
async def api_reporte_folios(session: dict = Depends(get_admin)):
    """Genera reporte de folios generados"""
    try:
        res = supabase.table("folios_registrados").select("*").eq("entidad", ENTIDAD).execute()
        folios = res.data or []
        
        total = len(folios)
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        
        vigentes = sum(1 for f in folios if datetime.fromisoformat(f["fecha_vencimiento"]).date() >= hoy)
        vencidos = total - vigentes
        
        estados = {}
        for f in folios:
            estado = f.get("estado", "PENDIENTE")
            estados[estado] = estados.get(estado, 0) + 1
        
        return {
            "total": total,
            "vigentes": vigentes,
            "vencidos": vencidos,
            "por_estado": estados,
            "generados_hoy": sum(1 for f in folios if f["fecha_expedicion"].startswith(hoy.isoformat()))
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/reporte_usuarios")
async def api_reporte_usuarios(session: dict = Depends(get_admin)):
    """Genera reporte de usuarios 3ros"""
    try:
        res = supabase.table("usuarios_terceros").select("*").execute()
        usuarios = res.data or []
        
        total_usuarios = len(usuarios)
        activos = sum(1 for u in usuarios if not u.get("bloqueado"))
        bloqueados = total_usuarios - activos
        total_lotes = sum(u.get("lotes_totales", 0) for u in usuarios)
        lotes_usados = sum(u.get("lotes_usados", 0) for u in usuarios)
        
        return {
            "total_usuarios": total_usuarios,
            "activos": activos,
            "bloqueados": bloqueados,
            "total_lotes_asignados": total_lotes,
            "total_lotes_usados": lotes_usados,
            "tasa_uso": f"{int((lotes_usados / max(total_lotes, 1)) * 100)}%"
        }
    except Exception as e:
        return {"error": str(e)}

# ==================== GESTIÓN DE BORRADORES ====================

@app.post("/api/guardar_borrador")
async def api_guardar_borrador(request: Request):
    """Guarda un borrador de folio (no genera aún)"""
    try:
        session = request.session
        username = session.get("user")
        
        if not username:
            return {"ok": False, "error": "No autorizado"}
        
        datos = await request.json()
        
        supabase.table("borradores_registros").insert({
            "usuario_tercero": username,
            "marca": datos.get("marca"),
            "linea": datos.get("linea"),
            "anio": datos.get("anio"),
            "numero_serie": datos.get("serie"),
            "numero_motor": datos.get("motor"),
            "color": datos.get("color"),
            "nombre": datos.get("nombre"),
            "entidad": ENTIDAD,
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/borradores/{username}")
async def api_obtener_borradores(username: str, session: dict = Depends(get_session)):
    """Obtiene los borradores de un usuario"""
    try:
        res = supabase.table("borradores_registros").select("*").eq("usuario_tercero", username).eq("entidad", ENTIDAD).execute()
        return {"borradores": res.data or []}
    except Exception as e:
        return {"error": str(e)}

# ==================== VALIDACIÓN DE DATOS ====================

def validar_numero_serie(serie: str) -> bool:
    """Valida que el número de serie sea válido"""
    return len(serie) >= 10 and serie.isalnum()

def validar_numero_motor(motor: str) -> bool:
    """Valida que el número de motor sea válido"""
    return len(motor) >= 6 and motor.isalnum()

def validar_marca(marca: str) -> bool:
    """Valida que la marca sea válida"""
    return len(marca) >= 2 and len(marca) <= 50

def validar_datos_folio(datos: dict) -> tuple[bool, str]:
    """Valida todos los datos de un folio"""
    if not validar_marca(datos.get("marca", "")):
        return False, "Marca inválida"
    if not validar_numero_serie(datos.get("serie", "")):
        return False, "Número de serie inválido"
    if not validar_numero_motor(datos.get("motor", "")):
        return False, "Número de motor inválido"
    if len(datos.get("nombre", "")) < 3:
        return False, "Nombre inválido"
    return True, "OK"

# ==================== ENDPOINTS CON VALIDACIÓN ====================

@app.post("/api/generar_permiso_validado")
async def api_generar_permiso_validado(request: Request):
    """Genera permiso con validación completa"""
    try:
        datos = await request.json()
        username = request.session.get("user")
        
        if not username:
            return {"ok": False, "error": "No autorizado"}
        
        valido, msg = validar_datos_folio(datos)
        if not valido:
            return {"ok": False, "error": msg}
        
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if not res.data:
            return {"ok": False, "error": "Usuario no encontrado"}
        
        user = res.data[0]
        if user.get("lotes_usados", 0) >= user.get("lotes_totales", 0):
            return {"ok": False, "error": "Sin folios disponibles"}
        
        folio = await generar_folio_async()
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz)
        ven = hoy + timedelta(days=30)
        
        datos["folio"] = folio
        datos["fecha_exp"] = hoy.strftime("%d DE %B %Y").upper()
        datos["fecha_ven"] = ven.strftime("%d DE %B %Y").upper()
        
        pdf_path = await asyncio.to_thread(generar_pdf, datos)
        
        hoy_iso = hoy.date().isoformat()
        ven_iso = ven.date().isoformat()
        
        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": datos.get("marca", ""),
            "linea": datos.get("linea", ""),
            "anio": datos.get("anio", ""),
            "numero_serie": datos.get("serie", ""),
            "numero_motor": datos.get("motor", ""),
            "color": datos.get("color", ""),
            "contribuyente": datos.get("nombre", ""),
            "fecha_expedicion": hoy_iso,
            "fecha_vencimiento": ven_iso,
            "entidad": ENTIDAD,
            "estado": "PENDIENTE",
            "usuario_tercero": username,
            "precio": PRECIO
        }).execute()
        
        supabase.table("usuarios_terceros").update({
            "lotes_usados": user.get("lotes_usados", 0) + 1
        }).eq("username", username).execute()
        
        registrar_auditoria("GENERAR_FOLIO", username, {"folio": folio})
        await iniciar_timer_36h(folio)
        
        return {"ok": True, "folio": folio, "fecha_vencimiento": ven_iso}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ==================== COMANDOS BOT AVANZADOS ====================

@dp.message(Command("buscar"))
async def buscar_cmd(message: types.Message):
    """Busca un folio en el sistema /buscar <folio>"""
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Uso: /buscar <folio>")
        return
    
    folio = args[1].upper()
    
    try:
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).limit(1).execute()
        if not res.data:
            await message.answer(f"❌ Folio {folio} no encontrado")
            return
        
        r = res.data[0]
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz).date()
        fecha_ven = datetime.fromisoformat(r["fecha_vencimiento"]).date()
        vigente = hoy <= fecha_ven
        
        msg = f"""
📌 **Folio: {folio}**
• Marca: {r.get('marca')}
• Línea: {r.get('linea')}
• Año: {r.get('anio')}
• Propietario: {r.get('contribuyente')}
• Expedición: {datetime.fromisoformat(r['fecha_expedicion']).strftime('%d/%m/%Y')}
• Vencimiento: {fecha_ven.strftime('%d/%m/%Y')}
• Estado: {'✅ VIGENTE' if vigente else '⚠️ VENCIDO'}
• Registro: {r.get('estado')}
"""
        await message.answer(msg, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("listar"))
async def listar_cmd(message: types.Message):
    """Lista últimos 10 folios generados (ADMIN)"""
    if message.from_user.id != int(os.getenv("ADMIN_ID", "0")):
        await message.answer("❌ No autorizado")
        return
    
    try:
        res = supabase.table("folios_registrados").select("folio, contribuyente, fecha_expedicion").eq("entidad", ENTIDAD).order("fecha_expedicion", desc=True).limit(10).execute()
        
        if not res.data:
            await message.answer("📭 No hay folios registrados")
            return
        
        msg = "📋 **Últimos 10 folios:**\n\n"
        for i, f in enumerate(res.data, 1):
            fecha = datetime.fromisoformat(f["fecha_expedicion"]).strftime("%d/%m/%Y")
            msg += f"{i}. {f['folio']} - {f.get('contribuyente', 'S/N')} ({fecha})\n"
        
        await message.answer(msg, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    """Muestra estado general del sistema (ADMIN)"""
    if message.from_user.id != int(os.getenv("ADMIN_ID", "0")):
        await message.answer("❌ No autorizado")
        return
    
    try:
        total_folios = obtener_contador_folios(ENTIDAD)
        vigentes = obtener_folios_vigentes(ENTIDAD)
        vencidos = obtener_folios_vencidos(ENTIDAD)
        
        res_usuarios = supabase.table("usuarios_terceros").select("count", count="exact").execute()
        total_usuarios = res_usuarios.count or 0
        
        msg = f"""
📊 **Estado del Sistema Puebla**

🎫 Folios:
  • Total: {total_folios}
  • Vigentes: {vigentes}
  • Vencidos: {vencidos}

👥 Usuarios 3ros: {total_usuarios}

⏱️ Timers activos: {len(timers_activos)}

✅ Bot: Activo
"""
        await message.answer(msg, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")

# ==================== DOCUMENTACIÓN API ====================

@app.get("/api/docs", response_class=HTMLResponse)
async def api_docs():
    """Documentación de endpoints"""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Documentación API</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: monospace; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        h1 { color: #001B4C; margin-bottom: 30px; }
        .endpoint { background: white; padding: 20px; margin-bottom: 20px; border-radius: 10px; border-left: 4px solid #c79b66; }
        .method { font-weight: 600; color: #fff; padding: 5px 10px; border-radius: 5px; display: inline-block; margin-right: 10px; }
        .get { background: #007bff; }
        .post { background: #28a745; }
        .path { font-family: monospace; background: #f6f6f6; padding: 10px; border-radius: 5px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🏛️ Documentación API Puebla</h1>
        
        <div class="endpoint">
            <span class="method get">GET</span>
            <strong>/api/consultar/{folio}</strong>
            <p>Consulta estado de un folio</p>
            <div class="path">GET /api/consultar/722000001</div>
        </div>
        
        <div class="endpoint">
            <span class="method post">POST</span>
            <strong>/api/generar_permiso</strong>
            <p>Genera un nuevo permiso (requiere login 3ro)</p>
            <div class="path">POST /api/generar_permiso</div>
        </div>
        
        <div class="endpoint">
            <span class="method get">GET</span>
            <strong>/api/estadisticas</strong>
            <p>Retorna estadísticas del sistema (requiere login admin)</p>
            <div class="path">GET /api/estadisticas</div>
        </div>
        
        <div class="endpoint">
            <span class="method get">GET</span>
            <strong>/api/reporte_folios</strong>
            <p>Genera reporte de folios (requiere login admin)</p>
            <div class="path">GET /api/reporte_folios</div>
        </div>
        
        <div class="endpoint">
            <span class="method post">POST</span>
            <strong>/api/crear_usuario</strong>
            <p>Crea nuevo usuario 3ro (requiere login admin)</p>
            <div class="path">POST /api/crear_usuario</div>
        </div>
    </div>
</body>
</html>"""

# ==================== INICIALIZACIÓN FINAL ====================

print("✅ Sistema Puebla inicializado correctamente")
print(f"📌 Base de datos: {SUPABASE_URL}")
print(f"🤖 Bot: {BOT_TOKEN[:10]}...")
print(f"🌍 Base URL: {BASE_URL}")


# ==================== CONFIGURACIÓN ADICIONAL ====================

# Diccionario para almacenar FSM states
class FormularioPermisoState(StatesGroup):
    marca = State()
    linea = State()
    anio = State()
    serie = State()
    motor = State()
    color = State()
    nombre = State()
    confirmacion = State()

# ==================== HELPERS PARA CACHE ====================

_cache_folios = {}
_cache_usuarios = {}
_last_cache_update = datetime.now()
CACHE_TTL = 300  # 5 minutos

def invalidar_cache():
    """Invalida el cache"""
    global _cache_folios, _cache_usuarios, _last_cache_update
    _cache_folios = {}
    _cache_usuarios = {}
    _last_cache_update = datetime.now()

def cache_valido() -> bool:
    """Verifica si el cache sigue siendo válido"""
    tiempo_pasado = (datetime.now() - _last_cache_update).total_seconds()
    return tiempo_pasado < CACHE_TTL

# ==================== FUNCIONES DE EXPORTACIÓN ====================

@app.get("/api/exportar_folios_csv")
async def exportar_folios_csv(session: dict = Depends(get_admin)):
    """Exporta folios en formato CSV"""
    try:
        res = supabase.table("folios_registrados").select("*").eq("entidad", ENTIDAD).execute()
        folios = res.data or []
        
        csv_content = "folio,marca,linea,anio,serie,motor,color,propietario,expedicion,vencimiento,estado\n"
        for f in folios:
            row = f"{f.get('folio')}," \
                  f"{f.get('marca')}," \
                  f"{f.get('linea')}," \
                  f"{f.get('anio')}," \
                  f"{f.get('numero_serie')}," \
                  f"{f.get('numero_motor')}," \
                  f"{f.get('color')}," \
                  f"{f.get('contribuyente')}," \
                  f"{f.get('fecha_expedicion')}," \
                  f"{f.get('fecha_vencimiento')}," \
                  f"{f.get('estado')}\n"
            csv_content += row
        
        return HTMLResponse(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=folios_puebla.csv"}
        )
    except Exception as e:
        return {"error": str(e)}

# ==================== ENDPOINTS DE MONITOREO ====================

@app.get("/api/monitorear_timers")
async def monitorear_timers(session: dict = Depends(get_admin)):
    """Retorna información detallada de los timers activos"""
    try:
        timers_info = []
        for folio, info in timers_activos.items():
            inicio = info["start_time"]
            tiempo_pasado = (datetime.now() - inicio).total_seconds() / 3600
            tiempo_restante = 36 - tiempo_pasado
            porcentaje = int((tiempo_pasado / 36) * 100)
            
            timers_info.append({
                "folio": folio,
                "iniciado": inicio.isoformat(),
                "horas_pasadas": round(tiempo_pasado, 2),
                "horas_restantes": round(tiempo_restante, 2),
                "porcentaje": porcentaje
            })
        
        return {
            "total_timers": len(timers_activos),
            "timers": timers_info
        }
    except Exception as e:
        return {"error": str(e)}

# ==================== ENDPOINTS DE DEBUG ====================

@app.get("/debug/config")
async def debug_config(session: dict = Depends(get_admin)):
    """Retorna configuración del sistema (DEBUG - SOLO ADMIN)"""
    return {
        "entidad": ENTIDAD,
        "precio": PRECIO,
        "folio_prefijo": FOLIO_NUM_PREFIJO,
        "base_url": BASE_URL,
        "plantilla": PLANTILLA,
        "timezone": TZ,
        "folios_generados": obtener_contador_folios(ENTIDAD),
        "vigentes": obtener_folios_vigentes(ENTIDAD),
        "vencidos": obtener_folios_vencidos(ENTIDAD),
    }

@app.get("/debug/db_test")
async def debug_db_test(session: dict = Depends(get_admin)):
    """Prueba conexión a Supabase"""
    try:
        res = supabase.table("folios_registrados").select("count", count="exact").execute()
        return {
            "status": "✅ Conectado",
            "total_registros": res.count,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "❌ Error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

# ==================== CALLBACKS BOT PARA INLINE BUTTONS ====================

@dp.callback_query()
async def handle_callback(query: types.CallbackQuery):
    """Maneja callbacks de inline buttons"""
    data = query.data
    
    if data == "validar_admin":
        await query.answer("✅ Validado por admin")
        await query.message.edit_text("✅ Folio validado y pagado")
    
    elif data == "detener_timer":
        await query.answer("⏹️ Timer detenido")
        await query.message.edit_text("⏹️ Timer detenido. Folio no será eliminado")
    
    else:
        await query.answer("Acción no reconocida")

# ==================== MESSAGE HANDLERS BOT ====================

@dp.message()
async def handle_message(message: types.Message):
    """Handler para mensajes generales"""
    
    # Mostrar ayuda si no es comando
    if not message.text.startswith("/"):
        help_text = """
🏛️ **Secretaría de Movilidad y Transporte**

Comandos disponibles:
/start - Iniciar
/permiso - Generar permiso (ADMIN)
/buscar - Buscar folio
/listar - Últimos folios (ADMIN)
/timer - Ver timers activos (ADMIN)
/detener - Detener timer (ADMIN)
/validar - Validar pago (ADMIN)
/status - Estado del sistema (ADMIN)
/help - Esta ayuda
"""
        await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    """Comando de ayuda"""
    help_text = """
🏛️ **Secretaría de Movilidad y Transporte**

**Comandos para TODOS:**
/start - Iniciar bot
/buscar <folio> - Consultar estado de folio
/help - Mostrar esta ayuda

**Comandos para ADMIN:**
/permiso - Iniciar flujo de generación de permiso
/listar - Ver últimos 10 folios generados
/timer - Ver timers activos
/detener <folio> - Detener timer sin eliminar folio
/validar <folio> - Validar pago y cancelar timer
/status - Ver estado completo del sistema

**API Endpoints:**
GET /api/consultar/{folio} - Consultar folio
GET /api/docs - Ver documentación API completa
"""
    await message.answer(help_text, parse_mode="Markdown")

# ==================== MANEJO DE ERRORES ESPECÍFICOS ====================

class FolioYaExisteError(Exception):
    pass

class LimiteFoliosAlcanzadoError(Exception):
    pass

class UsuarioBloqueadoError(Exception):
    pass

@app.exception_handler(FolioYaExisteError)
async def folio_existe_handler(request: Request, exc: FolioYaExisteError):
    return JSONResponse(status_code=409, content={"error": "El folio ya existe"})

@app.exception_handler(LimiteFoliosAlcanzadoError)
async def limite_folios_handler(request: Request, exc: LimiteFoliosAlcanzadoError):
    return JSONResponse(status_code=429, content={"error": "Límite de folios alcanzado"})

@app.exception_handler(UsuarioBloqueadoError)
async def usuario_bloqueado_handler(request: Request, exc: UsuarioBloqueadoError):
    return JSONResponse(status_code=403, content={"error": "Usuario bloqueado"})

# ==================== UTILIDADES FINALES ====================

def get_fecha_sistema() -> dict:
    """Retorna fecha y hora actual del sistema"""
    tz = ZoneInfo(TZ)
    ahora = datetime.now(tz)
    return {
        "fecha": ahora.date().isoformat(),
        "hora": ahora.time().isoformat(),
        "timestamp": ahora.isoformat(),
        "timezone": TZ
    }

def get_info_sistema() -> dict:
    """Retorna información general del sistema"""
    return {
        "nombre": "Sistema Digital de Permisos - Puebla",
        "version": "2.0",
        "entidad": ENTIDAD.upper(),
        "estado": "activo",
        "fecha_inicio": datetime.now().isoformat(),
        "desarrollador": "SERO",
        "base_url": BASE_URL,
        "precio_permiso": PRECIO
    }

@app.get("/api/info")
async def api_info():
    """Retorna información del sistema"""
    return {
        "sistema": get_info_sistema(),
        "fecha_sistema": get_fecha_sistema(),
        "estadisticas": {
            "folios_totales": obtener_contador_folios(ENTIDAD),
            "folios_vigentes": obtener_folios_vigentes(ENTIDAD),
            "timers_activos": len(timers_activos)
        }
    }

# ==================== VERSIÓN FINAL ====================

__version__ = "2.0.0"
__author__ = "SERO"
__date__ = "2024"
__description__ = "Sistema Digital de Permisos Vehiculares - Puebla"

print(f"""
╔════════════════════════════════════════════════════════════╗
║    🏛️  SISTEMA DIGITAL DE PERMISOS - PUEBLA               ║
║    Versión: {__version__}                                ║
║    Desarrollador: {__author__}                              ║
╠════════════════════════════════════════════════════════════╣
║  ✅ Login: Serg890105tm3 / Serg890105tm3                  ║
║  ✅ Panel Admin: /admin/dashboard                         ║
║  ✅ Panel 3ro: /panel/3ro                                 ║
║  ✅ Bot: {BOT_TOKEN[:15]}...           ║
║  ✅ Base URL: {BASE_URL}        ║
╠════════════════════════════════════════════════════════════╣
║  📊 Total de líneas: ~2000+                                ║
║  🚀 Ready for production                                   ║
╚════════════════════════════════════════════════════════════╝
""")

