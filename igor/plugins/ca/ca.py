import web
import json
import igorCA
import os
import sys

DEBUG=False

DATABASE_ACCESS=None
PLUGINDATA=None
COMMANDS=None

def myWebError(msg):
    return web.HTTPError(msg, {"Content-type": "text/plain"}, msg+'\n\n')

INDEX_HTML="""<html lang="en">
<head>
	<meta http-equiv="content-type" content="text/html; charset=utf-8">
	<title>Igor Certificate Authority</title>
</head>
<body>
	<h1>Igor Certificate Authority</h1>
	<h2>Root Certificate Chain</h2>
	<p>To trust certificates signed by this Igor CA, download the <a href="ca/root">root certificate chain</a> and install in your browser or system.</p>
	<p>If available, the <a href="/static/crl.pem">Certificate Revocation List</a> can be downloaded too.</p>
	<h2>Listing all Certificates</h2>
	<p>To list certificates signed by this Igor CA, see the <a href="ca/list">certificate listing</a>.</p>
	<p>CRL and Revocation to be done later.</p>
	<h2>Signing a certificate</h2>
	<p>Create a key and CSR (Certificate Signing Request) locally, possibly using the <i>igorCA csr</i> command.</p>
	<p>Enter the CSR in PEM for in the following field and submit.</p>
	<form action="ca/sign">
	<textarea name="csr" rows="8" cols="60"></textarea>
	<br>
	<input type="submit" value="Submit">
	</form>
	<p>The result is the (PEM-encoded) certificate you can use for your service (together with the key form the previous step).</p>
</body>
</html>
"""

class CAPlugin:
    def __init__(self):
        self.ca = None
    
    def initCA(self):
        if self.ca: return
        self.ca = igorCA.IgorCA('igor/plugins/ca')
        
    def index(self, token=None):
        self.initCA()
        return INDEX_HTML
    
    def list(self, token=None):
        self.initCA()
        listData = self.ca.do_list()
        web.header('Content-type', 'text/plain')
        return listData
        
    def status(self, token=None):
        self.initCA()
        statusData = self.ca.do_status()
        web.header('Content-type', 'text/plain')
        return statusData

    def dn(self, token=None):
        self.initCA()
        dnData = self.ca.do_dn()
        web.header('Content-type', 'application/json')
        return dnData
        
    def csrtemplate(self, token=None):
        self.initCA()
        tmplData = self.ca.do_csrtemplate()
        web.header('Content-type', 'text/plain')
        return tmplData
        
    def sign(self, csr, token=None):
        self.initCA()
        cert = self.ca.do_signCSR(csr)
        if not cert:
            raise myWebError('Could not sign certificate')
        web.header('Content-type', 'application/x-pem-file')
        web.header('Content-Disposition', 'attachment; filename="certificate.pem"')
        return cert
        
    def root(self, token=None):
        self.initCA()
        chain = self.ca.do_getRoot()
        if not chain:
            raise myWebError('Could not obtain root certificate chain')
        web.header('Content-type', 'application/x-pem-file')
        web.header('Content-Disposition', 'attachment; filename="igor-root-certificate-chain.pem"')
        return chain

def igorPlugin(pluginName, pluginData):
    return CAPlugin()
    