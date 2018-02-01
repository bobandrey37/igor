# Access control
import web
import xpath
import base64
import jwt
import random

NAMESPACES = { "au":"http://jackjansen.nl/igor/authentication" }

NORMAL_OPERATIONS = {'get', 'put', 'post', 'delete'}
AUTH_OPERATIONS = {'auth'}
ALL_OPERATIONS = NORMAL_OPERATIONS | AUTH_OPERATIONS

CASCADING_RULES = {'self', 'descendant', 'descendant-or-self', 'child'}
CASCADING_RULES_IMPLIED = {
    'self' : {},
    'descendant' : {'child'},
    'descendant-or-self' : {'self', 'descendant', 'child'},
    'child' : {}
}

DEBUG=False

# For the time being: define this to have the default token checker allow everything
# the dummy token allows
DEFAULT_IS_ALLOW_ALL=True

class AccessControlError(ValueError):
    pass

class BaseAccessToken:
    """An access token (or set of tokens) that can be carried by a request"""

    def __init__(self):
        self.identifier = None

    def __repr__(self):
        return "%s(0x%x)" % (self.__class__.__name__, id(self))
        
    def _getIdentifiers(self):
        """Internal method - Returns a list of all token IDs of this token (and any subtokens it contains)"""
        return [self.identifier]
        
    def _hasExternalRepresentation(self):
        """Internal method - return True if this token can be represented externally and _getExternalRepresentation can be called"""
        return False
        
    def _getExternalRepresentation(self):
        """Internal method - return the external representation of this token"""
        
    def _allows(self, operation, accessChecker):
        """Internal method - return True if this token allows 'operation' on the element represented by 'accessChecker'"""
        if DEBUG: print 'access: %s %s: no access at all allowed by %s' % (operation, accessChecker.destination, self)
        return False
        
    def addToHeaders(self, headers):
        """Add this token to the (http request) headers if it has an external representation"""
        pass

    def addToHeadersAsOTP(self, headers):
        """Add this token to the (http request) headers in one-time-password form, for internal Igor use only"""
        otp = singleton.produceOTPForToken(self)
        headers['Authorization'] = 'Basic ' + base64.b64encode(otp)
        
class IgorAccessToken(BaseAccessToken):
    """A token without an external representation that allows everything everywhere.
    To be used sparingly by Igor itself."""

    def __init__(self):
        self.identifier = '*SUPER*'
        
    def _allows(self, operation, accessChecker):
        if DEBUG: print 'access: %s %s: allowed by %s' % (operation, accessChecker.destination, self)
        return True
        
_igorSelfToken = IgorAccessToken()
_accessSelfToken = _igorSelfToken

class AccessToken(BaseAccessToken):
    """An access token (or set of tokens) that can be carried by a request"""

    def __init__(self, content, defaultIdentifier=None):
        BaseAccessToken.__init__(self)
        self.content = content
        #
        # Determine identifier
        #
        if defaultIdentifier == None:
            defaultIdentifier = 'no-id-%x' % id(self)
        self.identifier = content.get('id', defaultIdentifier)
        #
        # Check whether this capability is meant for this igor (no aud or aud matches our URL)
        #
        if 'aud' in content:
            audience = content['aud']
            ourUrl = singleton.database.getValue('services/igor/url', _accessSelfToken)
            self.validForSelf = (audience == ourUrl)
            if DEBUG: print 'access: <aud> matches: %s' % self.validForSelf
        else:
            self.validForSelf = True
        if DEBUG:  print 'access: Created:', repr(self)
        
    def __repr__(self):
        return "%s(0x%x, %s)" % (self.__class__.__name__, id(self), repr(self.content))
        
    def _hasExternalRepresentation(self):
        return 'iss' in self.content and 'aud' in self.content
        
    def _getExternalRepresentation(self):
        iss = self.content.get('iss')
        aud = self.content.get('aud')
        # xxxjack Could check for multiple aud values based on URL to contact...
        if not iss or not aud:
            if DEBUG: print 'access: _getExternalRepresentation: no iss and aud, so no external representation'
            return None
        keyPath = "au:access/au:sharedKeys/au:sharedKey[iss='%s'][aud='%s']/externalKey" % (iss, aud)
        externalKey = singleton.database.getValue(keyPath, _accessSelfToken, namespaces=NAMESPACES)
        if not externalKey:
            if DEBUG: print 'access: _getExternalRepresentation: no key found at %s' % keyPath
            return
        externalRepresentation = jwt.encode(self.content, externalKey, algorithm='HS256')
        if DEBUG: print 'access: %s: externalRepresentation %s' % (self, externalRepresentation)
        return externalRepresentation
        
    def _allows(self, operation, accessChecker):
        # First check this this capability is for us.
        if not self.validForSelf:
            if DEBUG: print 'access: Not for this Igor: AccessToken %s' % self
            return False
        cascadingRule = self.content.get(operation)
        if not cascadingRule:
            if DEBUG: print 'access: %s %s: no %s access allowed by AccessToken %s' % (operation, accessChecker.destination, operation, self)
            return False
        path = self.content.get('xpath')
        if not path:
            if DEBUG: print 'access: %s %s: no path-based access allowed by AccessToken %s' % (operation, accessChecker.destination, self)
            return False
        dest = accessChecker.destination
        destHead = dest[:len(path)]
        destTail = dest[len(path):]
        if cascadingRule == 'self':
            if dest != path:
                if DEBUG: print 'access: %s %s: does not match cascade=%s for path=%s' % (operation, dest, cascadingRule, path)
                return False
        elif cascadingRule == 'descendant-or-self':
            if destHead != path or destTail[:1] not in ('', '/'):
                if DEBUG: print 'access: %s %s: does not match cascade=%s for path=%s' % (operation, dest, cascadingRule, path)
                return False
        elif cascadingRule == 'descendant':
            if destHead != path or destTail[:1] != '/':
                if DEBUG: print 'access: %s %s: does not match cascade=%s for path=%s' % (operation, dest, cascadingRule, path)
                return False
        elif cascadingRule == 'child':
            if destHead != path or destTail[:1] != '/' or destTail.count('/') != 1:
                if DEBUG: print 'access: %s %s: does not match cascade=%s for path=%s' % (operation, dest, cascadingRule, path)
                return False
        else:
            raise AccessControlError('Capability has unknown cascading rule %s for operation %s' % (cascadingRule, operation))
        if DEBUG: print 'access: %s %s: allowed by AccessToken %s' % (operation, accessChecker.destination, self)
        return True

    def addToHeaders(self, headers):
        externalRepresentation = self._getExternalRepresentation()
        if not externalRepresentation:
            return
        headers['Authorization'] = 'Bearer ' + externalRepresentation
        
class ExternalAccessToken(BaseAccessToken):
    def __init__(self, content):
        assert 0
        
class MultiAccessToken(BaseAccessToken):

    def __init__(self, contentList):
        self.tokens = []
        for c in contentList:
            self.tokens.append(AccessToken(c))

    def _getIdentifiers(self):
        rv = []
        for t in self.tokens:
            rv += t._getIdentifiers()
        return rv
                    
    def _hasExternalRepresentation(self):
        for t in self.tokens[:1]:
            if t._hasExternalRepresentation():
                return True
        return False
        
    def _allows(self, operation, accessChecker):
        if DEBUG: print 'access: %s %s: MultiAccessToken(%d)' % (operation, accessChecker.destination, len(self.tokens))
        for t in self.tokens:
            if t._allows(operation, accessChecker):
                return True
        return False      

    def addToHeaders(self, headers):
        for t in self.tokens[:1]:
            if t._hasExternalRepresentation():
                t.addToHeaders(headers)
                return
                   
class AccessChecker:
    """An object that checks whether an operation (or request) has the right permission"""

    def __init__(self, destination):
        self.destination = destination
        
    def allowed(self, operation, token):
        """Test whether the token (or set of tokens) allows this operation on the element represented by this AccessChecker"""
        if not token:
            if DEBUG: print 'access: %s %s: no access allowed for token=None' % (operation, self.destination)
            return False
        if not operation in ALL_OPERATIONS:
            raise web.InternalError("Access: unknown operation '%s'" % operation)
        ok = token._allows(operation, self)
        if not ok:
            identifiers = token._getIdentifiers()
            print '\taccess: %s %s: no access allowed by %d tokens:' % (operation, self.destination, len(identifiers))
            for i in identifiers:
                print '\t\t%s' % i
        return ok
    
class DefaultAccessChecker(AccessChecker):
    """An object that checks whether an operation (or request) has the right permission"""

    def __init__(self):
        # This string will not occur anywhere (we hope:-)
        self.destination = "(using default-accesschecker)"

    def allowed(self, operation, token):
        if DEBUG: print 'access: no access allowed by DefaultAccessChecker'
        return False
        
class Access:
    def __init__(self):
        self.database = None
        self.session = None
        self._otp2token = {}
        self._defaultTokenInstance = None
        
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
            raise web.HTTPError("498 Invalid OTP presented")
            
    def invalidateOTPForToken(self, otp):
        """Invalidate an OTP, if it still exists. Used when a plugin script exits, in case it has not used its OTP"""
        if otp in self._otp2token:
            del self._otp2token[otp]
            
    def setDatabase(self, database):
        """Temporary helper method - Informs the access checker where it can find the database object"""
        self.database = database
        
    def setSession(self, session):
        """Temporary helper method - Informs the access checker where sessions are stored"""
        self.session = session
        
    def _defaultToken(self):
        """Internal method - returns token(s) for operations/users/plugins/etc that have no explicit tokens"""
        if self._defaultTokenInstance == None and self.database:
            defaultContainer = self.database.getElements('au:access/au:defaultCapabilities', 'get', _accessSelfToken, namespaces=NAMESPACES)
            if len(defaultContainer) != 1:
                raise web.HTTPError("501 Database should contain single au:access/au:defaultCapabilities")
            self._defaultTokenInstance = self._tokenForElement(defaultContainer[0])
        if self._defaultTokenInstance == None:
            print 'access: _defaultToken() called but no database (or no default token in database)'
        return self._defaultTokenInstance
        
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
            
    def checkerForEntrypoint(self, entrypoint):
        """Returns an AccessChecker for an external entrypoint that is not a tree element"""
        if not entrypoint or entrypoint[0] != '/' or entrypoint.startswith('/data'):
            print 'access: ERROR: attempt to get checkerForEntrypoint(%s)' % entrypoint
            return DefaultAccessChecker()
        return AccessChecker(entrypoint)
        
    def _tokenForElement(self, element):
        """Internal method - returns token(s) that are stored in a given element (identity/action/plugindata/etc)"""
        nodelist = xpath.find("au:capability", element, namespaces=NAMESPACES)
        if not nodelist:
            return None
        tokenDataList = map(lambda e: self.database.tagAndDictFromElement(e)[1], nodelist)
        if len(tokenDataList) > 1:
            return MultiAccessToken(tokenDataList)
        rv = AccessToken(tokenDataList[0])
        return rv
        
    def tokenForAction(self, element):
        """Return token(s) for an <action> element"""
        token =  self._tokenForElement(element)
        if token == None:
            # Check whether there is a default token for all actions
            if element.parentNode:
                token = self._tokenForElement(element.parentNode)
        if token == None:
            if DEBUG: print 'access: no token found for action %s' % self.database.getXPathForElement(element)
            token = self._defaultToken()
        return token
        
    def _tokenForUser(self, username):
        """Internal method - Return token(s) for a user with the given name"""
        if not username or '/' in username:
            raise web.HTTPError('401 Illegal username')
        elements = self.database.getElements('identities/%s' % username, 'get', _accessSelfToken)
        if len(elements) != 1:
            raise web.HTTPError('501 Database error: %d users named %s' % (len(elements), username))
        token = self._tokenForElement(elements[0])
        if token == None:
            token = self._defaultToken()
            if DEBUG: print 'access: no token found for user %s' % username
        return token
        
    def tokenForPlugin(self, pluginname):
        """Return token(s) for a plugin with the given pluginname"""
        # xxxjack not yet implemented
        return self.tokenForIgor()

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
        if DEBUG: print 'access: no token found for request %s' % headers.get('PATH_INFO', '???')
        return self._defaultToken()

    def userAndPasswordCorrect(self, username, password):
        """Return True if username/password combination is valid"""
        # xxxjack this method should not be in the Access element
        if self.database == None or not username or not password:
            if DEBUG: print 'access: basic authentication: database, username or password missing'
            return False
        if '/' in username:
            raise web.HTTPError('401 Illegal username')
        encryptedPassword = self.database.getValue('identities/%s/encryptedPassword' % username, _accessSelfToken)
        if not encryptedPassword:
            if DEBUG: print 'access: basic authentication: no encryptedPassword for user', username
            return False
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
        
    def _externalAccessToken(self, data):
        """Internal method - Create a token from the given "Authorization: bearer" data"""
        # xxxjack not yet implemented
        print 'xxxjack attempt to get external access token for', data
        return self._defaultToken()
     
#
# Create a singleton Access object
#   
singleton = Access()
