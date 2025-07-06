#!/bin/bash
set -e
echo "Multi HID Proxy をアンインストールします..."

if [ "$(id -u)" -ne 0 ]; then
  echo "このスクリプトはroot権限で実行する必要があります。sudo ./uninstall.sh をお試しください。" >&2
  exit 1
fi

echo "サービスを停止・無効化しています..."
systemctl stop multi_hid_proxy.service || true
systemctl stop multi-hid-gadget.service || true
systemctl disable multi_hid_proxy.service || true
systemctl disable multi-hid-gadget.service || true

echo "関連ファイルを削除しています..."
rm -f /etc/systemd/system/multi_hid_proxy.service
rm -f /etc/systemd/system/multi-hid-gadget.service
rm -f /usr/local/bin/multi_device_proxy.py
rm -f /usr/local/bin/setup_hid_gadget.sh

echo "Systemdデーモンをリロードしています..."
systemctl daemon-reload

echo "アンインストールが完了しました。"

