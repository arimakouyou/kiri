# Kiri - Multi-Device Proxy 設定ファイル

## 設定ファイルについて

`config.json` ファイルを使用して、プログラムの動作をカスタマイズできます。

## 設定ファイルの作成

1. `config.json.sample` をコピーして `config.json` を作成します：
   ```bash
   cp config.json.sample config.json
   ```

2. `config.json` を編集して設定をカスタマイズします。

## 設定項目

### email_address
- **説明**: GPIOボタン1+2の同時長押しで入力されるメールアドレス
- **デフォルト**: "test@example.com"
- **例**: "your.email@domain.com"

### gpio_settings
- **hold_time**: 長押しと判定される時間（秒）
  - デフォルト: 1.5
- **bounce_time**: ボタンのバウンス除去時間（秒）
  - デフォルト: 0.05
- **combination_check_delay**: 同時押し検出の待機時間（秒）
  - デフォルト: 0.2

### logging
- **level**: ログレベル
  - 選択肢: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
  - デフォルト: "ERROR"

### hid_paths
- **keyboard**: キーボードHIDデバイスのパス
  - デフォルト: "/dev/hidg0"
- **mouse_outputs**: マウスHIDデバイスのパス（配列）
  - デフォルト: ["/dev/hidg1", "/dev/hidg2"]

## 設定例

```json
{
  "email_address": "user@company.com",
  "gpio_settings": {
    "hold_time": 2.0,
    "bounce_time": 0.1,
    "combination_check_delay": 0.3
  },
  "logging": {
    "level": "INFO"
  },
  "hid_paths": {
    "keyboard": "/dev/hidg0",
    "mouse_outputs": ["/dev/hidg1", "/dev/hidg2"]
  }
}
```

## 注意事項

- 設定ファイルが見つからない場合は、デフォルト設定が使用されます
- 設定ファイルの形式が正しくない場合は、エラーログが出力され、デフォルト設定が使用されます
- プログラムを再起動すると、設定ファイルが再読み込みされます
