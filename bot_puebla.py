import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)


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


class BotPueblaRuntime:
    def __init__(self, *, bot, supabase, precio, entidad, timezone):
        self.bot = bot
        self.supabase = supabase
        self.precio = precio
        self.entidad = entidad
        self.timezone = timezone
        self.timers_activos = {}
        self.user_folios = {}

    def _ahora(self):
        return datetime.now(ZoneInfo(self.timezone))

    def _tiempo_restante(self, folio: str) -> str:
        item = self.timers_activos.get(folio)
        if not item:
            return "sin timer"
        fin = item["start_time"] + timedelta(hours=36)
        restante = fin - self._ahora()
        segundos = max(0, int(restante.total_seconds()))
        horas, rem = divmod(segundos, 3600)
        minutos = rem // 60
        return f"{horas} h {minutos} min"

    async def eliminar_folio_automatico(self, folio: str):
        try:
            item = self.timers_activos.get(folio)
            uid = item["user_id"] if item else None

            self.supabase.table("folios_registrados").delete().eq("folio", folio).execute()
            self.supabase.table("borradores_registros").delete().eq("folio", folio).execute()

            if uid:
                await self.bot.send_message(
                    uid,
                    "⏰ TIEMPO AGOTADO - PUEBLA\n\n"
                    f"El folio {folio} fue eliminado automáticamente al cumplir 36 horas.\n\n"
                    "Use /banamex para generar otro.",
                )

            self.limpiar_timer_folio(folio)
        except Exception as e:
            print(f"[BOT PUEBLA] Error eliminando folio {folio}: {e}")

    async def enviar_recordatorio(self, folio: str, minutos_restantes: int):
        try:
            if folio not in self.timers_activos:
                return
            uid = self.timers_activos[folio]["user_id"]
            await self.bot.send_message(
                uid,
                "⚡ RECORDATORIO - PUEBLA\n\n"
                f"Folio: {folio}\n"
                f"Tiempo restante: {minutos_restantes} min\n"
                f"Monto: ${self.precio}\n\n"
                "Si desea conservarlo, use el botón DETENER TIMER desde /banamex."
            )
        except Exception as e:
            print(f"[BOT PUEBLA] Error enviando recordatorio {folio}: {e}")

    async def iniciar_timer_36h(self, user_id: int, folio: str):
        async def timer_task():
            await asyncio.sleep(34.5 * 3600)
            if folio not in self.timers_activos:
                return
            await self.enviar_recordatorio(folio, 90)

            await asyncio.sleep(30 * 60)
            if folio not in self.timers_activos:
                return
            await self.enviar_recordatorio(folio, 60)

            await asyncio.sleep(30 * 60)
            if folio not in self.timers_activos:
                return
            await self.enviar_recordatorio(folio, 30)

            await asyncio.sleep(20 * 60)
            if folio not in self.timers_activos:
                return
            await self.enviar_recordatorio(folio, 10)

            await asyncio.sleep(10 * 60)
            if folio in self.timers_activos:
                await self.eliminar_folio_automatico(folio)

        task = asyncio.create_task(timer_task())
        self.timers_activos[folio] = {
            "task": task,
            "user_id": user_id,
            "start_time": self._ahora(),
        }
        self.user_folios.setdefault(user_id, []).append(folio)

    def cancelar_timer_folio(self, folio: str) -> bool:
        item = self.timers_activos.get(folio)
        if not item:
            return False

        item["task"].cancel()
        uid = item["user_id"]
        del self.timers_activos[folio]

        if uid in self.user_folios and folio in self.user_folios[uid]:
            self.user_folios[uid].remove(folio)
            if not self.user_folios[uid]:
                del self.user_folios[uid]
        return True

    def limpiar_timer_folio(self, folio: str):
        item = self.timers_activos.get(folio)
        if not item:
            return

        uid = item["user_id"]
        del self.timers_activos[folio]

        if uid in self.user_folios and folio in self.user_folios[uid]:
            self.user_folios[uid].remove(folio)
            if not self.user_folios[uid]:
                del self.user_folios[uid]

    async def enviar_lista_folios(self, chat_id: int, user_id: int):
        folios = [
            folio
            for folio in self.user_folios.get(user_id, [])
            if folio in self.timers_activos
        ]

        if not folios:
            await self.bot.send_message(
                chat_id,
                "📋 FOLIOS CON TIMER ACTIVO\n\nNo tienes folios con cuenta regresiva activa."
            )
            return

        lineas = ["📋 FOLIOS CON TIMER ACTIVO", ""]
        botones = []

        for i, folio in enumerate(folios, start=1):
            restante = self._tiempo_restante(folio)
            lineas.append(f"{i}. {folio} — ⏳ {restante}")
            botones.append([
                InlineKeyboardButton(
                    text=f"🛑 Detener timer · {folio}",
                    callback_data=f"stop_timer:{folio}",
                )
            ])

        lineas.extend([
            "",
            "Si NO detienes un timer, el folio seguirá su cuenta de 36 horas y se eliminará automáticamente al vencer."
        ])

        await self.bot.send_message(
            chat_id,
            "\n".join(lineas),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=botones),
        )


def crear_bot_puebla(
    *,
    bot_token: str,
    supabase,
    precio: int,
    entidad: str,
    timezone: str,
    generar_folio_async,
    generar_pdf,
):
    bot = Bot(token=bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    runtime = BotPueblaRuntime(
        bot=bot,
        supabase=supabase,
        precio=precio,
        entidad=entidad,
        timezone=timezone,
    )

    @dp.message(Command("banamex"))
    async def banamex_cmd(message: types.Message, state: FSMContext):
        await state.clear()

        await runtime.enviar_lista_folios(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
        )

        await message.answer(
            "🚗 NUEVO PERMISO - PUEBLA\n\n"
            f"💰 Costo: ${precio} MXN\n"
            "⏰ Plazo: 36 horas\n\n"
            "Paso 1/12: MARCA del vehículo:"
        )
        await state.set_state(PermisoForm.marca)

    @dp.message(Command("folios"))
    async def folios_cmd(message: types.Message, state: FSMContext):
        await runtime.enviar_lista_folios(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
        )

    @dp.callback_query(lambda c: c.data and c.data.startswith("stop_timer:"))
    async def stop_timer_callback(callback: CallbackQuery):
        folio = callback.data.split(":", 1)[1]
        item = runtime.timers_activos.get(folio)

        if not item:
            await callback.answer("Ese timer ya no está activo.", show_alert=True)
            return

        if item["user_id"] != callback.from_user.id:
            await callback.answer("Ese folio no pertenece a tu sesión.", show_alert=True)
            return

        runtime.cancelar_timer_folio(folio)
        await callback.answer("Timer detenido", show_alert=True)
        await callback.message.answer(
            "🛑 TIMER DETENIDO\n\n"
            f"Folio: {folio}\n"
            "Este folio ya NO se eliminará automáticamente a las 36 horas."
        )

        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    @dp.message(PermisoForm.marca)
    async def get_marca(message: types.Message, state: FSMContext):
        await state.update_data(marca=(message.text or "").upper().strip())
        await message.answer("Paso 2/12: LÍNEA/MODELO:")
        await state.set_state(PermisoForm.linea)

    @dp.message(PermisoForm.linea)
    async def get_linea(message: types.Message, state: FSMContext):
        await state.update_data(linea=(message.text or "").upper().strip())
        await message.answer("Paso 3/12: AÑO:")
        await state.set_state(PermisoForm.anio)

    @dp.message(PermisoForm.anio)
    async def get_anio(message: types.Message, state: FSMContext):
        await state.update_data(anio=(message.text or "").strip())
        await message.answer("Paso 4/12: NÚMERO DE SERIE:")
        await state.set_state(PermisoForm.serie)

    @dp.message(PermisoForm.serie)
    async def get_serie(message: types.Message, state: FSMContext):
        await state.update_data(serie=(message.text or "").upper().strip())
        await message.answer("Paso 5/12: NÚMERO DE MOTOR:")
        await state.set_state(PermisoForm.motor)

    @dp.message(PermisoForm.motor)
    async def get_motor(message: types.Message, state: FSMContext):
        await state.update_data(motor=(message.text or "").upper().strip())
        await message.answer("Paso 6/12: COLOR:")
        await state.set_state(PermisoForm.color)

    @dp.message(PermisoForm.color)
    async def get_color(message: types.Message, state: FSMContext):
        await state.update_data(color=(message.text or "").upper().strip())
        await message.answer("Paso 7/12: NOMBRE COMPLETO del titular:")
        await state.set_state(PermisoForm.nombre)

    @dp.message(PermisoForm.nombre)
    async def get_nombre(message: types.Message, state: FSMContext):
        await state.update_data(nombre=(message.text or "").upper().strip())
        await message.answer("Paso 8/12: COMBUSTIBLE (ej: GASOLINA):")
        await state.set_state(PermisoForm.combustible)

    @dp.message(PermisoForm.combustible)
    async def get_combustible(message: types.Message, state: FSMContext):
        await state.update_data(combustible=(message.text or "").upper().strip())
        await message.answer("Paso 9/12: CILINDROS CC O PBV:")
        await state.set_state(PermisoForm.cilindros)

    @dp.message(PermisoForm.cilindros)
    async def get_cilindros(message: types.Message, state: FSMContext):
        await state.update_data(cilindros=(message.text or "").upper().strip())
        await message.answer("Paso 10/12: VIGENCIA\n1 para 15 días\n2 para 30 días:")
        await state.set_state(PermisoForm.vigencia)

    @dp.message(PermisoForm.vigencia)
    async def get_vigencia(message: types.Message, state: FSMContext):
        vigencia = (message.text or "").strip()
        if vigencia not in ["1", "2"]:
            await message.answer("❌ Responde solo 1 o 2")
            return

        await state.update_data(vigencia=vigencia)
        await message.answer(
            "Paso 11/12: TIPO DE AUTO\n"
            "(Automóvil, Motocicleta, Trailer, Carroza, Carreta):"
        )
        await state.set_state(PermisoForm.tipo_auto)

    @dp.message(PermisoForm.tipo_auto)
    async def get_tipo_auto(message: types.Message, state: FSMContext):
        await state.update_data(tipo_auto=(message.text or "").upper().strip())
        await message.answer("Paso 12/12: PRESIDENCIA:")
        await state.set_state(PermisoForm.presidencia)

    @dp.message(PermisoForm.presidencia)
    async def get_presidencia(message: types.Message, state: FSMContext):
        datos = await state.get_data()
        datos["presidencia"] = (message.text or "").upper().strip()
        datos["folio"] = await generar_folio_async()

        hoy = datetime.now(ZoneInfo(timezone))
        vigencia_dias = 15 if datos["vigencia"] == "1" else 30
        ven = hoy + timedelta(days=vigencia_dias)

        datos["fecha_exp"] = f"{hoy.day:02d}-{hoy.month:02d}-{hoy.year}"
        datos["fecha_ven"] = f"{ven.day:02d}-{ven.month:02d}-{ven.year}"

        await state.clear()
        await message.answer(f"🔄 Generando permiso {datos['folio']}...")

        try:
            pdf_path = await asyncio.to_thread(generar_pdf, datos)

            await bot.send_document(
                message.chat.id,
                FSInputFile(pdf_path),
                caption=(
                    "📄 PERMISO - PUEBLA\n"
                    f"Folio: {datos['folio']}\n\n"
                    "⏰ TIMER ACTIVO (36 horas)"
                ),
            )

            supabase.table("folios_registrados").insert({
                "folio": datos["folio"],
                "marca": datos["marca"],
                "linea": datos["linea"],
                "anio": datos["anio"],
                "numero_serie": datos["serie"],
                "numero_motor": datos["motor"],
                "color": datos["color"],
                "contribuyente": datos["nombre"],
                "fecha_expedicion": hoy.date().isoformat(),
                "fecha_vencimiento": ven.date().isoformat(),
                "entidad": entidad,
                "estado": "PENDIENTE",
                "user_id": message.from_user.id,
                "username": message.from_user.username or "Sin username",
            }).execute()

            await runtime.iniciar_timer_36h(message.from_user.id, datos["folio"])

            await message.answer(
                "💰 INSTRUCCIONES DE PAGO\n\n"
                f"📄 Folio: {datos['folio']}\n"
                f"💵 Monto: ${precio} MXN\n"
                "⏰ Tiempo límite: 36 horas\n\n"
                "📸 Envíe su comprobante de pago.\n\n"
                "Use /banamex o /folios para ver sus folios activos y detener un timer."
            )

            await runtime.enviar_lista_folios(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
            )

        except Exception as e:
            print(f"[BOT PUEBLA] ERROR generando permiso: {e}")
            await message.answer(
                f"❌ Error: {e}\n\nUse /banamex para reintentar."
            )

    @dp.message()
    async def fallback(message: types.Message):
        await message.answer(
            "Use /banamex para iniciar un permiso o /folios para consultar timers activos."
        )

    return bot, dp, runtime
