from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.sessions import SessionMiddleware
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client, Client
import os
import hashlib
import secrets
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

# Admin creds
ADMIN_USER = "Serg890105tm3"
ADMIN_PASS = "Serg890105tm3"

os.makedirs(OUTPUT_DIR, exist_ok=True)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

# ==================== HASH ====================
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${hash_obj.hex()}"

def verify_password(password: str, hash_password: str) -> bool:
    try:
        salt, hash_hex = hash_password.split('$')
        hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return hash_obj.hex() == hash_hex
    except:
        return False

# ==================== TIMERS ====================
timers_activos = {}

async def eliminar_folio_automatico(folio: str):
    try:
        uid = timers_activos[folio]["user_id"] if folio in timers_activos else None
        supabase.table("folios_registrados").delete().eq("folio", folio).execute()
        if folio in timers_activos:
            del timers_activos[folio]
    except Exception as e:
        print(f"Error eliminando folio {folio}: {e}")

async def iniciar_timer_36h(user_id: int, folio: str):
    async def timer_task():
        await asyncio.sleep(36 * 3600)
        if folio in timers_activos:
            await eliminar_folio_automatico(folio)

    task = asyncio.create_task(timer_task())
    timers_activos[folio] = {"task": task, "user_id": user_id, "start_time": datetime.now()}

def detener_timer(folio: str) -> bool:
    if folio not in timers_activos: return False
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
        if r.data: return r.data[0]["ultimo_asignado"]
        return None
    except: return None

def _guardar_watermark(numero: int):
    try:
        supabase.table("folio_watermark").upsert({
            "prefijo": "PUE", "ultimo_asignado": numero
        }).execute()
    except: pass

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
    except: _folio_counter["siguiente"] = 1

def _folio_existe(folio: str) -> bool:
    try:
        r = supabase.table("folios_registrados").select("folio").eq("folio", folio).execute()
        return len(r.data) > 0
    except: return False

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
    print(f"✅ Puebla Admin iniciado")
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# ==================== FUNCIONES HELPER ====================
async def get_session(request: Request) -> dict:
    session = request.session
    if not session.get("user"):
        raise HTTPException(status_code=401, detail="No autorizado")
    return session

async def get_admin(request: Request) -> dict:
    session = request.session
    if not session.get("user") or session.get("user") != ADMIN_USER:
        raise HTTPException(status_code=403, detail="Admin only")
    return session

def get_all_tables():
    """Retorna lista de todas las tablas en Supabase"""
    try:
        # Tablas conocidas del sistema
        return [
            "folios_registrados",
            "usuarios_terceros",
            "consecutivos_puebla",
            "folio_watermark",
            "borradores_registros",
            "folios_auditoria"
        ]
    except:
        return []

# ==================== ROUTES ====================

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
        .form-group label { font-weight: 600; color: #495057; margin-bottom: 8px; }
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
        .footer { background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; }
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
                    mostrarNoExiste();
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
        
        function mostrarNoExiste() {
            const est = document.getElementById('estado');
            est.className = 'estado noexiste';
            est.textContent = '✗ No existe permiso';
            ['f','exp','ven','mar','lin','ano','ser','mot','col','pro'].forEach(id => {
                document.getElementById(id).textContent = '—';
            });
            document.getElementById('resultado').classList.add('visible');
        }
    </script>
</body>
</html>"""

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Login de usuario (admin o 3ro)"""
    
    # Check admin
    if username == ADMIN_USER and password == ADMIN_PASS:
        request.session["user"] = ADMIN_USER
        request.session["is_admin"] = True
        return RedirectResponse("/admin/dashboard", status_code=302)
    
    # Check usuario 3ro
    try:
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if res.data:
            user = res.data[0]
            if verify_password(password, user["password_hash"]):
                if user.get("bloqueado"):
                    return HTMLResponse("""<html><body style='background:#f5f5f5;padding:20px'>
                    <div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'>
                    <h1>❌ Cuenta Bloqueada</h1>
                    <p>Tu cuenta está bloqueada. Contacta al administrador.</p>
                    <a href='/login'>Volver</a>
                    </div></body></html>""")
                request.session["user"] = username
                request.session["is_admin"] = False
                return RedirectResponse("/panel/3ro", status_code=302)
    except:
        pass
    
    # Error
    return HTMLResponse("""<html><body style='background:#f5f5f5;padding:20px'>
    <div style='max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center'>
    <h1>❌ Credenciales inválidas</h1>
    <a href='/login'>Reintentar</a>
    </div></body></html>""")

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

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(session: dict = Depends(get_admin)):
    """Dashboard Admin"""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }
        .header { background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .header h1 { color: #001B4C; font-size: 1.5rem; font-weight: 300; }
        .nav { display: flex; gap: 20px; margin: 20px 0 0 0; border-bottom: 1px solid #eee; }
        .nav a { padding: 10px 20px; color: #495057; text-decoration: none; border-bottom: 3px solid transparent; }
        .nav a:hover { border-bottom-color: #c79b66; }
        .main { padding: 40px 0; }
        .card { background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 30px; }
        .card h2 { color: #001B4C; margin-bottom: 20px; }
        .tabla { width: 100%; border-collapse: collapse; }
        .tabla th { background: #f6f6f6; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #ddd; }
        .tabla td { padding: 12px; border-bottom: 1px solid #eee; }
        .tabla tr:hover { background: #f9f9f9; }
        .btn { padding: 8px 16px; background: #c79b66; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
        .btn:hover { background: #b8894e; }
        .btn-danger { background: #dc3545; }
        .btn-danger:hover { background: #c82333; }
        .footer { background: #5f1b2d; color: #fffbef; padding: 40px 0; text-align: center; margin-top: 60px; }
        @media (max-width: 768px) { .nav { flex-wrap: wrap; } }
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🏛️ Panel Admin Puebla</h1>
            <div class="nav">
                <a href="/admin/dashboard">📊 Dashboard</a>
                <a href="/admin/usuarios">👥 Usuarios 3ros</a>
                <a href="/admin/tablas">📋 Todas las Tablas</a>
                <a href="/logout">🚪 Salir</a>
            </div>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>📊 Resumen Rápido</h2>
                <p>Bienvenido Admin. Selecciona una opción arriba.</p>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Sistema de Permisos Puebla</p>
    </div>
</body>
</html>"""

@app.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_usuarios(session: dict = Depends(get_admin)):
    """Gestión de usuarios 3ros"""
    try:
        res = supabase.table("usuarios_terceros").select("*").execute()
        usuarios = res.data or []
    except:
        usuarios = []
    
    tabla_html = ""
    for u in usuarios:
        tabla_html += f"""
        <tr>
            <td>{u.get('username', '—')}</td>
            <td>{u.get('lotes_usados', 0)}/{u.get('lotes_totales', 0)}</td>
            <td>{int((u.get('lotes_usados', 0) / max(u.get('lotes_totales', 1), 1)) * 100)}%</td>
            <td>{'🔒 Bloqueado' if u.get('bloqueado') else '✅ Activo'}</td>
            <td><button class="btn" onclick="renovar('{u.get('username')}')">Renovar</button></td>
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
        .nav a {{ padding: 10px 20px; color: #495057; text-decoration: none; border-bottom: 3px solid transparent; }}
        .nav a.active {{ border-bottom-color: #c79b66; color: #001B4C; font-weight: 600; }}
        .main {{ padding: 40px 0; }}
        .card {{ background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .tabla {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        .tabla th {{ background: #f6f6f6; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #ddd; }}
        .tabla td {{ padding: 12px; border-bottom: 1px solid #eee; }}
        .tabla tr:hover {{ background: #f9f9f9; }}
        .btn {{ padding: 8px 16px; background: #c79b66; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }}
        .btn:hover {{ background: #b8894e; }}
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
                <a href="/admin/tablas">📋 Todas las Tablas</a>
                <a href="/logout">🚪 Salir</a>
            </div>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>👥 Gestión de Usuarios 3ros</h2>
                <table class="tabla">
                    <thead>
                        <tr>
                            <th>Usuario</th>
                            <th>Folios Usados</th>
                            <th>Porcentaje</th>
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
        function renovar(username) {{
            const lotes = prompt('¿Cuántos folios deseas agregar?');
            if (lotes) {{
                fetch(`/api/renovar_lotes/${{username}}/${{lotes}}`, {{method: 'POST'}})
                    .then(r => r.json())
                    .then(d => d.ok ? location.reload() : alert('Error'))
                    .catch(e => alert('Error: ' + e));
            }}
        }}
    </script>
</body>
</html>"""

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
            porcentaje = int((lotes_usado / max(lotes_total, 1)) * 100)
            
            if lotes_restantes <= 0:
                return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sin Folios</title></head><body style="background:#f5f5f5;padding:20px"><div style="max-width:600px;margin:50px auto;background:white;padding:30px;border-radius:10px;text-align:center"><h1>❌ Sin Folios Disponibles</h1><p>Has agotado tu límite. Contacta al administrador via WhatsApp o SMS.</p><a href="/logout">Salir</a></div></body></html>"""
            
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
                    alert('❌ Error: ' + d.error);
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
    except:
        return RedirectResponse("/login", status_code=302)

@app.get("/logout")
async def logout(request: Request):
    """Logout"""
    request.session.clear()
    return RedirectResponse("/", status_code=302)

# ==================== API ENDPOINTS ====================

@app.get("/api/consultar/{folio}")
async def api_consultar(folio: str):
    folio = folio.strip().upper()
    try:
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).eq("entidad", ENTIDAD).limit(1).execute()
        if not res.data: return {"ok": False}
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
async def api_generar_permiso(request: Request, datos: dict):
    """Genera permiso para usuario 3ro"""
    try:
        session = request.session
        username = session.get("user")
        if not username or session.get("is_admin"):
            return {"ok": False, "error": "No autorizado"}
        
        # Check lotes
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if not res.data:
            return {"ok": False, "error": "Usuario no encontrado"}
        
        user = res.data[0]
        if user.get("lotes_usados", 0) >= user.get("lotes_totales", 0):
            return {"ok": False, "error": "Sin folios disponibles"}
        
        # Generar folio
        folio = await generar_folio_async()
        tz = ZoneInfo(TZ)
        hoy = datetime.now(tz)
        ven = hoy + timedelta(days=30)
        
        datos["folio"] = folio
        datos["fecha_exp"] = hoy.strftime("%d DE %B %Y").upper()
        datos["fecha_ven"] = ven.strftime("%d DE %B %Y").upper()
        
        # Generar PDF
        pdf_path = await asyncio.to_thread(generar_pdf, datos)
        
        # Guardar en DB
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
        
        # Actualizar lotes usado
        supabase.table("usuarios_terceros").update({
            "lotes_usados": user.get("lotes_usados", 0) + 1
        }).eq("username", username).execute()
        
        # Iniciar timer
        await iniciar_timer_36h(0, folio)
        
        return {"ok": True, "folio": folio}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/renovar_lotes/{username}/{cantidad}")
async def api_renovar_lotes(username: str, cantidad: int, session: dict = Depends(get_admin)):
    """Renovar folios a usuario 3ro"""
    try:
        res = supabase.table("usuarios_terceros").select("*").eq("username", username).execute()
        if not res.data:
            return {"ok": False}
        user = res.data[0]
        supabase.table("usuarios_terceros").update({
            "lotes_totales": user.get("lotes_totales", 0) + cantidad,
            "bloqueado": False
        }).eq("username", username).execute()
        return {"ok": True}
    except:
        return {"ok": False}

@app.get("/admin/tablas", response_class=HTMLResponse)
async def admin_tablas(session: dict = Depends(get_admin)):
    """Editor de todas las tablas"""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Todas las Tablas</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }
        .header { background: white; padding: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .header h1 { color: #001B4C; font-size: 1.5rem; font-weight: 300; }
        .nav { display: flex; gap: 20px; margin: 20px 0 0 0; border-bottom: 1px solid #eee; }
        .nav a { padding: 10px 20px; color: #495057; text-decoration: none; border-bottom: 3px solid transparent; }
        .nav a.active { border-bottom-color: #c79b66; color: #001B4C; font-weight: 600; }
        .main { padding: 40px 0; }
        .card { background: white; border-radius: 15px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .selector { margin-bottom: 30px; }
        select { padding: 10px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 1rem; }
        .tabla { width: 100%; border-collapse: collapse; margin-top: 20px; }
        .tabla th { background: #f6f6f6; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #ddd; }
        .tabla td { padding: 12px; border-bottom: 1px solid #eee; }
        .tabla input { width: 100%; padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; }
        .tabla input:focus { outline: none; border-color: #001B4C; }
        .tabla tr:hover { background: #f9f9f9; }
        .btn { padding: 8px 16px; background: #c79b66; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
        .btn:hover { background: #b8894e; }
        .btn-danger { background: #dc3545; }
        .btn-danger:hover { background: #c82333; }
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
                <a href="/admin/tablas" class="active">📋 Todas las Tablas</a>
                <a href="/logout">🚪 Salir</a>
            </div>
        </div>
    </div>
    
    <div class="main">
        <div class="container">
            <div class="card">
                <h2>📋 Editor de Tablas Supabase</h2>
                <div class="selector">
                    <label>Selecciona tabla:</label>
                    <select id="tablaSelect" onchange="cargarTabla()">
                        <option value="">-- Selecciona --</option>
                        <option value="folios_registrados">Folios Registrados</option>
                        <option value="usuarios_terceros">Usuarios 3ros</option>
                        <option value="consecutivos_puebla">Consecutivos</option>
                        <option value="folio_watermark">Watermark</option>
                        <option value="borradores_registros">Borradores</option>
                        <option value="folios_auditoria">Auditoría</option>
                    </select>
                </div>
                <div id="contenedor"></div>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>© 2024 Sistema de Permisos Puebla</p>
    </div>
    
    <script>
        function cargarTabla() {
            const tabla = document.getElementById('tablaSelect').value;
            if (!tabla) return;
            fetch(`/api/tabla/${tabla}`)
                .then(r => r.json())
                .then(d => renderTabla(tabla, d))
                .catch(e => alert('Error: ' + e));
        }
        
        function renderTabla(tabla, datos) {
            let html = '<table class="tabla"><thead><tr>';
            if (datos.length === 0) {
                document.getElementById('contenedor').innerHTML = '<p>No hay datos</p>';
                return;
            }
            Object.keys(datos[0]).forEach(k => html += `<th>${k}</th>`);
            html += '<th>Acción</th></tr></thead><tbody>';
            datos.forEach((row, i) => {
                html += '<tr>';
                Object.entries(row).forEach(([k, v]) => html += `<td><input type="text" value="${v || ''}" data-row="${i}" data-col="${k}" data-tabla="${tabla}"></td>`);
                html += `<td><button class="btn btn-danger" onclick="eliminarFila('${tabla}', ${i})">Eliminar</button></td></tr>`;
            });
            html += '</tbody></table>';
            document.getElementById('contenedor').innerHTML = html;
        }
        
        function eliminarFila(tabla, i) {
            if (confirm('¿Eliminar fila?')) {
                console.log('Eliminar:', tabla, i);
            }
        }
    </script>
</body>
</html>"""

@app.get("/api/tabla/{tabla}")
async def api_tabla(tabla: str, session: dict = Depends(get_admin)):
    """Obtener datos de una tabla"""
    try:
        res = supabase.table(tabla).select("*").limit(100).execute()
        return res.data or []
    except:
        return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
