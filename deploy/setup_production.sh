#!/usr/bin/env bash
set -e

echo "=================================================="
echo " Thiết lập Production Service cho idcheck Studio "
echo "=================================================="

# 1. Kiểm tra quyền root / sudo
if [ "$EUID" -ne 0 ]; then
  echo "Vui lòng chạy script với quyền sudo: sudo bash deploy/setup_production.sh"
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Thư mục dự án: $REPO_DIR"

# 2. Cài đặt các gói phụ thuộc cần thiết (FFmpeg, Nginx)
echo "==> Cài đặt ffmpeg và nginx..."
apt-get update -y
apt-get install -y ffmpeg nginx

# 3. Copy systemd service file
echo "==> Cài đặt systemd service idcheck_studio..."
sed -i "s|/home/tran-tuan-kinh/idcheck|$REPO_DIR|g" "$REPO_DIR/deploy/idcheck_studio.service"
cp "$REPO_DIR/deploy/idcheck_studio.service" /etc/systemd/system/idcheck_studio.service

systemctl daemon-reload
systemctl enable idcheck_studio.service
systemctl restart idcheck_studio.service

# 4. Kiểm tra trạng thái service
if systemctl is-active --quiet idcheck_studio.service; then
  echo "✅ idcheck_studio.service đang chạy thành công!"
else
  echo "⚠️ Có lỗi khi khởi động idcheck_studio.service. Kiểm tra: journalctl -u idcheck_studio -n 50"
fi

echo "=================================================="
echo " Hoàn tất cài đặt! "
echo " Service quản lý: sudo systemctl status idcheck_studio"
echo " Xem logs: journalctl -u idcheck_studio -f"
echo "=================================================="
