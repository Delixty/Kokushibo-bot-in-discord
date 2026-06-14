import datetime
import math
import random
import sqlite3
import disnake
from disnake.ext import commands, tasks

# НАСТРОЙКИ БОТА И РОЛЕЙ 
LEVEL_REWARDS = {
    10: ("ID")),  # ID роли "Игрок"
    20: ("ID")),  # ID роли "Стандарт"
    35: ("ID")),  # ID роли "Мастер"
    50: ("ID")),  # ID роли "Ветеран"
    75: ("ID")),  # ID роли "Элита"
    100: ("ID"))  # ID роли "Легенда"
}

# [ФИЧА 3] Черный список каналов (опыт в них не капает)
IGNORE_CHANNELS = ["ID"]  # Замени нули на ID АФК-войсов

# [ФИЧА 4] Множитель опыта х2 для бустеров или VIP
BOOSTER_ROLE_ID = "ID"  # Замени ноль на ID роли бустера/VIP

# ID канала, куда слать красивые поздравления о повышении ранга
NOTIFICATION_CHANNEL_ID = "ID"  # Замени ноль на ID текстового канала левелапов

intents = disnake.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.InteractionBot(intents=intents, test_guilds=[1346734620418375741])

# --- КЭШ БОТА ---
voice_connected_users = {}
last_message_times = {}  # Словарь для защиты от спама: {user_id: timestamp}


# --- РАБОТА С БАЗОЙ ДАННЫХ ---


def init_db():
    conn = sqlite3.connect("server_stats.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            voice_time INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def get_user_data(user_id):
    conn = sqlite3.connect("server_stats.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp, level, voice_time FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = (0, 1, 0)
    conn.close()
    return {"xp": row[0], "level": row[1], "voice_time": row[2]}


def update_user_xp(user_id, xp_to_add):
    conn = sqlite3.connect("server_stats.db")
    cursor = conn.cursor()
    data = get_user_data(user_id)

    new_xp = data["xp"] + xp_to_add
    current_lvl = data["level"]

    xp_needed = int(100 * math.pow(current_lvl, 1.5))

    leveled_up = False
    if new_xp >= xp_needed:
        new_xp -= xp_needed
        current_lvl += 1
        leveled_up = True

    cursor.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (new_xp, current_lvl, user_id))
    conn.commit()
    conn.close()
    return leveled_up, current_lvl


def add_voice_time(user_id, seconds):
    conn = sqlite3.connect("server_stats.db")
    cursor = conn.cursor()
    get_user_data(user_id)
    cursor.execute("UPDATE users SET voice_time = voice_time + ? WHERE user_id = ?", (seconds, user_id))
    conn.commit()
    conn.close()


# --- ВЫДАЧА РОЛЕЙ И ОПОВЕЩЕНИЯ ---
async def process_level_roles(member, new_lvl, guild):
    # Если админ — полностью игнорируем выдачу ролей и спам
    if member.guild_permissions.administrator:
        return

    # Ищем специальный канал для поздравлений
    channel = guild.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        print(f"[Ошибка] Не найден канал для оповещений с ID {NOTIFICATION_CHANNEL_ID}")
        return

    new_role_id = LEVEL_REWARDS.get(new_lvl)
    new_role = guild.get_role(new_role_id) if new_role_id else None

    # Создаем красивое Embed-сообщение
    embed = disnake.Embed(
        title="🎉 ПОВЫШЕНИЕ РАНГА! 🎉",
        description=f"{member.mention} прокачался и достиг **{new_lvl} уровня**!",
        color=0x2b2d31,
        timestamp=datetime.datetime.now()
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    if new_role:
        try:
            roles_to_remove = []
            for lvl, r_id in LEVEL_REWARDS.items():
                if r_id != new_role_id:
                    old_role = guild.get_role(r_id)
                    if old_role and old_role in member.roles:
                        roles_to_remove.append(old_role)

            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            await member.add_roles(new_role)

            embed.add_field(
                name="🏅 Новый статус",
                value=f"Старые ранги удалены. Тебе присвоено звание: **{new_role.name}**!",
                inline=False
            )
        except disnake.Forbidden:
            embed.add_field(
                name="⚠️ Ошибка прав",
                value="Я не смог выдать роль. Поднимите роль бота выше в настройках сервера!",
                inline=False
            )
        except Exception as e:
            print(f"Ошибка при выдаче роли: {e}")
    else:
        embed.add_field(
            name="🚀 Вперед к новым вершинам!",
            value="До следующей награды осталось ещё немного!",
            inline=False
        )

    await channel.send(embed=embed)


# --- СОБЫТИЯ ---
@bot.event
async def on_ready():
    init_db()

    # Сканируем войсы при холодном старте бота
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            # [ФИЧА 3] Пропускаем АФК-комнаты из игнор-листа
            if channel.id in IGNORE_CHANNELS:
                continue
            for member in channel.members:
                if not member.bot and member.id not in voice_connected_users:
                    voice_connected_users[member.id] = datetime.datetime.now(datetime.timezone.utc)

    # Запускаем фоновую таску сохранения данных
    if not auto_save_voice_stats.is_running():
        auto_save_voice_stats.start()

    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Бот {bot.user} успешно запущен!")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    now = datetime.datetime.now(datetime.timezone.utc)

    last_time = last_message_times.get(message.author.id)
    if last_time:
        time_passed = (now - last_time).total_seconds()
        if time_passed < 60:
            return

    last_message_times[message.author.id] = now

    # [ФИЧА 4] Начисление опыта за сообщения (базовое vs X2 для бустеров)
    if any(role.id == BOOSTER_ROLE_ID for role in message.author.roles):
        xp_to_add = random.randint(30, 50)  # X2 опыт
    else:
        xp_to_add = random.randint(15, 25)  # Обычный опыт

    leveled_up, new_lvl = update_user_xp(message.author.id, xp_to_add)

    if leveled_up:
        await process_level_roles(message.author, new_lvl, message.guild)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. Если пользователь вышел из канала или перешел в другой
    if before.channel is not None and before.channel != after.channel:
        join_time = voice_connected_users.pop(member.id, None)
        if join_time:
            duration = int((now - join_time).total_seconds())
            add_voice_time(member.id, duration)

            # Начисляем опыт (только если канал не был в черном списке)
            if before.channel.id not in IGNORE_CHANNELS:
                xp_for_voice = duration // 30
                if xp_for_voice > 0:
                    # Проверка на х2 множитель
                    if any(role.id == BOOSTER_ROLE_ID for role in member.roles):
                        xp_for_voice *= 2

                    leveled_up, new_lvl = update_user_xp(member.id, xp_for_voice)
                    if leveled_up:
                        await process_level_roles(member, new_lvl, member.guild)

    # 2. Если пользователь зашел в канал (и этот канал НЕ в игноре)
    if after.channel is not None and before.channel != after.channel:
        if after.channel.id not in IGNORE_CHANNELS:
            voice_connected_users[member.id] = now


# --- СЛЭШ-КОМАНДЫ ---

# --- [ФИЧА 1] ФОНОВОЕ АВТОСОХРАНЕНИЕ КАЖДЫЕ 5 МИНУТ ---
@tasks.loop(minutes=5)
async def auto_save_voice_stats():
    now = datetime.datetime.now(datetime.timezone.utc)
    # Проходимся по всем, кто сейчас залогирован в войсе
    for user_id, join_time in list(voice_connected_users.items()):
        duration = int((now - join_time).total_seconds())
        if duration <= 0:
            continue

        # Сохраняем накопленное за 5 минут время в БД
        add_voice_time(user_id, duration)

        # Считаем опыт за эти 5 минут (1 XP за 30 сек в войсе)
        xp_for_voice = duration // 30
        if xp_for_voice > 0:
            # Проверяем, есть ли х2 буст
            member = None
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    break

            if member and any(role.id == BOOSTER_ROLE_ID for role in member.roles):
                xp_for_voice *= 2

            leveled_up, new_lvl = update_user_xp(user_id, xp_for_voice)
            if leveled_up and member:
                await process_level_roles(member, new_lvl, member.guild)

        # Сдвигаем точку отсчета на "прямо сейчас", чтобы время не засейвилось повторно
        voice_connected_users[user_id] = now

# [ФИЧА 2] КРАСИВАЯ КОМАНДА /profile С ПРОГРЕСС-БАРОМ
@bot.slash_command(name="profile", description="Посмотреть профиль и прогресс уровня")
async def profile(inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
    target = member or inter.author

    if target.bot:
        await inter.response.send_message("У ботов нет профилей!", ephemeral=True)
        return

    await inter.response.defer()

    db_data = get_user_data(target.id)

    now = datetime.datetime.now(datetime.timezone.utc)
    time_on_server = now - target.joined_at
    days_on_server = time_on_server.days

    total_seconds = db_data["voice_time"]

    # Прибавляем текущую активную сессию, если сидит в войсе прямо сейчас
    if target.id in voice_connected_users:
        current_session = int((now - voice_connected_users[target.id]).total_seconds())
        total_seconds += current_session

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    current_xp = db_data['xp']
    xp_needed = int(100 * math.pow(db_data["level"], 1.5))

    # Расчет текстового прогресс-бара
    percentage = min(100, int((current_xp / xp_needed) * 100))
    bar_length = 12  # Длина полоски в символах
    filled_blocks = int((percentage / 100) * bar_length)
    empty_blocks = bar_length - filled_blocks
    progress_bar = f"«{'█' * filled_blocks}{'░' * empty_blocks}» `{percentage}%`"

    embed = disnake.Embed(
        title=f"👤 Профиль участника: {target.display_name}",
        color=0x2b2d31,
        timestamp=datetime.datetime.now()
    )
    if target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)

    # Проверяем, есть ли у пользователя статус Бустера/VIP для плашки в профиле
    is_premium = "⭐ Премиум-буст (X2 XP)" if any(
        role.id == BOOSTER_ROLE_ID for role in target.roles) else "Обычный статус"

    embed.add_field(name="🌟 Ранг и Статус", value=f"**Уровень:** {db_data['level']}\n**Модификатор:** {is_premium}",
                    inline=False)
    embed.add_field(name="📈 Опыт до следующего уровня", value=f"{current_xp} / {xp_needed} XP\n{progress_bar}",
                    inline=False)
    embed.add_field(name="🎙 В голосовых каналах", value=f"⏱ {hours} ч. {minutes} мин.", inline=True)
    embed.add_field(name="📅 На сервере",
                    value=f"🗓 {days_on_server} дней\n*(С {target.joined_at.strftime('%d.%m.%Y')})*", inline=True)

    embed.set_footer(text=f"Запросил: {inter.author.display_name}", icon_url=inter.author.display_avatar.url)

    await inter.edit_original_message(embed=embed)


@bot.slash_command(name="leaderboard", description="Таблица лидеров сервера")
async def leaderboard(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer()

    conn = sqlite3.connect("server_stats.db")
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, level, xp FROM users ORDER BY level DESC, xp DESC LIMIT 10")
    top_xp = cursor.fetchall()

    cursor.execute("SELECT user_id, voice_time FROM users WHERE voice_time > 0 ORDER BY voice_time DESC LIMIT 10")
    top_voice = cursor.fetchall()

    conn.close()

    embed = disnake.Embed(title="🏆 Таблица лидеров сервера", color=0xffd700)

    xp_text = ""
    for i, (uid, lvl, xp) in enumerate(top_xp, 1):
        xp_text += f"**{i}.** <@{uid}> — Уровень: **{lvl}** ({xp} XP)\n"
    if not xp_text: xp_text = "Здесь пока никого нет."

    voice_text = ""
    for i, (uid, vtime) in enumerate(top_voice, 1):
        hours = vtime // 3600
        minutes = (vtime % 3600) // 60
        voice_text += f"**{i}.** <@{uid}> — **{hours} ч. {minutes} мин.**\n"
    if not voice_text: voice_text = "Здесь пока никого нет."

    embed.add_field(name="💬 Топ по уровню", value=xp_text, inline=False)
    embed.add_field(name="🎙 Топ по времени в войсе", value=voice_text, inline=False)

    await inter.edit_original_message(embed=embed)


# --- [ФИЧА 3] КОМАНДА /donate ДЛЯ СБОРА НА ХОСТИНГ ---
# Теперь отступы правильные, и команда находится ВНЕ других функций
@bot.slash_command(name="donate", description="Поддержать работу бота и сервера")
async def donate(inter: disnake.ApplicationCommandInteraction):
    # Создаем красивое сообщение
    embed = disnake.Embed(
        title="💳 Поддержка сервера",
        description="Мы собираем средства на оплату хостинга (99₽/мес), чтобы `kokushibo` работал 24/7 и радовал нас статистикой. Любая копейка приближает нас к цели!",
        color=0xffd700,  # Золотой цвет
        timestamp=datetime.datetime.now()
    )

    # Добавляем поле с картой
    embed.add_field(
        name="Куда отправлять:",
        value=f"Номер карты: `{2202206146567750}`\n\n*При переводе, если есть возможность, напиши в комментарии свой ник и пожелания, если имеются, чтобы я знал, кто помог!*",
        inline=False
    )

    embed.set_footer(text="Спасибо за поддержку развития проекта!")

    await inter.response.send_message(embed=embed)


# --- ЗАПУСК БОТА ---
bot.run(os.environ.get("BOT_TOKEN"))
