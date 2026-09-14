from __future__ import annotations

import asyncio
import html
import json
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_SCHEMA_TTL = 60


def crear_router_admin_tablas(
    supabase_admin,
    supabase_url: str,
    service_key: str,
    timezone: str = "America/Mexico_City",
):
    """Administrador CRUD genérico para las tablas expuestas por PostgREST/Supabase."""

    router = APIRouter()
    rest_url = supabase_url.rstrip("/") + "/rest/v1/"

    def exigir_admin(request: Request) -> None:
        if not request.session.get("admin"):
            raise HTTPException(status_code=401, detail="Sesión administrativa requerida")

    def nombre_seguro(value: str, tipo: str = "identificador") -> str:
        value = str(value or "").strip()
        if not _NAME_RE.fullmatch(value):
            raise HTTPException(status_code=400, detail=f"{tipo} inválido")
        return value

    async def cargar_openapi(force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and _SCHEMA_CACHE.get("data") and now - _SCHEMA_CACHE["ts"] < _SCHEMA_TTL:
            return _SCHEMA_CACHE["data"]

        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/openapi+json",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(rest_url, headers=headers) as resp:
                if resp.status >= 400:
                    detail = await resp.text()
                    raise HTTPException(status_code=502, detail=f"No se pudo leer el esquema de Supabase: {detail[:300]}")
                data = await resp.json(content_type=None)

        _SCHEMA_CACHE["ts"] = now
        _SCHEMA_CACHE["data"] = data
        return data

    def extraer_catalogo(openapi: dict[str, Any]) -> dict[str, dict[str, Any]]:
        definitions = openapi.get("definitions") or openapi.get("components", {}).get("schemas") or {}
        paths = openapi.get("paths") or {}
        tablas: dict[str, dict[str, Any]] = {}

        for path, ops in paths.items():
            if not isinstance(path, str) or not path.startswith("/") or path.count("/") != 1:
                continue
            table = path[1:]
            if not table or not _NAME_RE.fullmatch(table):
                continue
            if not isinstance(ops, dict) or "get" not in ops:
                continue

            definition = definitions.get(table, {}) if isinstance(definitions, dict) else {}
            props = definition.get("properties", {}) if isinstance(definition, dict) else {}
            required = set(definition.get("required", []) or []) if isinstance(definition, dict) else set()

            columnas = []
            pks = []
            for col, meta in props.items():
                if not _NAME_RE.fullmatch(str(col)):
                    continue
                meta = meta or {}
                desc = str(meta.get("description") or "")
                low = desc.lower()
                is_pk = any(token in low for token in ("primary key", "<pk/>", "<pk />"))
                if is_pk:
                    pks.append(col)
                columnas.append({
                    "name": col,
                    "type": meta.get("type", "string"),
                    "format": meta.get("format"),
                    "description": desc,
                    "required": col in required,
                    "primary": is_pk,
                    "readOnly": bool(meta.get("readOnly", False)),
                    "default": meta.get("default"),
                })

            names = [c["name"] for c in columnas]
            if not pks:
                for candidate in ("id", "uuid", "folio", f"{table}_id"):
                    if candidate in names:
                        pks = [candidate]
                        break

            tablas[table] = {
                "name": table,
                "columns": columnas,
                "primary_keys": pks,
                "expiration_column": next((x for x in ("fecha_vencimiento", "vencimiento", "expires_at", "fecha_ven") if x in names), None),
                "has_expiration": any(x in names for x in ("fecha_vencimiento", "vencimiento", "expires_at", "fecha_ven")),
                "has_entity": "entidad" in names,
                "has_status": "estado" in names,
            }

        return dict(sorted(tablas.items(), key=lambda kv: kv[0].lower()))

    async def catalogo(force: bool = False) -> dict[str, dict[str, Any]]:
        return extraer_catalogo(await cargar_openapi(force=force))

    async def tabla_meta(table: str) -> dict[str, Any]:
        table = nombre_seguro(table, "tabla")
        cats = await catalogo()
        if table not in cats:
            # El esquema puede haber cambiado hace segundos.
            cats = await catalogo(force=True)
        if table not in cats:
            raise HTTPException(status_code=404, detail="La tabla no existe o no está expuesta por Supabase REST")
        return cats[table]

    def valor_scalar(v: Any) -> bool:
        return v is None or isinstance(v, (str, int, float, bool))

    def clave_fila(row: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any] | None:
        pks = meta.get("primary_keys") or []
        if pks and all(k in row for k in pks):
            return {k: row.get(k) for k in pks}

        # Respaldo conservador para tablas sin PK identificable.
        for candidate in ("id", "uuid", "folio"):
            if candidate in row and valor_scalar(row.get(candidate)):
                return {candidate: row.get(candidate)}
        return None

    def query_por_clave(query, key: dict[str, Any]):
        if not key:
            raise HTTPException(status_code=400, detail="No se pudo identificar de forma segura el registro")
        for col, value in key.items():
            col = nombre_seguro(col, "columna")
            if value is None:
                query = query.is_(col, "null")
            else:
                query = query.eq(col, value)
        return query

    def normalizar_row(row: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        allowed = {c["name"] for c in meta.get("columns", [])}
        return {k: v for k, v in row.items() if k in allowed}

    @router.get("/admin/tablas", response_class=HTMLResponse)
    async def admin_tablas(request: Request):
        if not request.session.get("admin"):
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/login", status_code=302)
        username = html.escape(str(request.session.get("username", "Admin")))
        return HTMLResponse(_html_panel(username))

    @router.get("/admin/api/db/schema")
    async def api_schema(request: Request, refresh: int = 0):
        exigir_admin(request)
        cats = await catalogo(force=bool(refresh))
        return {"ok": True, "tables": list(cats.values()), "count": len(cats)}

    @router.get("/admin/api/db/rows")
    async def api_rows(
        request: Request,
        table: str,
        page: int = 1,
        limit: int = 50,
        q: str = "",
        vigencia: str = "todos",
        entidad: str = "",
        estado: str = "",
        sort: str = "",
        order: str = "desc",
    ):
        exigir_admin(request)
        meta = await tabla_meta(table)
        table = meta["name"]
        page = max(1, int(page))
        limit = min(200, max(10, int(limit)))
        start = (page - 1) * limit
        end = start + limit - 1
        col_names = [c["name"] for c in meta["columns"]]

        def run_query():
            query = supabase_admin.table(table).select("*", count="exact")

            if q.strip():
                # PostgREST OR solamente sobre columnas textuales para no provocar errores de tipo.
                text_cols = [
                    c["name"] for c in meta["columns"]
                    if c.get("type") == "string" and c["name"] not in {"created_at", "updated_at"}
                ][:18]
                clean = re.sub(r"[(),]", " ", q.strip())[:120]
                if text_cols and clean:
                    or_filter = ",".join(f"{c}.ilike.%{clean}%" for c in text_cols)
                    query = query.or_(or_filter)

            if meta.get("has_expiration") and vigencia in {"vigentes", "vencidos"}:
                hoy = datetime.now(ZoneInfo(timezone)).date().isoformat()
                exp_col = meta.get("expiration_column")
                if vigencia == "vigentes":
                    query = query.gte(exp_col, hoy)
                else:
                    query = query.lt(exp_col, hoy)

            if meta.get("has_entity") and entidad.strip():
                query = query.eq("entidad", entidad.strip())
            if meta.get("has_status") and estado.strip():
                query = query.eq("estado", estado.strip())

            if sort and sort in col_names:
                query = query.order(sort, desc=(order.lower() != "asc"))
            elif "created_at" in col_names:
                query = query.order("created_at", desc=True)
            elif meta.get("primary_keys"):
                query = query.order(meta["primary_keys"][0], desc=True)

            return query.range(start, end).execute()

        try:
            resp = await asyncio.to_thread(run_query)
            rows = resp.data or []
            for row in rows:
                row["__admin_key"] = clave_fila(row, meta)
            total = getattr(resp, "count", None)
            if total is None:
                total = len(rows) if len(rows) < limit else start + len(rows) + 1
            return {
                "ok": True,
                "table": table,
                "rows": rows,
                "page": page,
                "limit": limit,
                "total": total,
                "pages": max(1, (int(total) + limit - 1) // limit) if isinstance(total, int) else 1,
                "meta": meta,
            }
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/admin/api/db/create")
    async def api_create(request: Request):
        exigir_admin(request)
        body = await request.json()
        table = nombre_seguro(body.get("table"), "tabla")
        meta = await tabla_meta(table)
        row = body.get("row")
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail="row debe ser un objeto")
        row = normalizar_row(row, meta)
        if not row:
            raise HTTPException(status_code=400, detail="No hay campos válidos para insertar")

        try:
            resp = await asyncio.to_thread(lambda: supabase_admin.table(table).insert(row).execute())
            return {"ok": True, "data": resp.data}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.patch("/admin/api/db/update")
    async def api_update(request: Request):
        exigir_admin(request)
        body = await request.json()
        table = nombre_seguro(body.get("table"), "tabla")
        meta = await tabla_meta(table)
        key = body.get("key")
        changes = body.get("changes")
        if not isinstance(key, dict) or not isinstance(changes, dict):
            raise HTTPException(status_code=400, detail="key y changes deben ser objetos")
        allowed = {c["name"] for c in meta["columns"]}
        changes = {k: v for k, v in changes.items() if k in allowed}
        if not changes:
            raise HTTPException(status_code=400, detail="No hay cambios válidos")

        def run():
            query = supabase_admin.table(table).update(changes)
            return query_por_clave(query, key).execute()

        try:
            resp = await asyncio.to_thread(run)
            return {"ok": True, "data": resp.data}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/admin/api/db/delete")
    async def api_delete(request: Request):
        exigir_admin(request)
        body = await request.json()
        table = nombre_seguro(body.get("table"), "tabla")
        await tabla_meta(table)
        keys = body.get("keys")
        if not isinstance(keys, list) or not keys:
            raise HTTPException(status_code=400, detail="Debes enviar al menos una clave")
        if len(keys) > 1000:
            raise HTTPException(status_code=400, detail="Máximo 1000 registros por operación")

        # Una sola llamada SQL cuando todos comparten una PK simple; si no, fallback seguro.
        simple_col = None
        if all(isinstance(k, dict) and len(k) == 1 for k in keys):
            cols = {next(iter(k.keys())) for k in keys}
            if len(cols) == 1:
                simple_col = nombre_seguro(next(iter(cols)), "columna")

        try:
            if simple_col:
                values = [k[simple_col] for k in keys]
                resp = await asyncio.to_thread(
                    lambda: supabase_admin.table(table).delete().in_(simple_col, values).execute()
                )
                return {"ok": True, "requested": len(keys), "deleted": len(resp.data or [])}

            deleted = 0
            errors = []
            for key in keys:
                try:
                    def run_one(k=key):
                        q = supabase_admin.table(table).delete()
                        return query_por_clave(q, k).execute()
                    resp = await asyncio.to_thread(run_one)
                    deleted += len(resp.data or [])
                except Exception as exc:
                    errors.append(str(exc))
            return {"ok": not errors, "requested": len(keys), "deleted": deleted, "errors": errors[:10]}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return router


def _html_panel(username: str) -> str:
    # HTML/CSS/JS autocontenido para no agrandar el main.py ni requerir plantillas externas.
    return r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Administrador Supabase</title>
<style>
:root{--vino:#5f1b2d;--vino2:#48101e;--dorado:#c09761;--azul:#001B4C;--bg:#f4f4f4;--line:#e6e6e6;--ok:#198754;--bad:#b72f3c;--text:#4d4d4d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:Arial,Helvetica,sans-serif;color:var(--text)}
header{background:#fff;padding:18px 28px;border-bottom:6px solid var(--vino);display:flex;align-items:center;justify-content:space-between;gap:20px;position:sticky;top:0;z-index:20}
header h1{font-size:22px;color:var(--vino);font-weight:500;margin:0}header .user{font-size:13px;color:#777}header a{color:var(--vino);text-decoration:none;font-weight:700}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:calc(100vh - 72px)}
aside{background:#fff;border-right:1px solid var(--line);padding:18px 12px;overflow:auto}.side-title{font-size:12px;color:#999;text-transform:uppercase;letter-spacing:.8px;padding:6px 10px 12px}
#tableSearch{width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:9px;margin-bottom:12px}.table-btn{display:block;width:100%;border:0;background:transparent;text-align:left;padding:10px 12px;border-radius:9px;cursor:pointer;color:#555;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.table-btn:hover{background:#f7f2ed}.table-btn.active{background:#f1e4d6;color:var(--vino);font-weight:700}
main{padding:22px;min-width:0}.card{background:#fff;border-radius:16px;box-shadow:0 4px 18px rgba(0,0,0,.07);overflow:hidden}.top{padding:20px;border-bottom:1px solid var(--line)}
.heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.heading h2{margin:0;color:var(--vino);font-size:24px}.small{font-size:12px;color:#888}
.toolbar{display:grid;grid-template-columns:minmax(220px,2fr) repeat(3,minmax(130px,1fr)) auto auto;gap:10px;margin-top:16px}.toolbar input,.toolbar select{padding:10px 11px;border:1px solid #ddd;border-radius:9px;min-width:0}.btn{border:0;border-radius:9px;padding:10px 14px;cursor:pointer;font-weight:700}.primary{background:var(--vino);color:#fff}.gold{background:var(--dorado);color:#fff}.danger{background:var(--bad);color:#fff}.light{background:#eee;color:#555}.btn:disabled{opacity:.45;cursor:not-allowed}
.filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}.chip{border:1px solid #ddd;background:#fff;padding:7px 11px;border-radius:18px;cursor:pointer}.chip.active{background:var(--vino);border-color:var(--vino);color:#fff}
.bulk{display:none;align-items:center;gap:10px;padding:10px 14px;background:#fff4e8;border-bottom:1px solid #f0d5b8}.bulk.show{display:flex}.table-wrap{overflow:auto;max-height:65vh}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th,td{padding:10px 12px;border-bottom:1px solid #eee;text-align:left;max-width:330px;overflow:hidden;text-overflow:ellipsis}th{position:sticky;top:0;background:#fafafa;z-index:3;color:#666;cursor:pointer}tr:hover td{background:#fffdfb}.pk{font-weight:700;color:var(--vino)}.badge{display:inline-block;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:700}.vig{background:#dff4e7;color:#176a3a}.ven{background:#fde3e5;color:#9d2630}.null{color:#aaa;font-style:italic}.editable{cursor:cell}.editable:hover{outline:1px dashed var(--dorado);outline-offset:-3px}.cell-input{min-width:140px;padding:7px;border:1px solid var(--dorado);border-radius:6px}
.pager{padding:14px 18px;display:flex;justify-content:space-between;align-items:center;gap:10px;border-top:1px solid var(--line)}.pager .pages{display:flex;gap:7px;align-items:center}.empty{padding:60px 20px;text-align:center;color:#999}.error{margin:15px 0;padding:12px;background:#fde7e9;color:#8c2630;border-radius:9px;display:none}.loading{opacity:.6;pointer-events:none}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;padding:20px;z-index:100}.modal-bg.show{display:flex}.modal{width:min(850px,100%);max-height:90vh;overflow:auto;background:#fff;border-radius:16px;padding:22px}.modal h3{margin:0 0 16px;color:var(--vino)}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field label{display:block;font-size:12px;font-weight:700;color:#666;margin-bottom:5px}.field input,.field textarea,.field select{width:100%;padding:9px;border:1px solid #ddd;border-radius:8px}.field textarea{min-height:78px}.modal-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}
@media(max-width:900px){.layout{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line);max-height:220px}.toolbar{grid-template-columns:1fr 1fr}.form-grid{grid-template-columns:1fr}}@media(max-width:560px){main{padding:10px}.toolbar{grid-template-columns:1fr}.heading h2{font-size:20px}header{padding:14px}.layout{min-height:auto}}
</style>
</head>
<body>
<header><div><h1>Administrador de Base de Datos</h1><div class="small">Supabase · CRUD global</div></div><div class="user">Usuario: <b>__USERNAME__</b> · <a href="/admin">Panel</a> · <a href="/logout">Salir</a></div></header>
<div class="layout">
<aside><div class="side-title">Tablas disponibles</div><input id="tableSearch" placeholder="Buscar tabla..."><div id="tables"></div></aside>
<main>
<div id="error" class="error"></div>
<section class="card" id="card">
<div class="top">
<div class="heading"><div><h2 id="title">Selecciona una tabla</h2><div class="small" id="metaText">Cargando esquema...</div></div><div><button class="btn light" id="refreshBtn">↻ Actualizar</button> <button class="btn gold" id="addBtn" disabled>＋ Agregar registro</button></div></div>
<div class="toolbar"><input id="q" placeholder="Buscar en columnas de texto..."><input id="entity" placeholder="Entidad / estado"><input id="status" placeholder="Estado del registro"><select id="limit"><option>25</option><option selected>50</option><option>100</option><option>200</option></select><button class="btn primary" id="searchBtn">Buscar</button><button class="btn light" id="clearBtn">Limpiar</button></div>
<div class="filters" id="vigFilters"><button class="chip active" data-v="todos">Todos</button><button class="chip" data-v="vigentes">Vigentes</button><button class="chip" data-v="vencidos">Vencidos</button></div>
</div>
<div class="bulk" id="bulk"><b id="selectedText">0 seleccionados</b><button class="btn danger" id="deleteBtn">Eliminar seleccionados</button><button class="btn light" id="unselectBtn">Quitar selección</button></div>
<div class="table-wrap" id="tableWrap"><div class="empty">Selecciona una tabla de la izquierda.</div></div>
<div class="pager"><span id="countText">—</span><div class="pages"><button class="btn light" id="prevBtn">←</button><span id="pageText">Página 1</span><button class="btn light" id="nextBtn">→</button></div></div>
</section>
</main></div>
<div class="modal-bg" id="modalBg"><div class="modal"><h3>Agregar registro</h3><div class="form-grid" id="formGrid"></div><div class="modal-actions"><button class="btn light" id="cancelModal">Cancelar</button><button class="btn primary" id="saveNew">Guardar</button></div></div></div>
<script>
const S={tables:[],table:null,meta:null,rows:[],page:1,pages:1,total:0,vigencia:'todos',sort:'',order:'desc',selected:new Map()};
const $=s=>document.querySelector(s); const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function err(m){const e=$('#error');e.textContent=m;e.style.display='block';setTimeout(()=>e.style.display='none',8000)}
async function api(url,opt={}){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch{} if(!r.ok||d.ok===false)throw new Error(d.detail||d.error||('HTTP '+r.status));return d}
async function loadSchema(refresh=0){try{const d=await api('/admin/api/db/schema?refresh='+refresh);S.tables=d.tables||[];renderTables();$('#metaText').textContent=S.tables.length+' tablas expuestas por Supabase REST';if(!S.table&&S.tables.length)selectTable(S.tables[0].name)}catch(e){err(e.message)}}
function renderTables(){const f=$('#tableSearch').value.toLowerCase();$('#tables').innerHTML=S.tables.filter(t=>t.name.toLowerCase().includes(f)).map(t=>`<button class="table-btn ${S.table===t.name?'active':''}" data-t="${esc(t.name)}">${esc(t.name)}</button>`).join('');document.querySelectorAll('.table-btn').forEach(b=>b.onclick=()=>selectTable(b.dataset.t))}
function selectTable(name){S.table=name;S.meta=S.tables.find(t=>t.name===name);S.page=1;S.sort='';S.selected.clear();$('#title').textContent=name;$('#addBtn').disabled=false;$('#vigFilters').style.display=S.meta?.has_expiration?'flex':'none';renderTables();loadRows()}
function qp(){const p=new URLSearchParams({table:S.table,page:S.page,limit:$('#limit').value,q:$('#q').value,vigencia:S.vigencia,entidad:$('#entity').value,estado:$('#status').value,sort:S.sort,order:S.order});return p.toString()}
async function loadRows(){if(!S.table)return;$('#card').classList.add('loading');try{const d=await api('/admin/api/db/rows?'+qp());S.rows=d.rows||[];S.meta=d.meta;S.total=d.total;S.pages=d.pages||1;S.page=d.page;S.selected.clear();renderGrid();updateBulk()}catch(e){err(e.message)}finally{$('#card').classList.remove('loading')}}
function display(v,col,row){if(v===null||v===undefined)return '<span class="null">NULL</span>'; if(typeof v==='object')return esc(JSON.stringify(v)); if(col===S.meta?.expiration_column){const today=new Date().toISOString().slice(0,10);const x=String(v).slice(0,10);return `<span class="badge ${x>=today?'vig':'ven'}">${esc(v)}</span>`}return esc(v)}
function renderGrid(){const cols=S.meta?.columns||[];if(!S.rows.length){$('#tableWrap').innerHTML='<div class="empty">No hay registros con estos filtros.</div>'}else{let h='<table><thead><tr><th><input type="checkbox" id="allRows"></th>'+cols.map(c=>`<th data-sort="${esc(c.name)}">${esc(c.name)}${c.primary?' 🔑':''}</th>`).join('')+'</tr></thead><tbody>';S.rows.forEach((r,i)=>{const key=r.__admin_key;h+=`<tr><td><input class="rowChk" type="checkbox" data-i="${i}" ${key?'':'disabled title="Sin clave identificable"'}></td>`;cols.forEach(c=>{const cls=(S.meta.primary_keys||[]).includes(c.name)?'pk':'';const editable=!!key;h+=`<td class="${cls} ${editable?'editable':''}" data-i="${i}" data-c="${esc(c.name)}" title="${editable?'Doble clic para editar':''}">${display(r[c.name],c.name,r)}</td>`});h+='</tr>'});h+='</tbody></table>';$('#tableWrap').innerHTML=h;$('#allRows').onchange=e=>{document.querySelectorAll('.rowChk:not(:disabled)').forEach(ch=>{ch.checked=e.target.checked;toggleRow(+ch.dataset.i,e.target.checked)})};document.querySelectorAll('.rowChk').forEach(ch=>ch.onchange=()=>toggleRow(+ch.dataset.i,ch.checked));document.querySelectorAll('th[data-sort]').forEach(th=>th.onclick=()=>{const c=th.dataset.sort;if(S.sort===c)S.order=S.order==='asc'?'desc':'asc';else{S.sort=c;S.order='asc'}loadRows()});document.querySelectorAll('td.editable').forEach(td=>td.ondblclick=()=>editCell(td))}
$('#countText').textContent=`${S.total} registros`;$('#pageText').textContent=`Página ${S.page} de ${S.pages}`;$('#prevBtn').disabled=S.page<=1;$('#nextBtn').disabled=S.page>=S.pages;$('#metaText').textContent=`${cols.length} columnas · PK: ${(S.meta.primary_keys||[]).join(', ')||'no detectada'}`}
function toggleRow(i,on){const r=S.rows[i],k=r.__admin_key;if(!k)return;const id=JSON.stringify(k);if(on)S.selected.set(id,k);else S.selected.delete(id);updateBulk()}
function updateBulk(){const n=S.selected.size;$('#bulk').classList.toggle('show',n>0);$('#selectedText').textContent=n+' seleccionados'}
function inputValue(raw,type){if(raw===null||raw===undefined)return '';if(typeof raw==='object')return JSON.stringify(raw);return String(raw)}
async function editCell(td){if(td.querySelector('input'))return;const i=+td.dataset.i,c=td.dataset.c,r=S.rows[i],old=r[c];const inp=document.createElement('input');inp.className='cell-input';inp.value=inputValue(old);td.innerHTML='';td.appendChild(inp);inp.focus();inp.select();const save=async()=>{let v=inp.value;const cm=S.meta.columns.find(x=>x.name===c)||{};try{if(v==='NULL')v=null;else if(cm.type==='integer')v=parseInt(v,10);else if(cm.type==='number')v=parseFloat(v);else if(cm.type==='boolean')v=/^(true|1|si|sí)$/i.test(v);else if(cm.type==='object'||cm.type==='array')v=JSON.parse(v);await api('/admin/api/db/update',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({table:S.table,key:r.__admin_key,changes:{[c]:v}})});r[c]=v;if((S.meta.primary_keys||[]).includes(c)){loadRows();return}td.innerHTML=display(v,c,r)}catch(e){err(e.message);td.innerHTML=display(old,c,r)}};inp.onkeydown=e=>{if(e.key==='Enter')save();if(e.key==='Escape')td.innerHTML=display(old,c,r)};inp.onblur=save}
function buildModal(){const cols=S.meta?.columns||[];$('#formGrid').innerHTML=cols.map(c=>{const skip=c.readOnly||c.default!==undefined||c.primary;const type=c.type==='boolean'?'checkbox':(c.type==='integer'||c.type==='number'?'number':(c.format==='date'?'date':(c.format==='date-time'?'datetime-local':'text')));return `<div class="field" ${skip?'data-optional="1"':''}><label>${esc(c.name)}${c.required&&!skip?' *':''}${skip?' (opcional/auto)':''}</label>${c.type==='object'||c.type==='array'?`<textarea data-col="${esc(c.name)}" data-type="${esc(c.type)}" placeholder="JSON"></textarea>`:`<input data-col="${esc(c.name)}" data-type="${esc(c.type)}" type="${type}" ${type==='checkbox'?'data-check="1"':''}>`}</div>`}).join('');$('#modalBg').classList.add('show')}
async function saveNew(){const row={};document.querySelectorAll('#formGrid [data-col]').forEach(el=>{let v=el.dataset.check?el.checked:el.value;if(v===''&&!el.dataset.check)return;const t=el.dataset.type;if(t==='integer')v=parseInt(v,10);else if(t==='number')v=parseFloat(v);else if(t==='boolean')v=!!v;else if(t==='object'||t==='array')v=JSON.parse(v);row[el.dataset.col]=v});try{await api('/admin/api/db/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table:S.table,row})});$('#modalBg').classList.remove('show');loadRows()}catch(e){err(e.message)}}
$('#deleteBtn').onclick=async()=>{const n=S.selected.size;if(!n)return;if(!confirm(`¿Eliminar definitivamente ${n} registro(s) de ${S.table}?\n\nEsta acción no se puede deshacer.`))return;if(n>=20&&!confirm(`Confirmación final: vas a borrar ${n} registros de ${S.table}. ¿Continuar?`))return;try{const d=await api('/admin/api/db/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table:S.table,keys:[...S.selected.values()]})});if(d.errors?.length)err('Se eliminaron '+d.deleted+'; algunos fallaron: '+d.errors[0]);loadRows()}catch(e){err(e.message)}};
$('#unselectBtn').onclick=()=>{S.selected.clear();document.querySelectorAll('.rowChk').forEach(x=>x.checked=false);updateBulk()};$('#refreshBtn').onclick=()=>{loadSchema(1);if(S.table)loadRows()};$('#addBtn').onclick=buildModal;$('#cancelModal').onclick=()=>$('#modalBg').classList.remove('show');$('#saveNew').onclick=saveNew;$('#searchBtn').onclick=()=>{S.page=1;loadRows()};$('#clearBtn').onclick=()=>{$('#q').value='';$('#entity').value='';$('#status').value='';S.vigencia='todos';document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('active',x.dataset.v==='todos'));S.page=1;loadRows()};$('#q').onkeydown=e=>{if(e.key==='Enter'){S.page=1;loadRows()}};$('#prevBtn').onclick=()=>{if(S.page>1){S.page--;loadRows()}};$('#nextBtn').onclick=()=>{if(S.page<S.pages){S.page++;loadRows()}};$('#limit').onchange=()=>{S.page=1;loadRows()};$('#tableSearch').oninput=renderTables;document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{S.vigencia=c.dataset.v;document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('active',x===c));S.page=1;loadRows()});
loadSchema();
</script></body></html>'''.replace("__USERNAME__", username)
