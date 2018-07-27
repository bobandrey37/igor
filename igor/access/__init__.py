# Access control
import web
import xpath
import base64
import random
import time
import urlparse
import jwt
import sys

from .vars import *
from .capability import *
from .checker import *
from .consistency import *

_igorSelfToken = IgorAccessToken()
_accessSelfToken = _igorSelfToken

# xxxjack temporary

from . import capability
capability._accessSelfToken = _accessSelfToken

def _combineTokens(token1, token2):
    """Return union of two tokens (which may be AccessToken, MultiAccessToken or None)"""
    if token1 is None:
        return token2
    if token2 is None:
        return token1
    if hasattr(token1, '_appendToken'):
        token1._appendToken(token2)
        return token1
    return MultiAccessToken(tokenList=[token1, token2])


class OTPHandler:
    """Handle implementation of one-time-passwords (for passing tokens to plugins and scripts)"""
    def __init__(self):
        self._otp2token = {}

    def produceOTPForToken(self, token):
        """Produce a one-time-password form of this token, for use internally or for passing to a plugin script (to be used once)"""
        # The key format is carefully selected so it can be used as user:pass combination
        k = '-otp-%d:%d' % (random.getrandbits(64), random.getrandbits(64))
        self._otp2token[k] = token
        return k
        
    def _consumeOTPForToken(self, otp):
        """Internal method - turns an OTP back into the token it refers to and invalidates the OTP"""
        # xxxjack should use a mutex here
        token = self._otp2token.get(otp)
        if token:
            del self._otp2token[otp]
            return token
        else:
            print 'access: Invalid OTP presented: ', otp
            raise myWebError("498 Invalid OTP presented")
            
    def invalidateOTPForToken(self, otp):
        """Invalidate an OTP, if it still exists. Used when a plugin script exits, in case it has not used its OTP"""
        if otp in self._otp2token:
            del self._otp2token[otp]

class TokenStorage:
    """Handle storing and retrieving capabilities"""
    
    def __init__(self):
        self.database = None          
        self._tokenCache = {}
        self._defaultTokenInstance = None

    def _registerTokenWithIdentifier(self, identifier, token):
        self._tokenCache[identifier] = token
        
    def _loadTokenWithIdentifier(self, identifier):
        if identifier in self._tokenCache:
            return self._tokenCache[identifier]
        capNodeList = self.database.getElements("//au:capability[cid='%s']" % identifier, 'get', _accessSelfToken, namespaces=NAMESPACES)
        if len(capNodeList) == 0:
            print 'access: Warning: Cannot get token %s because it is not in the database' % identifier
            raise myWebError("500 Access: no capability with cid=%s" % identifier)
        elif len(capNodeList) > 1:
            print 'access: Error: Cannot get token %s because it occurs %d times in the database' % (identifier, len(capNodeList))
            raise myWebError("500 Access: multiple capabilities with cid=%s" % identifier)
        capData = self.database.tagAndDictFromElement(capNodeList[0])[1]
        return AccessToken(capData)

    def _defaultToken(self):
        """Internal method - returns token(s) for operations/users/plugins/etc that have no explicit tokens"""
        if self._defaultTokenInstance == None and self.database:
            defaultContainer = self.database.getElements('au:access/au:defaultCapabilities', 'get', _accessSelfToken, namespaces=NAMESPACES)
            if len(defaultContainer) != 1:
                raise myWebError("501 Database should contain single au:access/au:defaultCapabilities")
            self._defaultTokenInstance = self._tokenForElement(defaultContainer[0])
        if self._defaultTokenInstance == None:
            print 'access: _defaultToken() called but no database (or no default token in database)'
        return self._defaultTokenInstance
        
    def _tokenForUser(self, username):
        """Internal method - Return token(s) for a user with the given name"""
        if not username or '/' in username:
            raise myWebError('401 Illegal username')
        elements = self.database.getElements('identities/%s' % username, 'get', _accessSelfToken)
        if len(elements) != 1:
            raise myWebError('501 Database error: %d users named %s' % (len(elements), username))
        element = elements[0]
        token = self._tokenForElement(element, owner='identities/%s' % username)
        tokenForAllUsers = self._tokenForElement(element.parentNode)
        token = _combineTokens(token, tokenForAllUsers)
        return _combineTokens(token, self._defaultToken())
 
    def _tokenForElement(self, element, owner=None):
        """Internal method - returns token(s) that are stored in a given element (identity/action/plugindata/etc)"""
        nodelist = xpath.find("au:capability", element, namespaces=NAMESPACES)
        if not nodelist:
            return None
        tokenDataList = map(lambda e: self.database.tagAndDictFromElement(e)[1], nodelist)
        if len(tokenDataList) > 1:
            return MultiAccessToken(tokenDataList, owner=owner)
        rv = AccessToken(tokenDataList[0], owner=owner)
        return rv       
        
class RevokeList:
    """Handles revocation list"""
    def __init__(self):
        self._revokeList = []
        self.database = None

    def _addToRevokeList(self, tokenId, nva=None):
        """Add given token to the revocation list"""
        if self._revokeList is None:
            self._loadRevokeList()
        if not tokenId in self._revokeList:
            self._revokeList.append(tokenId)
            revokeData = dict(cid=tokenId)
            if nva:
                revokeData['nva'] = nva
            element = self.database.elementFromTagAndData("revokedCapability", revokeData, namespace=NAMESPACES)
            parents = self.database.getElements('au:access/au:revokedCapabilities', 'post', _accessSelfToken, namespaces=NAMESPACES)
            assert len(parents) == 1
            parents[0].appendChild(element)
        
    def _isTokenOnRevokeList(self, tokenId):
        """Check whether a given token is on the revoke list"""
        if self._revokeList is None:
            self._loadRevokeList()
        return tokenId in self._revokeList
        
    def _loadRevokeList(self):
        self._revokeList = self.database.getValues('au:access/au:revokedCapabilities/au:revokedCapability/cid', _accessSelfToken, namespaces=NAMESPACES)
     
class IssuerInterface:
    """Implement interface to the issuer"""
    def __init__(self):
        self._self_audience = None
        self.database = None

    def getSelfAudience(self):
        """Return an audience identifier that refers to us"""
        if not self._self_audience:
            self._self_audience = self.database.getValue('services/igor/url', _accessSelfToken)
        return self._self_audience

    def getSelfIssuer(self):
        """Return URL for ourselves as an issuer"""
        return urlparse.urljoin(self.getSelfAudience(),  '/issuer')

    def _getSharedKey(self, iss=None, aud=None):
        """Get secret key shared between issuer and audience"""
        if iss is None:
            iss = self.getSelfIssuer()
        if aud is None:
            aud = self.getSelfAudience()
        keyPath = "au:access/au:sharedKeys/au:sharedKey[iss='%s'][aud='%s']/externalKey" % (iss, aud)
        externalKey = self.database.getValue(keyPath, _accessSelfToken, namespaces=NAMESPACES)
        if not externalKey:
            print 'access: _getExternalRepresentation: no key found at %s' % keyPath
            raise myWebError('404 No shared key found for iss=%s, aud=%s' % (iss, aud))
        return externalKey

    def _decodeIncomingData(self, data):
        sharedKey = self._getSharedKey()
        if DEBUG: 
            print 'access._decodeIncomingData: %s: externalRepresentation %s' % (self, data)
            print 'access._decodeIncomingData: %s: externalKey %s' % (self, sharedKey)
        try:
            content = jwt.decode(data, sharedKey, issuer=singleton.getSelfIssuer(), audience=singleton.getSelfAudience(), algorithm='RS256')
        except jwt.DecodeError:
            print 'access: ERROR: incorrect signature on bearer token %s' % data
            print 'access: ERROR: content: %s' % jwt.decode(data, verify=False)
            raise myWebError('400 Incorrect signature on key')
        except jwt.InvalidIssuerError:
            print 'access: ERROR: incorrect issuer on bearer token %s' % data
            print 'access: ERROR: content: %s' % jwt.decode(data, verify=False)
            raise myWebError('400 Incorrect issuer on key')
        except jwt.InvalidAudienceError:
            print 'access: ERROR: incorrect audience on bearer token %s' % data
            print 'access: ERROR: content: %s' % jwt.decode(data, verify=False)
            raise myWebError('400 Incorrect audience on key')
        if DEBUG: 
            print 'access._decodeIncomingData: %s: tokenContent %s' % (self, content)
        return content

    def _encodeOutgoingData(self, tokenContent):
        iss = tokenContent.get('iss')
        aud = tokenContent.get('aud')
        # xxxjack Could check for multiple aud values based on URL to contact...
        if not iss or not aud:
            print 'access: _getExternalRepresentation: no iss and aud, so no external representation'
            raise myWebError('404 Cannot lookup shared key for iss=%s aud=%s' % (iss, aud))
        externalKey = singleton._getSharedKey(iss, aud)
        externalRepresentation = jwt.encode(tokenContent, externalKey, algorithm='HS256')
        if DEBUG: 
            print 'access._encodeOutgoingData: %s: tokenContent %s' % (self, tokenContent)
            print 'access._encodeOutgoingData: %s: externalKey %s' % (self, externalKey)
            print 'access._encodeOutgoingData: %s: externalRepresentation %s' % (self, externalRepresentation)
        return externalRepresentation
        
    def getSubjectList(self, token=None):
        """Return list of subjects that trust this issuer"""
        # xxxjack this is wrong: it also returns keys shared with other issuers
        subjectValues = self.database.getValues('au:access/au:sharedKeys/au:sharedKey/sub', _accessSelfToken, namespaces=NAMESPACES)
        subjectValues = map(lambda x : x[1], subjectValues)
        subjectValues = list(subjectValues)
        subjectValues.sort()
        return subjectValues

    def getAudienceList(self, token=None):
        """Return list of audiences that trust this issuer"""
        audienceValues = self.database.getValues('au:access/au:sharedKeys/au:sharedKey/sub', _accessSelfToken, namespaces=NAMESPACES)
        audienceValues = set(audienceValues)
        audienceValues = list(audienceValues)
        audienceValues.sort()
        return audienceValues
        
    def getKeyList(self, token=None):
        """Return list of tuples with (iss, sub, aud) for every key"""
        keyElements = self.database.getElements('au:access/au:sharedKeys/au:sharedKey', 'get', _accessSelfToken, namespaces=NAMESPACES)
        rv = []
        for kElement in keyElements:
            iss = self.database.getValue('iss', _accessSelfToken, namespaces=NAMESPACES, context=kElement)
            aud = self.database.getValue('aud', _accessSelfToken, namespaces=NAMESPACES, context=kElement)
            sub = self.database.getValue('sub', _accessSelfToken, namespaces=NAMESPACES, context=kElement)
            kDict = dict(aud=aud)
            if iss:
                kDict['iss'] = iss
            if sub:
                kDict['sub'] = sub
            rv.append(kDict)
        return rv
        
    def createSharedKey(self, sub=None, aud=None, token=None):
        """Create a secret key that is shared between issues and audience"""
        iss = self.getSelfIssuer()
        if not aud:
            aud = self.getSelfAudience()
        keyPath = "au:access/au:sharedKeys/au:sharedKey[iss='%s'][aud='%s']" % (iss, aud)
        if sub:
            keyPath += "[sub='%s']" % sub
        keyElements = self.database.getElements(keyPath, 'get', _accessSelfToken, namespaces=NAMESPACES)
        if keyElements:
            raise myWebError('409 Shared key already exists')
        keyBits = 'k' + str(random.getrandbits(64))
        keyData = dict(iss=iss, aud=aud, externalKey=keyBits)
        if sub:
            keyData['sub'] = sub
        parentElement = self.database.getElements('au:access/au:sharedKeys', 'post', _accessSelfToken, namespaces=NAMESPACES)
        if len(parentElement) != 1:
            if DEBUG_DELEGATION: print 'access: createSharedKey: no unique destination au:access/au:sharedKeys'
            raise web.notfound()
        parentElement = parentElement[0]
        element = self.database.elementFromTagAndData("sharedKey", keyData, namespace=NAMESPACES)
        parentElement.appendChild(element)
        self._save()
        return keyBits
        
    def deleteSharedKey(self, iss=None, sub=None, aud=None, token=None):
        """Delete a shared key"""
        if not iss:
            iss = self.getSelfIssuer()
        if not aud:
            aud = self.getSelfAudience()
        keyPath = "au:access/au:sharedKeys/au:sharedKey[iss='%s'][aud='%s']" % (iss, aud)
        if sub:
            keyPath += "[sub='%s']" % sub
        self.database.delValues(keyPath, _accessSelfToken, namespaces=NAMESPACES)
        self._save()
        return ''
        
class UserPasswords:
    """Implements checking of passwords for users"""
    
    def __init__(self):
        self.database = None

    def userAndPasswordCorrect(self, username, password):
        """Return True if username/password combination is valid"""
        # xxxjack this method should not be in the Access element
        if self.database == None or not username:
            if DEBUG: print 'access: basic authentication: database or username missing'
            return False
        if '/' in username:
            raise myWebError('401 Illegal username')
        encryptedPassword = self.database.getValue('identities/%s/encryptedPassword' % username, _accessSelfToken)
        if not encryptedPassword:
            if DEBUG: print 'access: basic authentication: no encryptedPassword for user', username
            return True
        import passlib.hash
        import passlib.utils.binary
        salt = encryptedPassword.split('$')[3]
        salt = passlib.utils.binary.ab64_decode(salt)
        passwordHash = passlib.hash.pbkdf2_sha256.using(salt=salt).hash(password)
        if encryptedPassword != passwordHash:
            if DEBUG: print 'access: basic authentication: password mismatch for user', username
            return False
        if DEBUG: print 'access: basic authentication: login for user', username
        return True

    def setUserPassword(self, username, password, token):
        """Change the password for the user"""
        import passlib.hash
        passwordHash = passlib.hash.pbkdf2_sha256.hash(password)
        element = self.database.elementFromTagAndData('encryptedPassword', passwordHash)
        self.database.delValues('identities/%s/encryptedPassword' % username, token)
        parentElements = self.database.getElements('identities/%s' % username, 'post', token)
        if len(parentElements) == 0:
            raise myWebError('404 User %s not found' % username)
        if len(parentElements) > 1:
            raise myWebError('404 Multiple entries for user %s' % username)
        parentElement = parentElements[0]
        parentElement.appendChild(element)

class Access(OTPHandler, TokenStorage, RevokeList, IssuerInterface, UserPasswords):
    def __init__(self):
        OTPHandler.__init__(self)
        TokenStorage.__init__(self)
        RevokeList.__init__(self)
        IssuerInterface.__init__(self)
        UserPasswords.__init__(self)
        self.database = None
        self.session = None
        self.COMMAND = None
        
    def _save(self):
        """Save database or capability store, if possible"""
        if self.COMMAND:
            self.COMMAND.queue('save', _accessSelfToken)

    def hasCapabilitySupport(self):
        return True
        
    def setDatabase(self, database):
        """Temporary helper method - Informs the access checker where it can find the database object"""
        self.database = database
        
    def setSession(self, session):
        """Temporary helper method - Informs the access checker where sessions are stored"""
        self.session = session
        
    def setCommand(self, command):
        """Temporary helper method - Set command processor so access can save the database"""
        self.COMMAND = command

    def checkerForElement(self, element):
        """Returns an AccessChecker for an XML element"""
        if not element:
            print 'access: ERROR: attempt to get checkerForElement(None)'
            return DefaultAccessChecker()
        path = self.database.getXPathForElement(element)
        if not path:
            print 'access: ERROR: attempt to get checkerForElement(%s) that has no XPath' % repr(element)
            return DefaultAccessChecker()
        if not path.startswith('/data'):
            print 'access: ERROR: attempt to get checkerForElement(%s) with unexpected XPath: %s' % (repr(element), path)
            return DefaultAccessChecker()
        return AccessChecker(path)
        
    def checkerForNewElement(self, path):
        """Returns an AccessChecker for an element that does not exist yet (specified by XPath)"""
        if not path.startswith('/data'):
            print 'access: ERROR: attempt to get checkerForNewElement() with unexpected XPath: %s' %  path
            return DefaultAccessChecker()
        return AccessChecker(path)
            
    def checkerForEntrypoint(self, entrypoint):
        """Returns an AccessChecker for an external entrypoint that is not a tree element"""
        if not entrypoint or entrypoint[0] != '/' or entrypoint.startswith('/data'):
            print 'access: ERROR: attempt to get checkerForEntrypoint(%s)' % entrypoint
            return DefaultAccessChecker()
        return AccessChecker(entrypoint)
        
    def tokenForAction(self, element, token=None):
        """Return token(s) for an <action> element"""
        
        tokenForAction = self._tokenForElement(element)
        if token is None:
            token = tokenForAction
        else:
            token = _combineTokens(token, tokenForAction)
        tokenForAllActions = self._tokenForElement(element.parentNode)
        token = _combineTokens(token, tokenForAllActions)
        return _combineTokens(token, self._defaultToken())
        
    def tokenForPlugin(self, pluginname, token=None):
        """Return token(s) for a plugin with the given pluginname"""
        # xxxjack should fix this: plugins are allowed to do everything.
        tokenForPlugin = self.tokenForIgor()
        if token is None:
            token = tokenForPlugin
        else:
            token = _combineTokens(token, tokenForPlugin)
        return token

    def tokenForIgor(self):
        """Return token for igor itself (use sparingly)"""
        return _igorSelfToken
        
    def tokenForRequest(self, headers):
        """Return token for the given incoming http(s) request"""
        if 'HTTP_AUTHORIZATION' in headers:
            authHeader = headers['HTTP_AUTHORIZATION']
            authFields = authHeader.split()
            if authFields[0].lower() == 'bearer':
                decoded = authFields[1] # base64.b64decode(authFields[1])
                if DEBUG: print 'access: tokenForRequest: returning token found in Authorization: Bearer header'
                return self._externalAccessToken(decoded)
            if authFields[0].lower() == 'basic':
                decoded = base64.b64decode(authFields[1])
                if decoded.startswith('-otp-'):
                    # This is a one time pad, not a username/password combination
                    if DEBUG: print 'access: tokenForRequest: found OTP in Authorization: Basic header'
                    return self._consumeOTPForToken(decoded)
                username, password = decoded.split(':')
                if DEBUG: print 'access: tokenForRequest: searching for token for Authorization: Basic %s:xxxxxx header' % username
                if self.userAndPasswordCorrect(username, password):
                    return self._tokenForUser(username)
                else:
                    web.header('WWW_Authenticate', 'Basic realm="igor"')
                    raise web.HTTPError('401 Unauthorized')
            # Add more here for other methods
        if self.session and 'user' in self.session and self.session.user:
            if DEBUG: print 'access: tokenForRequest: returning token for session.user %s' % self.session.user
            return self._tokenForUser(self.session.user)
        # xxxjack should we allow carrying tokens in cookies?
        if DEBUG: print 'access: no token found for request %s' % headers.get('PATH_INFO', '???'), 'returning', self._defaultToken()
        return self._defaultToken()
        
    def _externalAccessToken(self, data):
        """Internal method - Create a token from the given "Authorization: bearer" data"""
        content = self._decodeIncomingData(data)
        cid = content.get('cid')
        if not cid:
            print 'access: ERROR: no cid on bearer token %s' % content
            raise myWebError('400 Missing cid on key')
        if singleton._isTokenOnRevokeList(cid):
            print 'access: ERROR: token has been revoked: %s' % content
            raise myWebError('400 Revoked token')
        return ExternalAccessTokenImplementation(content)
    
    def getTokenDescription(self, token, tokenId=None):
        """Returns a list of dictionaries which describe the tokens"""
        if tokenId:
            originalToken = token
            token = token._getTokenWithIdentifier(tokenId)
            if not token:
                identifiers = originalToken._getIdentifiers()
                print '\taccess: getTokenDescription: no such token ID: %s. Tokens:' % tokenId
                for i in identifiers:
                    print '\t\t%s' % i
                raise myWebError('404 No such token: %s' % tokenId)
        return token._getTokenDescription()
        
    def newToken(self, token, tokenId, newOwner, newPath=None, **kwargs):
        """Create a new token based on an existing token. Returns ID of new token."""
        #
        # Split remaining args into rights and other content
        #
        newRights = {}
        content = {}
        for k, v in kwargs.items():
            # Note delegate right is checked implicitly, below.
            if k in NORMAL_OPERATIONS:
                newRights[k] = v
            else:
                content[k] = v
        #
        # Check that original token exists, and allows this delegation
        #
        originalToken = token
        token = token._getTokenWithIdentifier(tokenId)
        if newPath == None:
                newPath = token._getObject()
        if not token:
            identifiers = originalToken._getIdentifiers()
            print '\taccess: newToken: no such token ID: %s. Tokens:' % tokenId
            for i in identifiers:
                print '\t\t%s' % i
            raise myWebError('404 No such token: %s' % tokenId)
        if not token._allowsDelegation(newPath, newRights, content.get('aud')):
            raise myWebError('401 Delegation not allowed')
        #
        # Check the new parent exists
        #
        parentElement = self.database.getElements(newOwner, 'post', _accessSelfToken, namespaces=NAMESPACES)
        if len(parentElement) != 1:
            if DEBUG_DELEGATION: print 'access: newToken: no unique destination %s' % newOwner
            raise web.notfound()
        parentElement = parentElement[0]
        #
        # Construct the data for the new token.
        #
        newId = 'c%d' % random.getrandbits(64)
        token._addChild(newId)
        tokenData = dict(cid=newId, obj=newPath, parent=tokenId)
        moreData = token._getExternalContent()
        for k, v in moreData.items():
            if not k in tokenData:
                tokenData[k] = v
        tokenData.update(newRights)
        tokenData.update(content)

        element = self.database.elementFromTagAndData("capability", tokenData, namespace=NAMESPACES)
        #
        # Insert into the tree
        #
        parentElement.appendChild(element)
        #
        # Save
        #
        self._save()
        #
        # Return the ID
        #
        return newId
        
    def passToken(self, token, tokenId, newOwner):
        """Pass token ownership to a new owner. Token must be in the set of tokens that can be passed."""
        originalToken = token
        tokenToPass = token._getTokenWithIdentifier(tokenId)
        if not tokenToPass:
            identifiers = originalToken._getIdentifiers()
            print '\taccess: passToken: no such token ID: %s. Tokens:' % tokenId
            for i in identifiers:
                print '\t\t%s' % i
            raise myWebError("401 No such token: %s" % tokenId)
        oldOwner = tokenToPass._getOwner()
        if not oldOwner:
            raise myWebError("401 Not owner of token %s" % tokenId)
        if oldOwner == newOwner:
            return ''
        if not tokenToPass._setOwner(newOwner):
            raise myWebError("401 Cannot move token %s to new owner %s" % (tokenId, newOwner))
        token._removeToken(tokenId)
        #
        # Save
        #
        self._save()
        
    def revokeToken(self, token, parentId, tokenId):
        """Revoke a token"""
        parentToken = token._getTokenWithIdentifier(parentId)
        if not parentToken:
            identifiers = token._getIdentifiers()
            print '\taccess: revokeToken: no such token ID: %s. Tokens:' % parentId
            for i in identifiers:
                print '\t\t%s' % i
            raise myWebError("404 No such parent token: %s" % parentId)
        childToken = token._getTokenWithIdentifier(tokenId)
        if not childToken:
            print '\taccess: revokeToken: no such token ID: %s. Tokens:' % tokenId
            for i in identifiers:
                print '\t\t%s' % i
            raise myWebError("404 No such token: %s" % tokenId)
        self._addToRevokeList(tokenId, childToken.content.get('nva'))
        childToken._revoke()
        parentToken._delChild(tokenId)
        #
        # Save
        #
        self._save()
        
    def exportToken(self, token, tokenId, subject=None, lifetime=None, **kwargs):
        """Create an external representation of this token, destined for the given subject"""
        #
        # Add keys needed for external token
        #
        if subject:
            kwargs['sub'] = subject
        if not lifetime:
            lifetime = 60*60*24*365 # One year
        lifetime = int(lifetime)
        kwargs['nvb'] = str(int(time.time())-1)
        kwargs['nva'] = str(int(time.time()) + lifetime)
        if not 'aud' in kwargs:
            kwargs['aud'] = self.getSelfAudience()
        kwargs['iss'] = self.getSelfIssuer()
        #
        # Create the new token
        #
        newTokenId = self.newToken(token, tokenId, self._getExternalTokenOwner(), **kwargs)
        tokenToExport = token._getTokenWithIdentifier(newTokenId)
        if not tokenToExport:
            # The new token is a grandchild of our token, so we may not be able to get it directly.
            # Try harder.
            parentToken = token._getTokenWithIdentifier(tokenId)
            tokenToExport = parentToken._getTokenWithIdentifier(newTokenId)
        if not tokenToExport:
            raise myWebError('500 created token %s but it does not exist' % newTokenId)
        #
        # Create the external representation
        #
        assert tokenToExport
        assert tokenToExport._hasExternalRepresentationFor(self.getSelfAudience())
        externalRepresentation = tokenToExport._getExternalRepresentation()
        #
        # Save
        #
        self._save()
        return externalRepresentation
        
    def externalRepresentation(self, token, tokenId):
        tokenToExport = token._getTokenWithIdentifier(tokenId)
        if not tokenToExport:
            identifiers = token._getIdentifiers()
            print '\taccess: externalRepresentation: no such token ID: %s. Tokens:' % tokenId
            for i in identifiers:
                print '\t\t%s' % i
            raise myWebError("401 No such token: %s" % tokenId)
        assert tokenToExport._hasExternalRepresentationFor(self.getSelfAudience())
        externalRepresentation = tokenToExport._getExternalRepresentation()
        return externalRepresentation
        
    def _getExternalTokenOwner(self):
        """Return the location where we store external tokens"""
        return '/data/au:access/au:exportedCapabilities'
        
    def consistency(self, token=None, fix=False, restart=False):
        if fix:
            self.COMMAND.save(token)
        checker = CapabilityConsistency(self.database, fix, NAMESPACES, _accessSelfToken)
        nChanges, nErrors, rv = checker.check()
        if nChanges:
            self.COMMAND.save(token)
            if restart:
                rv += '\nRestarting Igor'
                self.COMMAND.queue('restart', _accessSelfToken)
            else:
                rv += '\nRestart Igor to update capability data structures'
        return rv
#
# Create a singleton Access object
#   
singleton = None

def createSingleton(noCapabilities=False):
    global singleton
    if singleton: return
    if noCapabilities:
        print >>sys.stderr, 'Warning: capability-base access control disabled'
        import dummyAccess
        dummyAccess.createSingleton(noCapabilities)
        singleton = dummyAccess.singleton
    else:
        singleton = Access()
        capability.singleton = singleton
    
