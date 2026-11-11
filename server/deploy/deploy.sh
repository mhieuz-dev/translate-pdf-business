#!/usr/bin/env bash
# Đưa máy chủ dịch PDF lên VPS (chạy từ máy cá nhân, thư mục gốc repo):
#   server/deploy/deploy.sh root@<ip-vps>
# Lần đầu: tạo user hệ thống, cài uv + môi trường Python, nạp sẵn mô hình, bật dịch vụ systemd.
# Các lần sau: chép code mới rồi khởi động lại dịch vụ. Không đụng tới dịch vụ khác trên VPS.
# Caddy KHÔNG tự sửa: dán server/deploy/Caddyfile.snippet bằng tay (xem README).
set -euo pipefail
TARGET="${1:?Cách dùng: deploy.sh root@<ip-vps>}"
MEM_HIGH="${MEM_HIGH:-1200M}"
MEM_MAX="${MEM_MAX:-1500M}"
SWAP_MAX="${SWAP_MAX:-1500M}"
cd "$(dirname "$0")/.."

echo "== Chép code lên $TARGET:/opt/thongngu-pdf"
ssh "$TARGET" 'mkdir -p /opt/thongngu-pdf /var/lib/thongngu-pdf'
tar czf - app.py notify_mail.py pdf_bridge.py thongngu_layout.py thongngu_bing.py requirements.txt fonts/*.ttf fonts/GUST-FONT-LICENSE.txt | ssh "$TARGET" 'tar xzf - -C /opt/thongngu-pdf'
sed -e "s/__MEM_HIGH__/$MEM_HIGH/" -e "s/__MEM_MAX__/$MEM_MAX/" -e "s/__SWAP_MAX__/$SWAP_MAX/" deploy/thongngu-pdf.service \
  | ssh "$TARGET" 'cat > /etc/systemd/system/thongngu-pdf.service'

echo "== Cài môi trường (lần đầu mất vài phút)"
ssh "$TARGET" bash -s <<'REMOTE'
set -euo pipefail
id thongngu >/dev/null 2>&1 || useradd --system --home /opt/thongngu-pdf --shell /usr/sbin/nologin thongngu
command -v uv >/dev/null || [ -x /root/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh
UV=$(command -v uv || echo /root/.local/bin/uv)
cd /opt/thongngu-pdf
[ -d .venv ] || "$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -q -r requirements.txt
mkdir -p .cache .config  # pdf2zh ghi ~/.config/pdf2zh, BabelDOC ghi ~/.cache/babeldoc
chown -R thongngu:thongngu /opt/thongngu-pdf /var/lib/thongngu-pdf
# Nạp sẵn mô hình nhận dạng bố cục (~340 MB) để người dùng đầu tiên không phải chờ tải.
sudo -u thongngu env HOME=/opt/thongngu-pdf XDG_CACHE_HOME=/opt/thongngu-pdf/.cache \
  /opt/thongngu-pdf/.venv/bin/python -c "import babeldoc.assets.assets as a; a.warmup()"
systemctl daemon-reload
systemctl enable thongngu-pdf >/dev/null
systemctl restart thongngu-pdf
sleep 3
systemctl is-active thongngu-pdf
curl -fsS http://127.0.0.1:8787/health && echo
REMOTE
echo "== Xong. Kiểm tra từ ngoài: curl https://mhieuz.com/thongngu-pdf/health"
