#!/bin/bash
set -e

# Скрипт автоматического обновления и перезапуска бота Polymarket
echo "🚀 Начинаем обновление бота..."

# 1. Прячем возможные локальные изменения, чтобы git pull не упал с ошибкой
git stash > /dev/null 2>&1

# 2. Скачиваем свежий код из ветки main
echo "📥 Скачиваем последние обновления с GitHub..."
git pull origin main

# 3. Делаем скрипты исполняемыми и защищаем .env файл
chmod +x deploy.sh
chmod +x create-agent-structure.sh
if [ -f .env ]; then
    chmod 600 .env
fi

# 4. Останавливаем systemd сервис без небезопасного pkill
echo "🔄 Останавливаем systemd сервис..."
sudo systemctl stop polymarket-bot.service || true

# 5. Запускаем начисто
echo "▶️  Запускаем сервис..."
sudo systemctl start polymarket-bot.service

echo "✅ Готово! Бот успешно обновлен и запущен."
echo "Проверить статус можно командой: sudo systemctl status polymarket-bot.service"
