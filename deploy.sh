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

# 4. Убиваем зомби-процессы, чтобы systemctl stop не зависал на 90 секунд
echo "🧹 Зачистка процессов..."
sudo pkill -9 -f main.py > /dev/null 2>&1

# 5. Останавливаем сервис
echo "🔄 Останавливаем systemd сервис..."
sudo systemctl stop polymarket-bot.service

# 6. Запускаем начисто
sudo systemctl start polymarket-bot.service

echo "✅ Готово! Бот успешно обновлен и запущен."
echo "Проверить статус можно командой: sudo systemctl status polymarket-bot.service"
