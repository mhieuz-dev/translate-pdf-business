#!/usr/bin/env bash
# Cài Thông Ngữ cho người dùng hiện tại (không cần sudo):
#   - chương trình:  ~/.local/bin/thong-ngu
#   - biểu tượng + mục trong menu ứng dụng
#   - phím tắt Ctrl+Alt+E (GNOME)
# Chạy từ thư mục gốc repo sau khi build:  npx tauri build --no-bundle && scripts/install-linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BIN=target/release/thong-ngu
[ -x "$BIN" ] || { echo "Chưa có $BIN. Chạy trước: npx tauri build --no-bundle"; exit 1; }

install -Dm755 "$BIN" "$HOME/.local/bin/thong-ngu"
for s in 32 64 128; do
  install -Dm644 "src-tauri/icons/${s}x${s}.png" "$HOME/.local/share/icons/hicolor/${s}x${s}/apps/thong-ngu.png"
done
install -Dm644 "src-tauri/icons/128x128@2x.png" "$HOME/.local/share/icons/hicolor/256x256/apps/thong-ngu.png"

install -Dm644 /dev/stdin "$HOME/.local/share/applications/thong-ngu.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Thông Ngữ
GenericName=Tra thuật ngữ Anh - Việt
Comment=Tra và lưu thuật ngữ Anh - Việt khi đọc, viết paper
Exec=$HOME/.local/bin/thong-ngu
Icon=thong-ngu
Terminal=false
Categories=Education;Office;Dictionary;
Keywords=dịch;thuật ngữ;paper;dictionary;translate;
StartupWMClass=thong-ngu
DESKTOP

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

# Lối tắt trên màn hình nền (nếu có thư mục Desktop)
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
  cp "$HOME/.local/share/applications/thong-ngu.desktop" "$DESK/thong-ngu.desktop"
  chmod +x "$DESK/thong-ngu.desktop"
  gio set "$DESK/thong-ngu.desktop" metadata::trusted true 2>/dev/null || true
fi

"$HOME/.local/bin/thong-ngu" --install-hotkey || echo "Chưa cài được phím tắt (lý do ở dòng trên). Có thể tự gán phím trong Cài đặt > Bàn phím để chạy: $HOME/.local/bin/thong-ngu --lookup"
echo "Đã cài Thông Ngữ. Tìm 'Thông Ngữ' trong menu ứng dụng hoặc mở từ màn hình nền."
