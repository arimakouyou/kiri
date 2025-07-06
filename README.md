# Multi-Device HID Proxy

## 1. 概要

このプロジェクトは、viggofalster/kiriをベースとしたRaspberry PiなどのLinuxデバイスを、複数のBluetoothキーボードやマウスを中継するUSBプロキシデバイスとして機能させるためのソフトウェア一式です。

これにより、複数の無線デバイスを、あたかも単一の有線USBデバイスであるかのようにホストPCに接続できます。実行中のデバイスの動的な接続・切断にも対応しています。

## 2. 主な機能

* **複数デバイス対応**: 複数のキーボードとマウスを同時に接続し、プロキシとして動作させます。
* **動的認識**: プログラムの実行中にBluetoothデバイスを接続・切断しても、自動で認識・解放します。
* **キーリマップ**: US配列のキーボードを日本語配列風にリマップする機能を内蔵しています。
* **Systemd連携**: システム起動時に、USBガジェットの設定とプロキシプログラムが自動的に起動します。
* **Keybow対応**: Raspberry PiのGPIOピンに接続したボタンに、カスタムの動作を割り当てることができます。

## 3. ファイル構成

このパッケージには以下のファイルが含まれています。

| ファイル名                      | 説明                                                              |
| ------------------------------- | ----------------------------------------------------------------- |
| `multi_device_proxy.py`         | メインのプロキシプログラムです。デバイスを監視し、入力を中継します。 |
| `setup_hid_gadget.sh`           | USB HIDガジェット（キーボードとマウス）を作成・設定するスクリプトです。 |
| `multi-hid-gadget.service`      | `setup_hid_gadget.sh`をシステム起動時に実行するためのSystemdサービスです。 |
| `multi-hid-proxy.service`       | `multi_device_proxy.py`をシステムサービスとして実行するための定義ファイルです。 |
| `install.sh`                    | すべてのファイルを適切な場所にコピーし、サービスを有効化するスクリプトです。 |
| `uninstall.sh`                  | インストールされたすべてのファイルをシステムから削除するスクリプトです。 |
| `README.md`                     | このファイルです。                                                |

## 4. 前提条件

* USB On-The-Go (OTG) をサポートするLinuxデバイス（例: Raspberry Pi Zero, Raspberry Pi 4など）
* Python 3
* 必要なPythonライブラリ:
    * `evdev`: `sudo apt-get install python3-evdev` などでインストール
    * `gpiozero` (任意): `sudo apt-get install python3-gpiozero` などでインストール

## 5. インストール手順

1. このパッケージに含まれるすべてのファイルを、デバイスの同じディレクトリに配置します。
2. ターミナルで以下のコマンドを実行し、インストールスクリプトに実行権限を与えます。
   ```bash
   chmod +x install.sh uninstall.sh
   ```
3. 以下のコマンドでインストールを実行します。
   ```bash
   sudo ./install.sh
   ```
4. インストール完了後、システムを再起動します。
   ```bash
   sudo reboot
   ```
   再起動後、サービスが自動的に開始され、プロキシが有効になります。

## 6. 設定

### デバイス名のカスタマイズ

お使いのキーボードやマウスのメーカーに合わせて、プロキシが認識するデバイスを変更できます。

1. `multi_device_proxy.py` ファイルを開きます。
2. `device_monitor` 関数内の以下の行を編集します。
   ```python
   MOUSE_DEVICENAME_PATTERN = 'HHKB-Studio4 Mouse|Logitech.*'
   KEYBOARD_DEVICENAME_PATTERN = 'HHKB-Studio4 Keyboard|HHKB-Hybrid.*|PFU.*'
   ```
   ここに、お使いのデバイス名に一致する正規表現パターンを追加・変更してください。デバイス名の確認方法は、[こちらの手順](#デバイス名の確認方法)を参照してください。

### 接続数の変更

同時に接続するキーボードとマウスの最大数を変更できます。

1. `multi_device_proxy.py` ファイルと `setup_hid_gadget.sh` ファイルの両方を開きます。
2. 両方のファイルで、キーボードとマウス用のHIDデバイス (`/dev/hidgX`) の数を一致させるように設定を変更してください。

## 7. アンインストール

システムからこのプログラムを削除するには、以下のコマンドを実行します。
```bash
sudo ./uninstall.sh
```

---

### 付録: デバイス名の確認方法

ターミナルで以下のコマンドを実行すると、現在システムに認識されている入力デバイスの一覧が表示されます。この中から、お使いのデバイスの正確な名前を確認できます。
```bash
python3 -c "import evdev; [print(f'Path: {p}, Name: \"{evdev.InputDevice(p).name}\"') for p in evdev.list_devices()]"

