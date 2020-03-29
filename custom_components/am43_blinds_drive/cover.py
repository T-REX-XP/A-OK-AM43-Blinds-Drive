"""
 Copyright 2020 T-REX-XP
"""
from bluepy import btle
import os
import datetime
from retrying import retry
import json
import logging
import voluptuous as vol

from homeassistant.components.cover import (CoverDevice, ENTITY_ID_FORMAT, PLATFORM_SCHEMA, SUPPORT_OPEN, SUPPORT_CLOSE,
                                            SUPPORT_STOP, SUPPORT_SET_POSITION)
from homeassistant.const import (CONF_NAME, CONF_MAC, CONF_DEVICE, CONF_FRIENDLY_NAME, CONF_COVERS, STATE_CLOSED,
                                 STATE_OPEN, STATE_UNKNOWN)
import homeassistant.helpers.config_validation as cv

REQUIREMENTS = ['retrying==1.3.3']

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'am43blinds'

STATE_CLOSING = 'closing'
STATE_OFFLINE = 'offline'
STATE_OPENING = 'opening'
STATE_STOPPED = 'stopped'

# AM43 Notification Identifiers
# Msg format: 9a <id> <len> <data * len> <xor csum>
IdMove = 0x0d  # not used in code yet
IdStop = 0x0a
IdBattery = 0xa2
IdLight = 0xaa
IdPosition = 0xa7
IdPosition2 = 0xa8  # not used in code yet
IdPosition3 = 0xa9  # not used in code yet
BatteryPct = None
LightPct = None
PositionPct = None

DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 3

COVER_SCHEMA = vol.Schema({
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_FRIENDLY_NAME, default=DEFAULT_NAME): cv.string
})
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_COVERS): vol.Schema({cv.slug: COVER_SCHEMA}),
})


class AM43Delegate(btle.DefaultDelegate):
    def __init__(self):
        btle.DefaultDelegate.__init__(self)

    def handleNotification(self, cHandle, data):
        if (data[1] == IdBattery):
            global BatteryPct
            # print("Battery: " + str(data[7]) + "%")
            BatteryPct = data[7]
        elif (data[1] == IdPosition):
            global PositionPct
            # print("Position: " + str(data[5]) + "%")
            PositionPct = data[5]
        elif (data[1] == IdLight):
            global LightPct
            # print("Light: " + str(data[3]) + "%")
            LightPct = data[3]
        else:
            _LOGGER.error("Unknown identifier notification recieved: " + str(data[1:2]))


# Constructs message and write to blind controller
def write_message(characteristic, dev, id, data, bWaitForNotifications):
    ret = False

    # Construct message
    msg = bytearray({0x9a})
    msg += bytearray({id})
    msg += bytearray({len(data)})
    msg += bytearray(data)

    # Calculate checksum (xor)
    csum = 0
    for x in msg:
        csum = csum ^ x
    msg += bytearray({csum})

    # print("".join("{:02x} ".format(x) for x in msg))

    if (characteristic):
        result = characteristic.write(msg)
        if (result["rsp"][0] == "wr"):
            ret = True
            if (bWaitForNotifications):
                if (dev.waitForNotifications(10)):
                    # print(datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S") + " -->  BTLE Notification recieved", flush=True)
                    pass
    return ret


@retry(stop_max_attempt_number=2, wait_fixed=2000)
def ScanForBTLEDevices():
    scanner = btle.Scanner()
    print(datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S") + " Scanning for bluetooth devices....", flush=True)
    devices = scanner.scan()

    bAllDevicesFound = True
    for AM43BlindsDevice in config['AM43_BLE_Devices']:
        AM43BlindsDeviceMacAddress = config.get('AM43_BLE_Devices', AM43BlindsDevice)  # Read BLE MAC from ini file

        bFound = False
        for dev in devices:
            if AM43BlindsDeviceMacAddress == dev.addr:
                print(datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S") + " Found " + AM43BlindsDeviceMacAddress,
                      flush=True)
                bFound = True
                break
            # else:
            # bFound = False
        if bFound == False:
            print(datetime.datetime.now().strftime(
                "%d-%m-%Y %H:%M:%S") + " " + AM43BlindsDeviceMacAddress + " not found on BTLE network!", flush=True)
            bAllDevicesFound = False

    if (bAllDevicesFound == True):
        print(datetime.datetime.now().strftime(
            "%d-%m-%Y %H:%M:%S") + " Every AM43 Blinds Controller is found on BTLE network", flush=True)
        return
    else:
        print(datetime.datetime.now().strftime(
            "%d-%m-%Y %H:%M:%S") + " Not all AM43 Blinds Controllers are found on BTLE network, restarting bluetooth stack and checking again....",
              flush=True)
        os.system("service bluetooth restart")
        raise ValueError(datetime.datetime.now().strftime(
            "%d-%m-%Y %H:%M:%S") + " Not all AM43 Blinds Controllers are found on BTLE network, restarting bluetooth stack and check again....")


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def ConnectBTLEDevice(AM43BlindsDeviceMacAddress, name):
    try:
        _LOGGER.debug(datetime.datetime.now().strftime(
            "%d-%m-%Y %H:%M:%S") + " Connecting to " + name + ": " + AM43BlindsDeviceMacAddress + "...")
        dev = btle.Peripheral(AM43BlindsDeviceMacAddress)
        return dev
    except:
        raise ValueError(" Cannot connect to " + AM43BlindsDeviceMacAddress + " trying again....")


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the AM43 covers."""
    covers = []
    devices = config.get(CONF_COVERS)
    for object_id, device_config in devices.items():
        dev_name = device_config.get(CONF_FRIENDLY_NAME, object_id)
        dev_mac = device_config.get(CONF_MAC, object_id)
        try:
            args = {
                CONF_FRIENDLY_NAME: dev_name,
                CONF_MAC: dev_mac,
                CONF_DEVICE: ConnectBTLEDevice(dev_mac, dev_name)
            }
            covers.append(AM43BlindsCover(hass, args, object_id))
        except:
            continue
    add_devices(covers, True)


class AM43BlindsCover(CoverDevice):
    """Representation of AM43BlindsCover cover."""
    # Reset variables
    bSuccess = False

    # pylint: disable=no-self-use
    def __init__(self, hass, args, object_id):
        """Initialize the cover."""
        self.hass = hass
        self.entity_id = ENTITY_ID_FORMAT.format(object_id)
        self._name = args[CONF_FRIENDLY_NAME]
        self._available = True
        self._state = None
        self._mac = args[CONF_MAC]
        self._device = args[CONF_DEVICE]
        self.battery_level = 100
        self._blindsControlService = self._device.getServiceByUUID("fe50")
        self._blindCharacteristics = self._blindsControlService.getCharacteristics("fe51")[0]
        self.update(self)

    def initBleServices(self):
        if self._device is None:
            self._device = ConnectBTLEDevice(self._mac, self._name)

        if self._blindsControlService is None:
            self._blindsControlService = self._device.getServiceByUUID("fe50")

        if self._blindCharacteristics is None:
            self._blindCharacteristics = self._blindsControlService.getCharacteristics("fe51")[0]

    def update(self):
        _LOGGER.debug("in update..." + self._name)
        self.initBleServices(self)
        bSuccess = self._device.setDelegate(AM43Delegate())
        bSuccess = write_message(self._blindCharacteristics, self._device, IdBattery, [0x01], True)
        bSuccess = write_message(self._blindCharacteristics, self._device, IdLight, [0x01], True)
        bSuccess = write_message(self._blindCharacteristics, self._device, IdPosition, [0x01], True)
        # retrieve global variables with current percentages
        global BatteryPct
        global LightPct
        global PositionPct
        _LOGGER.debug("Battery level: " + str(BatteryPct) + "%, " + "Blinds position: " + str(
            PositionPct) + "%, " + "Light sensor level: " + str(LightPct) + "%")
        self.battery_level = BatteryPct
        # ResultDict.update({AM43BlindsDevice.capitalize(): [{"battery": BatteryPct,"position": PositionPct,"light": LightPct,"macaddr": AM43BlindsDeviceMacAddress}]})

    @property
    def current_cover_position(self):
        global PositionPct
        return PositionPct

    @property
    def name(self):
        """Return the name of the cover."""
        return self._name

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        if self._state in [STATE_UNKNOWN, STATE_OFFLINE]:
            return None
        return self._state in [STATE_CLOSED, STATE_OPENING]

    @property
    def close_cover(self):
        """Close the cover."""
        self.initBleServices(self)
        bSuccess = write_message(self._blindCharacteristics, self._device, IdMove, [100], False)
        if (bSuccess):
            _LOGGER.debug("Writing Close" " to " + self._name + " : " + self._mac + " was succesfull!")
            self._state = STATE_CLOSED
            self.update(self)
        else:
            _LOGGER.error("Writing to Close" + self._name + " : " + self._mac + " FAILED")

    def open_cover(self):
        """Open the cover."""
        self.initBleServices(self)
        bSuccess = write_message(self._blindCharacteristics, self._device, IdMove, [0], False)

        if (bSuccess):
            _LOGGER.debug("Writing Open" " to " + self._name + " : " + self._mac + " was succesfull!")
            self._state = STATE_OPEN
            self.update(self)
        else:
            _LOGGER.error("Writing to Open" + self._name + " : " + self._mac + " FAILED")

    def stop_cover(self):
        """Stop the cover."""
        self.initBleServices(self)
        bSuccess = write_message(self._blindCharacteristics, self._device, IdStop, [0xcc], False)

        if (bSuccess):
            _LOGGER.debug("Writing STOP to " + self._name + " : " + self._mac + " was succesfull!")
            self.update(self)
        else:
            _LOGGER.error("Writing STOP to " + self._name + " : " + self._mac + " FAILED")

    def set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        self.initBleServices(self)
        bSuccess = write_message(self._blindCharacteristics, self._device, IdMove, [int(kwargs['position'])], False)
        if (bSuccess):
            _LOGGER.debug("Writing Set position to " + self._name + " : " + self._mac + " - " + kwargs[
                'position'] + " was succesfull!")
            self.update(self)
        else:
            _LOGGER.error(
                "Writing Set position to " + self._name + " : " + self._mac + " - " + kwargs['position'] + " FAILED")

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return 'blind'

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_SET_POSITION | SUPPORT_STOP
