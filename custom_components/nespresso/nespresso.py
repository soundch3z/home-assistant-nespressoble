import binascii
import ctypes
import logging
import struct
import time
from collections import namedtuple
from datetime import datetime
from uuid import UUID

import pygatt
from pygatt.exceptions import BLEError, NotConnectedError, NotificationTimeout

_LOGGER = logging.getLogger(__name__)

# Use full UUID since we do not use UUID from bluepy.btle
CHAR_UUID_DEVICE_NAME = UUID('00002a00-0000-1000-8000-00805f9b34fb')
CHAR_UUID_MANUFACTURER_NAME = UUID('00002a00-0000-1000-8000-00805f9b34fb')
CHAR_UUID_STATE = UUID('06aa3a12-f22a-11e3-9daa-0002a5d5c51b')
CHAR_UUID_NBCAPS = UUID('06aa3a15-f22a-11e3-9daa-0002a5d5c51b')
CHAR_UUID_SLIDER = UUID('06aa3a22-f22a-11e3-9daa-0002a5d5c51b')
CHAR_UUID_WATER_HARDNESS = UUID('06aa3a44-f22a-11e3-9daa-0002a5d5c51b')
CHAR_UUID_AUTH = UUID('06aa3a41-f22a-11e3-9daa-0002a5d5c51b')

Characteristic = namedtuple('Characteristic', ['uuid', 'name', 'format'])

c_uint8 = ctypes.c_uint8

manufacturer_characteristics = Characteristic(CHAR_UUID_MANUFACTURER_NAME, 'manufacturer', "utf-8")
device_info_characteristics = [manufacturer_characteristics,
                               Characteristic(CHAR_UUID_DEVICE_NAME, 'device_name', "utf-8")]

class Flags_bits( ctypes.LittleEndianStructure ):
     _fields_ = [
                 ("bit0",     c_uint8, 1 ),  # asByte & 1
                 ("bit1",     c_uint8, 1 ),  # asByte & 2
                 ("bit2",     c_uint8, 1 ),  # asByte & 4
                 ("bit3",     c_uint8, 1 ),  # asByte & 8
                 ("bit4",     c_uint8, 1 ),  # asByte & 16
                 ("bit5",     c_uint8, 1 ),  # asByte & 32
                 ("bit6",     c_uint8, 1 ),  # asByte & 64
                 ("bit7",     c_uint8, 1 ),  # asByte & 128
                ]

class Flags( ctypes.Union ):
     _anonymous_ = ("bit",)
     _fields_ = [
                 ("bit",    Flags_bits ),
                 ("asByte", c_uint8    )
                ]

class NespressoDeviceInfo:
    def __init__(self, manufacturer='', serial_nr='', model_nr='', device_name=''):
        self.manufacturer = manufacturer
        self.serial_nr = serial_nr
        self.model_nr = model_nr
        self.device_name = device_name

    def __str__(self):
        return "Manufacturer: {} Model: {} Serial: {} Device:{}".format(
            self.manufacturer, self.model_nr, self.serial_nr, self.device_name)


BYTE = Flags()
sensors_characteristics_uuid = [CHAR_UUID_STATE, CHAR_UUID_NBCAPS, CHAR_UUID_SLIDER, CHAR_UUID_WATER_HARDNESS]

sensors_characteristics_uuid_str = [str(x) for x in sensors_characteristics_uuid]


class BaseDecode:
    def __init__(self, name, format_type):
        self.name = name
        self.format_type = format_type

    def decode_data(self, raw_data):
        #val = struct.unpack(self.format_type,raw_data)
        val = raw_data
        if self.format_type == "caps_number":
            res = int.from_bytes(val,byteorder='big')
        elif self.format_type == "water_hardness":
            #BYTE.asByte = val[0]
            #res = BYTE.bit1
            res = val
        elif self.format_type == "slider":
            res = binascii.hexlify(val)
            if (res) == b'00':
                res = 0
            elif (res) == b'02':
                res = 1
            else :
                res = "N/A"
        elif self.format_type == "state":
            BYTE0 = Flags()
            BYTE1 = Flags()
            BYTE2 = Flags()
            BYTE3 = Flags()
            
            BYTE0.asByte = val[0]
            BYTE1.asByte = val[1]
            # TODO error counter
            BYTE2.asByte = val[2]
            BYTE3.asByte = val[3]
            return {"water_is_empty":BYTE0.bit0,
                    "descaled_needed":BYTE0.bit3,
                    "capsule_mechanism_jammed":BYTE0.bit4,
                    "always_1":BYTE0.bit6,
                    "water_temp_low":BYTE1.bit0,
                    "awake":BYTE1.bit1,
                    "water_engadged":BYTE1.bit2,
                    "sleeping":BYTE1.bit3,
                    "tray_sensor_during_brewing":BYTE1.bit4,
                    "tray_open_tray_sensor_full":BYTE1.bit6,
                    "capsule_engaged":BYTE1.bit7,
                    "Fault":BYTE3.bit5
                    }
        else:
            _LOGGER.debug("state_decoder else")
            res = val
        return {self.name:res}
 
sensor_decoders = {str(CHAR_UUID_STATE):BaseDecode(name="state", format_type='state'),
                   str(CHAR_UUID_NBCAPS):BaseDecode(name="caps_number", format_type='caps_number'),
                   str(CHAR_UUID_SLIDER):BaseDecode(name="slider", format_type='slider'),
                   str(CHAR_UUID_WATER_HARDNESS):BaseDecode(name="water_hardness", format_type='water_hardness'),}

class NespressoDetect:
    def __init__(self, scan_interval, AUTH_CODE=None, mac=None):
        self.adapter = pygatt.backends.GATTToolBackend()
        self.nespresso_devices = [] if mac is None else [mac]
        self.auth_code = AUTH_CODE
        self.sensors = []
        self.sensordata = {}
        self.scan_interval = scan_interval
        self.last_scan = -1

    def find_devices(self):
        # Scan for devices and try to figure out if it is an Nespresso device.
        self.adapter.start(reset_on_start=False)
        devices = self.adapter.scan(timeout=3)
        self.adapter.stop()

        for device in devices:
            mac = device['address']
            _LOGGER.debug("connecting to {}".format(mac))
            try:
                self.adapter.start(reset_on_start=False)
                dev = self.adapter.connect(mac, address_type=pygatt.BLEAddressType.random)
                _LOGGER.debug("Connected")
                try:
                    data = dev.char_read(manufacturer_characteristics.uuid)
                    manufacturer_name = data.decode(manufacturer_characteristics.format)
                    if "prodigio" in manufacturer_name.lower():
                        self.nespresso_devices.append(mac)
                except (BLEError, NotConnectedError, NotificationTimeout):
                    _LOGGER.debug("connection to {} failed".format(mac))
                finally:
                    dev.disconnect()
            except (BLEError, NotConnectedError, NotificationTimeout):
                _LOGGER.debug("Faild to connect")
            finally:
                self.adapter.stop()

        _LOGGER.debug("Found {} Nespresso devices".format(len(self.nespresso_devices)))
        return len(self.nespresso_devices)

    def get_info(self):
        # Try to get some info from the discovered Nespresso devices
        self.devices = {}

        for mac in self.nespresso_devices:
            device = NespressoDeviceInfo(serial_nr=mac)
            try:
                self.adapter.start(reset_on_start=False)
                dev = self.adapter.connect(mac, address_type=pygatt.BLEAddressType.random)
                for characteristic in device_info_characteristics:
                    try:
                        data = dev.char_read(characteristic.uuid)
                        setattr(device, characteristic.name, data.decode(characteristic.format))
                    except (BLEError, NotConnectedError, NotificationTimeout):
                        _LOGGER.exception("")
                dev.disconnect()
            except (BLEError, NotConnectedError, NotificationTimeout):
                _LOGGER.exception("")
            self.adapter.stop()
            self.devices[mac] = device

        return self.devices

    def get_sensors(self):
        self.sensors = {}
        for mac in self.nespresso_devices:
            try:
                self.adapter.start(reset_on_start=False)
                dev = self.adapter.connect(mac, address_type=pygatt.BLEAddressType.random)
                characteristics = dev.discover_characteristics()
                sensor_characteristics =  []
                for characteristic in characteristics.values():
                    _LOGGER.debug(characteristic)
                    if characteristic.uuid in sensors_characteristics_uuid_str:
                        sensor_characteristics.append(characteristic)
                self.sensors[mac] = sensor_characteristics
            except (BLEError, NotConnectedError, NotificationTimeout):
                _LOGGER.exception("Failed to discover sensors")

        return self.sensors

    def connectnespresso(self,device,tries=0):
        try:
            #Write the auth code from android or Ios apps to the specific UUID to allow catching value from the machine
            device.char_write(CHAR_UUID_AUTH, binascii.unhexlify(self.auth_code), wait_for_response=True)
        except Exception as error:
            print("Writing error")
            time.sleep(5) # wait 5s
            if tries < 3:
                print ("<3 write error")
                self.connectnespresso(device, tries+1) #retry
            else:
                print (">5 write error")
                raise error
            
    def get_sensor_data(self):
        if time.monotonic() - self.last_scan > self.scan_interval:
            self.last_scan = time.monotonic()
            for mac, characteristics in self.sensors.items():
                try:
                    self.adapter.start(reset_on_start=False)
                    dev = self.adapter.connect(mac, address_type=pygatt.BLEAddressType.random)
                    self.connectnespresso(dev)
                    for characteristic in characteristics:
                        _LOGGER.debug("characteristic {}".format(characteristic))
                        try:
                            data = dev.char_read_handle("0x{:04x}".format(characteristic.handle))
                            if characteristic.uuid in sensor_decoders:
                                _LOGGER.debug("{} data {}".format(characteristic.uuid, data))
                                sensor_data = sensor_decoders[characteristic.uuid].decode_data(data)
                                #sensor_data = str(data)
                                _LOGGER.debug("{} Got sensordata {}".format(mac, sensor_data))
                                if self.sensordata.get(mac) is None:
                                    self.sensordata[mac] = sensor_data
                                else:
                                    self.sensordata[mac].update(sensor_data)
                        except (BLEError, NotConnectedError, NotificationTimeout):
                            _LOGGER.exception("Failed to read characteristic")

                    dev.disconnect()
                except (BLEError, NotConnectedError, NotificationTimeout):
                    _LOGGER.exception("Failed to connect")
                self.adapter.stop()

        return self.sensordata

if __name__ == "__main__":
    logging.basicConfig()
    _LOGGER.setLevel(logging.INFO)
    ad = NespressoDetect(180)
    num_dev_found = ad.find_devices()
    if num_dev_found > 0:
        devices = ad.get_info()
        for mac, dev in devices.items():
            _LOGGER.info("{}: {}".format(mac, dev))

        devices_sensors = ad.get_sensors()
        for mac, sensors in devices_sensors.items():
            for sensor in sensors:
                _LOGGER.info("{}: {}".format(mac, sensor))

        sensordata = ad.get_sensor_data()
        for mac, data in sensordata.items():
            for name, val in data.items():
                _LOGGER.info("{}: {}: {}".format(mac, name, val))
