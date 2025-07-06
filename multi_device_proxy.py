#!/usr/bin/python3

import logging
import evdev
import time
import sys
import asyncio
import signal
import re
from enum import IntEnum
from evdev import InputDevice, ecodes, categorize
from hid_keys import hid_key_map as hid_keys

try:
    from gpiozero import Button
except ImportError:
    logging.warning("gpiozeroライブラリが見つかりません。GPIOボタン機能は無効になります。")
    class Button:
        def __init__(self, *args, **kwargs): pass
        def __getattr__(self, name): return lambda *args, **kwargs: None

class MouseIndex(IntEnum):
    TIP_SW, X_LSB, X_MSB, Y_LSB, Y_MSB, WHEEL_LSB, WHEEL_MSB, MAX = range(8)

class MouseProxy:
    def __init__(self, input_device_path, hid_output_path, loop):
        self.log = logging.getLogger(f"MouseProxy-{input_device_path.split('/')[-1]}")
        self.loop = loop
        self.input_device_path = input_device_path
        self.hid_output_path = hid_output_path
        self.device = None
        self.reset_state()

    def connect_device(self):
        try:
            self.device = InputDevice(self.input_device_path)
            self.device.grab()
            self.log.info(f"マウスを正常に捕捉: {self.device.path} ({self.device.name}) -> {self.hid_output_path}")
            return True
        except Exception as e:
            self.log.error(f"{self.input_device_path}への接続失敗: {e}")
            return False

    def reset_state(self):
        self.button_left = self.button_right = self.button_center = self.back = self.forward = 0
        self.movex = self.movey = self.scrolly = self.btn = 0

    async def run(self):
        while True:
            if not self.device and not self.connect_device():
                await asyncio.sleep(5)
                continue
            try:
                async for event in self.device.async_read_loop():
                    if event.type == ecodes.EV_KEY: self.handle_key_event(event)
                    elif event.type == ecodes.EV_REL: self.handle_rel_event(event)
                    elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                        self.update_state()
            except (OSError, asyncio.CancelledError) as e:
                self.log.error(f"マウス {self.input_device_path} 通信切断: {type(e).__name__}")
                if self.device: self.device.close()
                self.device = None
                break
            except Exception as e:
                self.log.error(f"予期せぬエラー: {e}", exc_info=True)
                break

    def handle_key_event(self, event):
        is_press = event.value == 1
        if event.code == ecodes.BTN_LEFT: self.button_left = 1 if is_press else 0
        elif event.code == ecodes.BTN_RIGHT: self.button_right = (1 << 1) if is_press else 0
        elif event.code == ecodes.BTN_MIDDLE: self.button_center = (1 << 2) if is_press else 0
        elif event.code == ecodes.BTN_SIDE: self.back = (1 << 3) if is_press else 0
        elif event.code == ecodes.BTN_EXTRA: self.forward = (1 << 4) if is_press else 0
        self.btn = self.button_left | self.button_right | self.button_center | self.back | self.forward

    def handle_rel_event(self, event):
        if event.code == ecodes.REL_X: self.movex = event.value
        elif event.code == ecodes.REL_Y: self.movey = event.value
        elif event.code == ecodes.REL_WHEEL: self.scrolly = event.value

    def update_state(self):
        data = bytearray(MouseIndex.MAX)
        data[MouseIndex.TIP_SW] = self.btn
        data[MouseIndex.X_LSB] = self.movex & 0xff
        data[MouseIndex.X_MSB] = (self.movex >> 8) & 0xff
        data[MouseIndex.Y_LSB] = self.movey & 0xff
        data[MouseIndex.Y_MSB] = (self.movey >> 8) & 0xff
        data[MouseIndex.WHEEL_LSB] = self.scrolly & 0xff
        data[MouseIndex.WHEEL_MSB] = (self.scrolly >> 8) & 0xff
        self.write_report(bytes(data))
        self.movex = self.movey = self.scrolly = 0

    def write_report(self, buffer):
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

class KeyboardProxy:
    def __init__(self, input_device_path, hid_output_path, loop):
        self.log = logging.getLogger(f"KeyboardProxy-{input_device_path.split('/')[-1]}")
        self.loop = loop
        self.input_device_path = input_device_path
        self.hid_output_path = hid_output_path
        self.device = None
        self.modifiers_map = {'KEY_LEFTCTRL': 0, 'KEY_LEFTSHIFT': 1, 'KEY_LEFTALT': 2, 'KEY_LEFTMETA': 3, 'KEY_RIGHTCTRL': 4, 'KEY_RIGHTSHIFT': 5, 'KEY_RIGHTALT': 6, 'KEY_RIGHTMETA': 7}
        self.reset_state()

    def connect_device(self):
        try:
            self.device = InputDevice(self.input_device_path)
            self.device.grab()
            self.log.info(f"キーボードを正常に捕捉: {self.device.path} ({self.device.name}) -> {self.hid_output_path}")
            return True
        except Exception as e:
            self.log.error(f"{self.input_device_path}への接続失敗: {e}")
            return False

    def reset_state(self):
        self.modifier = 0b00000000
        self.pressed_keys = set()
        self.isShiftUp = False
        self.isShiftDown = False
        self.Shiftbit = 0b00100010

    async def run(self):
        while True:
            if not self.device and not self.connect_device():
                await asyncio.sleep(5)
                continue
            try:
                async for event in self.device.async_read_loop():
                    if event.type != ecodes.EV_KEY: continue
                    data = categorize(event)
                    keycode = data.keycode
                    if isinstance(keycode, list): keycode = keycode[0]
                    keystate = data.keystate
                    if keycode in self.modifiers_map: self.update_modifier(keycode, keystate)
                    elif keystate == 0: self.release(keycode)
                    elif keystate == 1: self.press(keycode)
            except (OSError, asyncio.CancelledError) as e:
                self.log.error(f"キーボード {self.input_device_path} 通信切断: {type(e).__name__}")
                if self.device: self.device.close()
                self.device = None
                break
            except Exception as e:
                self.log.error(f"予期せぬエラー: {e}", exc_info=True)
                self.reset_state()

    def update_modifier(self, keycode, keystate):
        if keystate == 0: self.modifier &= ~(1 << self.modifiers_map[keycode])
        else: self.modifier |= (1 << self.modifiers_map[keycode])
        self.update_state()

    def release(self, keycode):
        if keycode in self.pressed_keys:
            self.pressed_keys.remove(keycode)
            self.update_state()

    def press(self, keycode):
        if keycode not in self.pressed_keys:
            self.pressed_keys.add(keycode)
            self.update_state()

    def remap(self, keycode):
        if keycode not in hid_keys: return 0
        if keycode == 'KEY_LEFTBRACE': keycode = 'KEY_RIGHTBRACE'
        elif keycode == 'KEY_RIGHTBRACE': keycode = 'KEY_BACKSLASH'
        elif self.modifier & self.Shiftbit:
            if keycode == 'KEY_7': keycode = 'KEY_6'
            elif keycode == 'KEY_8': keycode = 'KEY_APOSTROPHE'
            elif keycode == 'KEY_9': keycode = 'KEY_8'
            elif keycode == 'KEY_0': keycode = 'KEY_9'
            elif keycode == 'KEY_EQUAL': keycode = 'KEY_SEMICOLON'
            elif keycode == 'KEY_GRAVE': keycode = 'KEY_EQUAL'
            elif keycode == 'KEY_MINUS': keycode = 'KEY_RO'
            elif keycode == 'KEY_2': keycode = 'KEY_LEFTBRACE'; self.isShiftDown = True
            elif keycode == 'KEY_6': keycode = 'KEY_EQUAL'; self.isShiftDown = True
            elif keycode == 'KEY_BACKSLASH': keycode = 'KEY_YEN'
            elif keycode == 'KEY_SEMICOLON': keycode = 'KEY_APOSTROPHE'; self.isShiftDown = True
            elif keycode == 'KEY_APOSTROPHE': keycode = 'KEY_2'
        else:
            if keycode == 'KEY_APOSTROPHE': keycode = 'KEY_7'; self.isShiftUp = True
            elif keycode == 'KEY_GRAVE': keycode = 'KEY_LEFTBRACE'; self.isShiftUp = True
            elif keycode == 'KEY_EQUAL': keycode = 'KEY_MINUS'; self.isShiftUp = True
            elif keycode == 'KEY_BACKSLASH': keycode = 'KEY_RO'
        return hid_keys.get(keycode, 0)

    def update_state(self):
        self.isShiftUp = False
        self.isShiftDown = False
        report = bytearray(8)
        pressed_hid_codes = [self.remap(k) for k in self.pressed_keys]
        modifier = self.modifier
        if self.isShiftUp: modifier |= 0x02
        elif self.isShiftDown: modifier &= ~self.Shiftbit
        report[0] = modifier
        for i, code in enumerate(filter(None, pressed_hid_codes[:6])):
            report[2 + i] = code
        self.write_report(bytes(report))

    def write_report(self, report: bytes):
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
    def __init__(self, loop):
        self.loop = loop
        self.keyboard_hid_path = '/dev/hidg0'
        Button.was_held = False
        self.btn1 = Button(6, hold_time=3, bounce_time=0.05)
        self.btn1.when_held = self.held1
        self.btn1.when_released = self.released1
        self.btn2 = Button(22, hold_time=3, bounce_time=0.05)
        self.btn2.when_held = self.held2
        self.btn2.when_released = self.released2
        logging.info("KeyBow (GPIOボタン) マネージャーを初期化しました。")

    def send_key_combination(self, modifier_bits, key_code):
        try:
            press_report = bytearray(8)
            press_report[0] = modifier_bits
            press_report[2] = key_code
            
            release_report = bytearray(8)
            
            with open(self.keyboard_hid_path, 'rb+') as fd:
                fd.write(bytes(press_report))
                time.sleep(0.01)
                fd.write(bytes(release_report))
                
        except OSError as e:
            logging.error(f"HIDレポート送信エラー {self.keyboard_hid_path}: {e}")
        except Exception as e:
            logging.error(f"予期せぬエラー: {e}")

    def held1(self, btn):
        self.btn1.was_held = True
        if self.btn2.was_held:
            logging.info("両方のボタンが長押しされました。プログラムを終了します。")
            asyncio.create_task(shutdown(self.loop))

    def released1(self, btn):
        if not self.btn1.was_held: self.pressed1(btn)
        self.btn1.was_held = False

    def pressed1(self, btn): 
        logging.info("ボタン1が押されました。Alt+Aを送信します。")
        self.send_key_combination(0x04, 0x04)

    def held2(self, btn):
        self.btn2.was_held = True
        if self.btn1.was_held:
            logging.info("両方のボタンが長押しされました。プログラムを終了します。")
            asyncio.create_task(shutdown(self.loop))

    def released2(self, btn):
        if not self.btn2.was_held: self.pressed2(btn)
        self.btn2.was_held = False

    def pressed2(self, btn): 
        logging.info("ボタン2が押されました。Alt+Yを送信します。")
        self.send_key_combination(0x04, 0x1c)

async def shutdown(loop, signal=None):
    if signal: logging.info(f"終了シグナル {signal.name} を受信...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks: task.cancel()
    logging.info(f"{len(tasks)}個のタスクをキャンセルします。")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()
    logging.info("サービスは正常にシャットダウンしました。")

def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logging.error(f"捕捉されなかった例外: {msg}", exc_info=context.get('exception'))
    asyncio.create_task(shutdown(loop=loop))

async def device_monitor(loop):
    MOUSE_DEVICENAME_PATTERN = 'HHKB-Studio4 Mouse|Logitech.*'
    KEYBOARD_DEVICENAME_PATTERN = 'HHKB-Studio4 Keyboard|HHKB-Hybrid.*|PFU.*'
    KEYBOARD_HID_OUTPUTS = [f'/dev/hidg{i}' for i in range(0, 1)]
    MOUSE_HID_OUTPUTS = [f'/dev/hidg{i}' for i in range(1, 3)]
    SCAN_INTERVAL = 5

    managed_keyboards = {}
    managed_mice = {}
    available_keyboard_hids = set(KEYBOARD_HID_OUTPUTS)
    available_mouse_hids = set(MOUSE_HID_OUTPUTS)
    logging.info("マウスとキーボードの動的監視を開始します...")

    while True:
        try:
            reap_dead_tasks(managed_keyboards, available_keyboard_hids, "キーボード")
            reap_dead_tasks(managed_mice, available_mouse_hids, "マウス")
            all_devices = {dev.path: dev for dev in [evdev.InputDevice(path) for path in evdev.list_devices()]}
            current_keyboards = {p: d for p, d in all_devices.items() if re.match(KEYBOARD_DEVICENAME_PATTERN, d.name)}
            current_mice = {p: d for p, d in all_devices.items() if re.match(MOUSE_DEVICENAME_PATTERN, d.name)}
            manage_device_connections(current_keyboards, managed_keyboards, available_keyboard_hids, KeyboardProxy, "キーボード", loop)
            manage_device_connections(current_mice, managed_mice, available_mouse_hids, MouseProxy, "マウス", loop)
        except Exception as e:
            logging.error(f"デバイス監視ループでエラー: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)

def reap_dead_tasks(managed_devices, available_hids, device_type_name):
    dead_tasks_paths = [path for path, info in managed_devices.items() if info['task'].done()]
    for path in dead_tasks_paths:
        logging.info(f"終了した{device_type_name}タスクをクリーンアップします: {path}")
        info = managed_devices.pop(path)
        if info['task'].exception():
            logging.error(f"{device_type_name}タスク {path} が例外で終了: {info['task'].exception()}")
        available_hids.add(info['hid_output'])

def manage_device_connections(current_devices, managed_devices, available_hids, proxy_class, device_type_name, loop):
    current_paths = set(current_devices.keys())
    managed_paths = set(managed_devices.keys())
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
    for path in (managed_paths - current_paths):
        logging.info(f"{device_type_name} {path} が切断されました。クリーンアップします。")
        info = managed_devices.pop(path)
        info['task'].cancel()
        available_hids.add(info['hid_output'])
        logging.info(f"タスクをキャンセルし、HID出力 {info['hid_output']} を解放しました。")

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR, format='[%(asctime)s|%(name)s|%(levelname)s] %(message)s', stream=sys.stdout)
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)
    signals_to_handle = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for s in signals_to_handle:
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(loop, signal=s)))
    try:
        keybow = KeyBowManager(loop)
        loop.create_task(device_monitor(loop))
        loop.run_forever()
    finally:
        loop.close()
        logging.info("イベントループを閉じました。")

