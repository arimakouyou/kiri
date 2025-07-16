#!/usr/bin/python3
"""
Multi-Device Proxy
マウスとキーボードの複数デバイスを統合管理し、HIDガジェットとして出力するプロキシシステム
GPIOボタンによる追加機能も提供
"""

import logging
import evdev
import time
import sys
import asyncio
import signal
import re
import json
import os
from enum import IntEnum
from evdev import InputDevice, ecodes, categorize
from hid_keys import hid_key_map as hid_keys

# グローバル設定
REMAP_ENABLED = True  # キーリマップ機能の有効/無効

# 設定ファイルの読み込み機能
def load_config(config_path="config.json"):
    """設定ファイルを読み込む"""
    default_config = {
        "email_address": "test@example.com",
        "gpio_settings": {
            "hold_time": 1.5,
            "bounce_time": 0.05,
            "combination_check_delay": 0.2
        },
        "logging": {
            "level": "ERROR"
        },
        "hid_paths": {
            "keyboard": "/dev/hidg0",
            "mouse_outputs": ["/dev/hidg1", "/dev/hidg2"]
        }
    }
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logging.info(f"設定ファイル {config_path} を読み込みました")
                # デフォルト値と設定ファイルの値をマージ
                for key in default_config:
                    if key not in config:
                        config[key] = default_config[key]
                    elif isinstance(default_config[key], dict) and isinstance(config[key], dict):
                        for sub_key in default_config[key]:
                            if sub_key not in config[key]:
                                config[key][sub_key] = default_config[key][sub_key]
                return config
        else:
            logging.warning(f"設定ファイル {config_path} が見つかりません。デフォルト設定を使用します")
            return default_config
    except json.JSONDecodeError as e:
        logging.error(f"設定ファイルの読み込みエラー: {e}。デフォルト設定を使用します")
        return default_config
    except Exception as e:
        logging.error(f"設定ファイル読み込み中の予期せぬエラー: {e}。デフォルト設定を使用します")
        return default_config

# グローバル設定変数
CONFIG = load_config()

# GPIOライブラリの読み込み（Raspberry Pi以外では無効化）
try:
    from gpiozero import Button
except ImportError:
    logging.warning("gpiozeroライブラリが見つかりません。GPIOボタン機能は無効になります。")
    
    class Button:
        """GPIOライブラリが利用できない場合のダミークラス"""
        def __init__(self, *args, **kwargs): 
            pass
        def __getattr__(self, name): 
            return lambda *args, **kwargs: None

class MouseIndex(IntEnum):
    """マウスHIDレポートのバイト位置定義"""
    TIP_SW, X_LSB, X_MSB, Y_LSB, Y_MSB, WHEEL_LSB, WHEEL_MSB, MAX = range(8)


class MouseProxy:
    """
    マウスデバイスのプロキシクラス
    入力デバイスからイベントを受信し、HIDガジェットとして出力
    ドラッグ操作の安定化機能を含む
    """
    
    def __init__(self, input_device_path, hid_output_path, loop):
        """
        マウスプロキシの初期化
        
        Args:
            input_device_path (str): 入力デバイスのパス
            hid_output_path (str): HID出力デバイスのパス  
            loop: asyncioイベントループ
        """
        self.log = logging.getLogger(f"MouseProxy-{input_device_path.split('/')[-1]}")
        self.loop = loop
        self.input_device_path = input_device_path
        self.hid_output_path = hid_output_path
        self.device = None
        self.button_state_buffer = {}  # ボタン状態の保持用
        self.last_button_event_time = {}  # 最終ボタンイベント時刻
        self.reset_state()

    def connect_device(self):
        """デバイスへの接続を試行し、成功時はデバイスを排他制御する"""
        try:
            self.device = InputDevice(self.input_device_path)
            self.device.grab()
            self.log.info(f"マウスを正常に捕捉: {self.device.path} ({self.device.name}) -> {self.hid_output_path}")
            self.restore_button_state()
            return True
        except Exception as e:
            self.log.error(f"{self.input_device_path}への接続失敗: {e}")
            return False

    def reset_state(self):
        """マウス状態を初期化し、必要な属性を設定"""
        # 基本ボタン状態
        self.button_left = self.button_right = self.button_center = self.back = self.forward = 0
        self.move_x = self.move_y = self.scroll_y = self.btn = 0
        
        # 状態管理用バッファの初期化
        if not hasattr(self, 'button_state_buffer'):
            self.button_state_buffer = {}
        if not hasattr(self, 'last_button_event_time'):
            self.last_button_event_time = {}
            
        # ドラッグ安定化のための追加状態
        if not hasattr(self, 'button_debounce_buffer'):
            self.button_debounce_buffer = {}
        if not hasattr(self, 'drag_mode'):
            self.drag_mode = False
        if not hasattr(self, 'last_movement_time'):
            self.last_movement_time = 0
            
        # ドラッグ状態の履歴管理
        if not hasattr(self, 'movement_history'):
            self.movement_history = []
        if not hasattr(self, 'button_press_start_time'):
            self.button_press_start_time = {}
        if not hasattr(self, 'consecutive_movement_count'):
            self.consecutive_movement_count = 0

    async def run(self):
        """メインループ: デバイスイベントを継続的に処理"""
        while True:
            if not self.device and not self.connect_device():
                await asyncio.sleep(5)
                continue
            try:
                async for event in self.device.async_read_loop():
                    if event.type == ecodes.EV_KEY: 
                        self.handle_key_event(event)
                    elif event.type == ecodes.EV_REL: 
                        self.handle_rel_event(event)
                    elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                        self.update_state()
            except (OSError, asyncio.CancelledError) as e:
                self.log.error(f"マウス {self.input_device_path} 通信切断: {type(e).__name__}")
                self.log.info(f"切断時のボタン状態を保持: {self.btn}")
                if self.device: 
                    self.device.close()
                self.device = None
                break
            except Exception as e:
                self.log.error(f"予期せぬエラー: {e}", exc_info=True)
                break

    def handle_key_event(self, event):
        """ボタンイベントの処理（ドラッグ安定化機能付き）"""
        current_time = time.time()
        is_press = event.value == 1
        
        # デバウンス処理: 同じボタンの連続したイベントを無視
        if event.code in self.button_debounce_buffer:
            last_event_time, last_state = self.button_debounce_buffer[event.code]
            if current_time - last_event_time < 0.01 and last_state == is_press:
                return  # デバウンス期間内の同じ状態イベントは無視
        
        self.button_debounce_buffer[event.code] = (current_time, is_press)
        
        # ドラッグモードの検出と改善
        was_dragging = self.drag_mode
        if event.code == ecodes.BTN_LEFT:
            if is_press:
                self.button_press_start_time[ecodes.BTN_LEFT] = current_time
                # ボタンが押された直前に移動があった場合、ドラッグの可能性が高い
                if current_time - self.last_movement_time < 0.1:
                    self.drag_mode = True
                    self.log.debug(f"ドラッグモード開始: 移動から{current_time - self.last_movement_time:.3f}秒でボタン押下")
            elif not is_press:
                # ボタンが離された時のドラッグモード終了処理
                if self.drag_mode:
                    # ドラッグ中の意図しないボタンリリースを検出
                    time_since_movement = current_time - self.last_movement_time
                    if time_since_movement < 0.03:  # 30ms以内に移動があった場合
                        self.log.debug(f"ドラッグ中の偶発的なボタンリリースを検出: 移動から{time_since_movement:.3f}秒")
                        # 短時間の場合は、ボタンリリースを無視してドラッグを継続
                        return
                self.drag_mode = False
                self.consecutive_movement_count = 0
        
        # ドラッグ中のボタンリリースイベントを安定化
        if was_dragging and event.code == ecodes.BTN_LEFT and not is_press:
            # ドラッグ中に偶発的なリリースイベントが発生した場合の処理
            if current_time - self.last_movement_time < 0.05:
                self.log.debug(f"ドラッグ中の偶発的なボタンリリースを無視: {current_time - self.last_movement_time:.3f}秒前に移動")
                return
        
        # 各ボタンの状態更新
        if event.code == ecodes.BTN_LEFT:
            self.button_state_buffer[ecodes.BTN_LEFT] = is_press
            self.last_button_event_time[ecodes.BTN_LEFT] = current_time
            self.button_left = 1 if is_press else 0
        elif event.code == ecodes.BTN_RIGHT:
            self.button_state_buffer[ecodes.BTN_RIGHT] = is_press
            self.last_button_event_time[ecodes.BTN_RIGHT] = current_time
            self.button_right = (1 << 1) if is_press else 0
        elif event.code == ecodes.BTN_MIDDLE:
            self.button_state_buffer[ecodes.BTN_MIDDLE] = is_press
            self.last_button_event_time[ecodes.BTN_MIDDLE] = current_time
            self.button_center = (1 << 2) if is_press else 0
        elif event.code == ecodes.BTN_SIDE:
            self.button_state_buffer[ecodes.BTN_SIDE] = is_press
            self.last_button_event_time[ecodes.BTN_SIDE] = current_time
            self.back = (1 << 3) if is_press else 0
        elif event.code == ecodes.BTN_EXTRA:
            self.button_state_buffer[ecodes.BTN_EXTRA] = is_press
            self.last_button_event_time[ecodes.BTN_EXTRA] = current_time
            self.forward = (1 << 4) if is_press else 0
        
        # 総合ボタン状態の更新
        self.btn = self.button_left | self.button_right | self.button_center | self.back | self.forward
        self.log.debug(f"ボタンイベント: {event.code} = {is_press}, 統合状態: {self.btn}, ドラッグモード: {self.drag_mode}")

    def restore_button_state(self):
        """デバイス再接続時にボタン状態を復旧"""
        current_time = time.time()
        
        for button_code, is_pressed in self.button_state_buffer.items():
            last_event_time = self.last_button_event_time.get(button_code, 0)
            # 5秒以内の状態のみ復旧対象
            if current_time - last_event_time < 5.0 and is_pressed:
                if button_code == ecodes.BTN_LEFT:
                    self.button_left = 1
                elif button_code == ecodes.BTN_RIGHT:
                    self.button_right = (1 << 1)
                elif button_code == ecodes.BTN_MIDDLE:
                    self.button_center = (1 << 2)
                elif button_code == ecodes.BTN_SIDE:
                    self.back = (1 << 3)
                elif button_code == ecodes.BTN_EXTRA:
                    self.forward = (1 << 4)
        
        self.btn = self.button_left | self.button_right | self.button_center | self.back | self.forward
        if self.btn > 0:
            self.log.info(f"ボタン状態を復旧しました: {self.btn}")

    def handle_rel_event(self, event):
        """相対移動イベントの処理（ドラッグ検出機能付き）"""
        current_time = time.time()
        
        if event.code == ecodes.REL_X: 
            self.move_x = event.value
            self.last_movement_time = current_time
            # 移動履歴を記録してドラッグ検出精度を向上
            self.movement_history.append((current_time, 'X', event.value))
            if len(self.movement_history) > 10:  # 履歴は最新10件に制限
                self.movement_history.pop(0)
            # 連続的な移動をカウント
            if self.button_state_buffer.get(ecodes.BTN_LEFT, False):
                self.consecutive_movement_count += 1
                if self.consecutive_movement_count > 3:  # 3回以上の連続移動でドラッグ確定
                    self.drag_mode = True
                    
        elif event.code == ecodes.REL_Y: 
            self.move_y = event.value
            self.last_movement_time = current_time
            # 移動履歴を記録してドラッグ検出精度を向上
            self.movement_history.append((current_time, 'Y', event.value))
            if len(self.movement_history) > 10:  # 履歴は最新10件に制限
                self.movement_history.pop(0)
            # 連続的な移動をカウント
            if self.button_state_buffer.get(ecodes.BTN_LEFT, False):
                self.consecutive_movement_count += 1
                if self.consecutive_movement_count > 3:  # 3回以上の連続移動でドラッグ確定
                    self.drag_mode = True
                    
        elif event.code == ecodes.REL_WHEEL: 
            self.scroll_y = event.value

    def update_state(self):
        """マウス状態をHIDレポートとして送信"""
        data = bytearray(MouseIndex.MAX)
        data[MouseIndex.TIP_SW] = self.btn
        data[MouseIndex.X_LSB] = self.move_x & 0xff
        data[MouseIndex.X_MSB] = (self.move_x >> 8) & 0xff
        data[MouseIndex.Y_LSB] = self.move_y & 0xff
        data[MouseIndex.Y_MSB] = (self.move_y >> 8) & 0xff
        data[MouseIndex.WHEEL_LSB] = self.scroll_y & 0xff
        data[MouseIndex.WHEEL_MSB] = (self.scroll_y >> 8) & 0xff
        self.write_report(bytes(data))
        # 移動量をリセット（ボタン状態は保持）
        self.move_x = self.move_y = self.scroll_y = 0

    def write_report(self, buffer):
        """HIDレポートの書き込み（エラーハンドリング付き）"""
        try:
            with open(self.hid_output_path, 'rb+') as fd:
                fd.write(buffer)
        except OSError as e:
            if e.errno == 108:
                self.log.error(f"HIDエンドポイント {self.hid_output_path} がシャットダウンされました。タスクを終了します。")
                raise e
            else:
                self.log.error(f"{self.hid_output_path}への書き込みで予期せぬOSError: {e}")
        except Exception as e:
            self.log.error(f"{self.hid_output_path}への書き込みで予期せぬエラー: {e}")

    def is_button_stable(self, button_code, current_state, current_time):
        """ボタン状態が安定しているかチェック（ドラッグ安定化用）"""
        if button_code not in self.button_state_buffer:
            return True
        
        last_event_time = self.last_button_event_time.get(button_code, 0)
        time_since_last_event = current_time - last_event_time
        
        # ドラッグ中の左ボタンの場合、特別な安定化処理
        if button_code == ecodes.BTN_LEFT and self.drag_mode:
            # ドラッグ中に突然リリースされた場合、移動が続いているかチェック
            if not current_state and time_since_last_event < 0.1:
                time_since_movement = current_time - self.last_movement_time
                if time_since_movement < 0.02:  # 20ms以内に移動があった場合
                    self.log.debug(f"ドラッグ中のボタンリリースを安定化処理で保持: 移動から{time_since_movement:.3f}秒")
                    return False
        
        return True

class KeyboardProxy:
    """
    キーボードデバイスのプロキシクラス
    入力デバイスからキーイベントを受信し、HIDガジェットとして出力
    キーリマップ機能を含む
    """
    
    def __init__(self, input_device_path, hid_output_path, loop):
        """
        キーボードプロキシの初期化
        
        Args:
            input_device_path (str): 入力デバイスのパス
            hid_output_path (str): HID出力デバイスのパス
            loop: asyncioイベントループ
        """
        self.log = logging.getLogger(f"KeyboardProxy-{input_device_path.split('/')[-1]}")
        self.loop = loop
        self.input_device_path = input_device_path
        self.hid_output_path = hid_output_path
        self.device = None
        # 修飾キーのビット位置マップ
        self.modifiers_map = {
            'KEY_LEFTCTRL': 0, 'KEY_LEFTSHIFT': 1, 'KEY_LEFTALT': 2, 'KEY_LEFTMETA': 3, 
            'KEY_RIGHTCTRL': 4, 'KEY_RIGHTSHIFT': 5, 'KEY_RIGHTALT': 6, 'KEY_RIGHTMETA': 7
        }
        self.reset_state()

    def connect_device(self):
        """キーボードデバイスへの接続を試行し、成功時はデバイスを排他制御する"""
        try:
            self.device = InputDevice(self.input_device_path)
            self.device.grab()
            self.log.info(f"キーボードを正常に捕捉: {self.device.path} ({self.device.name}) -> {self.hid_output_path}")
            return True
        except Exception as e:
            self.log.error(f"{self.input_device_path}への接続失敗: {e}")
            return False

    def reset_state(self):
        """キーボード状態を初期化"""
        self.modifier = 0b00000000  # 修飾キーの状態
        self.pressed_keys = set()   # 現在押下中のキー
        self.is_shift_up = False    # シフトアップが必要かどうか
        self.is_shift_down = False  # シフトダウンが必要かどうか
        self.shift_bit = 0b00100010  # 左右シフトキーのビットマスク

    async def run(self):
        """メインループ: キーボードイベントを継続的に処理"""
        while True:
            if not self.device and not self.connect_device():
                await asyncio.sleep(5)
                continue
            try:
                async for event in self.device.async_read_loop():
                    if event.type != ecodes.EV_KEY: 
                        continue
                    data = categorize(event)
                    keycode = data.keycode
                    if isinstance(keycode, list): 
                        keycode = keycode[0]
                    keystate = data.keystate
                    
                    # イベント処理の振り分け
                    if keycode in self.modifiers_map: 
                        self.update_modifier(keycode, keystate)
                    elif keystate == 0: 
                        self.release(keycode)
                    elif keystate == 1: 
                        self.press(keycode)
                        
            except (OSError, asyncio.CancelledError) as e:
                self.log.error(f"キーボード {self.input_device_path} 通信切断: {type(e).__name__}")
                if self.device: 
                    self.device.close()
                self.device = None
                break
            except Exception as e:
                self.log.error(f"予期せぬエラー: {e}", exc_info=True)
                self.reset_state()

    def update_modifier(self, keycode, keystate):
        """修飾キーの状態を更新"""
        if keystate == 0: 
            self.modifier &= ~(1 << self.modifiers_map[keycode])
        else: 
            self.modifier |= (1 << self.modifiers_map[keycode])
        self.update_state()

    def release(self, keycode):
        """キーリリースイベントの処理"""
        if keycode in self.pressed_keys:
            self.pressed_keys.remove(keycode)
            self.update_state()

    def press(self, keycode):
        """キープレスイベントの処理"""
        if keycode not in self.pressed_keys:
            self.pressed_keys.add(keycode)
            self.update_state()

    def remap(self, keycode):
        """キーコードのリマップ処理（US配列→JIS配列変換など）"""
        global REMAP_ENABLED
        if not REMAP_ENABLED:
            return hid_keys.get(keycode, 0)
        if keycode not in hid_keys: 
            return 0
            
        # 基本的なリマップ
        if keycode == 'KEY_LEFTBRACE': 
            keycode = 'KEY_RIGHTBRACE'
        elif keycode == 'KEY_RIGHTBRACE': 
            keycode = 'KEY_BACKSLASH'
        # シフト押下時の特殊処理
        elif self.modifier & self.shift_bit:
            if keycode == 'KEY_7': keycode = 'KEY_6'
            elif keycode == 'KEY_8': keycode = 'KEY_APOSTROPHE'
            elif keycode == 'KEY_9': keycode = 'KEY_8'
            elif keycode == 'KEY_0': keycode = 'KEY_9'
            elif keycode == 'KEY_EQUAL': keycode = 'KEY_SEMICOLON'
            elif keycode == 'KEY_GRAVE': keycode = 'KEY_EQUAL'
            elif keycode == 'KEY_MINUS': keycode = 'KEY_RO'
            elif keycode == 'KEY_2': keycode = 'KEY_LEFTBRACE'; self.is_shift_down = True
            elif keycode == 'KEY_6': keycode = 'KEY_EQUAL'; self.is_shift_down = True
            elif keycode == 'KEY_BACKSLASH': keycode = 'KEY_YEN'
            elif keycode == 'KEY_SEMICOLON': keycode = 'KEY_APOSTROPHE'; self.is_shift_down = True
            elif keycode == 'KEY_APOSTROPHE': keycode = 'KEY_2'
        # シフト非押下時の特殊処理
        else:
            if keycode == 'KEY_APOSTROPHE': keycode = 'KEY_7'; self.is_shift_up = True
            elif keycode == 'KEY_GRAVE': keycode = 'KEY_LEFTBRACE'; self.is_shift_up = True
            elif keycode == 'KEY_EQUAL': keycode = 'KEY_MINUS'; self.is_shift_up = True
            elif keycode == 'KEY_BACKSLASH': keycode = 'KEY_RO'
        return hid_keys.get(keycode, 0)

    def update_state(self):
        """キーボード状態をHIDレポートとして送信"""
        self.is_shift_up = False
        self.is_shift_down = False
        report = bytearray(8)
        pressed_hid_codes = [self.remap(k) for k in self.pressed_keys]
        modifier = self.modifier
        
        # シフト状態の特殊処理
        if self.is_shift_up:
            modifier |= 0x02
            report[0] = 0x02
            self.write_report(bytes(report))
            time.sleep(0.01)
        elif self.is_shift_down: 
            modifier &= ~self.shift_bit
            
        # HIDレポートの構築
        report[0] = modifier  # 修飾キー
        for i, code in enumerate(filter(None, pressed_hid_codes[:6])):
            report[2 + i] = code  # 通常キー（最大6個）
        self.write_report(bytes(report))

    def write_report(self, report: bytes):
        """HIDレポートの書き込み（エラーハンドリング付き）"""
        try:
            with open(self.hid_output_path, 'rb+') as fd:
                fd.write(report)
        except OSError as e:
            if e.errno == 108:
                self.log.error(f"HIDエンドポイント {self.hid_output_path} がシャットダウンされました。タスクを終了します。")
                raise e
            else:
                self.log.error(f"{self.hid_output_path}への書き込みで予期せぬOSError: {e}")
        except Exception as e:
            self.log.error(f"{self.hid_output_path}への書き込みで予期せぬエラー: {e}")

class KeyBowManager:
    """
    GPIOボタンの管理クラス
    Raspberry PiのGPIOピンに接続されたボタンを監視し、
    キーボードショートカットやメール入力などの機能を提供
    """
    
    def __init__(self, loop):
        """
        KeyBowManagerの初期化
        
        Args:
            loop: asyncioイベントループ
        """
        self.loop = loop
        self.keyboard_hid_path = CONFIG["hid_paths"]["keyboard"]
        self.email_address = CONFIG["email_address"]
        Button.was_held = False
        
        # 設定ファイルから GPIO 設定を読み込み
        gpio_settings = CONFIG["gpio_settings"]
        hold_time = gpio_settings["hold_time"]
        bounce_time = gpio_settings["bounce_time"]
        self.combination_check_delay = gpio_settings["combination_check_delay"]
        
        # ボタン1の設定（GPIO 6番ピン）
        self.btn1 = Button(6, hold_time=hold_time, bounce_time=bounce_time)
        self.btn1.when_held = self.held1
        self.btn1.when_released = self.released1
        self.btn1.was_held = False
        self.btn1.combination_detected = False
        
        # ボタン2の設定（GPIO 22番ピン）
        self.btn2 = Button(22, hold_time=hold_time, bounce_time=bounce_time)
        self.btn2.when_held = self.held2
        self.btn2.when_released = self.released2
        self.btn2.was_held = False
        self.btn2.combination_detected = False
        
        # ボタン3の設定（GPIO 17番ピン）
        self.btn3 = Button(17, hold_time=hold_time, bounce_time=bounce_time)
        self.btn3.when_held = self.held3
        self.btn3.when_released = self.released3
        self.btn3.was_held = False
        self.btn3.combination_detected = False
        
        logging.info(f"KeyBow (GPIOボタン) マネージャーを初期化しました。（長押し時間: {hold_time}秒、メールアドレス: {self.email_address}）")

    def send_key_combination(self, modifier_bits, key_code):
        """
        キーボードコンビネーションの送信
        指定されたキーを押下→離す→Altキーを押下→離すの順序で実行
        """
        try:
            # メインキーの送信
            press_report = bytearray(8)
            press_report[0] = modifier_bits
            press_report[2] = key_code
            
            release_report = bytearray(8)
            
            # Altキーのみの送信（ミーティングコントロール復活用）
            alt_only_press = bytearray(8)
            alt_only_press[0] = 0x04
            
            alt_only_release = bytearray(8)
            
            with open(self.keyboard_hid_path, 'rb+') as fd:
                fd.write(bytes(press_report))
                time.sleep(0.01)
                fd.write(bytes(release_report))
                time.sleep(0.01)
                fd.write(bytes(alt_only_press))
                time.sleep(0.01)
                fd.write(bytes(alt_only_release))
                
        except OSError as e:
            logging.error(f"HIDレポート送信エラー {self.keyboard_hid_path}: {e}")
        except Exception as e:
            logging.error(f"予期せぬエラー: {e}")

    def send_email_address(self):
        """設定ファイルから読み込んだメールアドレスを入力"""
        email = self.email_address
        logging.info(f"メールアドレスを入力します: {email}")
        
        try:
            with open(self.keyboard_hid_path, 'rb+') as fd:
                for char in email:
                    if char == '@':
                        press_report = bytearray(8)
                        press_report[0] = 0x02
                        press_report[2] = 0x1f
                        fd.write(bytes(press_report))
                        time.sleep(0.1)
                        
                        release_report = bytearray(8)
                        fd.write(bytes(release_report))
                        time.sleep(0.1)
                    elif char == '-':
                        press_report = bytearray(8)
                        press_report[2] = 0x2d
                        fd.write(bytes(press_report))
                        time.sleep(0.1)
                        
                        release_report = bytearray(8)
                        fd.write(bytes(release_report))
                        time.sleep(0.1)
                    elif char == '.':
                        press_report = bytearray(8)
                        press_report[2] = 0x37
                        fd.write(bytes(press_report))
                        time.sleep(0.1)
                        
                        release_report = bytearray(8)
                        fd.write(bytes(release_report))
                        time.sleep(0.1)
                    elif char.isalpha():
                        key_name = f'KEY_{char.upper()}'
                        if key_name in hid_keys:
                            press_report = bytearray(8)
                            press_report[2] = hid_keys[key_name]
                            fd.write(bytes(press_report))
                            time.sleep(0.1)
                            
                            release_report = bytearray(8)
                            fd.write(bytes(release_report))
                            time.sleep(0.1)
                    elif char.isdigit():
                        # 数字の処理を追加
                        key_name = f'KEY_{char}'
                        if key_name in hid_keys:
                            press_report = bytearray(8)
                            press_report[2] = hid_keys[key_name]
                            fd.write(bytes(press_report))
                            time.sleep(0.1)
                            
                            release_report = bytearray(8)
                            fd.write(bytes(release_report))
                            time.sleep(0.1)
                        
        except OSError as e:
            logging.error(f"メールアドレス入力中にHIDレポート送信エラー: {e}")
        except Exception as e:
            logging.error(f"メールアドレス入力中に予期せぬエラー: {e}")

    def held1(self, btn):
        global REMAP_ENABLED
        self.btn1.was_held = True
        if self.btn3.was_held:
            logging.info("ボタン1と3が長押しされました。プログラムを終了します。")
            self.btn1.combination_detected = True
            self.btn3.combination_detected = True
            asyncio.create_task(shutdown(self.loop))
        elif self.btn2.was_held:
            logging.info("ボタン1と2が長押しされました。メールアドレスを入力します。")
            self.btn1.combination_detected = True
            self.btn2.combination_detected = True
            self.send_email_address()
        else:
            REMAP_ENABLED = not REMAP_ENABLED
            state_text = "有効" if REMAP_ENABLED else "無効"
            logging.info(f"GPIOボタン1長押し: キーリマップを{state_text}にしました。")

    def released1(self, btn):
        # 同時押しが検出されていた場合は通常のキーイベントをスキップ
        if not self.btn1.was_held and not self.btn1.combination_detected: 
            self.pressed1(btn)
        # フラグをリセット
        self.btn1.was_held = False
        self.btn1.combination_detected = False

    def pressed1(self, btn): 
        logging.info("ボタン1が押されました。Alt+Aを送信後、Altキーでミーティングコントロールを復活させます。")
        self.send_key_combination(0x04, 0x04)

    def held2(self, btn):
        self.btn2.was_held = True
        if self.btn1.was_held:
            logging.info("ボタン1と2が長押しされました。メールアドレスを入力します。")
            self.btn1.combination_detected = True
            self.btn2.combination_detected = True
            self.send_email_address()

    def released2(self, btn):
        # 同時押しが検出されていた場合は通常のキーイベントをスキップ
        if not self.btn2.was_held and not self.btn2.combination_detected: 
            self.pressed2(btn)
        # フラグをリセット
        self.btn2.was_held = False
        self.btn2.combination_detected = False

    def pressed2(self, btn): 
        logging.info("ボタン2が押されました。Alt+Yを送信後、Altキーでミーティングコントロールを復活させます。")
        self.send_key_combination(0x04, 0x1c)

    def held3(self, btn):
        self.btn3.was_held = True
        if self.btn1.was_held:
            logging.info("ボタン1と3が長押しされました。プログラムを終了します。")
            self.btn1.combination_detected = True
            self.btn3.combination_detected = True
            asyncio.create_task(shutdown(self.loop))

    def released3(self, btn):
        # 同時押しが検出されていた場合は通常のキーイベントをスキップ
        if not self.btn3.was_held and not self.btn3.combination_detected: 
            self.pressed3(btn)
        # フラグをリセット
        self.btn3.was_held = False
        self.btn3.combination_detected = False

    def pressed3(self, btn): 
        logging.info("ボタン3が押されました。スペースキーを送信します。")
        self.send_key_combination(0x00, 0x2c)

async def shutdown(loop, signal=None):
    """プログラムの正常終了処理"""
    if signal: 
        logging.info(f"終了シグナル {signal.name} を受信...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks: 
        task.cancel()
    logging.info(f"{len(tasks)}個のタスクをキャンセルします。")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()
    logging.info("サービスは正常にシャットダウンしました。")

def handle_exception(loop, context):
    """未処理例外のハンドリング"""
    msg = context.get("exception", context["message"])
    logging.error(f"捕捉されなかった例外: {msg}", exc_info=context.get('exception'))
    asyncio.create_task(shutdown(loop=loop))

async def device_monitor(loop):
    """デバイスの動的監視とプロキシ管理"""
    # デバイス名のパターン定義
    MOUSE_DEVICE_NAME_PATTERN = re.compile(r'HHKB-Studio[1-4] Mouse|Logitech.*')
    KEYBOARD_DEVICE_NAME_PATTERN = re.compile(r'HHKB-Studio[1-4] Keyboard|HHKB-Hybrid.*|PFU.*')
    
    # HID出力パスの定義
    KEYBOARD_HID_OUTPUTS = [f'/dev/hidg{i}' for i in range(0, 1)]
    MOUSE_HID_OUTPUTS = [f'/dev/hidg{i}' for i in range(1, 3)]
    SCAN_INTERVAL = 5  # デバイススキャン間隔（秒）

    # 管理対象デバイスと利用可能HID出力の初期化
    managed_keyboards = {}
    managed_mice = {}
    available_keyboard_hids = set(KEYBOARD_HID_OUTPUTS)
    available_mouse_hids = set(MOUSE_HID_OUTPUTS)
    cached_device_paths = set()
    cached_devices = {}
    logging.info("マウスとキーボードの動的監視を開始します...")

    while True:
        try:
            # 終了したタスクのクリーンアップ
            reap_dead_tasks(managed_keyboards, available_keyboard_hids, "キーボード")
            reap_dead_tasks(managed_mice, available_mouse_hids, "マウス")
            
            # デバイス一覧の更新（変更があった場合のみ）
            current_device_paths = set(evdev.list_devices())
            if current_device_paths != cached_device_paths:
                cached_devices = {}
                for path in current_device_paths:
                    try:
                        cached_devices[path] = evdev.InputDevice(path)
                    except (OSError, PermissionError):
                        continue
                cached_device_paths = current_device_paths
            
            # デバイスの分類
            current_keyboards = {p: d for p, d in cached_devices.items() 
                               if KEYBOARD_DEVICE_NAME_PATTERN.match(d.name)}
            current_mice = {p: d for p, d in cached_devices.items() 
                           if MOUSE_DEVICE_NAME_PATTERN.match(d.name)}
            
            # デバイス接続の管理
            manage_device_connections(current_keyboards, managed_keyboards, available_keyboard_hids, KeyboardProxy, "キーボード", loop)
            manage_device_connections(current_mice, managed_mice, available_mouse_hids, MouseProxy, "マウス", loop)
            
        except Exception as e:
            logging.error(f"デバイス監視ループでエラー: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)

def reap_dead_tasks(managed_devices, available_hids, device_type_name):
    """終了したタスクをクリーンアップし、HID出力を解放"""
    dead_tasks_paths = [path for path, info in managed_devices.items() if info['task'].done()]
    for path in dead_tasks_paths:
        logging.info(f"終了した{device_type_name}タスクをクリーンアップします: {path}")
        info = managed_devices.pop(path)
        if info['task'].exception():
            logging.error(f"{device_type_name}タスク {path} が例外で終了: {info['task'].exception()}")
        available_hids.add(info['hid_output'])

def manage_device_connections(current_devices, managed_devices, available_hids, proxy_class, device_type_name, loop):
    """デバイスの接続/切断を管理し、プロキシタスクを作成/削除"""
    current_paths = set(current_devices.keys())
    managed_paths = set(managed_devices.keys())
    
    # 新しく接続されたデバイスの処理
    for path in (current_paths - managed_paths):
        if not available_hids:
            logging.warning(f"新しい{device_type_name} {path} が見つかりましたが、利用可能なHID出力がありません。")
            continue
        output_path = available_hids.pop()
        device = current_devices[path]
        logging.info(f"新しい{device_type_name}を検出: {path} ({device.name})。プロキシを開始 -> {output_path}")
        proxy = proxy_class(input_device_path=path, hid_output_path=output_path, loop=loop)
        task = asyncio.create_task(proxy.run())
        managed_devices[path] = {'task': task, 'hid_output': output_path}
    
    # 切断されたデバイスの処理
    for path in (managed_paths - current_paths):
        logging.info(f"{device_type_name} {path} が切断されました。クリーンアップします。")
        info = managed_devices.pop(path)
        info['task'].cancel()
        available_hids.add(info['hid_output'])
        logging.info(f"タスクをキャンセルし、HID出力 {info['hid_output']} を解放しました。")

if __name__ == "__main__":
    # 設定ファイルからログレベルを読み込み
    log_level = getattr(logging, CONFIG["logging"]["level"].upper(), logging.ERROR)
    logging.basicConfig(level=log_level, format='[%(asctime)s|%(name)s|%(levelname)s] %(message)s', stream=sys.stdout)
    logging.info(f"設定ファイルから読み込んだログレベル: {CONFIG['logging']['level']}")
    
    # asyncioイベントループの設定
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)
    
    # シグナルハンドラーの設定
    signals_to_handle = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for s in signals_to_handle:
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(loop, signal=s)))
    
    try:
        # GPIOボタンマネージャーとデバイス監視の開始
        keybow = KeyBowManager(loop)
        loop.create_task(device_monitor(loop))
        loop.run_forever()
    finally:
        loop.close()
        logging.info("イベントループを閉じました。")

