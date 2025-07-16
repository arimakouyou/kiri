#!/bin/bash
set -e
echo "Multi HID Proxy をインストールします..."

if [ "$(id -u)" -ne 0 ]; then
  echo "このスクリプトはroot権限で実行する必要があります。sudo ./install.sh をお試しください。" >&2
  exit 1
fi

echo "スクリプトを /usr/local/bin/ にコピーしています..."
install -m 755 multi_device_proxy.py /usr/local/bin/multi_device_proxy.py
install -m 755 setup_hid_gadget.sh /usr/local/bin/setup_hid_gadget.sh
install -m 644 config.json /usr/local/bin/config.json

echo "Systemdサービスファイルをコピーしています..."
install -m 644 multi-hid-gadget.service /etc/systemd/system/
install -m 644 multi-hid-proxy.service /etc/systemd/system/

echo "Systemdデーモンをリロードしています..."
systemctl daemon-reload

echo "サービスの自動起動を有効にしています..."
systemctl enable multi-hid-gadget.service
systemctl enable multi-hid-proxy.service

echo ""
echo "インストールが完了しました！"
echo "--------------------------"
echo "設定を有効にするために、システムを再起動してください:"
echo "sudo reboot"

