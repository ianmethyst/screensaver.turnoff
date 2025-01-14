# -*- coding: utf-8 -*-
# Copyright: (c) 2019, Dag Wieers (@dagwieers) <dag@wieers.com>
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
''' This Kodi addon turns off display devices when Kodi goes into screensaver-mode '''

from __future__ import absolute_import, division, unicode_literals
import sys
# import atexit
import subprocess

from xbmc import executebuiltin, executeJSONRPC, log as xlog, Monitor
from xbmcaddon import Addon
from xbmcgui import Dialog, WindowXMLDialog

# NOTE: The below order relates to resources/settings.xml
DISPLAY_METHODS = [
    dict(name='do-nothing', title='Do nothing',
         function='log',
         args_off=[1, 'Do nothing to power off display'],
         args_on=[1, 'Do nothing to power back on display']),
    dict(name='cec-builtin', title='CEC (buil-in)',
         function='run_builtin',
         args_off=['CECStandby'],
         args_on=['CECActivateSource']),
    dict(name='no-signal-rpi', title='No Signal on Raspberry Pi (using vcgencmd)',
         function='run_command',
         args_off=['vcgencmd', 'display_power', '0'],
         args_on=['vcgencmd', 'display_power', '1']),
    dict(name='dpms-builtin', title='DPMS (built-in)',
         function='run_builtin',
         args_off=['ToggleDPMS'],
         args_on=['ToggleDPMS']),
    dict(name='dpms-xset', title='DPMS (using xset)',
         function='run_command',
         args_off=['xset', 'dpms', 'force', 'off'],
         args_on=['xset', 'dpms', 'force', 'on']),
    dict(name='dpms-vbetool', title='DPMS (using vbetool)',
         function='run_command',
         args_off=['vbetool', 'dpms', 'off'],
         args_on=['vbetool', 'dpms', 'on']),
    # TODO: This needs more outside testing
    dict(name='dpms-xrandr', title='DPMS (using xrandr)',
         function='run_command',
         args_off=['xrandr', '--output CRT-0', 'off'],
         args_on=['xrandr', '--output CRT-0', 'on']),
    # TODO: This needs more outside testing
    dict(name='cec-android', title='CEC on Android (kernel)',
         function='run_command',
         args_off=['su', '-c', 'echo 0 >/sys/devices/virtual/graphics/fb0/cec'],
         args_on=['su', '-c', 'echo 1 >/sys/devices/virtual/graphics/fb0/cec']),
    # NOTE: Contrary to what one might think, 1 means off and 0 means on
    dict(name='backlight-rpi', title='Backlight on Raspberry Pi (kernel)',
         function='run_command',
         args_off=['su', '-c', 'echo 1 >/sys/class/backlight/rpi_backlight/bl_power'],
         args_on=['su', '-c', 'echo 0 >/sys/class/backlight/rpi_backlight/bl_power']),
    # NOTE: Fails to come back on RPIv3
    dict(name='tvservice-rpi', title='HDMI on Raspberry Pi (tvservice)',
         function='run_command',
         args_off=['tvservice', '-o'],
         args_on=['tvservice', '-p']),
]

POWER_METHODS = [
    dict(name='do-nothing', title='Do nothing',
         function='log', args=[1, 'Do nothing to power off system']),
    dict(name='suspend-builtin', title='Suspend (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Suspend')),
    dict(name='hibernate-builtin', title='Hibernate (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Hibernate')),
    dict(name='quit-builtin', title='Quit (built-in)',
         function='jsonrpc', kwargs_off=dict(method='Application.Quit')),
    dict(name='shutdown-builtin', title='ShutDown action (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Shutdown')),
    dict(name='reboot-builtin', title='Reboot (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Reboot')),
    dict(name='powerdown-builtin', title='Powerdown (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Powerdown')),
]


class SafeDict(dict):
    ''' A safe dictionary implementation that does not break down on missing keys '''
    def __missing__(self, key):
        ''' Replace missing keys with the original placeholder '''
        return '{' + key + '}'


def from_unicode(text, encoding='utf-8'):
    ''' Force unicode to text '''
    if sys.version_info.major == 2 and isinstance(text, unicode):  # noqa: F821; pylint: disable=undefined-variable
        return text.encode(encoding)
    return text


def to_unicode(text, encoding='utf-8'):
    ''' Force text to unicode '''
    return text.decode(encoding) if isinstance(text, bytes) else text


def log(level=1, msg='', **kwargs):
    ''' Log info messages to Kodi '''
    if not DEBUG_LOGGING and not (level <= MAX_LOG_LEVEL or MAX_LOG_LEVEL == 0):
        return
    from string import Formatter
    if kwargs:
        msg = Formatter().vformat(msg, (), SafeDict(**kwargs))
    msg = '[{addon}] {msg}'.format(addon=ADDON_ID, msg=msg)
    xlog(from_unicode(msg), level % 3 if DEBUG_LOGGING else 2)


def log_error(msg, **kwargs):
    ''' Log error messages to Kodi '''
    from string import Formatter
    if kwargs:
        msg = Formatter().vformat(msg, (), SafeDict(**kwargs))
    msg = '[{addon}] {msg}'.format(addon=ADDON_ID, msg=msg)
    xlog(from_unicode(msg), 4)


def jsonrpc(**kwargs):
    ''' Perform JSONRPC calls '''
    from json import dumps, loads
    if 'id' not in kwargs:
        kwargs.update(id=1)
    if 'jsonrpc' not in kwargs:
        kwargs.update(jsonrpc='2.0')
    result = loads(executeJSONRPC(dumps(kwargs)))
    log(3, msg="Sending JSON-RPC payload: '{payload}' returns '{result}'", payload=kwargs, result=result)
    return result


def popup(heading='', msg='', delay=10000, icon=''):
    ''' Bring up a pop-up with a meaningful error '''
    if not heading:
        heading = 'Addon {addon} failed'.format(addon=ADDON_ID)
    if not icon:
        icon = ADDON_ICON
    Dialog().notification(heading, msg, icon, delay)


def set_mute(toggle=True):
    ''' Set mute using Kodi JSON-RPC interface '''
    toggle = 'true' if toggle else 'false'
    result = jsonrpc(method='Application.SetMute', params=dict(mute=toggle))
#    if '"result":'+toggle not in result:
#        log_error(msg="Error in JSON-RPC: '{payload}' returns '{result}'", payload=payload, result=result)
#        popup(msg="Error in JSON-RPC Application.SetMute: '%s'" % result)
    return result


def activate_window(window='home'):
    ''' Set mute using Kodi JSON-RPC interface '''
#    result = jsonrpc(method='GUI.ActivateWindow', params=dict(window=window, parameters=['Home']))
    result = jsonrpc(method='GUI.ActivateWindow', params=dict(window=window, parameters=[]))
#    if '"result":"OK"' not in result:
#        log_error(msg="Error in JSON-RPC: '{payload}' returns '{result}'", payload=payload, result=result)
#        popup(msg="Error in JSON-RPC GUI.ActivateWindow: '%s'" % result)
    return result


def run_builtin(builtin):
    ''' Run Kodi builtins while catching exceptions '''
    log(2, msg="Executing builtin '{builtin}'", builtin=builtin)
    try:
        executebuiltin(builtin, True)
    except Exception as exc:  # pylint: disable=broad-except
        log_error(msg="Exception executing builtin '{builtin}': {exc}", builtin=builtin, exc=exc)
        popup(msg="Exception executing builtin '%s': %s" % (builtin, exc))


def run_command(*command, **kwargs):
    ''' Run commands on the OS while catching exceptions '''
    # TODO: Add options for running using su or sudo
    try:
        cmd = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        (out, err) = cmd.communicate()
        if cmd.returncode == 0:
            log(2, msg="Running command '{command}' returned rc={rc}", command=' '.join(command), rc=cmd.returncode)
        else:
            log_error(msg="Running command '{command}' failed with rc={rc}", command=' '.join(command), rc=cmd.returncode)
            if err:
                log_error(msg="Command '{command}' returned on stderr: {stderr}", command=command[0], stderr=err)
            if out:
                log_error(msg="Command '{command}' returned on stdout: {stdout} ", command=command[0], stdout=out)
            popup(msg="%s\n%s" % (out, err))
            sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        log_error(msg="Exception running '{command}': {exc}", command=command[0], exc=exc)
        popup(msg="Exception running '%s': %s" % (command[0], exc))
        sys.exit(2)


def func(function, *args, **kwargs):
    ''' Execute a global function with arguments '''
    return globals()[function](*args, **kwargs)


class TurnOffMonitor(Monitor, object):
    ''' This is the monitor to exit TurnOffScreensaver '''

    def __init__(self, **kwargs):
        ''' Initialize monitor '''
        self.action = kwargs.get('action')
        super(TurnOffMonitor, self).__init__()

    def onScreensaverDeactivated(self):  # pylint: disable=invalid-name
        ''' Perform cleanup function '''
        self.action()


class TurnOffDialog(WindowXMLDialog, object):
    ''' The TurnOffScreensaver class managing the XML gui '''

    def __init__(self, *args):
        ''' Initialize dialog '''
        self.display = None
        self.monitor = None
        self.mute = None
        self.power = None
        super(TurnOffDialog, self).__init__(*args)

    def onInit(self):  # pylint: disable=invalid-name
        ''' Perform this when the screensaver is started '''
        display_method = int(ADDON.getSetting('display_method'))
        power_method = int(ADDON.getSetting('power_method'))

        self.display = DISPLAY_METHODS[display_method]
        self.mute = to_unicode(ADDON.getSetting('mute'))
        self.power = POWER_METHODS[power_method]

        logoff = to_unicode(ADDON.getSetting('logoff'))

        log(2, msg='display_method={display_method}, power_method={power_method}, logoff={logoff}, mute={mute}',
            display_method=self.display.get('name'), power_method=self.power.get('name'),
            logoff=logoff, mute=self.mute)
        # Turn off display
        if self.display.get('name') != 'do-nothing':
            log(1, msg="Turn display signal off using method '{display_method}'", display_method=self.display.get('name'))
        func(self.display.get('function'), *self.display.get('args_off'))

        # FIXME: Screensaver always seems to lock when started, requires unlock and re-login
        # Log off user
        if logoff == 'true':
            log(1, msg='Log off user')
            activate_window('loginscreen')
#            run_builtin('System.LogOff')
#            run_builtin('ActivateWindow(loginscreen)')
#            run_builtin('ActivateWindowAndFocus(loginscreen,return)')

        # Mute audio
        if self.mute == 'true':
            log(1, msg='Mute audio')
            set_mute(True)
            # NOTE: Since the Mute-builtin is a toggle, we need to do this to ensure Mute
#            run_builtin('VolumeDown')
#            run_builtin('Mute')

        self.monitor = TurnOffMonitor(action=self.resume)

        # Power off system
        if self.power.get('name') != 'do-nothing':
            log(1, msg="Turn system off using method '{power_method}'", power_method=self.power.get('name'))
        func(self.power.get('function'), **self.power.get('kwargs_off', {}))

    def resume(self):
        ''' Perform this when the Screensaver is stopped '''
        # Unmute audio
        if self.mute == 'true':
            log(1, msg='Unmute audio')
            set_mute(False)
#            run_builtin('Mute')
            # NOTE: Since the Mute-builtin is a toggle, we need to do this to ensure Unmute
#            run_builtin('VolumeUp')

        # Turn on display
        if self.display.get('name') != 'do-nothing':
            log(1, msg="Turn display signal back on using method '{display_method}'", display_method=self.display.get('name'))
        func(self.display.get('function'), *self.display.get('args_on'))

        # Clean up everything
        self.cleanup()

#    @atexit.register
    def cleanup(self):
        ''' Clean up function '''
        if hasattr(self, 'monitor'):
            del self.monitor

        self.close()
        del self


ADDON = Addon()
ADDON_NAME = to_unicode(ADDON.getAddonInfo('name'))
ADDON_ID = to_unicode(ADDON.getAddonInfo('id'))
ADDON_PATH = to_unicode(ADDON.getAddonInfo('path'))
ADDON_ICON = to_unicode(ADDON.getAddonInfo('icon'))

DEBUG_LOGGING = True
MAX_LOG_LEVEL = 3

if __name__ == '__main__':
    # Do not start screensaver when command fails
    TurnOffDialog('gui.xml', ADDON_PATH, 'default').doModal()
    sys.modules.clear()
