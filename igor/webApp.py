from __future__ import print_function
from __future__ import absolute_import
from __future__ import unicode_literals
from builtins import str
from past.builtins import basestring
from builtins import object
import gevent.pywsgi
from flask import Flask, Response, request, abort, redirect, jsonify, make_response, after_this_request, session
import werkzeug.exceptions
import web.template
import web.form
import shlex
import subprocess
import os
import sys
import re
import uuid
import json
import time
from . import mimetypematch
import copy
import imp
import xpath
from . import xmlDatabase
import mimetypes
from . import access
import traceback
import shelve

DEBUG=False

_SERVER = None
_WEBAPP = Flask(__name__)
_WEBAPP.secret_key = b'geheimpje'   # Overridden by setSSLInfo in cases where it really matters
# 
# urls = (
##     '/pluginscript/([^/]+)/([^/]+)', 'runScript',
#     '/data/(.*)', 'abstractDatabaseAccess',
##     '/evaluate/(.*)', 'abstractDatabaseEvaluate',
##     '/internal/([^/]+)', 'runCommand',
##     '/internal/([^/]+)/(.+)', 'runCommand',
##     '/action/(.+)', 'runAction',
##     '/trigger/(.+)', 'runTrigger',
##     '/plugin/([^/]+)', 'runPlugin',
##     '/plugin/([^/]+)/([^/_]+)', 'runPlugin',
##     '/login', 'runLogin',
##     '/([^/]*)', 'static',
# )

class MyServer:
    """This class is a wrapper with some extra functionality (setting the port) as well as a
    somewhat-micro-framework-independent interface to the framework"""
    def __init__(self, igor):
        self.igor = igor
        self.server = None
        self.port = None
        self.keyfile = None
        self.certfile = None
    
    def run(self, port=8080):
        self.port = port
        if self.keyfile or self.certfile:
            kwargs = dict(keyfile=self.keyfile, certfile=self.certfile)
        else:
            kwargs = {}
        self.server = gevent.pywsgi.WSGIServer(("0.0.0.0", self.port), _WEBAPP, **kwargs)
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
            
    def setSSLInfo(self, certfile, keyfile):
        """Signal that https is to be used and set key and cert"""
        self.certfile = certfile
        self.keyfile = keyfile
        fp = open(keyfile, 'rb')
        appKey = fp.read(8)
        _SERVER.secret_key = appKey

    def getSession(self, backingstorefile=None):
        """Create persistent session object"""
        return session
        
    def getHTTPError(self):
        """Return excpetion raised by other methods below (for catching)"""
        return werkzeug.exceptions.HTTPError
        
    def resetHTTPError(self):
        """Clear exception"""
        return # Nothing to do for Flask? webpy was: web.ctx.status = "200 OK"
        
    def raiseNotfound(self):
        """404 not found"""
        abort(404)
        
    def raiseSeeother(self, url):
        """303 See Other"""
        redirect(url, 303)
        
    def raiseHTTPError(self, status, headers={}, data=""):
        """General http errors"""
        resp = make_response(data, status)
        if headers:
            for k, v in headers.items():
                resp.headers[k] = v
        abort(resp)
        
    def addHeaders(self, headers):
        """Add headers to the reply (to be returned shortly)"""
        @after_this_request
        def _add_headers(resp):
            for k, v in headers.items():
                resp.headers[k] = v
            
    def getOperationTraceInfo(self):
        """Return information that helps debugging access control errors in current operation"""
        assert 0
        rv = {}
        try:
            rv['requestPath'] = request.path
        except AttributeError:
            pass
        try:
            rv['action'] = request.environ.get('original_action')
        except AttributeError:
            pass
        try:
            rv['representing'] = request.environ.get('representing')
        except AttributeError:
            pass
        return rv
        
    def request(self, url, method='GET', data=None, headers={}, env={}):
        print('xxxjack request.%s(%s, data=%s, headers=%s, env=%s)' % (method, url, data, headers, env))
        return _DummyReply()
        
class _DummyReply:
    def __init__(self):
        self.status = '500 Not Implemented Yet'
        self.data = ''    
# web.config.debug = DEBUG

def WebApp(igor):
    global _SERVER
    assert not _SERVER
    _SERVER = MyServer(igor)
    return _SERVER

def myWebError(msg, code=400):
    resp = make_response(msg, code)
    abort(resp)

@_WEBAPP.route('/', defaults={'name':'index.html'})
@_WEBAPP.route('/<path:name>')    
def get_static(name):
    allArgs = request.values.to_dict()
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    if not name:
        name = 'index.html'
    checker = _SERVER.igor.access.checkerForEntrypoint('/static/' + name)
    if not checker.allowed('get', token):
        myWebError('401 Unauthorized', 401)
    databaseDir = _SERVER.igor.pathnames.staticdir
    programDir = os.path.dirname(__file__)
    
    # First try static files in the databasedir/static
    filename = os.path.join(databaseDir, name)
    if os.path.exists(filename):
        mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        data = open(filename, 'rb').read()
        return Response(data, mimetype=mimetype)
    # Next try static files in the programdir/static
    filename = os.path.join(programDir, 'static', name)
    if os.path.exists(filename):
        mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        data = open(filename, 'rb').read()
        return Response(data, mimetype=mimetype)
    # Otherwise try a template
    filename = os.path.join(programDir, 'template', name)
    if os.path.exists(filename):
        mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        #
        # xxxjack note that the following set of globals basically exports the
        # whole object hierarchy to templates. This means that a template has
        # unlimited powers. This needs to be fixed at some time, so templates
        # can come from untrusted sources.
        #
        globals = dict(
            igor=_SERVER.igor,
            token=token,
            json=json,
            str=str,
            repr=repr,
            time=time,
            type=type
            )                
        template = web.template.frender(filename, globals=globals)
        try:
            data = template(**dict(allArgs))
        except xmlDatabase.DBAccessError:
            myWebError("401 Unauthorized (template rendering)", 401)
        return Response(str(data), mimetype=mimetype)
    abort(404)


@_WEBAPP.route('/pluginscript/<string:pluginName>/<string:scriptName>')    
def get_pluginscript(pluginName, scriptName):
    allArgs = request.values.to_dict()
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    checker = _SERVER.igor.access.checkerForEntrypoint(request.environ['PATH_INFO'])
    if not checker.allowed('get', token):
        myWebError('401 Unauthorized', 401)

    scriptDir = os.path.join(_SERVER.igor.pathnames.plugindir, pluginName, 'scripts')
        
    if '/' in scriptName or '.' in scriptName:
        myWebError("400 Cannot use / or . in scriptName", 400)
        
    if 'args' in allArgs:
        args = shlex.split(allArgs.args)
    else:
        args = []
    # xxxjack need to check that the incoming action is allowed on this plugin
    # Get the token for the plugin itself
    pluginToken = _SERVER.igor.access.tokenForPlugin(pluginName)
    # Setup global, per-plugin and per-user data for plugin scripts, if available
    env = copy.deepcopy(os.environ)
    try:
        # Tell plugin about our url, if we know it
        myUrl = _SERVER.igor.databaseAccessor.get_key('services/igor/url', 'application/x-python-object', 'content', pluginToken)
        env['IGORSERVER_URL'] = myUrl
        if myUrl[:6] == 'https:':
            env['IGORSERVER_NOVERIFY'] = 'true'
    except werkzeug.exceptions.HTTPError:
        pass # web.ctx.status = "200 OK" # Clear error, otherwise it is forwarded from this request
    try:
        pluginData = _SERVER.igor.databaseAccessor.get_key('plugindata/%s' % (pluginName), 'application/x-python-object', 'content', pluginToken)
    except werkzeug.exceptions.HTTPError:
        pass # web.ctx.status = "200 OK" # Clear error, otherwise it is forwarded from this request
        pluginData = {}
    # Put all other arguments into the environment with an "igor_" prefix
    for k, v in list(allArgs.items()):
        if k == 'args': continue
        if not v:
            v = ''
        env['igor_'+k] = v
    # If a user is logged in we use that as default for a user argument
    if 'user' in _SERVER.igor.session and not 'user' in allArgs:
        allArgs.user = _SERVER.igor.session.user
    # If there's a user argument see if we need to add per-user data
    if 'user' in allArgs:
        user = allArgs.user
        try:
            userData = _SERVER.igor.databaseAccessor.get_key('identities/%s/plugindata/%s' % (user, pluginName), 'application/x-python-object', 'content', token)
        except werkzeug.exceptions.HTTPError:
            pass # web.ctx.status = "200 OK" # Clear error, otherwise it is forwarded from this request
            userData = {}
        if userData:
            pluginData.update(userData)
    # Pass plugin data in environment, as JSON
    if pluginData:
        env['igor_pluginData'] = json.dumps(pluginData)
        if type(pluginData) == type({}):
            for k, v in list(pluginData.items()):
                env['igor_'+k] = str(v)
    # Finally pass the token as an OTP (which has the form user:pass)
    oneTimePassword = _SERVER.igor.access.produceOTPForToken(pluginToken)
    env['IGORSERVER_CREDENTIALS'] = oneTimePassword
    # Check whether we need to use an interpreter on the scriptName
    scriptName = os.path.join(scriptDir, scriptName)
    if os.path.exists(scriptName):
        interpreter = None
    elif os.path.exists(scriptName + '.py'):
        scriptName = scriptName + '.py'
        interpreter = "python"
    elif os.name == 'posix' and os.path.exists(scriptName + '.sh'):
        scriptName = scriptName + '.sh'
        interpreter = 'sh'
    else:
        myWebError("404 scriptName not found: %s" % scriptName, 404)
    if interpreter:
        args = [interpreter, scriptName] + args
    else: # Could add windows and .bat here too, if needed
        args = [scriptName] + args
    # Call the command and get the output
    try:
        rv = subprocess.check_output(args, stderr=subprocess.STDOUT, env=env)
        _SERVER.igor.access.invalidateOTPForToken(oneTimePassword)
    except subprocess.CalledProcessError as arg:
        _SERVER.igor.access.invalidateOTPForToken(oneTimePassword)
        msg = "502 Command %s exited with status code=%d" % (scriptName, arg.returncode)
        output = msg + '\n\n' + arg.output
        # Convenience for internal logging: if there is 1 line of output only we append it to the error message.
        argOutputLines = arg.output.split('\n')
        if len(argOutputLines) == 2 and argOutputLines[1] == '':
            msg += ': ' + argOutputLines[0]
            output = ''
        raise myWebError(msg + '\n' + output, 502)
    except OSError as arg:
        _SERVER.igor.access.invalidateOTPForToken(oneTimePassword)
        myWebError("502 Error running command: %s: %s" % (scriptName, arg.strerror), 502)
    return rv

@_WEBAPP.route('/internal/<string:command>', defaults={'subcommand':None})
@_WEBAPP.route('/internal/<string:command>/<string:subcommand>') 
def get_command(command, subcommand=None):
    allArgs = request.values.to_dict()
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    checker = _SERVER.igor.access.checkerForEntrypoint(request.environ['PATH_INFO'])
    if not checker.allowed('get', token):
        myWebError('401 Unauthorized', 401)

    try:
        method = getattr(_SERVER.igor.internal, command)
    except AttributeError:
        abort(404)
    if subcommand:
        allArgs['subcommand'] = subcommand
    try:
        rv = method(token=token, **dict(allArgs))
    except TypeError as arg:
        raise #myWebError("400 Error in command method %s parameters: %s" % (command, arg))
    if rv == None:
        return ''
    if isinstance(rv, str):
        return Response(rv, mimetype='text/plain')
    return Response(json.dumps(rv), mimetype='application/json')

@_WEBAPP.route('/internal/<string:command>', defaults={'subcommand':None}, methods=["POST"])
@_WEBAPP.route('/internal/<string:command>/<string:subcommand>', methods=["POST"]) 
def post_command(command, subcommand=None):
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    try:
        method = getattr(_SERVER.igor.internal, command)
    except AttributeError:
        abort(404)
    if request.is_json:
        allArgs = request.get_json()
    else:
        allArgs = {}
    if subcommand:
        allArgs['subcommand'] = subcommand
    try:
        rv = method(token=token, **allArgs)
    except TypeError as arg:
        myWebError("400 Error in command method %s parameters: %s" % (command, arg), 400)
    return rv

@_WEBAPP.route('/action/<string:actionname>')
def get_action(actionname):
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    checker = _SERVER.igor.access.checkerForEntrypoint(request.environ['PATH_INFO'])
    if not checker.allowed('get', token):
        myWebError('401 Unauthorized', 401)

    try:
        return _SERVER.igor.internal.runAction(actionname, token)
    except xmlDatabase.DBAccessError:
        myWebError("401 Unauthorized (while running action)", 401)
        
@_WEBAPP.route('/trigger/<string:triggername>')
def get_trigger(triggername):
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    checker = _SERVER.igor.access.checkerForEntrypoint(request.environ['PATH_INFO'])
    if not checker.allowed('get', token):
        myWebError('401 Unauthorized', 401)

    try:
        return _SERVER.igor.internal.runTrigger(triggername, token)
    except xmlDatabase.DBAccessError:
        myWebError("401 Unauthorized (while running trigger)", 401)
        
@_WEBAPP.route('/plugin/<string:pluginname>', defaults={'methodName':'index'})
@_WEBAPP.route('/plugin/<string:pluginname>/<string:methodName>')
def get_plugin(pluginName, methodName='index'):
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    checker = _SERVER.igor.access.checkerForEntrypoint(request.environ['PATH_INFO'])
    if not checker.allowed('get', token):
        myWebError('401 Unauthorized', 401)

    #
    # Import plugin as a submodule of igor.plugins
    #
    import igor.plugins # Make sure the base package exists
    moduleName = 'igor.plugins.'+pluginName
    if moduleName in sys.modules:
        # Imported previously.
        pluginModule = sys.modules[moduleName]
    else:
        # New. Try to import.
        moduleDir = os.path.join(_SERVER.igor.pathnames.plugindir, pluginName)
        try:
            mfile, mpath, mdescr = imp.find_module('igorplugin', [moduleDir])
            pluginModule = imp.load_module(moduleName, mfile, mpath, mdescr)
        except ImportError:
            print('------ import failed for', pluginName)
            traceback.print_exc()
            print('------')
            abort(404)
        pluginModule.SESSION = _SERVER.igor.session  # xxxjack
        pluginModule.IGOR = _SERVER.igor
    allArgs = request.values.to_dict()

    # xxxjack need to check that the incoming action is allowed on this plugin
    # Get the token for the plugin itself
    pluginToken = _SERVER.igor.access.tokenForPlugin(pluginName, token=token)
    allArgs['token'] = pluginToken
    
    # Find plugindata and per-user plugindata
    try:
        pluginData = _SERVER.igor.databaseAccessor.get_key('plugindata/%s' % (pluginName), 'application/x-python-object', 'content', pluginToken)
    except werkzeug.exceptions.HTTPError:
        pass # web.ctx.status = "200 OK" # Clear error, otherwise it is forwarded from this request
        pluginData = {}
    try:
        factory = getattr(pluginModule, 'igorPlugin')
    except AttributeError:
        myWebError("501 Plugin %s problem: misses igorPlugin() method" % (pluginName), 501)
    #
    # xxxjack note that the following set of globals basically exports the
    # whole object hierarchy to plugins. This means that a plugin has
    # unlimited powers. This needs to be fixed at some time, so plugin
    # can come from untrusted sources.
    #
    pluginObject = factory(_SERVER.igor, pluginName, pluginData)
    #
    # If there is a user argument also get userData
    #
    if 'user' in allArgs:
        user = allArgs['user']
        try:
            userData = _SERVER.igor.databaseAccessor.get_key('identities/%s/plugindata/%s' % (user, pluginName), 'application/x-python-object', 'content', pluginToken)
        except werkzeug.exceptions.HTTPError:
            pass # web.ctx.status = "200 OK" # Clear error, otherwise it is forwarded from this request
        else:
            allArgs['userData'] = userData
    #
    # Find the method and call it.
    #
    try:
        method = getattr(pluginObject, methodName)
    except AttributeError:
        print('----- Method', methodName, 'not found in', pluginObject)
        abort(404)
    try:
        rv = method(**dict(allArgs))
    except ValueError as arg:
        myWebError("400 Error in plugin method %s/%s parameters: %s" % (pluginName, methodName, arg), 400)
    if rv == None:
        rv = ''
    if not isinstance(rv, basestring):
        rv = str(rv)
    return rv

@_WEBAPP.route('/evaluate/<path:command>')
def get_evaluate(command):
    """Evaluate an XPath expression and return the result as plaintext"""
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    return _SERVER.igor.databaseAccessor.get_value(command, token)
    
@_WEBAPP.route('/login', methods=["GET", "POST"])
def getOrPost_login():
    allArgs = request.values.to_dict()
    if 'logout' in allArgs:
        _SERVER.igor.session.user = None
        redirect('/')
    message = None
    username = allArgs.get('username')
    password = allArgs.get('password')
    if username:
        if _SERVER.igor.access.userAndPasswordCorrect(username, password):
            _SERVER.igor.session.user = username
            redirect('/')
        message = "Password and/or username incorrect."
    form = web.form.Form(
        web.form.Textbox('username'),
        web.form.Password('password'),
        web.form.Button('login'),
        )
    programDir = os.path.dirname(__file__)
    template = web.template.frender(os.path.join(programDir, 'template', '_login.html'))
    return str(template(form, _SERVER.igor.session.get('user'), message))


@_WEBAPP.route('/data/', defaults={'name':''})
@_WEBAPP.route('/data/<path:name>')
def get_data(name):
    """Abstract database that handles the high-level HTTP GET.
    If no query get the content of a section of the database.
    If there is a query can be used as a 1-url shortcut for POST."""
    optArgs = request.values.to_dict()
    # See whether we have a variant request
    variant = None
    if '.VARIANT' in optArgs:
        variant = optArgs['.VARIANT']
        del optArgs['.VARIANT']
        
    if optArgs:
        # GET with a query is treated as POST with the query as JSON data,
        # unless the .METHOD argument states it should be treaded as another method.
        optArgs = dict(optArgs)
        method = putOrPost_data
        if '.METHOD' in optArgs:
            methods = {
                'PUT' : putOrPost_data,
                'POST' : putOrPost_data,
                'DELETE' : delete_data,
                }
            method = getattr(methods, optArgs['.METHOD'])
            del optArgs['.METHOD']
        rv = method(name, optArgs, mimetype="application/x-www-form-urlencoded")
        return rv
        
    token = _SERVER.igor.access.tokenForRequest(request.environ)

    returnType = _best_return_mimetype()
    if not returnType:
        abort(406)
    rv = _SERVER.igor.databaseAccessor.get_key(name, _best_return_mimetype(), variant, token)
    return Response(rv, mimetype=returnType)

@_WEBAPP.route('/data/<path:name>', methods=["PUT", "POST"])
def putOrPost_data(name, data=None, mimetype=None, replace=True):
    """Replace part of the document with new data, or inster new data
    in a specific location.
    """
    optArgs = request.values.to_dict()
    token = _SERVER.igor.access.tokenForRequest(request.environ)

    # See whether we have a variant request
    variant = None
    if '.VARIANT' in optArgs:
        variant = optArgs['.VARIANT']
        del optArgs['.VARIANT']
    
    if not data:
        # We either have a url-encoded query in optArgs or read raw data
        if request.values:
            data = request.values.to_dict()
            mimetype = "application/x-www-form-urlencoded"
        else:
            data = request.text
            mimetype = request.environ.get('CONTENT_TYPE', 'application/unknown')
    returnType = _best_return_mimetype()
    rv = _SERVER.igor.databaseAccessor.put_key(name, returnType, variant, data, mimetype, token, replace=replace)
    return Response(rv, mimetype=returnType)

@_WEBAPP.route('/data/<path:name>', methods=["DELETE"])
def delete_data(name, data=None, mimetype=None):
    token = _SERVER.igor.access.tokenForRequest(request.environ)
    rv = _SERVER.igor.databaseAccessor.delete_key(name, token)
    return rv

def _best_return_mimetype():
    """Return the best mimetype in which to encode the return data, or None"""
    if not _SERVER.igor.databaseAccessor.MIMETYPES:
        return None
    acceptable = request.environ.get("HTTP_ACCEPT")
    if not acceptable:
        return _SERVER.igor.databaseAccessor.MIMETYPES[0]
    return mimetypematch.match(acceptable, _SERVER.igor.databaseAccessor.MIMETYPES)

class XmlDatabaseAccess(object):
    """Class to access the database in a somewhat rest-like manner. Instantiated once, in the igor object."""
    
    MIMETYPES = ["application/xml", "application/json", "text/plain"]
    
    def __init__(self, igor):
        self.igor = igor
        self.rootTag = self.igor.database.getDocument(self.igor.access.tokenForIgor()).tagName
        
    def get_key(self, key, mimetype, variant, token):
        """Get subtree for 'key' as 'mimetype'. Variant can be used
        to state which data should be returned (single node, multinode,
        including refs, etc)"""
        try:
            if not key:
                rv = [self.igor.database.getDocument(token)]
                # This always returns XML, so just continue
            else:
                if key[0] != '/':
                    key = '/%s/%s' % (self.rootTag, key)
                rv = self.igor.database.getElements(key, 'get', token)
            rv = self.convertto(rv, mimetype, variant)
            return rv
        except xmlDatabase.DBAccessError:
            myWebError("401 Unauthorized", 401)
        except xpath.XPathError as arg:
            myWebError("400 XPath error: %s" % str(arg), 401)
        except xmlDatabase.DBKeyError as arg:
            myWebError("400 Database Key Error: %s" % str(arg), 400)
        except xmlDatabase.DBParamError as arg:
            myWebError("400 Database Parameter Error: %s" % str(arg), 400)
        
    def get_value(self, expression, token):
        """Evaluate a general expression and return the string value"""
        try:
            return self.igor.database.getValue(expression, token=token)
        except xmlDatabase.DBAccessError:
            myWebError("401 Unauthorized", 401)
        except xpath.XPathError as arg:
            myWebError("400 XPath error: %s" % str(arg), 400)
        except xmlDatabase.DBKeyError as arg:
            myWebError("400 Database Key Error: %s" % str(arg), 400)
        except xmlDatabase.DBParamError as arg:
            myWebError("400 Database Parameter Error: %s" % str(arg), 400)
        
    def put_key(self, key, mimetype, variant, data, datamimetype, token, replace=True):
        try:
            if not key:
                myWebError("400 cannot PUT or POST whole document", 400)
            if key[0] != '/':
                key = '/%s/%s' % (self.rootTag, key)
            if not variant: variant = 'ref'
            nodesToSignal = []
            with self.igor.database:
                unchanged = False
                parentPath, tag = self.igor.database.splitXPath(key)
                if not tag:
                    myWebError("400 PUT path must end with an element tag", 400)
                element = self.convertfrom(data, tag, datamimetype)
                oldElements = self.igor.database.getElements(key, 'put' if replace else 'post', token)
                if not oldElements:
                    #
                    # Does not exist yet. See if we can create it
                    #
                    if not parentPath or not tag:
                        myWebError("404 Not Found, parent or tag missing", 404)
                    #
                    # Find parent
                    #
                    # NOTE: we pass the tagname for the child element. This is so put rights on a
                    # child that does not exist yet can be checked.
                    parentElements = self.igor.database.getElements(parentPath, 'post', token, postChild=tag)
                    if not parentElements:
                        myWebError("404 Parent not found: %s" % parentPath, 404)
                    if len(parentElements) > 1:
                        myWebError("400 Bad request, XPath parent selects multiple items", 400)
                    parent = parentElements[0]
                    #
                    # Add new node to the end of the parent
                    #
                    parent.appendChild(element)
                    #
                    # Signal both parent and new node
                    #
                    nodesToSignal += xmlDatabase.recursiveNodeSet(element)
                    nodesToSignal += xmlDatabase.nodeSet(parent)
                else:
                    #
                    # Already exists. Check that it exists only once.
                    #
                    if len(oldElements) > 1:
                        parent1 = oldElements[0].parentNode
                        for otherNode in oldElements[1:]:
                            if otherNode.parentNode != parent1:
                                myWebError("400 Bad Request, XPath selects multiple items from multiple parents", 400)
                            
                    oldElement = oldElements[0]
                    if replace:
                        if len(oldElements) > 1:
                            myWebError("400 Bad PUT Request, XPath selects multiple items", 400)
                        #
                        # We should really do a selective replace here: change only the subtrees that need replacing.
                        # That will make the signalling much more fine-grained. Will do so, at some point in the future.
                        #
                        # For now we replace the first matching node and delete its siblings, but only if the new content
                        # is not identical to the old
                        #
                        if self.igor.database.identicalSubTrees(oldElement, element):
                            unchanged = True
                        else:
                            parent = oldElement.parentNode
                            parent.replaceChild(element, oldElement)
                            nodesToSignal += xmlDatabase.recursiveNodeSet(element)
                    else:
                        #
                        # POST, simply append the new node to the parent (and signal that parent)
                        #
                        parent = oldElement.parentNode
                        parent.appendChild(element)
                        nodesToSignal += xmlDatabase.recursiveNodeSet(element)
                        nodesToSignal += xmlDatabase.nodeSet(parent)
                    #
                    # We want to signal the new node
                    #
                
                if nodesToSignal: self.igor.database.signalNodelist(nodesToSignal)
                path = self.igor.database.getXPathForElement(element)
                rv = self.convertto(path, mimetype, variant)
                resp = Response(rv, mimetype=mimetype)
                if unchanged:
                    resp.status_code = 200
                return resp
        except xmlDatabase.DBAccessError:
            myWebError("401 Unauthorized", 401)
        except xpath.XPathError as arg:
            myWebError("400 XPath error: %s" % str(arg), 400)
        except xmlDatabase.DBKeyError as arg:
            myWebError("400 Database Key Error: %s" % str(arg), 400)
        except xmlDatabase.DBParamError as arg:
            myWebError("400 Database Parameter Error: %s" % str(arg), 400)
        
    def delete_key(self, key, token):
        try:
            key = '/%s/%s' % (self.rootTag, key)
            self.igor.database.delValues(key, token)
            return ''
        except xmlDatabase.DBAccessError:
            myWebError("401 Unauthorized", 401)
        except xpath.XPathError as arg:
            myWebError("400 XPath error: %s" % str(arg), 400)
        except xmlDatabase.DBKeyError as arg:
            myWebError("400 Database Key Error: %s" % str(arg), 400)
        
    def convertto(self, value, mimetype, variant):
        if variant == 'ref':
            if not isinstance(value, basestring):
                myWebError("400 Bad request, cannot use .VARIANT=ref for this operation", 400)
            if mimetype == "application/json":
                return json.dumps({"ref":value})+'\n'
            elif mimetype == "text/plain":
                return value+'\n'
            elif mimetype == "application/xml":
                return "<ref>%s</ref>\n" % value
            elif mimetype == "application/x-python-object":
                return value
            else:
                myWebError("500 Unimplemented mimetype %s for ref" % mimetype, 500)
        # Only nodesets need different treatment for default and multi
        if not isinstance(value, list):
            if mimetype == "application/json":
                return json.dumps(dict(value=value))+'\n'
            elif mimetype == "text/plain":
                return str(value)+'\n'
            elif mimetype == "application/xml":
                return u"<value>%s</value>\n" % str(value)
            elif mimetype == "application/x-python-object":
                return value
            else:
                myWebError("500 Unimplemented mimetype %s for default or multi, simple value" % mimetype, 500)
        if variant in ('multi', 'multiraw'):
            if mimetype == "application/json":
                rv = []
                for item in value:
                    r = self.igor.database.getXPathForElement(item)
                    t, v = self.igor.database.tagAndDictFromElement(item, stripHidden=(variant != 'multiraw'))
                    rv.append({"ref":r, t:v})
                return json.dumps(rv)+'\n'
            elif mimetype == "text/plain":
                myWebError("400 Bad request, cannot use .VARIANT=multi for mimetype text/plain", 400)
            elif mimetype == "application/xml":
                rv = "<items>\n"
                for item in value:
                    r = self.igor.database.getXPathForElement(item)
                    v = self.igor.database.xmlFromElement(item, stripHidden=(variant != 'multiraw'))
                    rv += "<item>\n<ref>%s</ref>\n" % r
                    rv += v
                    rv += "\n</item>\n"
                rv += "</items>\n"
                return rv
            elif mimetype == "application/x-python-object":
                rv = {}
                for item in value:
                    r = self.igor.database.getXPathForElement(item)
                    t, v = self.igor.database.tagAndDictFromElement(item, stripHidden=(variant != 'multiraw'))
                    rv[r] = v
                return rv
            else:
                myWebError("500 Unimplemented mimetype %s for multi, nodeset" % mimetype, 500)
        # Final case: single node return (either no variant or variant='raw')
        if len(value) == 0:
            abort(404)
        if mimetype == "application/json":
            if len(value) > 1:
                myWebError("400 Bad request, cannot return multiple items without .VARIANT=multi", 400)
            t, v = self.igor.database.tagAndDictFromElement(value[0], stripHidden=(variant != 'raw'))
            if variant == "content":
                rv = json.dumps(v)
            else:
                rv = json.dumps({t:v})
            return rv+'\n'
        elif mimetype == "text/plain":
            rv = ""
            for item in value:
                # xxxjack if variant != raw, will this leak information?
                v = xpath.expr.string_value(item)
                rv += v
                rv += '\n'
            return rv
        elif mimetype == "application/xml":
            if len(value) > 1:
                myWebError("400 Bad request, cannot return multiple items without .VARIANT=multi", 400)
            return self.igor.database.xmlFromElement(value[0], stripHidden=(variant != 'raw'))+'\n'
        elif mimetype == 'application/x-python-object':
            if len(value) > 1:
                myWebError("400 Bad request, cannot return multiple items without .VARIANT=multi", 400)
            t, v = self.igor.database.tagAndDictFromElement(value[0], stripHidden=(variant != 'raw'))
            return v

        else:
            myWebError("500 Unimplemented mimetype %s for default, single node" % mimetype, 500)
        
    def convertfrom(self, value, tag, mimetype):
        if mimetype == 'application/xml':
            if type(value) != type(''):
                value = value.decode('utf-8')
            element = self.igor.database.elementFromXML(value)
            if element.tagName != tag:
                myWebError("400 Bad request, toplevel XML tag %s does not match final XPath element %s" % (element.tagName, tag), 400)
            return element
        elif mimetype == 'application/x-www-form-urlencoded':
            # xxxjack here comes a special case, and I don't like it.
            # if the url-encoded data contains exactly one element and its name is the same as the
            # tag name we don't encode.
            if type(value) == type({}) and len(value) == 1 and tag in value:
                value = value[tag]
            element = self.igor.database.elementFromTagAndData(tag, value)
            return element
        elif mimetype == 'application/json':
            try:
                valueDict = json.loads(value)
            except ValueError:
                myWebError("400 No JSON object could be decoded from body", 400)
            if not isinstance(valueDict, dict):
                myWebError("400 Bad request, JSON toplevel object must be object", 400)
            # xxxjack here comes a special case, and I don't like it.
            # if the JSON dictionary contains exactly one element and its name is the same as the
            # tag name we don't encode.
            if len(valueDict) == 1 and tag in valueDict:
                element = self.igor.database.elementFromTagAndData(tag, valueDict[tag])
            else:
                element = self.igor.database.elementFromTagAndData(tag, valueDict)
            return element
        elif mimetype == 'text/plain':
            # xxxjack should check that value is a string or unicode
            if type(value) != type(''):
                value = value.decode('utf-8')
            element = self.igor.database.elementFromTagAndData(tag, value)
            return element
        elif mimetype == 'application/x-python-object':
            element = self.igor.database.elementFromTagAndData(tag, value)
            return element
        else:
            myWebError("500 Conversion from %s not implemented" % mimetype, 500)
        
