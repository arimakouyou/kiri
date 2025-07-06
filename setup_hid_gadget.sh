#!/bin/bash
#
# このスクリプトは、複数のキーボードとマウスを中継するUSB HIDプロキシデバイスを設定します。
# 1つのキーボードと2つのマウス用のHIDエンドポイントを作成します。
#
# 実行するにはroot権限が必要です。
# (e.g., sudo ./setup_hid_gadget.sh)
#

set -e
GADGET_BASE="/sys/kernel/config/usb_gadget"
GADGET_NAME="multi_hid_proxy"
GADGET_PATH="${GADGET_BASE}/${GADGET_NAME}"

if [ -d "${GADGET_PATH}" ]; then
    echo "既存のガジェット設定 ${GADGET_NAME} をクリーンアップします..."
    if [ -d "${GADGET_PATH}/UDC" ] && [ -n "$(cat ${GADGET_PATH}/UDC)" ]; then
        echo "" > "${GADGET_PATH}/UDC"
        sleep 1
    fi
    find "${GADGET_PATH}/configs/c.1" -type l -delete
    rm -rf "${GADGET_PATH}/functions/"*
    rmdir "${GADGET_PATH}/configs/c.1/strings/0x409"
    rmdir "${GADGET_PATH}/configs/c.1"
    rmdir "${GADGET_PATH}/strings/0x409"
    rmdir "${GADGET_PATH}"
    echo "クリーンアップ完了。"
fi

echo "新しいガジェット ${GADGET_NAME} を作成します..."
mkdir -p "${GADGET_PATH}"
cd "${GADGET_PATH}"

echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "proxy-20250706" > strings/0x409/serialnumber
echo "K.Arima" > strings/0x409/manufacturer
echo "Multi HID Proxy Device" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Config 1: Keyboard and Mouse" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# Keyboard (hidg0)
FUNC_PATH="functions/hid.usb0"
mkdir -p "${FUNC_PATH}"
echo 1 > "${FUNC_PATH}/protocol"
echo 1 > "${FUNC_PATH}/subclass"
echo 8 > "${FUNC_PATH}/report_length"
echo -ne "\x05\x01\x09\x06\xA1\x01\x05\x07\x19\xE0\x29\xE7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x01\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x26\xFF\x00\x05\x07\x19\x00\x2A\xFF\x00\x81\x00\xC0" > "${FUNC_PATH}/report_desc"
ln -s "${FUNC_PATH}" "configs/c.1/"

# Mouse (hidg1, hidg2)
for i in {1..2}
do
    FUNC_PATH="functions/hid.usb${i}"
    mkdir -p "${FUNC_PATH}"
    echo 2 > "${FUNC_PATH}/protocol"
    echo 1 > "${FUNC_PATH}/subclass"
    echo 7 > "${FUNC_PATH}/report_length"
    echo -ne "\x05\x01\x09\x02\xA1\x01\x09\x01\xA1\x00\x05\x09\x19\x01\x29\x05\x15\x00\x25\x01\x95\x05\x75\x01\x81\x02\x95\x01\x75\x03\x81\x03\x05\x01\x09\x30\x09\x31\x09\x38\x16\x01\x80\x26\xFF\x7F\x75\x10\x95\x03\x81\x06\xC0\xC0" > "${FUNC_PATH}/report_desc"
    ln -s "${FUNC_PATH}" "configs/c.1/"
done

UDC_NAME=$(ls /sys/class/udc | head -n 1)
if [ -z "${UDC_NAME}" ]; then
    echo "エラー: 利用可能なUDCが見つかりません。" >&2
    exit 1
fi
echo "${UDC_NAME}" > UDC
echo "USBガジェットの設定が完了しました。"

