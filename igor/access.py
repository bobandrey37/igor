# Access control
import web
import xpath

NAMESPACES = { "au":"http://jackjansen.nl/igor/authentication" }

class AccessControlError(ValueError):
    pass

class DummyAccessToken:
    """An access token (or set of tokens) that can be carried by a request"""

    def __init__(self):
        pass

    def addToHeaders(self, headers):
        pass
        
    def getContent(self):
        return None
        
class IgorAccessToken(DummyAccessToken):
    def addToHeaders(self, headers):
        pass

_igorSelfToken = DummyAccessToken()

class AccessToken(DummyAccessToken):
    """An access token (or set of tokens) that can be carried by a request"""

    def __init__(self, content):
        self.content = content
        
    def addToHeaders(self, headers):
        headers['Authorization'] = 'Bearer ' + self.content
        
    def getContent(self):
        return self.content
        
class DummyAccessChecker:
    """An object that checks whether an operation (or request) has the right permission"""

    def __init__(self):
        pass
        
    def allowed(self, operation, token):
        return True
           
class AccessChecker(DummyAccessChecker):
    """An object that checks whether an operation (or request) has the right permission"""

    def __init__(self, content):
        self.content = content
        
    def allowed(self, operation, token):
        if token is _igorSelfToken:
            return True
        rv = token.getContent() == self.content
        return rv

class Access:
    def __init__(self):
        pass
        
    def checkerForElement(self, element):
        nodelist = xpath.find("au:requires", element, namespaces=NAMESPACES)
        if not nodelist:
            return DummyAccessChecker()
        if len(nodelist) > 1:
            raise AccessControlError("Action has multiple au:requires")
        requiresValue = "".join(t.nodeValue for t in nodelist[0].childNodes if t.nodeType == t.TEXT_NODE)
        return AccessChecker(requiresValue)
            
        
    def tokenForAction(self, element):
        nodelist = xpath.find("au:carries", element, namespaces=NAMESPACES)
        if not nodelist:
            return DummyAccessToken()
        if len(nodelist) > 1:
            raise AccessControlError("Action has multiple au:carries")
        carriesValue = "".join(t.nodeValue for t in nodelist[0].childNodes if t.nodeType == t.TEXT_NODE)
        return AccessToken(carriesValue)
        
    def tokenForPlugin(self, pluginname):
        return self.tokenForIgor()

    def tokenForIgor(self):
        return _igorSelfToken
        
    def tokenForRequest(self, headers):
        if 'HTTP_AUTHORIZATION' in headers:
            authHeader = headers['HTTP_AUTHORIZATION']
            authFields = authHeader.split()
            if authFields[0].lower() == 'bearer':
                return AccessToken(authFields[1])
            # Add more here for other methods
        return DummyAccessToken()

singleton = Access()