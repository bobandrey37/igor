from __future__ import print_function
from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
from builtins import object
import os
import sys
import re
import json
import urllib.request, urllib.parse, urllib.error

NAME_RE = re.compile(r'[a-zA-Z_][-a-zA-Z0-9_.]+')

DEBUG=False

class DevicePlugin(object):
    def __init__(self, igor):
        self.igor = igor
        self.hasCapabilities = self.igor.internal.accessControl('hasCapabilitySupport')
    
    def index(self, token=None):
        raise self.igor.app.raiseNotfound()
    
    def add(self, token=None, name=None, description=None, returnTo=None, **kwargs):
        if not NAME_RE.match(name):
            self.igor.app.raiseHTTPError('400 Illegal name for device')
        if not description:
            description = kwargs
        elif type(description) != type({}):
            description = json.loads(description)
        if type(description) != type({}):
            self.igor.app.raiseHTTPError('400 description must be dictionary or json object')
            
        deviceType = description.get('deviceType', None)
        if not deviceType:
            self.igor.app.raiseHTTPError('400 deviceType missing')
            
        isDevice = deviceType in {'activeDevice', 'activeSensorDevice'}
        isSensor = deviceType in {'activeSensor', 'polledSensor', 'passiveSensor'}
        if not isDevice and not isSensor:
            self.igor.app.raiseHTTPError('400 unknown deviceType %s' % deviceType)
        
        isActive = deviceType in {'activeSensor', 'activeSensorDevice'}
        isPassive = deviceType == 'passiveSensor'
        hasPlugin = not isPassive
        hostname = description.get('hostname', None)
        if not hostname and (isDevice or isActive):
            hostname = name + '.local'

        if isDevice:
            databaseEntry = 'devices/%s' % name
        elif isSensor:
            databaseEntry = 'sensors/%s' % name
        else:
            assert 0
            
        if self.igor.databaseAccessor.get_key(databaseEntry, 'application/x-python-object', 'multi', token):
            self.igor.app.raiseHTTPError('400 %s already exists' % name)
            
        rv = dict(name=name, deviceType=deviceType, isDevice=isDevice, isSensor=isSensor)
        if hostname:
            rv['hostname'] = hostname

        if hasPlugin:
            pluginName = description.get('plugin', '')
            if not pluginName:
                self.igor.app.raiseHTTPError('400 deviceType %s requires plugin' % deviceType)
            queryString = urllib.parse.urlencode(dict(action='addstd', pluginName=name, stdName=pluginName, fromDevicePage='true'))
            rv['addPluginLink'] = '/plugins.html?' + queryString
        else:
            # Create item
            entryValues = {}
            self.igor.databaseAccessor.put_key(databaseEntry, 'text/plain', 'ref', entryValues, 'application/x-python-object', token, replace=True)
            # Create status item
            self.igor.databaseAccessor.put_key('status/' + databaseEntry, 'text/plain', 'ref', '', 'text/plain', token, replace=True)

        
        if isDevice and self.hasCapabilities:
            deviceKey = self._genSecretKey(aud=hostname, token=token)
            rv['audSharedKeyId'] = deviceKey
            deviceTokenId = self.igor.internal.accessControl('newToken',
                token=token,
                tokenId='external',
                newOwner='identities/admin',
                newPath=description.get('obj', '/'),
                get='descendant-or-self',
                put='descendant-or-self',
                post='descendant',
                delete='descendant',
                delegate=True,
                aud=hostname
                )
            rv['deviceTokenId'] = deviceTokenId
        if isActive and self.hasCapabilities:
            deviceKey = self._genSecretKey(sub=hostname, token=token)
            rv['subSharedKeyId'] = deviceKey
            actions = description.get('actions', {})
            if actions:
                actionResults = {}
                for actionName in list(actions.keys()):
                    actionData = self._addAction(token, subject=hostname, **actions[actionName])
                    actionResults[actionName] = actionData
                rv['actions'] = actionResults
        if returnTo:
            queryString = urllib.parse.urlencode(rv)
            if '?' in returnTo:
                returnTo = returnTo + '&' + queryString
            else:
                returnTo = returnTo + '?' + queryString
            return self.igor.app.raiseSeeother(returnTo)
        return json.dumps(rv)

    def _genSecretKey(self, token=None, aud=None, sub=None):
        return self.igor.internal.accessControl('createSharedKey', token=token, aud=aud, sub=sub)
                
    def addAction(self, token=None, subject=None, verb='get', obj=None, returnTo=None):
        rv = self._addAction(token, subject, verb, obj)
        if returnTo:
            queryString = urllib.parse.urlencode(rv)
            if '?' in returnTo:
                returnTo = returnTo + '&' + queryString
            else:
                returnTo = returnTo + '?' + queryString
            return self.igor.app.raiseSeeother(returnTo)
        return json.dumps(rv)

    def _addAction(self, token=None, subject=None, verb='get', obj=None):
        if not self.hasCapabilities:
            return{}
        if not obj:
            self.igor.app.raiseHTTPError('400 missing obj for action')
        if obj.startswith('/action/'):
            parentTokenId = 'admin-action'
        else:
            self.igor.app.raiseHTTPError('400 bad action %s' % obj)
        print('xxxjack obj', obj)
        newTokenId = actionTokenId = self.igor.internal.accessControl('newToken',
            token=token,
            tokenId=parentTokenId,
            newOwner='identities/admin',
            newPath=obj,
            delegate=True,
            **{verb : 'self'}
            )
        newTokenRepresentation = self.igor.internal.accessControl('exportToken',
            token=token,
            tokenId=newTokenId,
            subject=subject
            )
        return dict(verb=verb, obj=obj, newToken=newTokenRepresentation)
        
    def delete(self, name, hostname=None, token=None, returnTo=None):
        if not NAME_RE.match(name):
            self.igor.app.raiseHTTPError('400 Illegal name for user')
        if not hostname:
            hostname = name + '.local'
        if self.hasCapabilities:
            self._delSecretKey(aud=hostname, token=token)
            self._delSecretKey(sub=hostname, token=token)
        isDevice = not not self.igor.databaseAccessor.get_key('devices/%s' % name, 'application/x-python-object', 'multi', token)
        isSensor = not not self.igor.databaseAccessor.get_key('sensors/%s' % name, 'application/x-python-object', 'multi', token)
        self.igor.databaseAccessor.delete_key('devices/%s' % name, token)
        self.igor.databaseAccessor.delete_key('sensors/%s' % name, token)
        self.igor.databaseAccessor.delete_key('status/devices/%s' % name, token)
        self.igor.databaseAccessor.delete_key('status/sensors/%s' % name, token)
        self.igor.internal.save(token)
        if returnTo:
            return self.igor.app.raiseSeeother(returnTo)
        return ''
        
    def _delSecretKey(self, token=None, aud=None, sub=None):
        self.igor.internal.accessControl('deleteSharedKey', token=token, aud=aud, sub=sub)
        
    def list(self, token=None):
        rv = self.igor.internal._getDeviceListData(token)
        return json.dumps(rv)
            
def igorPlugin(igor, pluginName, pluginData):
    return DevicePlugin(igor)
    
