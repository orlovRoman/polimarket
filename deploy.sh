#!/bin/bash

# Скрипт автоматического обновления и перезапуска бота Polymarket
echo "🚀 Начинаем обновление бота..."

# 1. Прячем возможные локальные изменения, чтобы git pull не упал с ошибкой
git stash > /dev/null 2>&1

# 2. Скачиваем свежий код из ветки main
echo "📥 Скачиваем последние обновления с GitHub..."
git pull origin main

# 3. Делаем скрипты исполняемыми (на всякий случай)
chmod +x deploy.sh
chmod +x create-agent-structure.sh

# 4. Перезапускаем сервис
echo "🔄 Перезапускаем systemd сервис..."
sudo systemctl stop polymarket-bot.service

# 5. Убиваем зомби-процессы, если они зависли
echo "🧹 Зачистка зомби-процессов..."
sudo pkill -f main.py > /dev/null 2>&1

# 6. Запускаем начисто
sudo systemctl start polymarket-bot.service

echo "✅ Готово! Бот успешно обновлен и запущен."
echo "Проверить статус можно командой: sudo systemctl status polymarket-bot.service"
